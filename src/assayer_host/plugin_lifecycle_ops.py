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

from assayer_platform import PLATFORM_API_VERSION, PlatformContractError
from assayer_platform.plugin_catalog import (
    PluginCatalog,
    parse_catalog,
    resolve_version,
    version_key,
)
from assayer_platform.plugin_source import source_tree_checksum
from assayer_platform.plugin_verify import verify_plugin_source
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
from assayer_platform.compiled_plugin_lifecycle import CompiledPluginLifecycleManager
from assayer_platform.plugin_installation import PluginInstallationStore

from .plugin_intent import IntentStep


# The public compiled-artifact catalog. Registry publication is external;
# ``add`` and by-name install consume it. Override with ``--index``.
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/elioyu-07/assayer-registry/main/plugins.json"
CATALOG_READ_TIMEOUT_SECONDS = 3
MUTATING_CATALOG_TIMEOUT_SECONDS = 10
PLUGIN_DOWNLOAD_TIMEOUT_SECONDS = 30

_ALLOWED_OPERATIONS = frozenset({"install", "upgrade", "downgrade", "rollback", "uninstall"})
def download_bytes(
    url: str, *, timeout_seconds: int = PLUGIN_DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return response.read()
    except OSError as error:
        raise PlatformContractError(
            "PLUGIN_DOWNLOAD_FAILED",
            f"The plugin could not be downloaded from {url}: {error}",
        ) from error


def _read_catalog_text(
    index: str, *, timeout_seconds: int = CATALOG_READ_TIMEOUT_SECONDS,
) -> str:
    """Read the raw catalog source text from a URL or a filesystem path."""
    if index.startswith(("http://", "https://")):
        try:
            return download_bytes(
                index, timeout_seconds=timeout_seconds,
            ).decode("utf-8")
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


def load_catalog_source(
    index: str, *, timeout_seconds: int = CATALOG_READ_TIMEOUT_SECONDS,
) -> PluginCatalog:
    return parse_catalog(_read_catalog_text(index, timeout_seconds=timeout_seconds))


def catalog_index_digest(index: str) -> str:
    """SHA-256 of the raw catalog source, used to bind a plan to its catalog.

    Recomputing this at execute time detects a catalog that changed after the
    plan was shown, closing the plan/execute TOCTOU window for by-name installs
    and upgrades.
    """
    return hashlib.sha256(_read_catalog_text(
        index, timeout_seconds=MUTATING_CATALOG_TIMEOUT_SECONDS,
    ).encode("utf-8")).hexdigest()


def lifecycle_manager(store_root: str) -> CompiledPluginLifecycleManager:
    return CompiledPluginLifecycleManager(PluginInstallationStore(store_root))


def add_from_catalog(plugin: str, *, version: str | None, index: str, store_root: str, operation: str = "install") -> dict:
    """Download, verify, and install one catalog-declared compiled artifact."""
    catalog = load_catalog_source(index)
    resolved = resolve_version(catalog, plugin, version)
    return _install_catalog_artifact(
        operation=operation,
        plugin_id=plugin,
        version=resolved.version,
        source=resolved.artifact_url,
        checksum=resolved.sha256,
        platform_api_version=resolved.platform_api_version,
        store_root=store_root,
    )


def apply_resolved_catalog_change(
    *, operation: str, plugin_id: str, version: str, source: str,
    checksum: str, platform_api_version: str | None = None, store_root: str,
) -> dict:
    """Execute a catalog-bound install or upgrade after rechecking its bytes."""
    return _install_catalog_artifact(
        operation=operation, plugin_id=plugin_id, version=version,
        source=source, checksum=checksum, platform_api_version=platform_api_version,
        store_root=store_root,
    )


def _install_catalog_artifact(*, operation: str, plugin_id: str, version: str,
                              source: str, checksum: str,
                              platform_api_version: str | None,
                              store_root: str) -> dict:
    if operation not in {"install", "upgrade"}:
        raise PlatformContractError("INVALID_OPERATION", f"Catalog cannot perform {operation}.")
    if platform_api_version is not None and platform_api_version != PLATFORM_API_VERSION:
        raise PlatformContractError(
            "PLATFORM_API_VERSION_UNSUPPORTED",
            f"Catalog artifact requires platform API {platform_api_version}; this platform provides {PLATFORM_API_VERSION}.",
        )
    try:
        payload = download_bytes(source)
    except PlatformContractError:
        raise
    digest = hashlib.sha256(payload).hexdigest()
    if digest.lower() != checksum.lower():
        raise PlatformContractError(
            "PLUGIN_ARTIFACT_DIGEST_MISMATCH",
            f"Catalog artifact digest mismatch for {plugin_id}@{version}.",
        )
    with tempfile.TemporaryDirectory(prefix="assayer-catalog-plugin-") as tmp:
        artifact = Path(tmp) / "compiled-plugin.json"
        artifact.write_bytes(payload)
        contract = load_compiled_plugin_contract(artifact)
        if contract.plugin_id != plugin_id or contract.version != version:
            raise PlatformContractError(
                "PLUGIN_ARTIFACT_IDENTITY_MISMATCH",
                f"Catalog identity does not match compiled artifact: expected {plugin_id}@{version}, "
                f"got {contract.plugin_id}@{contract.version}.",
            )
        manager = CompiledPluginLifecycleManager(PluginInstallationStore(store_root))
        return manager.install(artifact) if operation == "install" else manager.upgrade(artifact)


def apply_local_source_change(
    *, operation: str, source: str | Path, store_root: str,
) -> dict:
    """Compile, verify, and install one exact JSON contract from local source."""
    if operation not in {"install", "upgrade"}:
        raise PlatformContractError(
            "INVALID_OPERATION", f"Local source cannot perform {operation}.",
        )
    source_root = Path(source).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="assayer-local-plugin-") as tmp:
        temporary = Path(tmp)
        verification = verify_plugin_source(
            source_root, output_dir=temporary / "verified",
        )
        if verification.get("status") != "passed":
            error = verification.get("error", {})
            code = str(error.get("code", "PLUGIN_VERIFY_FAILED"))
            message = str(error.get(
                "message", "The local plugin release did not pass verification.",
            ))
            raise PlatformContractError(code, message)
        if "artifact" in verification:
            manager = CompiledPluginLifecycleManager(PluginInstallationStore(store_root))
            if operation == "install":
                return manager.install(verification["artifact"])
            return manager.upgrade(verification["artifact"])
        raise PlatformContractError(
            "COMPILED_PLUGIN_ARTIFACT_MISSING",
            "The declaration verifier did not produce a compiled plugin contract artifact.",
        )


