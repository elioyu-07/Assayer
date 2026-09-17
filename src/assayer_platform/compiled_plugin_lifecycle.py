"""Installation and discovery for data-only compiled plugin contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
import shutil
import time
from typing import Any

from .compiled_plugin_contract import (
    COMPILED_PLUGIN_CONTRACT,
    CompiledPluginContract,
    load_compiled_plugin_contract,
)
from .contract import PlatformContractError
from .plugin_installation import PluginInstallationStore


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    core = version.split("+", 1)[0]
    main, separator, prerelease = core.partition("-")
    try:
        major, minor, patch = (int(part) for part in main.split("."))
    except ValueError as error:
        raise PlatformContractError("PLUGIN_VERSION_INVALID", f"Invalid plugin version: {version}") from error
    return (major, minor, patch, 1 if not separator else 0, prerelease)


def _artifact_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class CompiledPluginLifecycleManager:
    """Persist contracts without importing plugin code or creating registrations."""

    def __init__(self, store: PluginInstallationStore, *, clock=time.time) -> None:
        self._store = store
        self._clock = clock

    def _stage(self, artifact: Path, contract: CompiledPluginContract) -> dict[str, Any]:
        target = self._store.package_dir(contract.plugin_id, contract.version)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, target / COMPILED_PLUGIN_CONTRACT)
        return {
            "pluginId": contract.plugin_id,
            "version": contract.version,
            "packageRoot": target.relative_to(self._store.root).as_posix(),
            "installedAt": int(self._clock()),
            "checksum": _artifact_checksum(artifact),
            "contractDigest": contract.digest,
            "conformance": {
                "schemaVersion": "1.0.0",
                "pluginId": contract.plugin_id,
                "status": "passed",
                "artifactType": "compiled-plugin-contract",
                "contractDigest": contract.digest,
                "issues": [],
            },
        }

    def _persist(self, index: dict, staged: dict[str, Any], operation: str) -> dict[str, Any]:
        entry = self._store.upsert(
            index,
            staged["pluginId"],
            version=staged["version"],
            package_root=staged["packageRoot"],
            installed_at=staged["installedAt"],
            conformance=staged["conformance"],
        )
        record = entry["versions"][staged["version"]]
        record["checksum"] = staged["checksum"]
        record["contractDigest"] = staged["contractDigest"]
        record["artifactType"] = "compiled-plugin-contract"
        entry["state"] = "installed"
        entry.pop("stateReason", None)
        self._store.save(index)
        return {
            "schemaVersion": "1.0.0",
            "operation": operation,
            "status": "completed",
            "pluginId": staged["pluginId"],
            "version": self._store.active_version(entry),
            "contractDigest": staged["contractDigest"],
        }

    def install(self, artifact: str | Path) -> dict[str, Any]:
        path = Path(artifact).expanduser().resolve()
        contract = load_compiled_plugin_contract(path)
        index = self._store.load()
        existing = self._store.plugin(index, contract.plugin_id)
        if existing is not None and existing.get("state") != "dirty":
            raise PlatformContractError("PLUGIN_CONFLICT", f"Plugin is already installed: {contract.plugin_id}")
        active = self._store.active_version(existing) if existing else None
        if active is not None and _version_key(contract.version) < _version_key(active):
            raise PlatformContractError("PLUGIN_DOWNGRADE_REQUIRED", f"Contract version {contract.version} is older than {active}")
        result = self._persist(index, self._stage(path, contract), "install")
        if existing is not None:
            result["recoveredFrom"] = "dirty"
        return result

    def upgrade(self, artifact: str | Path) -> dict[str, Any]:
        path = Path(artifact).expanduser().resolve()
        contract = load_compiled_plugin_contract(path)
        index = self._store.load()
        existing = self._store.plugin(index, contract.plugin_id)
        if existing is None:
            raise PlatformContractError("UNKNOWN_PLUGIN", f"Plugin is not installed: {contract.plugin_id}")
        active = self._store.active_version(existing)
        if active is not None and _version_key(contract.version) < _version_key(active):
            raise PlatformContractError("PLUGIN_DOWNGRADE_REQUIRED", f"Contract version {contract.version} is older than {active}")
        if self._store.record(existing, contract.version) is not None:
            return self._reactivate(index, contract.plugin_id, contract.version, "upgrade")
        result = self._persist(index, self._stage(path, contract), "upgrade")
        result["previousVersion"] = active
        return result

    def _reactivate(self, index: dict, plugin_id: str, version: str, operation: str) -> dict[str, Any]:
        entry = index["plugins"][plugin_id]
        record = self._store.record(entry, version)
        if record is None:
            raise PlatformContractError("PLUGIN_VERSION_UNAVAILABLE", f"No installed contract: {plugin_id}@{version}")
        path = Path(self._store.root) / record["packageRoot"] / COMPILED_PLUGIN_CONTRACT
        contract = load_compiled_plugin_contract(path)
        previous = self._store.active_version(entry)
        if version != previous:
            entry["history"] = self._store.version_history(entry) + [version]
            entry["activeVersion"] = version
            entry["state"] = "installed"
            entry.pop("stateReason", None)
            self._store.save(index)
        return {
            "schemaVersion": "1.0.0",
            "operation": operation,
            "status": "completed",
            "pluginId": plugin_id,
            "version": version,
            "previousVersion": previous,
            "contractDigest": contract.digest,
        }

    def rollback(self, plugin_id: str) -> dict[str, Any]:
        index = self._store.load()
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict) or len(self._store.version_history(entry)) < 2:
            raise PlatformContractError("ROLLBACK_UNAVAILABLE", f"Plugin has no previous contract: {plugin_id}")
        return self._reactivate(index, plugin_id, self._store.version_history(entry)[-2], "rollback")

    def uninstall(self, plugin_id: str) -> dict[str, Any]:
        index = self._store.load()
        if self._store.plugin(index, plugin_id) is None:
            raise PlatformContractError("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
        self._store.remove(index, plugin_id)
        self._store.save(index)
        package_root = self._store.root / "packages" / plugin_id
        if package_root.exists():
            shutil.rmtree(package_root)
        return {"schemaVersion": "1.0.0", "operation": "uninstall", "status": "completed", "pluginId": plugin_id, "version": None}

    def list(self) -> list[dict[str, Any]]:
        index = self._store.load()
        result = []
        for plugin_id, entry in sorted(index["plugins"].items()):
            active = self._store.active_version(entry)
            item = {
                "pluginId": plugin_id,
                "activeVersion": active,
                "history": self._store.version_history(entry),
                "state": entry.get("state", "installed"),
                "artifactType": "compiled-plugin-contract",
                "versions": dict(entry.get("versions", {})),
            }
            record = self._store.record(entry, active) if active is not None else None
            if record is not None:
                path = Path(self._store.root) / record["packageRoot"] / COMPILED_PLUGIN_CONTRACT
                contract = load_compiled_plugin_contract(path)
                item.update({
                    "version": contract.version,
                    "name": contract.payload["plugin"]["name"],
                    "description": contract.payload["plugin"]["description"],
                    "subjectKind": contract.payload["input"]["subjectKind"],
                    "inputKind": contract.input_kind,
                    "scopeSchema": _plain(contract.payload["input"]["scopeSchema"]),
                    "checks": [{"checkId": check["id"], "version": contract.version}
                               for check in contract.payload["checks"]],
                    "contractDigest": contract.digest,
                })
            result.append(item)
        return result

    def get(self, plugin_id: str) -> dict[str, Any] | None:
        """Return one data-only lifecycle record for Host read-only surfaces."""
        index = self._store.load()
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict):
            return None
        return {
            "pluginId": plugin_id,
            "activeVersion": self._store.active_version(entry),
            "history": self._store.version_history(entry),
            "state": entry.get("state", "installed"),
            "artifactType": "compiled-plugin-contract",
            "versions": dict(entry.get("versions", {})),
        }

    def downgrade(self, plugin_id: str, version: str) -> dict[str, Any]:
        """Reactivate an already installed older contract; never build code."""
        index = self._store.load()
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict) or self._store.record(entry, version) is None:
            raise PlatformContractError("PLUGIN_VERSION_UNAVAILABLE", f"No installed contract: {plugin_id}@{version}")
        current = self._store.active_version(entry)
        if current == version:
            return {"schemaVersion": "1.0.0", "operation": "downgrade", "status": "completed", "pluginId": plugin_id, "version": version}
        return self._reactivate(index, plugin_id, version, "downgrade")


def load_installed_compiled_plugin(store: PluginInstallationStore, plugin_id: str) -> CompiledPluginContract:
    index = store.load()
    entry = index["plugins"].get(plugin_id)
    if not isinstance(entry, dict) or entry.get("state") == "dirty":
        raise PlatformContractError("PLUGIN_NOT_FOUND", f"No active compiled plugin: {plugin_id}")
    version = store.active_version(entry)
    record = store.record(entry, version) if version is not None else None
    if record is None:
        raise PlatformContractError("PLUGIN_NOT_FOUND", f"No active compiled plugin: {plugin_id}")
    path = Path(store.root) / record["packageRoot"] / COMPILED_PLUGIN_CONTRACT
    contract = load_compiled_plugin_contract(path)
    if record.get("contractDigest") not in {None, contract.digest}:
        raise PlatformContractError("PLUGIN_CHECKSUM_MISMATCH", f"Installed contract digest mismatch: {plugin_id}@{version}")
    return contract


__all__ = [
    "CompiledPluginLifecycleManager",
    "load_installed_compiled_plugin",
]
