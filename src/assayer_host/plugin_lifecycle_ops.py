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
import re
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

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
    read_package_descriptor,
)
from assayer_platform.plugin_source import source_tree_checksum
from assayer_platform.plugin_verify import verify_plugin_source
from assayer_platform.simple_plugin_compiler import compile_simple_plugin

from .plugin_intent import IntentStep


# The public plugin catalog. ``plugins publish`` writes to this registry repo
# (its ``--registry`` / ``--registry-path`` defaults) via pull request; ``add``
# and by-name install read from it. Override with ``--index``.
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/elioyu-07/assayer-registry/main/plugins.json"
CATALOG_READ_TIMEOUT_SECONDS = 3
MUTATING_CATALOG_TIMEOUT_SECONDS = 10
PLUGIN_DOWNLOAD_TIMEOUT_SECONDS = 30

_ALLOWED_OPERATIONS = frozenset({"install", "upgrade", "downgrade", "rollback", "uninstall"})
_GITHUB_RELEASE_PATH = re.compile(
    r"^/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)$"
)
_GITHUB_RAW_PATH = re.compile(r"^/([^/]+)/([^/]+)/([^/]+)/(.+)$")


def _download_github_release_asset(url: str, *, timeout_seconds: int) -> bytes:
    """Use GitHub's API when the browser release endpoint is unreachable.

    Some managed networks allow ``api.github.com`` and the signed asset CDN
    while timing out ``github.com/releases/download``. The catalog remains the
    authority for the expected digest; this fallback only resolves the same
    public release asset through GitHub's supported API surface.
    """
    parsed = urlparse(url)
    match = _GITHUB_RELEASE_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or match is None:
        raise ValueError("not a GitHub release asset URL")
    owner, repository, tag, asset_name = (unquote(part) for part in match.groups())
    api_url = (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(repository, safe='')}/releases/tags/"
        f"{quote(tag, safe='')}"
    )
    metadata_request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "assayer-plugin-lifecycle",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(metadata_request, timeout=timeout_seconds) as response:
        release = json.loads(response.read().decode("utf-8"))
    assets = release.get("assets", []) if isinstance(release, dict) else []
    asset = next(
        (
            item for item in assets
            if isinstance(item, dict)
            and item.get("name") == asset_name
            and isinstance(item.get("url"), str)
        ),
        None,
    )
    if asset is None:
        raise OSError("the release metadata does not contain the requested asset")
    asset_request = urllib.request.Request(
        asset["url"],
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "assayer-plugin-lifecycle",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(asset_request, timeout=timeout_seconds) as response:
        return response.read()


def _download_github_raw_file(url: str, *, timeout_seconds: int) -> bytes:
    """Resolve a raw.githubusercontent file through the GitHub Contents API."""
    parsed = urlparse(url)
    match = _GITHUB_RAW_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "raw.githubusercontent.com"
        or match is None
    ):
        raise ValueError("not a GitHub raw content URL")
    owner, repository, reference, content_path = (
        unquote(part) for part in match.groups()
    )
    api_url = (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(repository, safe='')}/contents/"
        f"{quote(content_path, safe='/')}?ref={quote(reference, safe='')}"
    )
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "assayer-plugin-lifecycle",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def download_bytes(
    url: str, *, timeout_seconds: int = PLUGIN_DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    parsed = urlparse(url)
    github_release = (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and _GITHUB_RELEASE_PATH.fullmatch(parsed.path) is not None
    )
    if github_release:
        try:
            return _download_github_release_asset(
                url, timeout_seconds=timeout_seconds,
            )
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            # API rate limiting or a transient metadata failure must not make
            # the normal public release URL unusable.
            github_release = False
    github_raw = (
        parsed.scheme == "https"
        and parsed.netloc == "raw.githubusercontent.com"
        and _GITHUB_RAW_PATH.fullmatch(parsed.path) is not None
    )
    if github_raw:
        try:
            return _download_github_raw_file(
                url, timeout_seconds=timeout_seconds,
            )
        except (OSError, ValueError):
            # Keep the canonical raw URL as a fallback if the API is rate
            # limited or temporarily unavailable.
            github_raw = False
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
    catalog = load_catalog_source(
        index, timeout_seconds=MUTATING_CATALOG_TIMEOUT_SECONDS,
    )
    resolved = resolve_version(catalog, plugin, version)
    return apply_resolved_catalog_change(
        operation=operation,
        plugin_id=resolved.plugin_id,
        version=resolved.version,
        source=resolved.wheel_url,
        checksum=resolved.sha256,
        store_root=store_root,
    )


def apply_resolved_catalog_change(
    *, operation: str, plugin_id: str, version: str, source: str,
    checksum: str, store_root: str,
) -> dict:
    """Install the exact catalog artifact already bound into a verified plan."""
    if operation not in {"install", "upgrade"}:
        raise PlatformContractError(
            "INVALID_OPERATION", f"Catalog artifact cannot perform {operation}.",
        )
    data = download_bytes(source)
    manager = lifecycle_manager(store_root)
    with tempfile.TemporaryDirectory(prefix="assayer-add-") as tmp:
        root = materialize_wheel(data, checksum, Path(tmp))
        verify_catalog_identity(root, plugin_id, version)
        if operation == "upgrade":
            return manager.upgrade(root, wheel_sha256=checksum.lower())
        return manager.install(root, wheel_sha256=checksum.lower())


def apply_local_source_change(
    *, operation: str, source: str | Path, store_root: str,
) -> dict:
    """Build, verify, and install one exact wheel from a local authoring tree."""
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
            # A rejected plugin contract remains visible as dirty for diagnosis,
            # but no source tree is ever copied into the store. Environment and
            # reachability failures do not create a misleading plugin record.
            if code not in {
                "PLUGIN_SOURCE_UNREACHABLE",
                "PLUGIN_WHEEL_BUILDER_UNAVAILABLE",
                "PLUGIN_WHEEL_BUILD_TIMEOUT",
            }:
                try:
                    descriptor = json.loads(
                        (source_root / RELEASE_DESCRIPTOR).read_text(encoding="utf-8"),
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, PlatformContractError):
                    descriptor = None
                if isinstance(descriptor, dict):
                    plugin_id = descriptor.get("pluginId")
                    plugin_version = descriptor.get("pluginVersion")
                    if isinstance(plugin_id, str) and isinstance(plugin_version, str):
                        try:
                            return lifecycle_manager(store_root).quarantine(
                                plugin_id, plugin_version, code, operation=operation,
                            )
                        except PlatformContractError:
                            descriptor = None
            raise PlatformContractError(code, message)
        wheel = Path(str(verification["wheel"]))
        data = wheel.read_bytes()
        checksum = str(verification["sha256"])
        materialized = materialize_wheel(
            data, checksum, temporary / "materialized",
        )
        verify_catalog_identity(
            materialized,
            str(verification["pluginId"]),
            str(verification["pluginVersion"]),
        )
        manager = lifecycle_manager(store_root)
        if operation == "upgrade":
            return manager.upgrade(materialized, wheel_sha256=checksum)
        return manager.install(materialized, wheel_sha256=checksum)


def read_local_source_descriptor(package_root: str | Path) -> dict:
    """Validate and resolve identity for Advanced or ordinary local sources."""
    root = Path(package_root).expanduser().resolve()
    if (root / RELEASE_DESCRIPTOR).is_file():
        return read_package_descriptor(root)
    if (root / "plugin.yaml").is_file():
        with tempfile.TemporaryDirectory(prefix="assayer-local-plan-") as tmp:
            generated = Path(tmp) / "generated"
            compile_simple_plugin(root, generated)
            return read_package_descriptor(generated)
    # Preserve the established precise missing/invalid descriptor failures.
    return read_package_descriptor(root)


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
        text = _read_catalog_text(
            catalog_index, timeout_seconds=MUTATING_CATALOG_TIMEOUT_SECONDS,
        )
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
                current_digest = source_tree_checksum(source)
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
            "compatibility": (
                {
                    "protocolMinVersion": registration.compatibility.protocol_min_version,
                    "protocolMaxVersion": registration.compatibility.protocol_max_version,
                    "sdkMinVersion": registration.compatibility.sdk_min_version,
                    "sdkMaxVersion": registration.compatibility.sdk_max_version,
                    "capabilities": sorted(registration.compatibility.capabilities),
                }
                if registration.compatibility is not None else None
            ),
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