def read_local_source_descriptor(package_root: str | Path) -> dict:
    """Compile and resolve identity for one declaration-only local source."""
    root = Path(package_root).expanduser().resolve()
    if (root / "plugin.yaml").is_file():
        with tempfile.TemporaryDirectory(prefix="assayer-local-plan-") as tmp:
            generated = Path(tmp) / "generated"
            compile_plugin_contract(root, generated)
            contract = load_compiled_plugin_contract(generated)
            return {
                "schemaVersion": "1.0.0",
                "pluginId": contract.plugin_id,
                "pluginVersion": contract.version,
                "contractDigest": contract.digest,
                "artifactType": "compiled-plugin-contract",
            }
    raise PlatformContractError(
        "PLUGIN_DECLARATION_NOT_FOUND",
        "Only declaration-only plugin sources containing plugin.yaml are accepted.",
    )


def store_registry(store_root: str):
    """Return the data-only lifecycle catalog; never discover Python plugins."""
    return lifecycle_manager(store_root)


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
    catalog_platform_api_version: str | None = None
    catalog_digest: str | None = None
    package_digest: str | None = None

    if package is not None:
        package_path = Path(package).expanduser().resolve()
        descriptor = read_local_source_descriptor(package_path)
        plugin_id = descriptor["pluginId"]
        target_version = descriptor["pluginVersion"]
        source = str(package_path)
        try:
            package_digest = source_tree_checksum(package_path)
        except (OSError, PlatformContractError) as error:
            raise PlatformContractError(
                "PLUGIN_PACKAGE_INVALID",
                f"The plugin package could not be fingerprinted: {error}",
            ) from error
    elif operation in {"install", "upgrade"}:
        catalog = load_catalog_source(catalog_index)
        resolved = resolve_version(catalog, plugin_id or "", target_version)
        plugin_id = resolved.plugin_id
        target_version = resolved.version
        source = resolved.artifact_url
        checksum = resolved.sha256
        catalog_platform_api_version = resolved.platform_api_version
        catalog_digest = catalog_index_digest(catalog_index)
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

    if operation == "install":
        preconditions.append(_precondition(
            "PLUGIN_INSTALLED",
            "pass" if not installed else "fail",
            "Plugin is not installed" if not installed else "Plugin is already installed",
        ))
        if installed:
            block("PLUGIN_CONFLICT", f"Plugin is already installed: {plugin_id}")
        if (
            status == "ready"
            and current_state == "dirty"
            and current_version is not None
            and target_version is not None
            and version_key(target_version) < version_key(current_version)
        ):
            block(
                "PLUGIN_DOWNGRADE_REQUIRED",
                f"Repair target {target_version} is older than recorded version "
                f"{current_version}; publish or select {current_version} or newer.",
            )
            preconditions.append(_precondition(
                "PLUGIN_REPAIR_VERSION", "fail",
                f"Repair cannot replace recorded {current_version} with older {target_version}",
            ))
        elif status == "ready" and current_state == "dirty":
            preconditions.append(_precondition(
                "PLUGIN_REPAIR_VERSION", "pass",
                f"Repair target {target_version} is not older than recorded {current_version}",
            ))

    elif operation == "upgrade":
        if current is None:
            block("UNKNOWN_PLUGIN", f"Plugin is not installed: {plugin_id}")
            preconditions.append(_precondition("PLUGIN_INSTALLED", "fail", "Plugin is not installed"))
        else:
            preconditions.append(_precondition("PLUGIN_INSTALLED", "pass", "Plugin is installed"))
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
        "changeKind": "repair" if operation == "install" and current_state == "dirty" else operation,
        "pluginId": plugin_id,
        "currentVersion": current_version,
        "targetVersion": target_version,
        "currentState": current_state,
        "nextState": next_state,
        "status": status,
        "preconditions": preconditions,
        "source": source,
        "checksum": checksum,
        "platformApiVersion": catalog_platform_api_version,
        "gates": [
            "declaration compilation",
            "compiled contract validation",
            "contract digest verification",
            "data-only installation",
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
                current_digest = source_tree_checksum(source)
            except (OSError, PlatformContractError):
                return "PLAN_STALE"
            if current_digest != plan["packageDigest"]:
                return "PLAN_STALE"
    return None


def store_index_entries(store_root: str) -> list[dict]:
    """Raw store index entries (including quarantined plugins) without imports."""
    store_path = Path(store_root).expanduser().resolve()
    if not (store_path / "index.json").is_file():
        return []
    return lifecycle_manager(store_path).list()


def plugin_catalog(registry) -> list[dict]:
    catalog = []
    for item in registry.list():
        if item.get("artifactType") != "compiled-plugin-contract":
            continue
        catalog.append({
            "pluginId": item["pluginId"],
            "version": item.get("version", item.get("activeVersion")),
            "artifactType": item["artifactType"],
            "inputKind": item.get("inputKind"),
            "subjectKind": item.get("subjectKind"),
            "scopeSchema": item.get("scopeSchema", {}),
            "checks": item.get("checks", []),
            "contractDigest": item.get("contractDigest"),
            "state": item.get("state", "installed"),
        })
    return sorted(catalog, key=lambda item: item["pluginId"])


def known_plugin_ids(store_root: str) -> tuple[str, ...]:
    ids: list[str] = []
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
            ) from None
    try:
        return json.loads(scope_json)
    except (TypeError, json.JSONDecodeError):
        raise PlatformContractError(
            "INVALID_SCOPE", "Plugin scope must be valid JSON",
        ) from None


