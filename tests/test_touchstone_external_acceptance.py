"""The external Touchstone distribution completes the full M4 exit gate.

The journey is recorded by ``scripts/touchstone_plugin_external_acceptance.py``:
install -> discover -> run (Agent semantic review) -> valid result -> upgrade
-> rollback -> uninstall, using only the real package and Spec business input.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "touchstone_plugin_external_acceptance.py"
SPEC = importlib.util.spec_from_file_location("touchstone_plugin_external_acceptance", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("spec external journey script cannot be loaded")
touchstone_plugin_external_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(touchstone_plugin_external_acceptance)


class TouchstoneExternalJourneyTest(unittest.TestCase):
    def test_full_external_journey_completes_the_m4_exit_gate(self):
        result = touchstone_plugin_external_acceptance.run()
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["pluginId"], "assayer.touchstone")
        self.assertEqual(
            [item["operation"] for item in result["steps"]],
            ["install", "discover", "run", "upgrade", "rollback", "uninstall"],
        )
        run_step = next(item for item in result["steps"] if item["operation"] == "run")
        self.assertEqual(run_step["terminalStatus"], "completed")
        self.assertEqual(result["assertions"]["structuredSummary"], True)
        self.assertFalse(result["assertions"]["htmlOutput"])
        self.assertTrue(result["assertions"]["ledgerPublished"])
        self.assertEqual(result["assertions"]["installedCount"], 0)


if __name__ == "__main__":
    unittest.main()
