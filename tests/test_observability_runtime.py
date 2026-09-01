import tempfile
import time
import unittest
from pathlib import Path

from assayer_host.store import SQLiteStore
from assayer_host.observability import render_observability
from assayer_host.reporting import DerivedReportBuilder


class RuntimeObservabilityStoreTest(unittest.TestCase):
    def test_operation_times_are_real_and_terminal_time_is_idempotent(self):
        store = SQLiteStore()
        scan = {
            "scanId": "scan-runtime-001", "runId": "run-runtime-001", "runRevision": 0,
            "status": "exploring", "loginStatus": "succeeded", "createdAt": "2026-08-31T00:00:00Z",
            "ruleRegistryDigest": "a" * 64, "capabilitiesJson": "[]", "outputDir": "", "entryUrl": "https://example.com/",
        }
        op = {
            "operationId": "operation-runtime-001", "scanId": scan["scanId"], "requestId": "request-runtime-001",
            "tool": "inspect_page", "operationKind": "read", "idempotencyKey": "runtime-1", "requestDigest": "b" * 64,
            "status": "running", "acceptedAtRevision": 0,
        }
        with store.transaction():
            store.insert_scan(scan)
            store.insert_operation(op)
        first = store.get_operation(op["operationId"])
        self.assertIsNotNone(first["accepted_at"])
        self.assertIsNone(first["ended_at"])
        with store.transaction():
            store.update_operation({**op, "status": "succeeded"})
        finished = store.get_operation(op["operationId"])
        self.assertIsNotNone(finished["ended_at"])
        self.assertGreaterEqual(finished["duration_ms"], 0)
        ended_at = finished["ended_at"]
        time.sleep(0.002)
        with store.transaction():
            store.update_operation({**op, "status": "succeeded"})
        self.assertEqual(store.get_operation(op["operationId"])["ended_at"], ended_at)
        events = store.list_runtime_events(scan["scanId"])
        self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
        self.assertEqual([item["name"] for item in events], ["scan.started", "operation.started", "operation.finished"])
        store.close()

    def test_legacy_operations_table_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE scans (scan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, run_revision INTEGER NOT NULL, status TEXT NOT NULL, login_status TEXT NOT NULL, created_at TEXT NOT NULL, rule_registry_digest TEXT NOT NULL, current_page_state_id TEXT, capabilities_json TEXT NOT NULL, output_dir TEXT NOT NULL, entry_url TEXT NOT NULL DEFAULT '')")
            conn.execute("CREATE TABLE operations (operation_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, request_id TEXT NOT NULL, tool TEXT NOT NULL, operation_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL, status TEXT NOT NULL, accepted_at_revision INTEGER NOT NULL, error_code TEXT, error_message TEXT, result_json TEXT, case_ref TEXT)")
            conn.commit(); conn.close()
            store = SQLiteStore(path)
            columns = {row[1] for row in store._conn.execute("PRAGMA table_info(operations)")}
            self.assertTrue({"accepted_at", "ended_at", "accepted_monotonic_ns", "duration_ms"}.issubset(columns))
            store.close()

    def test_public_decision_is_correlated_and_secret_patterns_are_redacted(self):
        store = SQLiteStore()
        scan = {"scanId": "scan-decision-001", "runId": "run-decision-001", "runRevision": 0,
                "status": "exploring", "loginStatus": "succeeded", "createdAt": "2026-08-31T00:00:00Z",
                "ruleRegistryDigest": "a" * 64, "capabilitiesJson": "[]", "outputDir": "", "entryUrl": "https://example.com/"}
        op = {"operationId": "operation-decision-001", "scanId": scan["scanId"], "requestId": "request-decision-001",
              "agentTurnId": "turn-decision-001", "decisionReason": "Inspect page after token=private-value",
              "modelTelemetry": {"durationMs": 12, "retryCount": 1}, "tool": "inspect_page", "operationKind": "read",
              "idempotencyKey": "decision-1", "requestDigest": "b" * 64, "status": "running", "acceptedAtRevision": 0}
        with store.transaction():
            store.insert_scan(scan); store.insert_operation(op)
        events = store.list_runtime_events(scan["scanId"])
        decision = next(item for item in events if item["name"] == "agent.decision.recorded")
        model = next(item for item in events if item["name"] == "model.call.finished")
        self.assertEqual(decision["correlation"]["operationId"], op["operationId"])
        self.assertNotIn("private-value", decision["summary"])
        self.assertEqual(model["durationMs"], 12)
        self.assertEqual(model["attributes"]["retryCount"], 1)
        store.close()

    def test_manifest_privacy_gate_fails_on_unredacted_sensitive_value(self):
        events = [{"eventId": "event-privacy-001", "scanId": "scan-privacy-001", "runId": "run-privacy-001",
                   "sequence": 1, "occurredAt": "2026-08-31T00:00:00Z", "monotonicOffsetMs": 0,
                   "source": "host", "category": "lifecycle", "name": "scan.started", "phase": "start",
                   "severity": "info", "outcome": "started", "summary": "token=leaked-value",
                   "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": {}}]
        _, _, manifest = render_observability({"scanId": "scan-privacy-001", "runId": "run-privacy-001"}, events, [], [])
        self.assertEqual(manifest["privacy"]["status"], "failed")
        checks = {item["name"]: item["status"] for item in manifest["coreCompleteness"]["checks"]}
        self.assertEqual(checks["privacy_gate_passed"], "failed")

    def test_fault_codes_map_to_actionable_attribution_layers(self):
        codes = {
            "BROWSER_SESSION_FAILED": "environment", "ACTION_ADAPTER_UNAVAILABLE": "browser_adapter",
            "STALE_STATE": "host_contract", "DECISION_PENDING": "agent_strategy",
            "AGENT_CONTROL_BUDGET_EXCEEDED": "agent_strategy",
            "RULE_CONTRACT_UNAVAILABLE": "rule_contract", "AGENT_LEASE_EXPIRED": "transport_runtime",
            "PERSISTENT_WRITE_OBSERVED": "target_application", "UNKNOWN_FAILURE": "unattributed",
        }
        failed = [{"operationId": f"operation-{index:03d}", "tool": "test", "status": "failed_known",
                   "reason": {"code": code, "message": code}} for index, code in enumerate(codes, 1)]
        attributed = DerivedReportBuilder._attributions(failed)
        self.assertEqual([item["layer"] for item in attributed], list(codes.values()))
        self.assertTrue(all(item["recommendation"] and item["operationRefs"] for item in attributed))


if __name__ == "__main__":
    unittest.main()
