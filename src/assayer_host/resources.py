"""Locate Assayer runtime resources in development and installed environments."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_resource_root(path: Path) -> bool:
    return (path / "schemas").is_dir() and (path / "rules" / "registry.json").is_file()


def default_resource_root() -> Path:
    """Return the validated installed or source-tree Assayer resource root."""
    configured = os.environ.get("ASSAYER_RESOURCE_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not _is_resource_root(candidate):
            raise RuntimeError(
                f"ASSAYER_RESOURCE_ROOT does not contain schemas/ and rules/registry.json: {candidate}"
            )
        return candidate

    package_target_root = Path(__file__).resolve().parent.parent
    candidates = (
        Path(sys.prefix) / "share" / "assayer",
        Path(sys.prefix) / "local" / "share" / "assayer",
        package_target_root / "share" / "assayer",
    )
    for candidate in candidates:
        if _is_resource_root(candidate):
            return candidate

    source_root = Path(__file__).resolve().parents[2]
    if _is_resource_root(source_root):
        return source_root

    raise RuntimeError(
        "Assayer runtime resources are unavailable; install the assayer distribution with bundled rules and schemas"
    )


def default_schema_root() -> Path:
    return default_resource_root() / "schemas"


def default_rules_root() -> Path:
    return default_resource_root() / "rules"
