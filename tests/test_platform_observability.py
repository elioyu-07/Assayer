from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    Operation,
    PlatformEvent,
    PlatformLedger,
    PlatformRun,
    build_platform_observability,
)
from assayer_platform.ledger import write_platform_artifacts


class PlatformObservabilityTest(unittest.TestCase):
    def ledger(self) -> PlatformLedger:
        run = PlatformRun("run-observe-001", "fixture.plugin", "1.0.0", "FIX-001", "1.0.0", "a" * 64, "now")
        events = (
            PlatformEvent("event:1", 1, run.run_id, "platform.run.started", "start", "started", "now"),
            PlatformEvent("event:2", 2, run.run_id, "agent.turn.finished", "finish", "succeeded", "now", details={"durationMs": 120}),
            PlatformEvent("event:3", 3, run.run_id, "host.request.rejected", "finish", "rejected", "now", details={"errorCode": "STALE_STATE"}),
            PlatformEvent("event:4", 4, run.run_id, "inspection.batch.split", "instant", "split", "now"),
        )
        return PlatformLedger(
            run, "running",
            (Operation("operation:1", "inspect", "item-1", "FIX-001", "succeeded", duration_ms=25),),
            events,
            workflow={"state": "blocked", "phase": "recovery", "canFinish": False, "requiredNextStep": "recover_work_item", "remaining": {}},
        )

    def test_summary_exposes_failures_rejections_retries_and_missing_timing(self):
        value = build_platform_observability(self.ledger())
        self.assertEqual(value["rejections"]["count"], 1)
        self.assertEqual(value["retries"]["count"], 1)
        self.assertEqual(value["timing"]["agent"]["durationMs"], 120)
        self.assertEqual(value["timing"]["model"]["status"], "not_exposed")
        self.assertEqual(value["currentPosition"]["requiredNextStep"], "recover_work_item")

    def test_artifacts_include_machine_and_human_observability_views(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_platform_artifacts(self.ledger(), directory, include_canonical_result=False)
            names = {Path(path).name for path in paths}
            self.assertIn("platform-observability.json", names)
            self.assertIn("platform-observability.md", names)
            value = json.loads((Path(directory) / "platform-observability.json").read_text(encoding="utf-8"))
            self.assertEqual(value["status"], "running")

    def test_host_rejection_is_recorded_as_diagnostic_event(self):
        value = build_platform_observability(self.ledger())
        self.assertEqual(value["rejections"]["items"][0]["errorCode"], "STALE_STATE")


if __name__ == "__main__":
    unittest.main()
