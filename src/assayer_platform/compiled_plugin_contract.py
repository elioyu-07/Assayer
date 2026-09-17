"""The single executable truth compiled from an ordinary plugin declaration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from .contract import PlatformContractError


COMPILED_PLUGIN_CONTRACT = "compiled-plugin.json"
def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def canonical_contract_bytes(value: Mapping[str, Any]) -> bytes:
    """Canonicalize a contract without its self-referential digest field."""
    payload = _plain({str(key): item for key, item in value.items() if key != "contractDigest"})
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def contract_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_bytes(value)).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CompiledPluginContract:
    """Validated, immutable view of a data-only ordinary plugin contract."""

    payload: Mapping[str, Any]
    digest: str

    @property
    def plugin_id(self) -> str:
        return str(self.payload["plugin"]["id"])

    @property
    def version(self) -> str:
        return str(self.payload["plugin"]["version"])

    @property
    def input_kind(self) -> str:
        return str(self.payload["input"]["kind"])


def validate_compiled_plugin_contract(value: Any) -> tuple[str, ...]:
    try:
        from .registry import schema_path

        schema = json.loads(
            schema_path("compiled-plugin-contract.schema.json").read_text(encoding="utf-8"),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, PlatformContractError) as error:
        return (f"compiled contract schema is unavailable: {error}",)
    errors = [
        f"{error.json_path}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(value)
    ]
    if errors or not isinstance(value, Mapping):
        return tuple(sorted(errors))
    expected_digest = contract_digest(value)
    if value.get("contractDigest") != expected_digest:
        errors.append("$.contractDigest: digest does not match the canonical contract")
    semantic = value.get("semanticReview")
    if isinstance(semantic, Mapping) and isinstance(semantic.get("text"), str):
        expected_semantic = hashlib.sha256(semantic["text"].encode("utf-8")).hexdigest()
        if semantic.get("sha256") != expected_semantic:
            errors.append("$.semanticReview.sha256: digest does not match semanticReview.text")
    checks = value.get("checks")
    if isinstance(checks, list):
        check_ids = [item.get("id") for item in checks if isinstance(item, Mapping)]
        if len(check_ids) != len(set(check_ids)):
            errors.append("$.checks: Check IDs must be unique")
        declared = set(check_ids)
        cases = value.get("cases")
        if isinstance(cases, list):
            case_ids = [item.get("id") for item in cases if isinstance(item, Mapping)]
            if len(case_ids) != len(set(case_ids)):
                errors.append("$.cases: case IDs must be unique")
            unknown = sorted({item.get("checkId") for item in cases if isinstance(item, Mapping)} - declared)
            if unknown:
                errors.append("$.cases: cases reference unknown Checks: " + ", ".join(map(str, unknown)))
    return tuple(sorted(errors))


def load_compiled_plugin_contract(path: str | Path) -> CompiledPluginContract:
    target = Path(path).expanduser().resolve()
    if target.is_dir():
        target = target / COMPILED_PLUGIN_CONTRACT
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlatformContractError(
            "COMPILED_PLUGIN_CONTRACT_UNAVAILABLE",
            f"Compiled plugin contract cannot be read: {error}",
        ) from error
    errors = validate_compiled_plugin_contract(value)
    if errors:
        raise PlatformContractError(
            "COMPILED_PLUGIN_CONTRACT_INVALID",
            errors[0],
            errors=tuple({"pointer": "/", "message": error} for error in errors),
        )
    digest = str(value["contractDigest"])
    return CompiledPluginContract(_freeze(value), digest)


__all__ = [
    "COMPILED_PLUGIN_CONTRACT",
    "CompiledPluginContract",
    "canonical_contract_bytes",
    "contract_digest",
    "load_compiled_plugin_contract",
    "validate_compiled_plugin_contract",
]
