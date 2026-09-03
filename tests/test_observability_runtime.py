import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from assayer_host.store import SQLiteStore
from assayer_host.observability import render_observability, render_performance_bill
from assayer_host.reporting import DerivedReportBuilder


class RuntimeObservabilityStoreTest(unittest.TestCase):
    @staticmethod
    def render_fixture(name: str) -> dict[str, bytes]:
        ledger = json.loads((Path("examples") / name).read_text(encoding="utf-8"))
        return DerivedReportBuilder().render(ledger)

    def test_audit_log_is_a_human_readable_run_diary(self):
        ledger = json.loads(Path("examples/minimal-ledger.json").read_text(encoding="utf-8"))
        rendered = DerivedReportBuilder().render(ledger)
        diary = rendered["audit.log"].decode("utf-8")
        self.assertIn("Assayer Run Diary", diary)
        self.assertIn("Status: completed (conclusions valid: yes)", diary)
        self.assertIn("Timeline", diary)
        self.assertIn("Started a bounded evidence-gathering Case.", diary)
        self.assertIn("Coverage", diary)
        self.assertIn("Entrypoints: 0 processed, 0 skipped, 0 remaining", diary)
        self.assertIn("Terminal state: completed", diary)
        self.assertNotIn("succeeded operation-", diary.lower())

    def test_audit_log_explains_failed_operation(self):
        ledger = json.loads(Path("examples/minimal-ledger.json").read_text(encoding="utf-8"))
        operation = ledger["operations"][0]
        operation["status"] = "rejected"
        operation["reason"] = {
            "code": "STALE_STATE",
            "message": "The page changed before the operation could run; token=private-value",
        }
        operation["decisionReason"] = "Inspect after authorization=private-value"
        diary = DerivedReportBuilder().render(ledger)["audit.log"].decode("utf-8")
        self.assertIn("| FAILED", diary)
        self.assertIn(
            "Blocked or failed: STALE_STATE — The page changed before the operation could run; token=[REDACTED]",
            diary,
        )
        self.assertIn("Goal: Inspect after authorization=[REDACTED]", diary)
        self.assertNotIn("private-value", diary)

    def test_completed_result_explains_that_no_action_is_required(self):
        summary = self.render_fixture("minimal-ledger.json")["audit-summary.md"].decode("utf-8")
        self.assertIn("# Assayer Audit Result", summary)
        self.assertIn("Status: **completed**", summary)
        self.assertIn("Formal conclusions: **valid**", summary)
        self.assertIn("Next step: No remediation is required", summary)
        self.assertIn("No unfinished or explicitly skipped scope remains.", summary)

    def test_issue_result_leads_with_remediation(self):
        summary = self.render_fixture("issue-ledger.json")["audit-summary.md"].decode("utf-8")
        self.assertIn("Next step: Address the published issues", summary)
        self.assertIn("Filter region lacks reset capability", summary)
        self.assertIn("Recommendation: Add a reset entrypoint.", summary)

    def test_partial_result_names_remaining_scope_and_reason(self):
        rendered = self.render_fixture("partial-ledger.json")
        summary = rendered["audit-summary.md"].decode("utf-8")
        diagnostics = rendered["run-diagnostics.md"].decode("utf-8")
        self.assertIn("Status: **partial**", summary)
        self.assertIn("Entrypoints remaining: 1", summary)
        self.assertIn("Remaining: Enter filter interaction — Interaction capability is unavailable.", summary)
        self.assertIn("Incomplete rule: FUA-10@1.1.0", summary)
        self.assertIn("No Host operation failed; the Run is partial", diagnostics)

    def test_failed_result_invalidates_conclusions_and_attributes_terminal_reason(self):
        rendered = self.render_fixture("failed-ledger.json")
        summary = rendered["audit-summary.md"].decode("utf-8")
        diagnostics = json.loads(rendered["run-diagnostics.json"])
        self.assertIn("Status: **failed**", summary)
        self.assertIn("Formal conclusions: **invalid**", summary)
        self.assertIn("do not use conclusions from this Run", summary)
        self.assertEqual(diagnostics["attributions"][0]["layer"], "browser_adapter")
        self.assertIn("REQUEST_RESULT_UNKNOWN", diagnostics["attributions"][0]["reason"])

    def test_needs_review_result_names_gap_and_checks_completed(self):
        ledger = json.loads(Path("examples/minimal-ledger.json").read_text(encoding="utf-8"))
        assessment = ledger["assessments"][0]
        assessment.update({
            "result": "needs_review",
            "coverage": {
                **assessment["coverage"], "resolvedDimensions": ["filter_present"],
                "unresolvedDimensions": ["binding_to_list"], "complete": False,
            },
            "blocker": {
                "code": "BINDING_UNRESOLVED",
                "message": "Visual and DOM evidence cannot identify which list belongs to this filter.",
            },
        })
        ledger["scan"]["status"] = "partial"
        ledger["scan"]["terminalReason"] = {
            "code": "COVERAGE_PARTIAL", "message": "One object requires review.",
        }
        rule_summary = ledger["scan"]["coverageProof"]["ruleSummaries"][0]
        rule_summary.update({
            "resultCounts": {"needs_review": 1}, "coverageComplete": False,
        })
        summary = DerivedReportBuilder().render(ledger)["audit-summary.md"].decode("utf-8")
        self.assertIn("Needs review: 1", summary)
        self.assertIn("Missing fact or blocker: Visual and DOM evidence cannot identify", summary)
        self.assertIn("Unresolved dimensions: binding_to_list", summary)
        self.assertIn("Checks completed: 1 evidence item(s) across 1 restored Case(s)", summary)

    def test_performance_bill_measures_duplicate_entrypoints_and_unique_screenshot_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            screenshot_dir = Path(tmp) / "screenshots"
            screenshot_dir.mkdir()
            image = b"fake-image-bytes"
            (screenshot_dir / "raw.png").write_bytes(image)
            (screenshot_dir / "issue.png").write_bytes(image)
            digest = hashlib.sha256(image).hexdigest()
            ledger = {
                "scan": {
                    "scanId": "scan-bill-001", "runId": "run-bill-001", "status": "completed",
                    "startedAt": "2026-09-02T00:00:00Z", "endedAt": "2026-09-02T00:00:10Z",
                },
                "operations": [
                    {"operationId": "operation-bill-001", "tool": "inspect_page", "operationKind": "read", "status": "succeeded", "durationMs": 0, "agentTurnId": "facade-test-turn-0001:discover_scope", "acceptedAt": "2026-09-02T00:00:00Z", "endedAt": "2026-09-02T00:00:00Z"},
                    {"operationId": "operation-bill-001b", "tool": "inspect_page", "operationKind": "read", "status": "succeeded", "durationMs": 25, "agentTurnId": "facade-test-turn-0001:discover_scope", "acceptedAt": "2026-09-02T00:00:00.075Z", "endedAt": "2026-09-02T00:00:00.100Z"},
                    {"operationId": "operation-bill-002", "tool": "explore_entrypoint", "operationKind": "browser_action", "status": "succeeded", "durationMs": 200, "agentTurnId": "facade-test-turn-0002:investigate_object", "acceptedAt": "2026-09-02T00:00:00.350Z", "endedAt": "2026-09-02T00:00:00.550Z"},
                ],
                "pageStates": [{"pageStateId": "page-001"}],
                "entrypoints": [
                    {"entrypointId": "entrypoint-001", "identity": {"materialDigest": "a" * 64}},
                    {"entrypointId": "entrypoint-002", "identity": {"materialDigest": "a" * 64}},
                    {"entrypointId": "entrypoint-003", "identity": {"materialDigest": "b" * 64}},
                ],
                "objects": [{"objectId": "object-001", "rebindStatus": "matched"}],
                "assessments": [{"assessmentId": "assessment-001"}],
                "screenshots": [
                    {"screenshotId": "screenshot-raw", "status": "captured", "digest": digest, "path": "screenshots/raw.png"},
                    {"screenshotId": "screenshot-issue", "status": "captured", "digest": digest, "path": "screenshots/issue.png"},
                ],
            }
            events = [
                {"name": "transport.request.started", "phase": "start", "attributes": {"requestBytes": 50}},
                {"name": "transport.request.finished", "phase": "finish", "durationMs": 210, "attributes": {"responseBytes": 75}},
                {"name": "browser.operation.finished", "source": "browser", "phase": "finish", "durationMs": 200, "attributes": {}},
            ]
            json_bytes, markdown_bytes, bill = render_performance_bill(ledger, events, tmp)
            self.assertEqual(bill["measurement"]["totalDurationMs"], 10000)
            self.assertEqual(bill["measurement"]["hostOperationDurationMs"], 225)
            self.assertEqual(bill["measurement"]["outsideHostDurationMs"], 9775)
            self.assertEqual(bill["activity"]["agentTurnsObserved"], 2)
            self.assertEqual(bill["activity"]["agentTurnGapCount"], 1)
            self.assertEqual(bill["activity"]["publicToolCallsObserved"], 2)
            self.assertEqual(bill["activity"]["publicToolCallsByName"], {"discover_scope": 1, "investigate_object": 1})
            self.assertEqual(bill["measurement"]["agentTurnGapDurationMs"], 250)
            self.assertEqual(bill["measurement"]["longestAgentTurnGapMs"], 250)
            self.assertEqual(bill["largestAgentTurnGaps"][0]["fromTool"], "discover_scope")
            self.assertEqual(bill["largestAgentTurnGaps"][0]["toTool"], "investigate_object")
            self.assertEqual(bill["activity"]["toolCalls"], 3)
            self.assertEqual(bill["activity"]["duplicateEntrypoints"], 1)
            self.assertEqual(bill["activity"]["duplicateEntrypointRatio"], 0.3333)
            self.assertEqual(bill["activity"]["screenshotsCaptured"], 2)
            self.assertEqual(bill["activity"]["uniqueScreenshotDigests"], 1)
            self.assertEqual(bill["activity"]["screenshotBytes"], len(image))
            self.assertEqual(json.loads(json_bytes)["activity"]["responseBytes"], 75)
            self.assertIn(b"Model latency: not exposed", markdown_bytes)
            self.assertIn(b"Between-Agent-turn time: 250 ms", markdown_bytes)

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
