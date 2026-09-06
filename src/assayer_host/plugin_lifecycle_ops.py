"""Shared plugin-lifecycle operations used by both the CLI and the MCP surface.

This module deliberately imports only from ``assayer_platform`` and the store
registry so that ``transport`` can depend on it without creating a cycle back
through ``cli``.  The operations here are deterministic and fail-closed; the
``resolve_intent`` / ``execute_intent_step`` pair lets any natural-language
request (CLI or Agent) land on the exact same code path.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from assayer_platform import PlatformContractError, PlatformRunner, discover_plugin_registry
from assayer_platform.conformance import RELEASE_DESCRIPTOR
from assayer_platform import installed_plugin_registry
from assayer_platform.plugin_catalog import (
    PluginCatalog,
    latest_published,
    parse_catalog,
    resolve_version,
    version_key,
)
from assayer_platform.plugin_distribution import materialize_wheel
from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import (
    PluginLifecycleManager,
    package_checksum,
    read_package_descriptor,
)

from .plugin_intent import IntentStep


# The public plugin catalog. ``plugins publish`` writes to this registry repo
# (its ``--registry`` / ``--registry-path`` defaults) via pull request; ``add``
# and by-name install read from it. Override with ``--index``.
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/elioyu-07/assayer-registry/main/plugins.json"

_ALLOWED_OPERATIONS = frozenset({"install", "upgrade", "downgrade", "rollback", "uninstall"})


def download_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except OSError as error:
        raise PlatformContractError(
            "PLUGIN_DOWNLOAD_FAILED",
            f"The plugin could not be downloaded from {url}: {error}",
        ) from error


def _read_catalog_text(index: str) -> str:
    """Read the raw catalog source text from a URL or a filesystem path."""
    if index.startswith(("http://", "https://")):
        try:
            return download_bytes(index).decode("utf-8")
        except UnicodeError as error:
            raise PlatformContractError(
                "PLUGIN_CATALOG_INVALID",
                f"The catalog is not valid UTF-8: {error}",
            ) from error
    try:
        return Path(index).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PlatformContractError(
            "PLUGIN_CATALOG_INVALID",
            f"The catalog could not be read: {error}",
        ) from error


def load_catalog_source(index: str) -> PluginCatalog:
    return parse_catalog(_read_catalog_text(index))


def catalog_index_digest(index: str) -> str:
    """SHA-256 of the raw catalog source, used to bind a plan to its catalog.

    Recomputing this at execute time detects a catalog that changed after the
    plan was shown, closing the plan/execute TOCTOU window for by-name installs
    and upgrades.
    """
    return hashlib.sha256(_read_catalog_text(index).encode("utf-8")).hexdigest()


def lifecycle_manager(store_root: str, latest_available=None) -> PluginLifecycleManager:
    return PluginLifecycleManager(
        PluginInstallationStore(store_root), latest_available=latest_available,
    )


def verify_catalog_identity(package_root: str | Path, plugin_id: str, version: str) -> None:
    """Fail closed when a materialized package does not match the catalog entry.

    The catalog digest proves the bytes are exactly what the publisher shipped,
    but not that the publisher pointed ``wheelUrl`` at the plugin the entry
    claims.  Cross-check the materialized release descriptor so a mismatched
    wheel can never be installed under another plugin's identity.
    """
    root = Path(package_root).expanduser().resolve()
    try:
        descriptor = json.loads((root / RELEASE_DESCRIPTOR).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlatformContractError(
            "PLUGIN_CATALOG_MISMATCH",
            f"The materialized package has no readable release descriptor: {error}",
        ) from error
    if descriptor.get("pluginId") != plugin_id or descriptor.get("pluginVersion") != version:
        raise PlatformContractError(
            "PLUGIN_CATALOG_MISMATCH",
            f"The materialized package ({descriptor.get('pluginId')}@{descriptor.get('pluginVersion')}) "
            f"does not match the catalog entry ({plugin_id}@{version}).",
        )


def add_from_catalog(plugin: str, *, version: str | None, index: str, store_root: str, operation: str = "install") -> dict:
    """Download, verify, materialize, and install (or upgrade) a plugin by name."""
    catalog = load_catalog_source(index)
    resolved = resolve_version(catalog, plugin, version)
    data = download_bytes(resolved.wheel_url)
    manager = lifecycle_manager(store_root)
    with tempfile.TemporaryDirectory(prefix="assayer-add-") as tmp:
        root = materialize_wheel(data, resolved.sha256, Path(tmp))
        verify_catalog_identity(root, resolved.plugin_id, resolved.version)
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


def store_index_digest(store_root: str) -> str | None:
    """Content digest of the durable store index, or None when no index exists.

    ``index.json`` is the single atomic write the installation store performs,
    so its digest is a stable "the plugin set changed" signal used to invalidate
    a pending plan when the store is mutated underneath it.
    """
    index_path = Path(store_root).expanduser().resolve() / "index.json"
    if not index_path.is_file():
        return None
    return hashlib.sha256(index_path.read_bytes()).hexdigest()


def _precondition(code: str, status: str, message: str) -> dict:
    return {"code": code, "status": status, "message": message}


def plan_plugin_change(
    operation: str,
    *,
    plugin_id: str | None = None,
    version: str | None = None,
    package: str | None = None,
    store_root: str,
    catalog_index: str = DEFAULT_CATALOG_URL,
) -> dict:
    """Resolve a read-only, deterministic plan for a lifecycle mutation.

    Nothing executes here.  The plan carries a ``status`` (``ready``,
    ``blocked``, or ``noop``), a set of preconditions, and — for catalog and
    local-package sources — a content digest that binds the confirmed plan to
    the exact bytes it would execute, so a change underneath the plan is
    detectable at execute time.
    """
    if operation not in _ALLOWED_OPERATIONS:
        raise PlatformContractError(
            "INVALID_OPERATION",
            f"Unsupported plugin lifecycle operation: {operation}",
        )

    target_version = version
    source: str | None = None
    checksum: str | None = None
    catalog_digest: str | None = None
    package_digest: str | None = None

    if package is not None:
        package_path = Path(package).expanduser().resolve()
        descriptor = read_package_descriptor(package_path)
        plugin_id = descriptor["pluginId"]
        target_version = descriptor["pluginVersion"]
        source = str(package_path)
        try:
            package_digest = package_checksum(package_path)
        except (OSError, PlatformContractError) as error:
            raise PlatformContractError(
                "PLUGIN_PACKAGE_INVALID",
                f"The plugin package could not be fingerprinted: {error}",
            ) from error

    manager = lifecycle_manager(store_root)
    current = manager.get(plugin_id) if plugin_id is not None else None
    current_version = current["activeVersion"] if current else None
    current_state = current.get("state") if current else "absent"
    installed = current is not None and current_state != "dirty"
    versions = current.get("versions") if current else {}

    status = "ready"
    blocker: dict | None = None
    preconditions: list[dict] = []

    def block(code: str, message: str) -> None:
        nonlocal status, blocker
        status = "blocked"
        blocker = {"code": code, "message": message}

    def resolve_catalog():
        nonlocal target_version, source, checksum, catalog_digest
        text = _read_catalog_text(catalog_index)
        resolved = resolve_version(parse_catalog(text), plugin_id, version)
        target_version = resolved.version
        source = resolved.wheel_url
        checksum = resolved.sha256
        catalog_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if operation == "install":
        preconditions.append(_precondition(
            "PLUGIN_INSTALLED",
            "pass" if not installed else "fail",
            "Plugin is not installed" if not installed else "Plugin is already installed",
        ))
        if installed:
            block("PLUGIN_CONFLICT", f"Plugin is already installed: {plugin_id}")
        elif package is None:
            try:
                resolve_catalog()
            except PlatformContractError as error:
                block(error.code, error.message)
            else:
                preconditions.append(_precondition(
                    "PLUGIN_AVAILABLE", "pass",
                    f"Resolved {plugin_id}@{target_version} from the catalog",
                ))

    elif operation == "upgrade":
        if current is None:
            block("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
            preconditions.append(_precondition("PLUGIN_INSTALLED", "fail", "Plugin is not installed"))
        else:
            preconditions.append(_precondition("PLUGIN_INSTALLED", "pass", "Plugin is installed"))
            if package is None:
                try:
                    resolve_catalog()
                except PlatformContractError as error:
                    block(error.code, error.message)
            if status == "ready":
                if target_version == current_version:
                    status = "noop"
                    preconditions.append(_precondition(
                        "PLUGIN_UP_TO_DATE", "noop",
                        f"Plugin is already at {current_version}",
                    ))
                elif current_version is not None and version_key(target_version) < version_key(current_version):
                    block("PLUGIN_DOWNGRADE_REQUIRED",
                          f"Target {target_version} is older than active {current_version}; use downgrade.")
                elif target_version in versions:
                    preconditions.append(_precondition(
                        "PLUGIN_REACTIVATION", "pass",
                        f"Version {target_version} is already materialized and will be re-activated",
                    ))

    elif operation == "downgrade":
        if current is None:
            block("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
            preconditions.append(_precondition("PLUGIN_INSTALLED", "fail", "Plugin is not installed"))
        else:
            preconditions.append(_precondition("PLUGIN_INSTALLED", "pass", "Plugin is installed"))
            if version not in versions:
                block("PLUGIN_VERSION_UNAVAILABLE",
                      f"Plugin has no installed version to downgrade to: {plugin_id}@{version}")
                preconditions.append(_precondition("PLUGIN_VERSION_UNAVAILABLE", "fail",
                                                   f"Version {version} is not materialized"))
            elif version == current_version:
                status = "noop"
                preconditions.append(_precondition("PLUGIN_UP_TO_DATE", "noop",
                                                   f"Plugin is already at {current_version}"))

    elif operation == "rollback":
        if current is None:
            block("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
            preconditions.append(_precondition("PLUGIN_INSTALLED", "fail", "Plugin is not installed"))
        else:
            history = current.get("history", [])
            if len(history) < 2:
                block("ROLLBACK_UNAVAILABLE", f"Plugin has no previous version to roll back to: {plugin_id}")
                preconditions.append(_precondition("ROLLBACK_UNAVAILABLE", "fail", "No previous version in history"))
            else:
                target_version = history[-2]
                preconditions.append(_precondition(
                    "ROLLBACK_AVAILABLE", "pass",
                    f"Will roll back to {target_version}",
                ))

    elif operation == "uninstall":
        if current is None:
            block("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
            preconditions.append(_precondition("PLUGIN_INSTALLED", "fail", "Plugin is not installed"))
        else:
            preconditions.append(_precondition("PLUGIN_INSTALLED", "pass", "Plugin is installed"))

    next_state = "absent" if operation == "uninstall" else "installed"

    plan = {
        "operation": operation,
        "pluginId": plugin_id,
        "currentVersion": current_version,
        "targetVersion": target_version,
        "currentState": current_state,
        "nextState": next_state,
        "status": status,
        "preconditions": preconditions,
        "source": source,
        "checksum": checksum,
        "gates": [
            "package inspection",
            "isolated installation conformance",
            "checksum verification",
            "registration validation",
        ],
        "requiresConfirmation": status == "ready",
    }
    if blocker is not None:
        plan["blocker"] = blocker
    if catalog_digest is not None:
        plan["catalogDigest"] = catalog_digest
    if package_digest is not None:
        plan["packageDigest"] = package_digest
    return plan


def verify_plan_binding(plan: dict, store_root: str, catalog_index: str) -> str | None:
    """Return an error code when the plan's bound source changed since planning.

    A catalog plan is bound to the raw catalog digest; a local-package plan is
    bound to the package tree digest.  Recomputing these at execute time closes
    the plan/execute TOCTOU window so the executed bytes are exactly what the
    user confirmed.
    """
    if plan.get("catalogDigest") is not None:
        try:
            current_digest = catalog_index_digest(catalog_index)
        except PlatformContractError:
            return "PLUGIN_CATALOG_UNAVAILABLE"
        if current_digest != plan["catalogDigest"]:
            return "PLAN_STALE"
    if plan.get("packageDigest") is not None:
        source = plan.get("source")
        if source:
            try:
                current_digest = package_checksum(source)
            except (OSError, PlatformContractError):
                return "PLAN_STALE"
            if current_digest != plan["packageDigest"]:
                return "PLAN_STALE"
    return None


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
                    step.plugin_id, version=step.version, index=catalog_index, store_root=store_root,
                )
        elif operation == "upgrade":
            if step.package is not None:
                result = manager.upgrade(step.package)
            else:
                result = add_from_catalog(
                    step.plugin_id, version=step.version, index=catalog_index,
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
    "catalog_index_digest",
    "download_bytes",
    "execute_intent_step",
    "known_plugin_ids",
    "latest_available_from",
    "lifecycle_manager",
    "load_catalog_source",
    "load_scope",
    "plan_plugin_change",
    "plugin_catalog",
    "store_index_digest",
    "store_index_entries",
    "store_registry",
    "verify_catalog_identity",
    "verify_plan_binding",
]
