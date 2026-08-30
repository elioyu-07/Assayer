import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_f_host import HostCore, run_deterministic_harness


EXPECTED_ARTIFACTS = {
    "audit-ledger.json", "issues.json", "page-element-judgement.json",
    "run-diagnostics.json", "audit-summary.md", "run-diagnostics.md", "audit.log",
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
            self.assertEqual(len(summary["steps"]), 10)
            ledger = json.loads((Path(tmp) / "audit-ledger.json").read_text())
            self.assertEqual(ledger["scan"]["status"], "completed")
            self.assertEqual(len(ledger["operations"]), 10)
            self.assertEqual(ledger["assessments"][0]["result"], "scanned_no_issue")
            self.assertEqual(json.loads((Path(tmp) / "issues.json").read_text())["issues"], [])
            self.assertFalse(any(path.suffix == ".html" for path in Path(tmp).rglob("*")))
            text_artifacts = "\n".join(
                path.read_text() for path in Path(tmp).iterdir()
                if path.suffix in {".json", ".md", ".log"}
            )
            self.assertNotIn("deterministic-secret", text_artifacts)
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
            self.assertNotIn("重置", ledger["pageStates"][0]["safeEntrypoints"][0]["label"])
            self.assertNotIn("重置", ledger["objects"][0]["identity"]["visibleText"])

    def test_module_cli_emits_machine_readable_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, "-m", "agent_f_host", "--output-dir", tmp],
                check=True, capture_output=True, text=True,
            )
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["mode"], "deterministic")
            self.assertEqual(summary["completion"]["runRevision"], 8)

    def test_default_host_still_fails_closed_without_browser_adapters(self):
        core = HostCore()
        try:
            self.assertEqual(type(core._login_adapter).__name__, "UnavailableLoginAdapter")
            self.assertEqual(type(core._action_adapter).__name__, "UnavailableActionAdapter")
            self.assertEqual(type(core._recovery_adapter).__name__, "UnavailableRecoveryAdapter")
        finally:
            core.close()


if __name__ == "__main__":
    unittest.main()
