from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from assayer_host.lifecycle import InstallationStatusBuilder
from assayer_host.resources import default_schema_root


PYTHON_IDENTITY = {
    "implementation": "CPython",
    "version": "3.11.9",
    "platform": "test-platform",
}


class InstallationStatusTests(unittest.TestCase):
    def test_aligned_release_metadata_is_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            status = InstallationStatusBuilder(
                directory,
                environ={
                    "ASSAYER_PLUGIN_VERSION": "0.1.0+codex.20260903",
                    "ASSAYER_RUNTIME_VERSION": "0.1.0",
                    "ASSAYER_BUNDLE_VERIFIED": "1",
                },
                package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            ).build()
        self.assertEqual(status["status"], "healthy")
        self.assertEqual(status["identity"], {
            "packageVersion": "0.1.0",
            "pluginVersion": "0.1.0+codex.20260903",
            "runtimeVersion": "0.1.0",
        })
        self.assertTrue(all(item["status"] == "pass" for item in status["checks"]))
        schema = json.loads((default_schema_root() / "installation-status.schema.json").read_text())
        Draft202012Validator(schema).validate(status)

    def test_source_run_without_launcher_metadata_is_limited(self):
        with tempfile.TemporaryDirectory() as directory:
            status = InstallationStatusBuilder(
                directory, environ={}, package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            ).build()
        self.assertEqual(status["status"], "limited")
        self.assertIn("LAUNCHER_METADATA_MISSING", {item["code"] for item in status["checks"]})
        self.assertIn("BUNDLE_INTEGRITY_UNVERIFIED", {item["code"] for item in status["checks"]})

    def test_uninstalled_source_run_is_limited_instead_of_falsely_degraded(self):
        with tempfile.TemporaryDirectory() as directory:
            status = InstallationStatusBuilder(
                directory, environ={}, package_version=None,
                python_identity=PYTHON_IDENTITY,
            ).build()
        self.assertEqual(status["status"], "limited")
        missing = next(item for item in status["checks"] if item["code"] == "PACKAGE_IDENTITY_MISSING")
        self.assertEqual(missing["status"], "warning")

    def test_mismatched_release_identity_is_degraded(self):
        with tempfile.TemporaryDirectory() as directory:
            status = InstallationStatusBuilder(
                directory,
                environ={
                    "ASSAYER_PLUGIN_VERSION": "0.2.0+codex.release",
                    "ASSAYER_RUNTIME_VERSION": "0.2.0",
                    "ASSAYER_BUNDLE_VERIFIED": "1",
                },
                package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            ).build()
        self.assertEqual(status["status"], "degraded")
        mismatch = next(item for item in status["checks"] if item["code"] == "VERSION_IDENTITY_MISMATCH")
        self.assertEqual(mismatch["status"], "fail")

    def test_invalid_environment_identity_is_not_reflected(self):
        secret = "/private/token-value"
        with tempfile.TemporaryDirectory() as directory:
            status = InstallationStatusBuilder(
                directory,
                environ={
                    "ASSAYER_PLUGIN_VERSION": secret,
                    "ASSAYER_RUNTIME_VERSION": "0.1.0",
                    "ASSAYER_BUNDLE_VERIFIED": "1",
                },
                package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            ).build()
        self.assertEqual(status["status"], "degraded")
        self.assertIsNone(status["identity"]["pluginVersion"])
        self.assertNotIn(secret, json.dumps(status))
        self.assertIn("LAUNCHER_METADATA_INVALID", {item["code"] for item in status["checks"]})

    def test_recent_runs_expose_only_safe_ids_and_allowlisted_artifact_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "scan-internal-directory"
            run.mkdir()
            (run / "audit-ledger.json").write_text(json.dumps({
                "scan": {"runId": "run-safe-001"},
                "secret": str(root / "private-secret.txt"),
            }), encoding="utf-8")
            (run / "audit-summary.md").write_text("summary", encoding="utf-8")
            (run / "run-diagnostics.md").write_text("diagnostics", encoding="utf-8")
            (run / "canonical-result.json").write_text(json.dumps({
                "run": {"runId": "run-safe-001"},
            }), encoding="utf-8")
            (run / "private-secret.txt").write_text("secret", encoding="utf-8")
            status = InstallationStatusBuilder(
                root, environ={}, package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            ).build()
            serialized = json.dumps(status)
        self.assertEqual(status["recentRuns"], [{
            "runId": "run-safe-001",
            "artifacts": ["canonical-result.json", "audit-summary.md", "run-diagnostics.md"],
        }])
        self.assertNotIn(directory, serialized)
        self.assertNotIn("private-secret.txt", serialized)

    def test_invalid_recent_run_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            builder = InstallationStatusBuilder(
                directory, environ={}, package_version="0.1.0",
                python_identity=PYTHON_IDENTITY,
            )
            with self.assertRaises(ValueError):
                builder.build(max_recent_runs=0)


if __name__ == "__main__":
    unittest.main()
