from __future__ import annotations

import json
import unittest

from jsonschema import Draft202012Validator

from assayer_host.release_lifecycle import PluginInstallation, PluginLifecyclePlanner
from assayer_host.resources import default_schema_root


class PluginLifecyclePlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = PluginLifecyclePlanner()
        self.current = PluginInstallation(
            "assayer@release-010", "0.1.0", True, True, True, True, True,
        )
        self.target = PluginInstallation(
            "assayer@release-011", "0.1.1", False, False, True, True, True,
        )

    def assert_valid(self, plan):
        schema = json.loads((default_schema_root() / "plugin-lifecycle-plan.schema.json").read_text())
        Draft202012Validator(schema).validate(plan)

    def test_upgrade_removes_current_only_after_both_release_sources_are_verified(self):
        plan = self.planner.build(
            "upgrade", self.current, target=self.target,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "ready")
        actions = [item["action"] for item in plan["steps"]]
        self.assertLess(actions.index("remove_current"), actions.index("install_target"))
        self.assertLess(actions.index("install_target"), actions.index("verify_target"))
        self.assertEqual(
            [item["action"] for item in plan["compensationSteps"]],
            ["remove_failed_target", "restore_current", "verify_restored"],
        )
        self.assertTrue(plan["rollback"]["readyBeforeMutation"])
        self.assert_valid(plan)

    def test_mutable_current_source_cannot_claim_rollback_readiness(self):
        current = PluginInstallation(
            "assayer@personal", "0.1.0", True, True, True, True, False,
        )
        plan = self.planner.build(
            "upgrade", current, target=self.target,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "blocked")
        self.assertIn(
            "CURRENT_RELEASE_RESTORABLE",
            {item["code"] for item in plan["preconditions"] if item["status"] == "fail"},
        )
        self.assertFalse(plan["rollback"]["readyBeforeMutation"])
        self.assert_valid(plan)

    def test_rollback_uses_the_same_safe_replacement_contract(self):
        newer = PluginInstallation(
            "assayer@release-011", "0.1.1", True, True, True, True, True,
        )
        older = PluginInstallation(
            "assayer@release-010", "0.1.0", False, False, True, True, True,
        )
        plan = self.planner.build(
            "rollback", newer, target=older,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["rollback"]["readyBeforeMutation"])
        self.assert_valid(plan)

    def test_mutable_same_selector_is_blocked(self):
        target = PluginInstallation(
            self.current.selector, "0.1.1", False, False, True, True, True,
        )
        plan = self.planner.build(
            "upgrade", self.current, target=target,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["steps"], [])
        failed = {item["code"] for item in plan["preconditions"] if item["status"] == "fail"}
        self.assertIn("TARGET_SELECTOR_DISTINCT", failed)
        self.assert_valid(plan)

    def test_unverified_target_is_blocked(self):
        target = PluginInstallation(
            self.target.selector, self.target.version, False, False, True, False, True,
        )
        plan = self.planner.build(
            "upgrade", self.current, target=target,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "blocked")
        self.assertIn(
            "TARGET_RELEASE_VERIFIED",
            {item["code"] for item in plan["preconditions"] if item["status"] == "fail"},
        )
        self.assert_valid(plan)

    def test_active_run_blocks_every_mutation(self):
        plan = self.planner.build(
            "rollback", self.current, target=self.target,
            active_run=True, release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["steps"], [])
        self.assert_valid(plan)

    def test_preinstalled_target_is_blocked_to_prevent_duplicate_names(self):
        target = PluginInstallation(
            self.target.selector, self.target.version, True, True, True, True, True,
        )
        plan = self.planner.build(
            "upgrade", self.current, target=target,
            release_direction_verified=True,
        )
        self.assertEqual(plan["status"], "blocked")
        self.assertIn(
            "TARGET_NOT_INSTALLED",
            {item["code"] for item in plan["preconditions"] if item["status"] == "fail"},
        )
        self.assert_valid(plan)

    def test_uninstall_is_ready_only_when_no_run_is_active(self):
        ready = self.planner.build("uninstall", self.current)
        blocked = self.planner.build("uninstall", self.current, active_run=True)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(blocked["status"], "blocked")
        self.assert_valid(ready)
        self.assert_valid(blocked)

    def test_absent_uninstall_is_idempotent_noop(self):
        absent = PluginInstallation(
            "assayer@release-010", None, False, False, False, False,
        )
        plan = self.planner.build("uninstall", absent)
        self.assertEqual(plan["status"], "noop")
        self.assertEqual(plan["steps"], [])
        self.assert_valid(plan)

    def test_invalid_selector_and_version_are_rejected_without_reflection(self):
        with self.assertRaises(ValueError):
            PluginInstallation("../../secret", "0.1.0", True, True, True)
        with self.assertRaises(ValueError):
            PluginInstallation("assayer@release", "/private/token", True, True, True)


if __name__ == "__main__":
    unittest.main()
