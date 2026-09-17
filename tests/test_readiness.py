import json
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_host.readiness import collect_readiness, target_kind


class ReadinessTest(unittest.TestCase):
    def test_source_plugin_placeholder_does_not_claim_it_can_be_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = collect_readiness(
                target="./spec.md",
                codex_executable="/usr/bin/codex",
                plugin_root=directory,
            )
        bundle = next(item for item in report.checks if item.check_id == "assayer_bundle")
        self.assertEqual(bundle.status, "missing")
        self.assertFalse(report.ready)
        self.assertIn("Reinstall", bundle.repair_command or "")

    def test_target_kind_classifies_web_and_markdown_targets(self):
        self.assertEqual(target_kind("https://example.test/a"), "web")
        self.assertEqual(target_kind("./spec.md"), "markdown")

    def test_web_target_has_no_browser_check(self):
        report = collect_readiness(target="https://example.test/a", codex_executable="/usr/bin/codex")
        self.assertFalse(any(item.check_id == "chromium" for item in report.checks))

    def test_matching_bundle_is_reported_ready_without_starting_runtime(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ASSAYER_BUNDLE_VERIFIED": "1"}, clear=False,
        ):
            root = Path(directory)
            (root / "runtime" / "wheels").mkdir(parents=True)
            (root / "runtime" / "wheels" / "assayer-0.1.2-py3-none-any.whl").write_bytes(b"fixture")
            (root / "runtime" / "bundle-manifest.json").write_text(json.dumps({
                "runtime": {
                    "pythonVersion": f"{sys.version_info.major}.{sys.version_info.minor}",
                    "platform": sysconfig.get_platform(),
                },
            }), encoding="utf-8")
            report = collect_readiness(
                target="./spec.md", codex_executable="/usr/bin/codex",
                plugin_root=root,
            )
            self.assertTrue(report.ready, report.as_dict())
            self.assertEqual(
                next(item for item in report.checks if item.check_id == "assayer_bundle").status,
                "ok",
            )
            self.assertEqual(
                next(item for item in report.checks if item.check_id == "private_runtime").status,
                "ok",
            )

    def test_required_domain_plugin_is_reported_as_a_blocker(self):
        report = collect_readiness(
            target="./spec.md", plugin_id="missing.plugin",
            codex_executable="/usr/bin/codex",
        )
        plugin = next(item for item in report.checks if item.check_id == "domain_plugin")
        self.assertEqual(plugin.status, "missing")
        self.assertFalse(report.ready)
        self.assertIn("missing.plugin", plugin.message)


if __name__ == "__main__":
    unittest.main()
