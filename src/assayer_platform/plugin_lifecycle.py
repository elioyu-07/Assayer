"""Fail-closed lifecycle for independently installed audit plugins.

This module owns the platform-side *persistent* plugin lifecycle: install,
upgrade, rollback, and uninstall of a domain plugin package into a durable
store, plus store-backed discovery that merges installed plugins into a
``PluginRegistry``.

It deliberately does **not** import a concrete plugin implementation for
validation.  Validation reuses :func:`assayer_platform.conformance.inspect_plugin_package`
(static, no plugin-code import) and defers runtime construction to discovery,
which loads a registration only after the package is on disk and its identity
was validated.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .conformance import RELEASE_DESCRIPTOR, inspect_plugin_package
from .contract import PlatformContractError
from .plugin_installation import PluginInstallationStore
from .plugin_registry import PluginRegistration, PluginRegistry


_OPERATIONS = {"install", "upgrade", "downgrade", "rollback", "uninstall"}


def _version_key(version: str) -> tuple:
    """Comparable semantic-version key; a release sorts above its pre-releases."""
    core = version.split("+", 1)[0]
    main, separator, prerelease = core.partition("-")
    try:
        major, minor, patch = (int(part) for part in main.split("."))
    except ValueError:
        raise PlatformContractError(
            "PLUGIN_VERSION_INVALID",
            f"Plugin version must be a semantic version: {version}",
        )
    return (major, minor, patch, 1 if not separator else 0, prerelease)


def _descriptor(package_root: str | Path) -> dict:
    root = Path(package_root).expanduser().resolve()
    try:
        value = json.loads((root / RELEASE_DESCRIPTOR).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlatformContractError(
            "PLUGIN_PACKAGE_NOT_FOUND",
            "The plugin package does not contain a release descriptor.",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlatformContractError(
            "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            f"The plugin release descriptor could not be read: {error}",
        )
    if not isinstance(value, dict) or not isinstance(value.get("pluginId"), str) or not isinstance(value.get("pluginVersion"), str):
        raise PlatformContractError(
            "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            "The plugin release descriptor must declare pluginId and pluginVersion.",
        )
    return value


def _package_checksum(root: Path) -> str:
    """Deterministic content hash over every source file in a materialized package.

    Python bytecode caches (``__pycache__`` and ``*.pyc``) are excluded because
    they are regenerated on import and would make the checksum unstable across
    otherwise identical installs.
    """
    digest = hashlib.sha256()
    for path in sorted(
        item for item in root.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _copy_installer(package_root: Path, target: Path) -> None:
    shutil.copytree(
        package_root, target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def load_registration(package_root: str | Path) -> PluginRegistration:
    """Load a plugin registration from a materialized package source.

    Importing the registration module happens only after the package was
    statically validated; this loader is the single runtime import boundary for
    independently installed plugins.
    """
    root = Path(package_root).expanduser().resolve()
    descriptor = _descriptor(root)
    module_name, separator, attribute_name = descriptor["registration"].partition(":")
    if not separator or not module_name or not attribute_name:
        raise PlatformContractError(
            "PLUGIN_REGISTRATION_INVALID",
            "The release descriptor registration must use module:attribute form.",
        )
    runtime_source = (root / descriptor["runtimeSource"]).resolve()
    try:
        runtime_source.relative_to(root)
    except ValueError:
        raise PlatformContractError(
            "PLUGIN_PACKAGE_PATH_UNSAFE",
            "The declared runtime source escapes the plugin package.",
        )
    inserted = str(runtime_source)
    sys.path.insert(0, inserted)
    try:
        module = importlib.import_module(module_name)
    finally:
        if sys.path and sys.path[0] == inserted:
            sys.path.pop(0)
    value = getattr(module, attribute_name)
    if callable(value) and not isinstance(value, PluginRegistration):
        value = value()
    if isinstance(value, PluginRegistry):
        values = value.list()
        if len(values) != 1:
            raise PlatformContractError(
                "PLUGIN_REGISTRATION_INVALID",
                "The installed registration registry must contain exactly one plugin.",
            )
        value = values[0]
    if not isinstance(value, PluginRegistration):
        value = getattr(value, "registration", value)
    if not isinstance(value, PluginRegistration):
        raise PlatformContractError(
            "PLUGIN_REGISTRATION_INVALID",
            "The installed registration did not provide a PluginRegistration.",
        )
    return value


class PluginLifecycleManager:
    """Execute durable install, upgrade, rollback, and uninstall operations."""

    def __init__(
        self,
        store: PluginInstallationStore,
        *,
        static_validator: Callable[[Path], Any] = inspect_plugin_package,
        installer: Callable[[Path, Path], None] = _copy_installer,
        registration_loader: Callable[[Path], PluginRegistration] = load_registration,
        clock: Callable[[], float] = time.time,
        latest_available: Callable[[str], str | None] | None = None,
    ) -> None:
        self._store = store
        self._static_validator = static_validator
        self._installer = installer
        self._loader = registration_loader
        self._clock = clock
        self._latest_available = latest_available

    def _stage(self, plugin_id: str, version: str, package_root: Path) -> dict:
        report = self._static_validator(package_root)
        conformance = report.as_dict()
        if not report.passed:
            first = report.issues[0]
            reason = getattr(first, "code", None) or "PLUGIN_PACKAGE_INVALID"
            return {
                "pluginId": plugin_id,
                "version": version,
                "passed": False,
                "reason": reason,
                "conformance": conformance,
            }
        target = self._store.package_dir(plugin_id, version)
        if target.exists():
            shutil.rmtree(target)
        self._installer(package_root, target)
        if not target.is_dir():
            raise PlatformContractError(
                "PLUGIN_MATERIALIZATION_FAILED",
                "The plugin package was not materialized into the installation store.",
            )
        installed_at = int(self._clock())
        relative = target.relative_to(self._store.root).as_posix()
        return {
            "pluginId": plugin_id,
            "version": version,
            "packageRoot": relative,
            "installedAt": installed_at,
            "conformance": conformance,
            "checksum": _package_checksum(target),
            "passed": True,
            "reason": None,
        }

    def _quarantine(self, index: dict, plugin_id: str, reason: str) -> None:
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict):
            entry = {
                "activeVersion": None,
                "history": [],
                "versions": {},
                "state": "dirty",
                "stateReason": reason,
            }
            index["plugins"][plugin_id] = entry
        else:
            entry["state"] = "dirty"
            entry["stateReason"] = reason
        self._store.save(index)

    def _persist(self, index: dict, staged: dict, *, operation: str) -> dict:
        entry = self._store.upsert(
            index,
            staged["pluginId"],
            version=staged["version"],
            package_root=staged["packageRoot"],
            installed_at=staged["installedAt"],
            conformance=staged["conformance"],
        )
        entry["versions"][staged["version"]]["checksum"] = staged["checksum"]
        entry["state"] = "installed"
        entry.pop("stateReason", None)
        self._store.save(index)
        return self._result(operation, staged["pluginId"], self._store.active_version(entry))

    def _quarantined_result(self, operation: str, plugin_id: str, version: str, reason: str) -> dict:
        result = self._result(operation, plugin_id, version)
        result["status"] = "quarantined"
        result["state"] = "dirty"
        result["reason"] = reason
        return result

    def _source_root(self, package_root: str | Path) -> Path:
        root = Path(package_root).expanduser().resolve()
        if not root.is_dir():
            raise PlatformContractError(
                "PLUGIN_SOURCE_UNREACHABLE",
                f"The plugin source is unreachable or does not exist: {root}",
            )
        return root

    def install(self, package_root: str | Path) -> dict:
        root = self._source_root(package_root)
        descriptor = _descriptor(root)
        plugin_id, version = descriptor["pluginId"], descriptor["pluginVersion"]
        index = self._store.load()
        entry = self._store.plugin(index, plugin_id)
        if entry is not None and entry.get("state") != "dirty":
            raise PlatformContractError(
                "PLUGIN_CONFLICT",
                f"Plugin is already installed: {plugin_id}",
            )
        staged = self._stage(plugin_id, version, root)
        if not staged["passed"]:
            self._quarantine(index, plugin_id, staged["reason"])
            return self._quarantined_result("install", plugin_id, version, staged["reason"])
        result = self._persist(index, staged, operation="install")
        if entry is not None:
            result["recoveredFrom"] = "dirty"
        return result

    def upgrade(self, package_root: str | Path) -> dict:
        root = self._source_root(package_root)
        descriptor = _descriptor(root)
        plugin_id, version = descriptor["pluginId"], descriptor["pluginVersion"]
        index = self._store.load()
        entry = self._store.plugin(index, plugin_id)
        if entry is None:
            raise PlatformContractError(
                "UNKNOWN_PLUGIN",
                f"Plugin is not installed: {plugin_id}",
            )
        if self._store.record(entry, version) is not None:
            raise PlatformContractError(
                "PLUGIN_VERSION_CONFLICT",
                f"Plugin version is already installed: {plugin_id}@{version}",
            )
        active = self._store.active_version(entry)
        if active is not None and _version_key(version) < _version_key(active):
            raise PlatformContractError(
                "PLUGIN_DOWNGRADE_REQUIRED",
                f"Plugin target version {version} is older than the active version "
                f"{active}; use downgrade to move back.",
            )
        previous = active
        staged = self._stage(plugin_id, version, root)
        if not staged["passed"]:
            self._quarantine(index, plugin_id, staged["reason"])
            return self._quarantined_result("upgrade", plugin_id, version, staged["reason"])
        result = self._persist(index, staged, operation="upgrade")
        result["previousVersion"] = previous
        return result

    def downgrade(self, plugin_id: str, version: str) -> dict:
        index = self._store.load()
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict):
            raise PlatformContractError(
                "UNKNOWN_PLUGIN",
                f"Plugin is not installed: {plugin_id}",
            )
        history = self._store.version_history(entry)
        if version not in history:
            raise PlatformContractError(
                "PLUGIN_VERSION_UNAVAILABLE",
                f"Plugin has no installed version to downgrade to: {plugin_id}@{version}",
            )
        previous = history[-1]
        if previous == version:
            result = self._result("downgrade", plugin_id, version)
            result["previousVersion"] = previous
            return result
        entry["history"] = history[: history.index(version) + 1]
        entry["activeVersion"] = version
        self._store.save(index)
        result = self._result("downgrade", plugin_id, version)
        result["previousVersion"] = previous
        return result

    def rollback(self, plugin_id: str) -> dict:
        index = self._store.load()
        entry = index["plugins"].get(plugin_id)
        if not isinstance(entry, dict):
            raise PlatformContractError(
                "UNKNOWN_PLUGIN",
                f"Plugin is not installed: {plugin_id}",
            )
        history = self._store.version_history(entry)
        if len(history) < 2:
            raise PlatformContractError(
                "ROLLBACK_UNAVAILABLE",
                f"Plugin has no previous version to roll back to: {plugin_id}",
            )
        rolled_back_from = history[-1]
        entry["history"] = history[:-1]
        entry["activeVersion"] = entry["history"][-1]
        self._store.save(index)
        result = self._result("rollback", plugin_id, entry["activeVersion"])
        result["previousVersion"] = rolled_back_from
        return result

    def uninstall(self, plugin_id: str) -> dict:
        index = self._store.load()
        if self._store.plugin(index, plugin_id) is None:
            raise PlatformContractError(
                "UNKNOWN_PLUGIN",
                f"Plugin is not installed: {plugin_id}",
            )
        self._store.remove(index, plugin_id)
        self._store.save(index)
        package_root = self._store.root / "packages" / plugin_id
        if package_root.exists():
            shutil.rmtree(package_root)
        return self._result("uninstall", plugin_id, None)

    def _entry_view(self, plugin_id: str, entry: dict) -> dict:
        active = self._store.active_version(entry)
        view = {
            "pluginId": plugin_id,
            "activeVersion": active,
            "history": self._store.version_history(entry),
            "state": entry.get("state", "installed"),
            "stateReason": entry.get("stateReason"),
        }
        if (
            view["state"] == "installed"
            and active is not None
            and self._latest_available is not None
        ):
            newest = self._latest_available(plugin_id)
            if newest is not None and _version_key(newest) > _version_key(active):
                view["state"] = "upgradable"
                view["upgradeTo"] = newest
        return view

    def list(self) -> list[dict]:
        index = self._store.load()
        return [
            self._entry_view(plugin_id, entry)
            for plugin_id, entry in sorted(index["plugins"].items())
        ]

    def get(self, plugin_id: str) -> dict | None:
        index = self._store.load()
        entry = self._store.plugin(index, plugin_id)
        if entry is None:
            return None
        view = self._entry_view(plugin_id, entry)
        view["versions"] = dict(entry.get("versions", {}))
        return view

    @staticmethod
    def _result(operation: str, plugin_id: str, version: str | None) -> dict:
        return {
            "schemaVersion": "1.0.0",
            "operation": operation,
            "status": "completed",
            "pluginId": plugin_id,
            "version": version,
        }


def discover_plugin_registry(
    store: PluginInstallationStore,
    *,
    builtins: Iterable[PluginRegistration] = (),
    loader: Callable[[Path], PluginRegistration] = load_registration,
) -> PluginRegistry:
    """Build a registry from built-ins plus every installed plugin's active version.

    Conflicts (duplicate identity, capability-missing, platform API mismatch)
    fail closed through the existing ``PluginRegistry.register`` conformance.
    """
    registry = PluginRegistry(builtins)
    index = store.load()
    for plugin_id, entry in index["plugins"].items():
        if entry.get("state") == "dirty":
            continue
        active = store.active_version(entry)
        if active is None:
            continue
        record = store.record(entry, active)
        if record is None:
            continue
        package_root = Path(store.root) / record["packageRoot"]
        checksum = record.get("checksum")
        if checksum and _package_checksum(package_root) != checksum:
            entry["state"] = "dirty"
            entry["stateReason"] = "PLUGIN_CHECKSUM_MISMATCH"
            store.save(index)
            continue
        registration = loader(package_root)
        registry.register(registration)
    return registry


__all__ = [
    "PluginLifecycleManager",
    "PluginInstallationStore",
    "discover_plugin_registry",
    "load_registration",
]
