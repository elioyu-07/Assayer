from __future__ import annotations

import json
import hashlib
from pathlib import Path
from unittest.mock import patch
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
        "artifactType": "compiled-plugin-contract",
        "artifactUrl": "https://github.com/acme/plugins/releases/download/v1.2.0/test-minimal-1.2.0.compiled-plugin.json",
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

    def test_rejects_invalid_catalog_contracts(self):
        wrong_schema = json.loads(_catalog())
        wrong_schema["schemaVersion"] = "0.0.1"
        cases = {
            "schema version": json.dumps(wrong_schema),
            "checksum": _catalog({"1.2.0": _version(sha256="not-a-digest")}),
            "artifact URL": _catalog({"1.2.0": _version(artifactUrl="ftp://example.com/compiled-plugin.json")}),
            "wheel URL": _catalog({"1.2.0": _version(wheelUrl="https://example.com/legacy.whl", artifactUrl=None)}),
            "artifact type": _catalog({"1.2.0": _version(artifactType="python-wheel")}),
            "empty versions": _catalog(versions={}),
            "version identity": _catalog({"1.2.0": _version(version="1.3.0")}),
        }
        for name, source in cases.items():
            with self.subTest(name=name), self.assertRaises(PlatformContractError):
                parse_catalog(source)

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
            artifact_url="https://github.com/acme/plugins/releases/download/v1.3.0/test-minimal-1.3.0.compiled-plugin.json",
            sha256="b" * 64,
            published_at="2026-09-06T00:00:00Z",
        )
        catalog = parse_catalog(updated)
        self.assertIn("1.3.0", catalog.plugins["test-minimal"].versions)
        self.assertEqual(
            catalog.plugins["test-minimal"].versions["1.3.0"].artifact_url,
            "https://github.com/acme/plugins/releases/download/v1.3.0/test-minimal-1.3.0.compiled-plugin.json",
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
                artifact_url="https://example.com/compiled-plugin.json",
                sha256="b" * 64,
                published_at="2026-09-06T00:00:00Z",
            )

    def test_catalog_artifact_is_digest_checked_and_installed_as_compiled_contract(self):
        from assayer_host.plugin_lifecycle_ops import add_from_catalog
        from assayer_platform.declaration_compiler import compile_plugin_contract

        fixture = Path(__file__).parent / "fixtures" / "plugins" / "policy-pack"
        with __import__("tempfile").TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifact"
            compile_plugin_contract(fixture, artifact_dir)
            payload = (artifact_dir / "compiled-plugin.json").read_bytes()
            catalog = json.loads(_catalog())
            catalog["plugins"]["test-minimal"]["versions"]["1.2.0"]["artifactUrl"] = "https://example.test/compiled-plugin.json"
            catalog["plugins"]["test-minimal"]["versions"]["1.2.0"]["sha256"] = hashlib.sha256(payload).hexdigest()
            # The downloaded artifact must agree with the catalog identity.
            contract = json.loads(payload)
            contract["plugin"]["id"] = "test-minimal"
            contract["plugin"]["version"] = "1.2.0"
            from assayer_platform.compiled_plugin_contract import contract_digest
            contract["contractDigest"] = contract_digest(contract)
            payload = json.dumps(contract, ensure_ascii=False, separators=(",", ":")).encode()
            catalog["plugins"]["test-minimal"]["versions"]["1.2.0"]["sha256"] = hashlib.sha256(payload).hexdigest()
            catalog_path = root / "plugins.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with patch("assayer_host.plugin_lifecycle_ops.download_bytes", return_value=payload):
                result = add_from_catalog("test-minimal", version="1.2.0", index=str(catalog_path), store_root=str(root / "store"))
            self.assertEqual(result["status"], "completed")

    def test_catalog_artifact_digest_mismatch_fails_closed(self):
        from assayer_host.plugin_lifecycle_ops import add_from_catalog
        with __import__("tempfile").TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "plugins.json"
            catalog_path.write_text(_catalog(), encoding="utf-8")
            with patch("assayer_host.plugin_lifecycle_ops.download_bytes", return_value=b"not-the-artifact"):
                with self.assertRaises(PlatformContractError) as error:
                    add_from_catalog("test-minimal", version="1.2.0", index=str(catalog_path), store_root=str(root / "store"))
            self.assertEqual(error.exception.code, "PLUGIN_ARTIFACT_DIGEST_MISMATCH")


if __name__ == "__main__":
    unittest.main()
