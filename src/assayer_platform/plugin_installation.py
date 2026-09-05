"""Durable, atomic index of independently installed audit plugins.

The store is deliberately dumb: it owns one ``index.json`` under its root and
nothing else.  Lifecycle decisions (validation, activation order, rollback)
belong to :mod:`assayer_platform.plugin_lifecycle`, which mutates the index
through this store's typed helpers.  Keeping the store separate makes the
fail-closed lifecycle testable without touching a filesystem package manager.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .contract import PlatformContractError


_INDEX_FILENAME = "index.json"
_INDEX_SCHEMA_VERSION = "1.0.0"
_ENTITY_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _validate_identity(plugin_id: str, version: str) -> None:
    if not isinstance(plugin_id, str) or not _ENTITY_ID.fullmatch(plugin_id):
        raise PlatformContractError(
            "PLUGIN_IDENTITY_INVALID",
            "Plugin ID must match the platform entity identifier form.",
        )
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise PlatformContractError(
            "PLUGIN_VERSION_INVALID",
            "Plugin version must be a semantic version.",
        )


def _empty_index() -> dict:
    return {"schemaVersion": _INDEX_SCHEMA_VERSION, "plugins": {}}


class PluginInstallationStore:
    """Atomically persist one plugin-installation index per directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.root / _INDEX_FILENAME

    def package_dir(self, plugin_id: str, version: str) -> Path:
        _validate_identity(plugin_id, version)
        return self.root / "packages" / plugin_id / version

    def load(self) -> dict:
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _empty_index()
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PlatformContractError(
                "PLUGIN_INDEX_CORRUPT",
                "The plugin installation index could not be read.",
            )
        if not isinstance(value, dict) or value.get("schemaVersion") != _INDEX_SCHEMA_VERSION:
            raise PlatformContractError(
                "PLUGIN_INDEX_INVALID",
                "The plugin installation index is not a valid installation index.",
            )
        plugins = value.get("plugins", {})
        if not isinstance(plugins, dict):
            raise PlatformContractError(
                "PLUGIN_INDEX_INVALID",
                "The plugin installation index has no valid plugins table.",
            )
        return {"schemaVersion": _INDEX_SCHEMA_VERSION, "plugins": plugins}

    def save(self, index: dict) -> None:
        plugins = index.get("plugins")
        if (
            not isinstance(index, dict)
            or index.get("schemaVersion") != _INDEX_SCHEMA_VERSION
            or not isinstance(plugins, dict)
        ):
            raise PlatformContractError(
                "PLUGIN_INDEX_INVALID",
                "The plugin installation index is not a valid installation index.",
            )
        temporary = self.index_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        temporary.replace(self.index_path)

    def plugin(self, index: dict, plugin_id: str) -> dict | None:
        entry = index["plugins"].get(plugin_id)
        return dict(entry) if isinstance(entry, dict) else None

    def record(self, entry: dict, version: str) -> dict | None:
        versions = entry.get("versions")
        if not isinstance(versions, dict):
            return None
        record = versions.get(version)
        return dict(record) if isinstance(record, dict) else None

    def active_version(self, entry: dict) -> str | None:
        history = entry.get("history")
        if not isinstance(history, list) or not history:
            return None
        active = history[-1]
        return active if isinstance(active, str) else None

    def version_history(self, entry: dict) -> list[str]:
        history = entry.get("history")
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, str)]

    def upsert(
        self,
        index: dict,
        plugin_id: str,
        *,
        version: str,
        package_root: str,
        installed_at: int,
        conformance: Mapping[str, Any],
    ) -> dict:
        _validate_identity(plugin_id, version)
        entry = index["plugins"].get(plugin_id)
        if entry is None:
            entry = {"activeVersion": None, "history": [], "versions": {}}
            index["plugins"][plugin_id] = entry
        history = entry.get("history")
        if not isinstance(history, list):
            history = []
            entry["history"] = history
        if version not in history:
            history.append(version)
        versions = entry.get("versions")
        if not isinstance(versions, dict):
            versions = {}
            entry["versions"] = versions
        versions[version] = {
            "installedAt": installed_at,
            "packageRoot": package_root,
            "conformance": dict(conformance),
        }
        entry["activeVersion"] = history[-1]
        return entry

    def remove(self, index: dict, plugin_id: str) -> dict | None:
        return index["plugins"].pop(plugin_id, None)
