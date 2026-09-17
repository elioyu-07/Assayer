"""End-to-end verification of a data-only compiled plugin contract."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
from assayer_platform.plugin_verify import verify_plugin_source


class PluginVerifyTests(unittest.TestCase):
    def test_verify_publishes_exact_data_only_contract(self):
        source = Path(__file__).parent / "fixtures" / "plugins" / "policy-pack"
        self.assertFalse((source / "plugin.py").exists())
        with tempfile.TemporaryDirectory() as directory:
            result = verify_plugin_source(source, output_dir=directory)

            self.assertEqual(result["status"], "passed", result)
            artifact = Path(result["artifact"])
            self.assertTrue(artifact.is_file())
            self.assertEqual(len(result["sha256"]), 64)
            self.assertTrue(result["acceptance"]["dataOnly"])
            self.assertFalse(result["acceptance"]["pluginCodeImported"])
            contract = load_compiled_plugin_contract(artifact)
            self.assertEqual(contract.plugin_id, "test.policy-pack")
        self.assertFalse((source / "build").exists())
        self.assertFalse((source / "dist").exists())

    def test_advanced_source_is_rejected_without_compatibility_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "assayer-plugin-release.json").write_text(
                '{"pluginId":"legacy.advanced","pluginVersion":"1.0.0"}',
                encoding="utf-8",
            )
            for relative in (
                ".git/config", ".venv/bin/python", "build/junk", "dist/old.whl",
                "tests/test_source_only.py",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source only", encoding="utf-8")

            result = verify_plugin_source(source, output_dir=root / "verified")

            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(result["error"]["code"], "ADVANCED_SPI_REMOVED")

    def test_browser_policy_pack_compiles_without_runtime_code(self):
        source = Path("plugins/frontend-audit")
        with tempfile.TemporaryDirectory() as directory:
            result = verify_plugin_source(source, output_dir=directory)

        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["pluginId"], "assayer.frontend-audit")
        self.assertTrue(result["acceptance"]["dataOnly"])


if __name__ == "__main__":
    unittest.main()