def execute_intent_step(
    step: IntentStep,
    store_root: str,
    output_root: str,
    catalog_index: str = DEFAULT_CATALOG_URL,
) -> dict:
    del output_root
    manager = lifecycle_manager(store_root)
    operation = step.operation
    if operation == "list":
        catalog = plugin_catalog(store_registry(store_root))
        entries = store_index_entries(store_root)
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
        return {
            "operation": "run",
            "status": "failed",
            "error": {
                "code": "COMPILED_RUN_REQUIRED",
                "message": "The legacy plugin run command is removed; start a compiled contract Run through the platform runtime.",
            },
        }
    try:
        if operation == "install":
            if step.package is not None:
                result = apply_local_source_change(
                    operation="install", source=step.package, store_root=store_root,
                )
            else:
                result = add_from_catalog(
                    step.plugin_id, version=step.version, index=catalog_index, store_root=store_root,
                )
        elif operation == "upgrade":
            if step.package is not None:
                result = apply_local_source_change(
                    operation="upgrade", source=step.package, store_root=store_root,
                )
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
    "apply_local_source_change",
    "apply_resolved_catalog_change",
    "catalog_index_digest",
    "download_bytes",
    "execute_intent_step",
    "known_plugin_ids",
    "lifecycle_manager",
    "load_catalog_source",
    "load_scope",
    "plan_plugin_change",
    "plugin_catalog",
    "store_index_digest",
    "store_index_entries",
    "store_registry",
    "verify_plan_binding",
]
