"""Deterministic natural-language intent resolution for the plugin workbench."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_host.plugin_intent import IntentResolutionError, resolve_intent

KNOWN = ("test-minimal", "assayer.frontend-audit")


class PluginIntentResolutionTest(unittest.TestCase):
    def test_resolves_list_intent(self):
        plan = resolve_intent("what plugins do i have", known_plugin_ids=KNOWN)
        self.assertEqual([step.operation for step in plan], ["list"])

    def test_resolves_info_intent_by_short_name(self):
        plan = resolve_intent("tell me about test-minimal", known_plugin_ids=KNOWN)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].operation, "info")
        self.assertEqual(plan[0].plugin_id, "test-minimal")

    def test_resolves_install_intent_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "pkg"
            package.mkdir()
            plan = resolve_intent(f"install {package}", known_plugin_ids=KNOWN)
            self.assertEqual(plan[0].operation, "install")
            self.assertEqual(plan[0].package, str(package.resolve()))
            self.assertTrue(plan[0].dangerous)

    def test_resolves_downgrade_with_version(self):
        plan = resolve_intent("pin test-minimal to 0.9.0", known_plugin_ids=KNOWN)
        self.assertEqual(plan[0].operation, "downgrade")
        self.assertEqual(plan[0].plugin_id, "test-minimal")
        self.assertEqual(plan[0].version, "0.9.0")

    def test_resolves_rollback_and_uninstall(self):
        self.assertEqual(
            resolve_intent("undo the last update for test-minimal", known_plugin_ids=KNOWN)[0].operation,
            "rollback",
        )
        self.assertEqual(
            resolve_intent("remove test-minimal", known_plugin_ids=KNOWN)[0].operation,
            "uninstall",
        )

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

    def test_unresolvable_intent_fails_closed(self):
        with self.assertRaises(IntentResolutionError) as ctx:
            resolve_intent("do the thing", known_plugin_ids=KNOWN)
        self.assertEqual(ctx.exception.code, "INTENT_UNRESOLVED")

    def test_downgrade_without_version_asks_for_detail(self):
        with self.assertRaises(IntentResolutionError) as ctx:
            resolve_intent("downgrade test-minimal", known_plugin_ids=KNOWN)
        self.assertEqual(ctx.exception.code, "INTENT_NEEDS_DETAIL")

    def test_install_bare_name_is_unresolved(self):
        with self.assertRaises(IntentResolutionError) as ctx:
            resolve_intent("install test-minimal", known_plugin_ids=KNOWN)
        self.assertEqual(ctx.exception.code, "INTENT_PACKAGE_UNRESOLVED")

    def test_empty_intent_fails_closed(self):
        with self.assertRaises(IntentResolutionError) as ctx:
            resolve_intent("   ", known_plugin_ids=KNOWN)
        self.assertEqual(ctx.exception.code, "INTENT_EMPTY")


if __name__ == "__main__":
    unittest.main()
