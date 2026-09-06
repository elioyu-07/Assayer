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

    def test_upsert_adds_a_new_version_and_preserves_metadata(self):
        from assayer_platform.plugin_catalog import upsert_catalog_version

        updated = upsert_catalog_version(
            _catalog(),
            plugin_id="test-minimal",
            name="test-minimal",
            description="Minimal external plugin",
            version="1.3.0",
            platform_api_version="1.0.0",
            wheel_url="https://github.com/acme/plugins/releases/download/v1.3.0/test_minimal-1.3.0-py3-none-any.whl",
            sha256="b" * 64,
            published_at="2026-09-06T00:00:00Z",
        )
        catalog = parse_catalog(updated)
        self.assertIn("1.3.0", catalog.plugins["test-minimal"].versions)
        self.assertEqual(
            catalog.plugins["test-minimal"].versions["1.3.0"].wheel_url,
            "https://github.com/acme/plugins/releases/download/v1.3.0/test_minimal-1.3.0-py3-none-any.whl",
        )
        # Existing version and metadata are preserved.
        self.assertIn("1.2.0", catalog.plugins["test-minimal"].versions)
        self.assertEqual(catalog.plugins["test-minimal"].name, "test-minimal")

    def test_upsert_rejects_invalid_input(self):
        from assayer_platform.plugin_catalog import upsert_catalog_version

        with self.assertRaises(PlatformContractError):
            upsert_catalog_version(
                "{not json}",
                plugin_id="test-minimal",
                name="test-minimal",
                description="",
                version="1.3.0",
                platform_api_version="1.0.0",
                wheel_url="https://example.com/x.whl",
                sha256="b" * 64,
                published_at="2026-09-06T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
