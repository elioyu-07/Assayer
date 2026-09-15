"""Platform resource resolution and plugin-manifest loading.

The manifest loader itself lives in the plugin SDK
(:mod:`assayer_plugin_sdk.manifest`) and is re-exported here so existing
platform imports keep working.  This module keeps only the platform-side schema
resource resolver used for providers and other platform-owned schemas.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_plugin_sdk.manifest import (  # noqa: F401
    load_plugin_manifest,
    validate_plugin_manifest,
)
from assayer_plugin_sdk.resources import schema_root as sdk_schema_root

from .contract import PlatformContractError


# A platform-owned schema that stays in the platform resource directory after
# the plugin-facing schemas move to the SDK (WS3).  Do not use an SDK-owned
# schema (such as plugin-manifest.schema.json) as an existence sentinel here.
_PLATFORM_SCHEMA_SENTINEL = "platform-ledger.schema.json"


def _schema_root() -> Path:
    configured = os.environ.get("ASSAYER_RESOURCE_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve() / "schemas"
        if (candidate / _PLATFORM_SCHEMA_SENTINEL).is_file():
            return candidate
        raise PlatformContractError(
            "RESOURCE_UNAVAILABLE", "ASSAYER_RESOURCE_ROOT does not contain platform schemas",
        )
    candidates = (
        Path(sys.prefix) / "share" / "assayer" / "schemas",
        Path(sys.prefix) / "local" / "share" / "assayer" / "schemas",
        Path(__file__).resolve().parents[2] / "schemas",
    )
    for candidate in candidates:
        if (candidate / _PLATFORM_SCHEMA_SENTINEL).is_file():
            return candidate
    raise PlatformContractError("RESOURCE_UNAVAILABLE", "Platform schemas are unavailable")


def schema_store(platform_root: Path | None = None) -> dict[str, Any]:
    """Load the canonical SDK and platform schema sets into one resolver store.

    Plugin-facing schemas are owned and shipped only by ``assayer-plugin-sdk``.
    Platform schemas remain in the platform resource root and resolve shared
    references through this combined store. Duplicate names or identifiers are
    rejected so a platform resource cannot shadow an SDK contract.
    """
    roots = (sdk_schema_root(), platform_root or _schema_root())
    schemas: dict[str, Any] = {}
    origins: dict[str, Path] = {}
    for root in roots:
        for path in sorted(root.rglob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                raise PlatformContractError(
                    "INVALID_SCHEMA_RESOURCE",
                    f"Schema resource has no non-empty $id: {path}",
                )
            for key in (path.name, schema_id):
                if key in schemas:
                    raise PlatformContractError(
                        "SCHEMA_RESOURCE_CONFLICT",
                        f"Schema resource {key} is declared by both {origins[key]} and {path}",
                    )
                schemas[key] = schema
                origins[key] = path
    return schemas


def schema_path(filename: str, platform_root: Path | None = None) -> Path:
    """Resolve one schema to its owning SDK or platform resource directory."""
    sdk_path = sdk_schema_root() / filename
    if sdk_path.is_file():
        return sdk_path
    platform_path = (platform_root or _schema_root()) / filename
    if platform_path.is_file():
        return platform_path
    raise PlatformContractError(
        "RESOURCE_UNAVAILABLE", f"Schema resource is unavailable: {filename}",
    )


def schema_validator(
    filename: str, platform_root: Path | None = None,
) -> Draft202012Validator:
    """Build one validator against the canonical combined Schema store."""
    schemas = schema_store(platform_root)
    schema = schemas.get(filename)
    if schema is None:
        raise PlatformContractError(
            "RESOURCE_UNAVAILABLE", f"Schema resource is unavailable: {filename}",
        )
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


__all__ = [
    "load_plugin_manifest",
    "schema_path",
    "schema_store",
    "schema_validator",
    "validate_plugin_manifest",
]
