"""Shared plugin-lifecycle operations used by both the CLI and the MCP surface.

This module deliberately imports only from ``assayer_platform`` and the store
registry so that ``transport`` can depend on it without creating a cycle back
through ``cli``.  The operations here are deterministic and fail-closed; the
``resolve_intent`` / ``execute_intent_step`` pair lets any natural-language
request (CLI or Agent) land on the exact same code path.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

from assayer_platform import PlatformContractError, PlatformRunner, discover_plugin_registry
from assayer_platform import installed_plugin_registry
from assayer_platform.plugin_catalog import (
    PluginCatalog,
    latest_published,
    load_catalog,
    parse_catalog,
    resolve_version,
)
from assayer_platform.plugin_distribution import materialize_wheel
from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import PluginLifecycleManager

from .plugin_intent import IntentStep


# The public plugin catalog. ``plugins publish`` writes to this registry repo
# (its ``--registry`` / ``--registry-path`` defaults) via pull request; ``add``
# and by-name install read from it. Override with ``--index``.
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/elioyu-07/assayer-registry/main/plugins.json"


def download_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except OSError as error:
        raise PlatformContractError(
            "PLUGIN_DOWNLOAD_FAILED",
            f"The plugin could not be downloaded from {url}: {error}",
        ) from error


def load_catalog_source(index: str) -> PluginCatalog:
    if index.startswith(("http://", "https://")):
        try:
            text = download_bytes(index).decode("utf-8")
        except UnicodeError as error:
            raise PlatformContractError(
                "PLUGIN_CATALOG_INVALID",
                f"The catalog is not valid UTF-8: {error}",
            ) from error
        return parse_catalog(text)
    return load_catalog(index)


def lifecycle_manager(store_root: str, latest_available=None) -> PluginLifecycleManager:
    return PluginLifecycleManager(
        PluginInstallationStore(store_root), latest_available=latest_available,
    )


def add_from_catalog(plugin: str, *, version: str | None, index: str, store_root: str, operation: str = "install") -> dict:
    """Download, verify, materialize, and install (or upgrade) a plugin by name."""
    catalog = load_catalog_source(index)
    resolved = resolve_version(catalog, plugin, version)
    data = download_bytes(resolved.wheel_url)
    manager = lifecycle_manager(store_root)
    with tempfile.TemporaryDirectory(prefix="assayer-add-") as tmp:
        root = materialize_wheel(data, resolved.sha256, Path(tmp))
        if operation == "upgrade":
            return manager.upgrade(root)
        return manager.install(root)


def store_registry(store_root: str):
    """Built-ins plus every plugin installed in the durable store.

    Falls back to the entry-point registry when no store index exists so that
    read-only commands never create a store directory as a side effect.
    """
    store_path = Path(store_root).expanduser().resolve()
    if not (store_path / "index.json").is_file():
        return installed_plugin_registry()
    return discover_plugin_registry(
        PluginInstallationStore(store_path),
        builtins=installed_plugin_registry().list(),
    )


def store_index_entries(store_root: str, latest_available=None) -> list[dict]:
    """Raw store index entries (including quarantined plugins) without imports."""
    store_path = Path(store_root).expanduser().resolve()
    if not (store_path / "index.json").is_file():
        return []
    return lifecycle_manager(store_path, latest_available).list()


def latest_available_from(index: str | None):
    """Build a ``latest_available`` callback from a catalog, or None without a source.

    An unreachable or invalid source is treated as "no source" for read-only
    ``upgradable`` markers: listing still succeeds, just without markers.
    """
    if not index:
        return None
    try:
        catalog = load_catalog_source(index)
    except PlatformContractError:
        return None

    def latest(plugin_id: str) -> str | None:
        return latest_published(catalog, plugin_id)

    return latest


def annotate_upgradable(catalog: list[dict], entries: list[dict]) -> None:
    upgradable = {
        entry["pluginId"]: entry["upgradeTo"]
        for entry in entries
        if entry.get("state") == "upgradable" and entry.get("upgradeTo")
    }
    for plugin in catalog:
        upgrade_to = upgradable.get(plugin["pluginId"])
        if upgrade_to is not None:
            plugin["state"] = "upgradable"
            plugin["upgradeTo"] = upgrade_to


def plugin_catalog(registry) -> list[dict]:
    catalog = []
    for registration in registry.list():
        manifest = registration.manifest
        catalog.append({
            "pluginId": manifest.plugin_id,
            "version": manifest.version,
            "platformApiVersion": manifest.platform_api_version,
            "domains": list(manifest.domains),
            "subjectKinds": list(manifest.subject_kinds),
            "capabilities": sorted(registration.capabilities),
            "executionModes": sorted(registration.execution_modes),
            "supportsCommit": registration.committer_factory is not None,
            "scopeSchema": dict(registration.scope_schema),
            "checks": [
                {"checkId": check.check_id, "version": check.version}
                for check in manifest.checks
            ],
        })
    return sorted(catalog, key=lambda item: item["pluginId"])


def known_plugin_ids(store_root: str) -> tuple[str, ...]:
    ids = [registration.manifest.plugin_id for registration in installed_plugin_registry().list()]
    for entry in store_index_entries(store_root):
        plugin_id = entry.get("pluginId")
        if plugin_id and plugin_id not in ids:
            ids.append(plugin_id)
    return tuple(ids)


def load_scope(scope_json: str | None, scope_file: str | None):
    if scope_file is not None:
        try:
            return json.loads(
                Path(scope_file).expanduser().resolve().read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PlatformContractError(
                "INVALID_SCOPE", "Plugin scope file must contain valid JSON",
            )
    try:
        return json.loads(scope_json)
    except json.JSONDecodeError:
        raise PlatformContractError("INVALID_SCOPE", "Plugin scope must be valid JSON")


def execute_intent_step(
    step: IntentStep,
    store_root: str,
    output_root: str,
    latest_available=None,
    catalog_index: str = DEFAULT_CATALOG_URL,
) -> dict:
    manager = lifecycle_manager(store_root, latest_available)
    operation = step.operation
    if operation == "list":
        catalog = plugin_catalog(store_registry(store_root))
        entries = store_index_entries(store_root, latest_available)
        annotate_upgradable(catalog, entries)
        quarantined = [entry for entry in entries if entry.get("state") == "dirty"]
        payload = {"operation": "list", "status": "completed", "plugins": catalog}
        if quarantined:
            payload["quarantined"] = quarantined
        return payload
    if operation == "info":
        entry = manager.get(step.plugin_id)
        if entry is None:
            return {"operation": "info", "status": "failed",
                    "error": {"code": "UNKNOWN_PLUGIN",
                              "message": f"Plugin is not installed: {step.plugin_id}"}}
        return {"operation": "info", "status": "completed", **entry}
    if operation == "run":
        try:
            scope = load_scope(None, step.scope_file)
            result = PlatformRunner(store_registry(store_root), output_root).run(
                plugin_id=step.plugin_id, check_id=step.check_id,
                check_version=None, scope=scope,
            )
        except PlatformContractError as error:
            return {"operation": "run", "status": "failed",
                    "error": {"code": error.code, "message": error.message}}
        return {
            "operation": "run",
            "status": "completed" if result.status in {"completed", "partial"} else "failed",
            "runId": result.run_id,
            "decisions": [decision.result for decision in result.decisions],
        }
    try:
        if operation == "install":
            if step.package is not None:
                result = manager.install(step.package)
            else:
                result = add_from_catalog(
                    step.plugin_id, version=None, index=catalog_index, store_root=store_root,
                )
        elif operation == "upgrade":
            if step.package is not None:
                result = manager.upgrade(step.package)
            else:
                result = add_from_catalog(
                    step.plugin_id, version=None, index=catalog_index,
                    store_root=store_root, operation="upgrade",
                )
        elif operation == "downgrade":
            result = manager.downgrade(step.plugin_id, step.version)
        elif operation == "rollback":
            result = manager.rollback(step.plugin_id)
        else:
            result = manager.uninstall(step.plugin_id)
    except PlatformContractError as error:
        return {"operation": operation, "status": "failed",
                "error": {"code": error.code, "message": error.message}}
    return result


__all__ = [
    "DEFAULT_CATALOG_URL",
    "add_from_catalog",
    "annotate_upgradable",
    "download_bytes",
    "execute_intent_step",
    "known_plugin_ids",
    "latest_available_from",
    "lifecycle_manager",
    "load_catalog_source",
    "load_scope",
    "plugin_catalog",
    "store_index_entries",
    "store_registry",
]
