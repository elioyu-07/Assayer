"""Platform resource resolution and plugin-manifest loading.

The manifest loader itself lives in the plugin SDK
(:mod:`assayer_plugin_sdk.manifest`) and is re-exported here so existing
platform imports keep working.  This module keeps only the platform-side schema
resource resolver used for providers and other platform-owned schemas.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from assayer_plugin_sdk.manifest import (  # noqa: F401
    load_plugin_manifest,
    validate_plugin_manifest,
)

from .contract import PlatformContractError


# A platform-owned schema that stays in the platform resource directory after
# the plugin-facing schemas move to the SDK (WS3).  Do not use an SDK-owned
# schema (such as plugin-manifest.schema.json) as an existence sentinel here.
_PLATFORM_SCHEMA_SENTINEL = "platform-ledger.schema.json"


def _version_core(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


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


__all__ = ["_schema_root", "_version_core", "load_plugin_manifest", "validate_plugin_manifest"]
