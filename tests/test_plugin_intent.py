"""Deterministic natural-language intent resolution for the plugin workbench."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_host.plugin_intent import IntentResolutionError, resolve_intent

KNOWN = ("test-minimal",)


class PluginIntentResolutionTest(unittest.TestCase):
    def test_resolves_supported_management_intents(self):
        cases = (
            ("what plugins do i have", "list", None, None),
            ("tell me about test-minimal", "info", "test-minimal", None),
            ("pin test-minimal to 0.9.0", "downgrade", "test-minimal", "0.9.0"),
            ("undo the last update for test-minimal", "rollback", "test-minimal", None),
            ("remove test-minimal", "uninstall", "test-minimal", None),
            ("install test-minimal", "install", "test-minimal", None),
        )
        for source, operation, plugin_id, version in cases:
            with self.subTest(source=source):
                plan = resolve_intent(source, known_plugin_ids=KNOWN)
                self.assertEqual(len(plan), 1)
                self.assertEqual(plan[0].operation, operation)
                self.assertEqual(plan[0].plugin_id, plugin_id)
                self.assertEqual(plan[0].version, version)

    def test_resolves_install_intent_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "pkg"
            package.mkdir()
            plan = resolve_intent(f"install {package}", known_plugin_ids=KNOWN)
            self.assertEqual(plan[0].operation, "install")
            self.assertEqual(plan[0].package, str(package.resolve()))
            self.assertTrue(plan[0].dangerous)

    def test_run_requires_check_and_scope_file(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = Path(directory) / "input.md"
            scope.write_text("# input", encoding="utf-8")
            plan = resolve_intent(
                f"run test-minimal TST-001 on {scope}", known_plugin_ids=KNOWN,
            )
            self.assertEqual(plan[0].operation, "run")
            self.assertEqual(plan[0].check_id, "TST-001")
            self.assertEqual(plan[0].scope_file, str(scope.resolve()))

    def test_invalid_or_incomplete_intents_fail_with_stable_codes(self):
        cases = (
            ("do the thing", "INTENT_UNRESOLVED"),
            ("downgrade test-minimal", "INTENT_NEEDS_DETAIL"),
            ("   ", "INTENT_EMPTY"),
        )
        for source, code in cases:
            with self.subTest(source=source), self.assertRaises(IntentResolutionError) as caught:
                resolve_intent(source, known_plugin_ids=KNOWN)
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
