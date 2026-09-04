import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    Operation,
    PlatformEvent,
    PlatformLedger,
    PlatformRun,
    build_platform_performance_bill,
    render_platform_performance_bill,
)
from assayer_platform.ledger import write_platform_artifacts


ROOT = Path(__file__).resolve().parents[1]


def ledger(*, mode="serial", reason="plugin_forbidden", metrics=None):
    run = PlatformRun(
        "run-performance-001",
        "example.quality-plugin",
        "1.0.0",
        "QUALITY-001",
        "1.0.0",
        "a" * 64,
        "2026-09-04T00:00:00Z",
    )
    events = (
        PlatformEvent(
            "event:run-performance-001:1", 1, run.run_id,
            "platform.run.started", "start", "started",
            "2026-09-04T00:00:00Z",
        ),
        PlatformEvent(
            "event:run-performance-001:2", 2, run.run_id,
            "inspection.parallel.planned", "instant", mode,
            "2026-09-04T00:00:00Z",
            details={
                "schemaVersion": "1.0.0", "mode": mode, "reason": reason,
                "taskCount": 4, "workerCount": 2 if mode == "parallel" else 1,
                "orderedMerge": True, "failureIsolation": True,
            },
        ),
        PlatformEvent(
            "event:run-performance-001:3", 3, run.run_id,
            "platform.run.terminal", "finish", "completed",
            "2026-09-04T00:00:01Z",
        ),
    )
    operations = (
        Operation(
            "operation:run-performance-001:1", "inspect", "work-item-001",
            "QUALITY-001", "succeeded", started_at="2026-09-04T00:00:00Z",
            ended_at="2026-09-04T00:00:00Z", duration_ms=80,
        ),
    )
    return PlatformLedger(
        run, "completed", operations, events,
        workflow={
            "state": "completed", "phase": "finished", "canFinish": True,
            "requiredNextStep": None,
            "remaining": {
                "workItemsToInspect": 0, "workItemsToDecide": 0,
                "reviewItems": 0, "failures": 0,
            },
        },
        metrics=metrics or {"wallClockMs": 1000},
    )


class PlatformPerformanceBillTest(unittest.TestCase):
    def validator(self):
        schema_root = ROOT / "schemas"
        schema = json.loads(
            (schema_root / "platform-performance-bill.schema.json").read_text(encoding="utf-8")
        )
        common = json.loads((schema_root / "common.schema.json").read_text(encoding="utf-8"))
        return Draft202012Validator(
            schema,
            resolver=RefResolver(
                schema["$id"], schema,
                store={common["$id"]: common, "common.schema.json": common},
            ),
        )

    def test_serial_bill_explains_fallback_and_never_uses_zero_for_missing_telemetry(self):
        bill = build_platform_performance_bill(ledger())
        self.validator().validate(bill)
        self.assertEqual(bill["parallelism"]["mode"], "serial")
        self.assertEqual(bill["parallelism"]["reason"], "plugin_forbidden")
        self.assertEqual(
            bill["parallelism"]["estimatedWaitReduction"]["status"],
            "not_applicable",
        )
        for name in ("agentWait", "transport", "model", "provider"):
            timing = bill["measurement"][name]
            self.assertEqual(timing["status"], "not_exposed")
            self.assertNotIn("durationMs", timing)

    def test_parallel_bill_separates_measured_work_from_estimated_reduction(self):
        value = ledger(
            mode="parallel",
            reason="enabled",
            metrics={
                "wallClockMs": 240,
                "parallelEnabled": 1,
                "parallelWorkers": 2,
                "parallelTasks": 4,
                "parallelWallMs": 100,
                "parallelTaskDurationMs": 180,
                "parallelEstimatedWaitSavedMs": 80,
            },
        )
        json_bytes, markdown_bytes, bill = render_platform_performance_bill(value)
        self.validator().validate(json.loads(json_bytes))
        self.assertEqual(bill["parallelism"]["inspectionWindow"]["status"], "captured")
        self.assertEqual(bill["parallelism"]["summedTaskWork"]["durationMs"], 180)
        self.assertEqual(bill["parallelism"]["estimatedWaitReduction"]["status"], "estimated")
        self.assertIn(b"estimate only; not end-to-end speedup", markdown_bytes)

    def test_provider_timing_is_captured_only_when_the_provider_boundary_exposes_it(self):
        bill = build_platform_performance_bill(ledger(metrics={
            "wallClockMs": 100,
            "providerTimingAvailable": 1,
            "providerRequestCount": 3,
            "providerRequestDurationMs": 75,
        }))
        self.validator().validate(bill)
        self.assertEqual(bill["measurement"]["provider"], {
            "status": "captured",
            "durationMs": 75,
            "aggregation": "summed_work",
            "requestCount": 3,
        })

    def test_bill_is_domain_neutral_and_is_written_beside_platform_trace(self):
        value = ledger()
        bill = build_platform_performance_bill(value)
        encoded = json.dumps(bill, sort_keys=True)
        for frontend_field in ("scanId", "pagesVisited", "entrypoints", "screenshots"):
            self.assertNotIn(frontend_field, encoded)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_platform_artifacts(value, directory)
            names = {path.name for path in paths}
            self.assertIn("platform-performance-bill.json", names)
            self.assertIn("platform-performance-bill.md", names)


if __name__ == "__main__":
    unittest.main()
