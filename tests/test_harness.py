import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from assayer_host import CredentialVault, DeterministicLoginAdapter, DeterministicPageAdapter, HostCore, LoginSecret, run_deterministic_harness


EXPECTED_ARTIFACTS = {
    "audit-ledger.json", "issues.json", "page-element-judgement.json",
    "run-diagnostics.json", "audit-summary.md", "run-diagnostics.md", "audit.log",
    "runtime-events.jsonl", "observability-manifest.json",
    "performance-bill.json", "performance-bill.md",
}


class DeterministicHarnessTest(unittest.TestCase):
    def test_no_issue_flow_runs_every_lifecycle_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_deterministic_harness(tmp)
            self.assertEqual(summary["mode"], "deterministic")
            self.assertEqual(summary["completion"]["scanStatus"], "completed")
            self.assertTrue(summary["completion"]["conclusionsValid"])
            self.assertEqual(set(summary["artifacts"]), EXPECTED_ARTIFACTS)
            self.assertEqual(summary["responses"], {"action": "ok", "restore": "ok", "commit": "ok"})
            self.assertEqual(len(summary["steps"]), 11)
            ledger = json.loads((Path(tmp) / "audit-ledger.json").read_text())
            self.assertEqual(ledger["scan"]["status"], "completed")
            self.assertEqual(len(ledger["operations"]), 11)
            self.assertTrue(all(item["endedAt"] >= item["acceptedAt"] and item["durationMs"] >= 0 for item in ledger["operations"]))
            events = [json.loads(line) for line in (Path(tmp) / "runtime-events.jsonl").read_text().splitlines()]
            self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
            manifest = json.loads((Path(tmp) / "observability-manifest.json").read_text())
            self.assertEqual(manifest["diagnosticCompleteness"], "limited")
            checks = {item["name"]: item["status"] for item in manifest["coreCompleteness"]["checks"]}
            self.assertEqual(checks["assessment_timeline_closed"], "passed")
            assessment_event = next(item for item in events if item["name"] == "assessment.committed")
            self.assertEqual(assessment_event["correlation"]["assessmentRef"], ledger["assessments"][0]["assessmentId"])
            self.assertEqual(set(assessment_event["correlation"]["findingRefs"]), set(ledger["assessments"][0]["findingRefs"]))
            event_lines = (Path(tmp) / "runtime-events.jsonl").read_bytes()
            self.assertEqual(manifest["eventStream"]["digest"], hashlib.sha256(event_lines).hexdigest())
            operation_ids = {item["operationId"] for item in ledger["operations"]}
            self.assertEqual({item["correlation"]["operationId"] for item in events if item["name"] == "operation.started"}, operation_ids)
            self.assertEqual({item["correlation"]["operationId"] for item in events if item["name"] == "operation.finished"}, operation_ids)
            forbidden = (Path(tmp) / "runtime-events.jsonl").read_text() + (Path(tmp) / "observability-manifest.json").read_text()
            for token in ("password", "cookie", "authorization", "hiddenReasoning", "chainOfThought", "requestBody", "responseBody", "screenshotBase64", "prompt"):
                self.assertNotIn(token, forbidden)
            self.assertEqual(ledger["assessments"][0]["result"], "scanned_no_issue")
            self.assertEqual(json.loads((Path(tmp) / "issues.json").read_text())["issues"], [])
            self.assertFalse(any(path.suffix == ".html" for path in Path(tmp).rglob("*")))
            text_artifacts = "\n".join(
                path.read_text() for path in Path(tmp).iterdir()
                if path.suffix in {".json", ".md", ".log"}
            )
            self.assertNotIn("deterministic-secret", text_artifacts)
            self.assertNotIn("harness-user", text_artifacts)
            self.assertNotIn(str(Path(tmp).resolve()), text_artifacts)

    def test_issue_flow_produces_traceable_visual_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_deterministic_harness(tmp, result="issue_found")
            ledger = json.loads((Path(tmp) / "audit-ledger.json").read_text())
            report = json.loads((Path(tmp) / "issues.json").read_text())
            self.assertEqual(summary["completion"]["scanStatus"], "completed")
            self.assertEqual(len(ledger["issues"]), 1)
            self.assertEqual(report["issues"][0]["issueId"], ledger["issues"][0]["issueId"])
            self.assertEqual(len(list((Path(tmp) / "screenshots").glob("*.png"))), 2)
            self.assertNotIn("Reset", ledger["pageStates"][0]["safeEntrypoints"][0]["label"])
            self.assertNotIn("Reset", ledger["objects"][0]["identity"]["visibleText"])

    def test_module_cli_emits_machine_readable_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, "-m", "assayer_host", "--output-dir", tmp],
                check=True, capture_output=True, text=True,
            )
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["mode"], "deterministic")
            self.assertEqual(summary["completion"]["runRevision"], 9)

    def test_default_host_still_fails_closed_without_browser_adapters(self):
        core = HostCore()
        try:
            self.assertEqual(type(core._login_adapter).__name__, "UnavailableLoginAdapter")
            self.assertEqual(type(core._page_adapter).__name__, "UnavailablePageAdapter")
            self.assertEqual(type(core._object_identity_adapter).__name__, "UnavailableObjectIdentityAdapter")
            self.assertEqual(type(core._action_adapter).__name__, "UnavailableActionAdapter")
            self.assertEqual(type(core._recovery_adapter).__name__, "UnavailableRecoveryAdapter")
        finally:
            core.close()

    def test_injecting_only_login_cannot_activate_fixture_page(self):
        vault = CredentialVault()
        vault.put("credential-only", LoginSecret("test-user", "secret"))
        core = HostCore(credential_vault=vault, login_adapter=DeterministicLoginAdapter())
        try:
            started = core.handle({
                "protocolVersion": "1.0", "requestId": "default-page-start", "agentTurnId": "turn-001",
                "tool": "start_audit", "idempotencyKey": "default-page-start",
                "input": {"url": "https://test.example.com", "ruleRegistryVersion": "1.0.0",
                          "outputDir": "/tmp/assayer-unavailable-page", "browserProfile": "default",
                          "credentialHandle": "credential-only"},
            })["result"]
            response = core.handle({
                "protocolVersion": "1.0", "requestId": "default-page-read", "scanId": started["scanId"],
                "runId": started["runId"], "agentTurnId": "turn-002", "tool": "inspect_page",
                "idempotencyKey": "default-page-read", "expectedRunRevision": 1,
                "input": {"pageStateId": started["currentPageStateId"], "include": ["objects"]},
            })
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["error"]["code"], "INTERNAL_FAILURE")
            self.assertEqual(response["error"]["message"], "Read-only page adapter is not configured")
        finally:
            core.close()

    def test_injecting_page_without_identity_cannot_create_fixture_object(self):
        vault = CredentialVault()
        vault.put("credential-page", LoginSecret("test-user", "secret"))
        core = HostCore(credential_vault=vault, login_adapter=DeterministicLoginAdapter(),
                        page_adapter=DeterministicPageAdapter())
        try:
            started = core.handle({
                "protocolVersion": "1.0", "requestId": "default-identity-start", "agentTurnId": "turn-001",
                "tool": "start_audit", "idempotencyKey": "default-identity-start",
                "input": {"url": "https://test.example.com", "ruleRegistryVersion": "1.0.0",
                          "outputDir": "/tmp/assayer-unavailable-identity", "browserProfile": "default",
                          "credentialHandle": "credential-page"},
            })["result"]
            page = core.handle({
                "protocolVersion": "1.0", "requestId": "default-identity-page", "scanId": started["scanId"],
                "runId": started["runId"], "agentTurnId": "turn-002", "tool": "inspect_page",
                "idempotencyKey": "default-identity-page", "expectedRunRevision": 1,
                "input": {"pageStateId": started["currentPageStateId"], "include": ["objects"]},
            })["result"]
            response = core.handle({
                "protocolVersion": "1.0", "requestId": "default-identity-object", "scanId": started["scanId"],
                "runId": started["runId"], "agentTurnId": "turn-003", "tool": "inspect_object",
                "idempotencyKey": "default-identity-object", "expectedRunRevision": 1,
                "input": {"candidateId": page["candidateRefs"][0]},
            })
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["error"]["message"], "Object identity adapter is not configured")
        finally:
            core.close()


if __name__ == "__main__":
    unittest.main()
