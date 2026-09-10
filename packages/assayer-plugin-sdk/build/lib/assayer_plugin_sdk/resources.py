"""SDK-owned schema resource resolution.

Plugin-facing helpers validate their inputs against schemas that belong to the
SDK, not to the platform. This module resolves that schema directory without
importing :mod:`assayer_platform`, so an installed plugin needs only the SDK.

Resolution order:

1. ``ASSAYER_SDK_SCHEMA_ROOT`` when set (must be an existing directory);
2. the schema directory bundled next to this module (``assayer_plugin_sdk/schemas``);
3. the repository ``schemas/`` directory (development checkout only).
"""

from __future__ import annotations

import os
from pathlib import Path

from .plugin_sdk import PluginContractError

_PACKAGE_ROOT = Path(__file__).resolve().parent


def schema_root() -> Path:
    """Return the directory that holds the SDK-owned schema files."""
    configured = os.environ.get("ASSAYER_SDK_SCHEMA_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        raise PluginContractError(
            f"ASSAYER_SDK_SCHEMA_ROOT does not contain plugin SDK schemas: {configured}",
            code="SDK_SCHEMA_ROOT_UNAVAILABLE",
        )
    bundled = _PACKAGE_ROOT / "schemas"
    if bundled.is_dir():
        return bundled
    repo = _PACKAGE_ROOT.parents[1] / "schemas"
    if repo.is_dir():
        return repo
    raise PluginContractError(
        "Plugin SDK schemas are unavailable; install the assayer-plugin-sdk package "
        "or set ASSAYER_SDK_SCHEMA_ROOT",
        code="SDK_SCHEMA_ROOT_UNAVAILABLE",
    )


__all__ = ["schema_root"]
