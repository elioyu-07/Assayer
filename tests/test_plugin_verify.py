"""End-to-end verification of a compiler-generated exact wheel."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from assayer_platform.plugin_verify import verify_plugin_source, verify_simple_plugin


class PluginVerifyTests(unittest.TestCase):
    def test_verify_builds_and_exercises_exact_wheel_without_source_artifacts(self):
        source = Path(__file__).parent / "fixtures" / "plugins" / "policy-pack"
        self.assertFalse((source / "plugin.py").exists())
        with tempfile.TemporaryDirectory() as directory:
            result = verify_simple_plugin(source, output_dir=directory)

            self.assertEqual(result["status"], "passed", result)
            wheel = Path(result["wheel"])
            self.assertTrue(wheel.is_file())
            self.assertEqual(len(result["sha256"]), 64)
            self.assertTrue(result["acceptance"]["isolatedInstall"])
            self.assertTrue(result["acceptance"]["resume"])
            self.assertTrue(result["acceptance"]["pagination"])
            self.assertTrue(result["acceptance"]["replay"])
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
            self.assertTrue(any(name.endswith("/business-cases.json") for name in names))
            self.assertTrue(any(name.endswith("/policy.json") for name in names))
            self.assertFalse(any(name.endswith("/author.py") for name in names))
            self.assertFalse(any(
                "/.git/" in name or "/.venv/" in name or "/tests/" in name
                for name in names
            ))
        self.assertFalse((source / "build").exists())
        self.assertFalse((source / "dist").exists())

    def test_advanced_source_is_staged_and_verified_as_one_sanitized_wheel(self):
        source_fixture = Path(__file__).parent / "fixtures" / "plugins" / "minimal"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(source_fixture, source)
            for relative in (
                ".git/config", ".venv/bin/python", "build/junk", "dist/old.whl",
                "tests/test_source_only.py",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source only", encoding="utf-8")

            result = verify_plugin_source(source, output_dir=root / "verified")

            self.assertEqual(result["status"], "passed", result)
            with zipfile.ZipFile(result["wheel"]) as archive:
                names = {name for name in archive.namelist()}
            forbidden = {".git", ".venv", "tests", "build", "dist", "__pycache__"}
            self.assertFalse(any(forbidden.intersection(Path(name).parts) for name in names))
            self.assertTrue((source / ".venv/bin/python").is_file())
            self.assertTrue((source / "dist/old.whl").is_file())

    def test_browser_policy_pack_runs_provider_backed_generated_acceptance(self):
        source = Path("plugins/frontend-audit")
        with tempfile.TemporaryDirectory() as directory:
            result = verify_simple_plugin(source, output_dir=directory)

        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["pluginId"], "assayer.frontend-audit")
        self.assertTrue(result["acceptance"]["resume"])


if __name__ == "__main__":
    unittest.main()
