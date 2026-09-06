"""Remote plugin catalog: the ``plugins.json`` distribution index.

The catalog is deliberately dumb: one ``plugins.json`` file in a git repository
acts as the registry of record for independently distributed audit plugins.  It
maps a plugin ID to its published versions, each carrying only what a client
needs to *download and verify* a wheel: a download URL and a SHA-256 digest.

Responsibility boundaries:

* The catalog answers "what exists, where do I download it, is it intact".
  It does **not** answer "is it compliant" — conformance is recomputed locally
  by :mod:`assayer_platform.plugin_lifecycle` after materialization.
* The catalog is read-only from the client's perspective.  Publication writes
  it via a pull request (see the ``plugins publish`` host command).

This module is intentionally free of network and plugin-code imports so it can
be validated without any side effects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse

from .contract import PlatformContractError


CATALOG_FILENAME = "plugins.json"
CATALOG_SCHEMA_VERSION = "1.0.0"

_ENTITY_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class CatalogVersion:
    """One published, downloadable version of a plugin."""

    plugin_id: str
    version: str
    platform_api_version: str
    wheel_url: str
    sha256: str
    published_at: str | None = None


@dataclass(frozen=True)
class CatalogPlugin:
    """A plugin's identity and its published versions keyed by version string."""

    plugin_id: str
    name: str
    description: str
    versions: Mapping[str, CatalogVersion]


@dataclass(frozen=True)
class PluginCatalog:
    """A parsed and validated ``plugins.json`` catalog."""

    schema_version: str
    plugins: Mapping[str, CatalogPlugin]

    def plugin(self, plugin_id: str) -> CatalogPlugin | None:
        return self.plugins.get(plugin_id)

    def version(self, plugin_id: str, version: str) -> CatalogVersion | None:
        plugin = self.plugins.get(plugin_id)
        return plugin.versions.get(version) if plugin is not None else None


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


def version_key(version: str) -> tuple:
    """Public comparable semantic-version key used by lifecycle plan preconditions."""
    return _version_key(version)


def _fail(code: str, message: str) -> PlatformContractError:
    return PlatformContractError(code, message)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _validate_https_url(value: Any) -> None:
    if not isinstance(value, str):
        raise _fail("PLUGIN_CATALOG_INVALID", "wheelUrl must be a string.")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise _fail(
            "PLUGIN_CATALOG_INVALID",
            f"wheelUrl must be an absolute http(s) URL: {value}",
        )


def parse_catalog(text: str) -> PluginCatalog:
    """Parse and validate catalog content, raising on the first contract violation."""
    try:
        value = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _fail("PLUGIN_CATALOG_INVALID", f"The catalog is not valid JSON: {error}") from error
    if not _is_mapping(value):
        raise _fail("PLUGIN_CATALOG_INVALID", "The catalog must be a JSON object.")
    if value.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise _fail(
            "PLUGIN_CATALOG_INVALID",
            f"The catalog schemaVersion must be {CATALOG_SCHEMA_VERSION}.",
        )
    plugins_value = value.get("plugins")
    if not _is_mapping(plugins_value):
        raise _fail("PLUGIN_CATALOG_INVALID", "The catalog has no valid plugins table.")

    plugins: dict[str, CatalogPlugin] = {}
    for plugin_id, plugin_value in plugins_value.items():
        if not isinstance(plugin_id, str) or not _ENTITY_ID.fullmatch(plugin_id):
            raise _fail(
                "PLUGIN_CATALOG_INVALID",
                f"A catalog plugin key is not a valid entity ID: {plugin_id}",
            )
        if not _is_mapping(plugin_value):
            raise _fail("PLUGIN_CATALOG_INVALID", f"Plugin {plugin_id} must be an object.")
        if plugin_value.get("pluginId", plugin_id) != plugin_id:
            raise _fail(
                "PLUGIN_CATALOG_INVALID",
                f"Plugin {plugin_id} has a mismatched pluginId field.",
            )
        name = plugin_value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _fail("PLUGIN_CATALOG_INVALID", f"Plugin {plugin_id} has no valid name.")
        description = plugin_value.get("description", "")
        if not isinstance(description, str):
            raise _fail("PLUGIN_CATALOG_INVALID", f"Plugin {plugin_id} description must be a string.")
        versions_value = plugin_value.get("versions")
        if not _is_mapping(versions_value) or not versions_value:
            raise _fail(
                "PLUGIN_CATALOG_INVALID",
                f"Plugin {plugin_id} must declare at least one version.",
            )

        versions: dict[str, CatalogVersion] = {}
        for version, version_value in versions_value.items():
            if not isinstance(version, str) or not _SEMVER.fullmatch(version):
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id} has an invalid version key: {version}",
                )
            if not _is_mapping(version_value):
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} must be an object.",
                )
            if version_value.get("version", version) != version:
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} has a mismatched version field.",
                )
            if version_value.get("pluginId", plugin_id) != plugin_id:
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} has a mismatched pluginId field.",
                )
            platform_api_version = version_value.get("platformApiVersion")
            if not isinstance(platform_api_version, str) or not _SEMVER.fullmatch(platform_api_version):
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} has an invalid platformApiVersion.",
                )
            sha256 = version_value.get("sha256")
            if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} has an invalid sha256 digest.",
                )
            _validate_https_url(version_value.get("wheelUrl"))
            published_at = version_value.get("publishedAt")
            if published_at is not None and not isinstance(published_at, str):
                raise _fail(
                    "PLUGIN_CATALOG_INVALID",
                    f"Plugin {plugin_id}@{version} has a non-string publishedAt.",
                )
            versions[version] = CatalogVersion(
                plugin_id=plugin_id,
                version=version,
                platform_api_version=platform_api_version,
                wheel_url=version_value["wheelUrl"],
                sha256=sha256.lower(),
                published_at=published_at,
            )
        plugins[plugin_id] = CatalogPlugin(
            plugin_id=plugin_id,
            name=name,
            description=description,
            versions=MappingProxyType(versions),
        )

    return PluginCatalog(
        schema_version=CATALOG_SCHEMA_VERSION,
        plugins=MappingProxyType(plugins),
    )


