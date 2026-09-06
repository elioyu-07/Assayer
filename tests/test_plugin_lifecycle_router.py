"""Natural-language workflow routing stays separate from execution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_host.plugin_intent import IntentResolutionError
from assayer_host.plugin_lifecycle_router import route_plugin_request


KNOWN = ("test-minimal",)


class PluginLifecycleRouterTest(unittest.TestCase):
    def test_routes_assayer_product_lifecycle_to_marketplace(self):
        route = route_plugin_request("upgrade Assayer itself", known_plugin_ids=KNOWN)
        self.assertEqual(route.workflow, "product_upgrade")
        self.assertEqual(route.steps, ())

    def test_routes_domain_plugin_lifecycle_to_lifecycle_workflow(self):
        route = route_plugin_request("upgrade test-minimal", known_plugin_ids=KNOWN)
        self.assertEqual(route.workflow, "plugin_lifecycle")
        self.assertEqual(route.steps[0].operation, "upgrade")
        self.assertEqual(route.steps[0].plugin_id, "test-minimal")

    def test_routes_plugin_run_to_interactive_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "input.json"
            target.write_text("{}", encoding="utf-8")
            route = route_plugin_request(
                f"review test-minimal TST-001 on {target}",
                known_plugin_ids=KNOWN,
            )
        self.assertEqual(route.workflow, "plugin_run")
        self.assertEqual(route.steps[0].operation, "run")

    def test_routes_install_then_run_as_ordered_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "input.json"
            target.write_text("{}", encoding="utf-8")
            route = route_plugin_request(
                f"install test-minimal then run test-minimal TST-001 on {target}",
                known_plugin_ids=KNOWN,
            )
        self.assertEqual(route.workflow, "install_then_run")
        self.assertEqual([step.operation for step in route.steps], ["install", "run"])

    def test_compound_request_still_fails_closed_when_run_details_are_missing(self):
        with self.assertRaises(IntentResolutionError) as error:
            route_plugin_request("install test-minimal then use it", known_plugin_ids=KNOWN)
        self.assertEqual(error.exception.code, "INTENT_NEEDS_DETAIL")


if __name__ == "__main__":
    unittest.main()
