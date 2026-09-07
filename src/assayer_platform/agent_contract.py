"""Versioned executable contracts for Agent-facing plugin semantics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any

from .contract import PlatformContractError


AGENT_CONTRACT_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
AGENT_CONTRACT_CANONICALIZATION_VERSION = "1.0.0"


def _plain_json(value: Any, *, location: str) -> Any:
    """Return a detached JSON value or reject unsupported contract data."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PlatformContractError(
                    "INVALID_AGENT_CONTRACT_BUNDLE",
                    f"Agent contract keys must be strings at {location}",
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
        "INVALID_AGENT_CONTRACT_BUNDLE",
        f"Agent contract contains a non-JSON value at {location}",
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True, init=False)
class AgentContractBundle:
    """One immutable executable Agent contract bound to one plugin Check.

    Schemas are stored as canonical JSON rather than caller-owned dictionaries,
    so later mutation cannot change the bundle or its digest.
    """

    contract_id: str
    contract_version: str
    check_id: str
    check_version: str
    semantic_instructions_path: str
    semantic_instructions_sha256: str
    schema_dialect: str
    _checkpoint_payload_schemas_json: str = field(repr=False)
    _finalization_schema_json: str = field(repr=False)
    _contract_digest: str = field(repr=False)

    def __init__(
        self,
        contract_id: str,
        contract_version: str,
        check_id: str,
        check_version: str,
        checkpoint_payload_schemas: Mapping[str, Mapping[str, Any]],
        finalization_schema: Mapping[str, Any] | None,
        semantic_instructions_path: str,
        semantic_instructions_sha256: str,
        *,
        schema_dialect: str = AGENT_CONTRACT_SCHEMA_DIALECT,
    ) -> None:
        if not isinstance(checkpoint_payload_schemas, Mapping):
            raise PlatformContractError(
                "INVALID_AGENT_CONTRACT_BUNDLE",
                "Agent contract checkpoint payload schemas must be an object",
            )
        checkpoint_schemas = _plain_json(
            checkpoint_payload_schemas, location="/checkpointPayloadSchemas",
        )
        if finalization_schema is not None and not isinstance(finalization_schema, Mapping):
            raise PlatformContractError(
                "INVALID_AGENT_CONTRACT_BUNDLE",
                "Agent contract finalization schema must be an object or null",
            )
        finalization = _plain_json(
            finalization_schema, location="/finalizationSchema",
        )
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
                    "INVALID_AGENT_CONTRACT_BUNDLE",
                    f"Agent contract {name} must be a string",
                )
            object.__setattr__(self, name, value)
        object.__setattr__(
            self, "_checkpoint_payload_schemas_json", _canonical_json(checkpoint_schemas),
        )
        object.__setattr__(
            self, "_finalization_schema_json", _canonical_json(finalization),
        )
        object.__setattr__(self, "_contract_digest", self._compute_digest())

    @property
    def check_ref(self) -> tuple[str, str]:
        return self.check_id, self.check_version

    @property
    def checkpoint_payload_schemas(self) -> dict[str, dict[str, Any]]:
        return json.loads(self._checkpoint_payload_schemas_json)

    @property
    def finalization_schema(self) -> dict[str, Any] | None:
        return json.loads(self._finalization_schema_json)

    @property
    def contract_digest(self) -> str:
        return self._contract_digest

    def as_dict(self, *, include_digest: bool = False) -> dict[str, Any]:
        value = {
            "contractId": self.contract_id,
            "contractVersion": self.contract_version,
            "checkId": self.check_id,
            "checkVersion": self.check_version,
            "schemaDialect": self.schema_dialect,
            "checkpointPayloadSchemas": self.checkpoint_payload_schemas,
            "finalizationSchema": self.finalization_schema,
            "semanticInstructions": {
                "path": self.semantic_instructions_path,
                "sha256": self.semantic_instructions_sha256,
            },
        }
        if include_digest:
            value["contractDigest"] = self.contract_digest
        return value

    def _compute_digest(self) -> str:
        preimage = {
            "canonicalizationVersion": AGENT_CONTRACT_CANONICALIZATION_VERSION,
            "bundle": self.as_dict(),
        }
        return "sha256:" + hashlib.sha256(_canonical_json(preimage).encode("utf-8")).hexdigest()


__all__ = [
    "AGENT_CONTRACT_CANONICALIZATION_VERSION",
    "AGENT_CONTRACT_SCHEMA_DIALECT",
    "AgentContractBundle",
]