def load_catalog(path: str | Any) -> PluginCatalog:
    """Read and parse a catalog from a filesystem path (``str`` or ``Path``)."""
    candidate = Path(path) if isinstance(path, str) else path
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _fail("PLUGIN_CATALOG_INVALID", f"The catalog could not be read: {error}") from error
    return parse_catalog(text)


def latest_version(catalog: PluginCatalog, plugin_id: str) -> CatalogVersion:
    """Resolve the highest published version of a plugin."""
    plugin = catalog.plugin(plugin_id)
    if plugin is None:
        raise _fail("UNKNOWN_PLUGIN", f"Plugin is not in the catalog: {plugin_id}")
    return max(plugin.versions.values(), key=lambda item: _version_key(item.version))


def latest_published(catalog: PluginCatalog, plugin_id: str) -> str | None:
    """Return the highest published version string, or None when unknown.

    Unlike :func:`latest_version`, this is non-raising and used for the read-only
    ``upgradable`` check: the client asks "what is the newest version the source
    knows" and compares it against the installed version locally.
    """
    plugin = catalog.plugin(plugin_id)
    if plugin is None:
        return None
    return max(plugin.versions.values(), key=lambda item: _version_key(item.version)).version


def resolve_version(catalog: PluginCatalog, plugin_id: str, version: str | None = None) -> CatalogVersion:
    """Resolve an exact version, or the latest when none is requested."""
    if version is None:
        return latest_version(catalog, plugin_id)
    resolved = catalog.version(plugin_id, version)
    if resolved is None:
        raise _fail(
            "PLUGIN_VERSION_UNAVAILABLE",
            f"Plugin version is not in the catalog: {plugin_id}@{version}",
        )
    return resolved


def upsert_catalog_version(
    text: str,
    *,
    plugin_id: str,
    name: str,
    description: str,
    version: str,
    platform_api_version: str,
    wheel_url: str,
    sha256: str,
    published_at: str,
) -> str:
    """Return catalog JSON text with a plugin version added or updated.

    Preserves the existing catalog's plugin metadata and other versions, and
    validates both the input and the result before returning.  Used by
    ``plugins publish`` to compose the registry update without network side
    effects.
    """
    parse_catalog(text)
    payload = json.loads(text)
    plugins = payload.setdefault("plugins", {})
    plugin = plugins.setdefault(plugin_id, {})
    plugin["pluginId"] = plugin_id
    plugin["name"] = name or plugin_id
    if description:
        plugin["description"] = description
    versions = plugin.setdefault("versions", {})
    versions[version] = {
        "pluginId": plugin_id,
        "version": version,
        "platformApiVersion": platform_api_version,
        "wheelUrl": wheel_url,
        "sha256": sha256.lower(),
        "publishedAt": published_at,
    }
    result = json.dumps(payload, indent=2) + "\n"
    parse_catalog(result)
    return result
