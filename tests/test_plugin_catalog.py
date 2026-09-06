from __future__ import annotations

import json
import unittest

from assayer_platform import PlatformContractError
from assayer_platform.plugin_catalog import (
    CATALOG_FILENAME,
    CATALOG_SCHEMA_VERSION,
    load_catalog,
    parse_catalog,
    resolve_version,
)


def _version(**overrides):
    base = {
        "pluginId": "test-minimal",
        "version": "1.2.0",
        "platformApiVersion": "1.0.0",
        "wheelUrl": "https://github.com/acme/plugins/releases/download/v1.2.0/test_minimal-1.2.0-py3-none-any.whl",
        "sha256": "a" * 64,
        "publishedAt": "2026-09-06T00:00:00Z",
    }
    base.update(overrides)
    return base


def _catalog(versions=None):
    return json.dumps({
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "plugins": {
            "test-minimal": {
                "pluginId": "test-minimal",
                "name": "test-minimal",
                "description": "Minimal external plugin",
                "versions": versions if versions is not None else {
                    "1.2.0": _version(),
                },
            },
        },
    })


class PluginCatalogParseTests(unittest.TestCase):
    def test_parses_valid_catalog(self):
        catalog = parse_catalog(_catalog())
        self.assertEqual(catalog.schema_version, CATALOG_SCHEMA_VERSION)
        self.assertIn("test-minimal", catalog.plugins)

    def test_resolves_latest_version(self):
        catalog = parse_catalog(_catalog({
            "1.1.0": _version(version="1.1.0"),
            "1.2.0": _version(version="1.2.0"),
        }))
        latest = resolve_version(catalog, "test-minimal")
        self.assertEqual(latest.version, "1.2.0")

    def test_resolves_exact_version(self):
        catalog = parse_catalog(_catalog({
            "1.1.0": _version(version="1.1.0"),
            "1.2.0": _version(version="1.2.0"),
        }))
        self.assertEqual(resolve_version(catalog, "test-minimal", "1.1.0").version, "1.1.0")

    def test_rejects_missing_plugin(self):
        catalog = parse_catalog(_catalog())
        with self.assertRaises(PlatformContractError) as ctx:
            resolve_version(catalog, "nope")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PLUGIN")

    def test_rejects_missing_version(self):
        catalog = parse_catalog(_catalog())
        with self.assertRaises(PlatformContractError) as ctx:
            resolve_version(catalog, "test-minimal", "9.9.9")
        self.assertEqual(ctx.exception.code, "PLUGIN_VERSION_UNAVAILABLE")

    def test_rejects_wrong_schema_version(self):
        payload = json.loads(_catalog())
        payload["schemaVersion"] = "0.0.1"
        with self.assertRaises(PlatformContractError):
            parse_catalog(json.dumps(payload))

    def test_rejects_bad_sha256(self):
        with self.assertRaises(PlatformContractError):
            parse_catalog(_catalog({"1.2.0": _version(sha256="not-a-digest")}))

    def test_rejects_non_https_url(self):
        with self.assertRaises(PlatformContractError):
            parse_catalog(_catalog({"1.2.0": _version(wheelUrl="ftp://example.com/x.whl")}))

    def test_rejects_empty_versions(self):
        with self.assertRaises(PlatformContractError):
            parse_catalog(_catalog(versions={}))

    def test_rejects_version_key_field_mismatch(self):
        with self.assertRaises(PlatformContractError):
            parse_catalog(_catalog({"1.2.0": _version(version="1.3.0")}))

    def test_load_catalog_from_path(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CATALOG_FILENAME
            path.write_text(_catalog(), encoding="utf-8")
            catalog = load_catalog(path)
            self.assertIn("test-minimal", catalog.plugins)


if __name__ == "__main__":
    unittest.main()
