"""Versioned executable contracts for Agent-facing plugin semantics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from .contract import PlatformContractError


DOMAIN_RESULT_CONTRACT_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION = "1.0.0"

# These names belong to the Host protocol, never to a model-authored domain
# result.  Rejecting them at contract construction prevents a plugin from
# accidentally recreating the platform envelope inside its own Schema.
_PLATFORM_RESULT_FIELDS = frozenset({
    "runId", "run_id", "workItemId", "work_item_id", "collectionId",
    "collection_id", "itemIds", "item_ids", "taskDigest", "task_digest",
    "contractDigest", "contract_digest", "finalization",
    "revision", "runRevision", "run_revision", "operationId", "operation_id",
    "checkId", "checkVersion",
})


def _plain_json(value: Any, *, location: str) -> Any:
    """Return a detached JSON value or reject unsupported contract data."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain result contract keys must be strings at {location}",
                )
            result[key] = _plain_json(item, location=f"{location}/{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _plain_json(item, location=f"{location}/{index}")
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise PlatformContractError(
        "INVALID_DOMAIN_RESULT_CONTRACT",
        f"Domain result contract contains a non-JSON value at {location}",
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _reject_platform_result_fields(value: Any, *, location: str) -> None:
    """Reject platform envelope fields anywhere in a domain-result Schema."""
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            forbidden = sorted(set(properties) & _PLATFORM_RESULT_FIELDS)
            if forbidden:
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    "Domain result schema declares platform-owned field(s) "
                    f"{', '.join(forbidden)} at {location}/properties",
                )
        for key, item in value.items():
            _reject_platform_result_fields(item, location=f"{location}/{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_platform_result_fields(item, location=f"{location}/{index}")


def _schema_pointer(path: Sequence[Any]) -> str:
    """Return an RFC 6901 pointer for a contract-schema location."""
    if not path:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1")
        for part in path
    )


def _domain_result_shape(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a bounded, model-friendly shape summary from ``resultSchema``.

    The JSON Schema remains the sole executable authority.  This summary is
    deliberately derived from it rather than maintained by a plugin, so a
    required-field rename cannot leave the Agent with stale prose or examples.
    It is guidance only; the Host still validates the complete schema before
    invoking plugin code.
    """
    required: list[str] = []
    allowed: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    definitions = schema.get("$defs", {})

    def visit(node: Any, path: tuple[Any, ...], active_refs: frozenset[str] = frozenset()) -> None:
        if not isinstance(node, Mapping):
            return
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            key = reference.rsplit("/", 1)[-1]
            target = definitions.get(key) if isinstance(definitions, Mapping) else None
            if isinstance(target, Mapping) and reference not in active_refs:
                visit(target, path, active_refs | {reference})
            return
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            required_names = node.get("required", ())
            required_set = set(required_names) if isinstance(required_names, (list, tuple)) else set()
            for name, child in properties.items():
                if not isinstance(name, str):
                    continue
                child_path = (*path, name)
                pointer = _schema_pointer(child_path)
                if child_path not in seen:
                    seen.add(child_path)
                    allowed.append(pointer)
                if name in required_set:
                    required.append(pointer)
                visit(child, child_path, active_refs)
        items = node.get("items")
        if isinstance(items, Mapping):
            # Array members are described once with a wildcard segment; this
            # avoids pretending that a collection has a fixed length.
            visit(items, (*path, "*"), active_refs)

    visit(schema, ())
    return {
        "requiredPaths": sorted(required),
        "allowedPaths": sorted(allowed),
        "strict": schema.get("additionalProperties") is False,
    }


@dataclass(frozen=True, init=False)
class DomainResultContract:
    """One executable, platform-envelope-free domain result contract.

    This is the target Agent-facing contract for SDK v2.  It describes only
    the value the Agent is allowed to author; task identity, lifecycle state,
    and persistence metadata remain Host-owned.
    """

    contract_id: str
    contract_version: str
    check_id: str
    check_version: str
    semantic_instructions_path: str
    semantic_instructions_sha256: str
    schema_dialect: str
    _result_schema_json: str = field(repr=False)
    _semantic_rules_json: str = field(repr=False)
    _contract_digest: str = field(repr=False)

    def __init__(
        self,
        contract_id: str,
        contract_version: str,
        check_id: str,
        check_version: str,
        result_schema: Mapping[str, Any],
        semantic_instructions_path: str,
        semantic_instructions_sha256: str,
        *,
        semantic_rules: Sequence[Mapping[str, Any]] = (),
        schema_dialect: str = DOMAIN_RESULT_CONTRACT_SCHEMA_DIALECT,
    ) -> None:
        if not isinstance(result_schema, Mapping):
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT_CONTRACT",
                "Domain result schema must be an object",
            )
        schema = _plain_json(result_schema, location="/resultSchema")
        if schema.get("type") != "object":
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT_CONTRACT",
                "Domain result schema must describe a top-level object",
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT_CONTRACT",
                f"Domain result schema is not valid Draft 2020-12 JSON Schema: {error.message}",
            ) from error
        _reject_platform_result_fields(schema, location="/resultSchema")
        if not isinstance(semantic_rules, Sequence) or isinstance(
            semantic_rules, (str, bytes, bytearray),
        ):
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT_CONTRACT",
                "Domain semantic rules must be an array",
            )
        rules = _plain_json(semantic_rules, location="/semanticRules")
        seen_rule_ids: set[str] = set()
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain semantic rule /semanticRules/{index} must be an object",
                )
            rule_id = rule.get("ruleId")
            instruction = rule.get("instruction")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain semantic rule /semanticRules/{index} requires ruleId",
                )
            if rule_id in seen_rule_ids:
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain semantic ruleId must be unique: {rule_id}",
                )
            if not isinstance(instruction, str) or not instruction.strip():
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain semantic rule {rule_id} requires instruction",
                )
            seen_rule_ids.add(rule_id)
        values = {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "check_id": check_id,
            "check_version": check_version,
            "semantic_instructions_path": semantic_instructions_path,
            "semantic_instructions_sha256": semantic_instructions_sha256,
            "schema_dialect": schema_dialect,
        }
        for name, value in values.items():
            if not isinstance(value, str):
                raise PlatformContractError(
                    "INVALID_DOMAIN_RESULT_CONTRACT",
                    f"Domain result contract {name} must be a string",
                )
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_result_schema_json", _canonical_json(schema))
        object.__setattr__(self, "_semantic_rules_json", _canonical_json(rules))
        object.__setattr__(self, "_contract_digest", self._compute_digest())

    @property
    def check_ref(self) -> tuple[str, str]:
        return self.check_id, self.check_version

    @property
    def result_schema(self) -> dict[str, Any]:
        return json.loads(self._result_schema_json)

    @property
    def semantic_rules(self) -> list[dict[str, Any]]:
        return json.loads(self._semantic_rules_json)

    @property
    def result_shape(self) -> dict[str, Any]:
        """Return a deterministic summary derived from the executable schema."""
        return _domain_result_shape(self.result_schema)

    @property
    def contract_digest(self) -> str:
        return self._contract_digest

    def as_dict(self, *, include_digest: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "contractId": self.contract_id,
            "contractVersion": self.contract_version,
            "checkId": self.check_id,
            "checkVersion": self.check_version,
            "schemaDialect": self.schema_dialect,
            "inputKind": "domainResult",
            "resultSchema": self.result_schema,
            "semanticInstructions": {
                "path": self.semantic_instructions_path,
                "sha256": self.semantic_instructions_sha256,
            },
        }
        if self.semantic_rules:
            value["semanticRules"] = self.semantic_rules
        if include_digest:
            value["contractDigest"] = self.contract_digest
        return value

    def _compute_digest(self) -> str:
        preimage = {
            "canonicalizationVersion": DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION,
            "bundle": self.as_dict(),
        }
        return "sha256:" + hashlib.sha256(_canonical_json(preimage).encode("utf-8")).hexdigest()


__all__ = [
    "DOMAIN_RESULT_CONTRACT_SCHEMA_DIALECT",
    "DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION",
    "DomainResultContract",
]
