import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    CommitReceipt,
    PlatformContext,
    PlatformContractError,
    PlatformEvent,
    PlatformKernel,
    InteractivePlatformSession,
    JsonPlatformLedgerStore,
    inspect_result_conformance,
)
from assayer_platform.builtin_plugins.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)


ROOT = Path(__file__).resolve().parents[1]


class ResultConformanceTest(unittest.TestCase):
    def run_result(self, directory):
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
        return PlatformKernel().run(
            ConfigQualityPlugin(), str(path), "CFG-001",
            ConfigurationDecisionProvider(),
            PlatformContext("run-result-conformance", frozenset({"structured_read"})),
        )

    def validator(self):
        schemas = {}
        for path in (ROOT / "schemas").glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
        schema = schemas["result-conformance.schema.json"]
        return Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        )

    def test_valid_terminal_result_passes_the_shared_schema_backed_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            report = inspect_result_conformance(self.run_result(directory))
        self.assertTrue(report.passed, report.as_dict())
        self.validator().validate(report.as_dict())

    def test_missing_result_receipt_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_result(directory)
            corrupted = replace(result, receipts=())
            report = inspect_result_conformance(corrupted)
            with self.assertRaises(PlatformContractError) as rejected:
                PlatformKernel().publish(corrupted, object())
        self.assertFalse(report.passed)
        self.assertIn("RESULT_RECEIPT_MISMATCH", {item.code for item in report.issues})
        self.assertEqual(rejected.exception.code, "PUBLICATION_CONFORMANCE_FAILED")

    def test_latest_uncertain_recovery_invalidates_an_otherwise_committed_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_result(directory)
        ledger = result.ledger
        terminal = ledger.events[-1]
        recovery = PlatformEvent(
            f"event:{result.run_id}:{terminal.sequence}", terminal.sequence,
            result.run_id, "recovery.finished", "finish", "uncertain",
            terminal.occurred_at, work_item_id=result.decisions[0].work_item_id,
            check_id=result.decisions[0].check_id,
        )
        shifted_terminal = replace(
            terminal,
            event_id=f"event:{result.run_id}:{terminal.sequence + 1}",
            sequence=terminal.sequence + 1,
        )
        corrupted_ledger = replace(
            ledger, events=ledger.events[:-1] + (recovery, shifted_terminal),
        )
        report = inspect_result_conformance(replace(result, ledger=corrupted_ledger))
        self.assertIn("RESULT_RECOVERY_UNRESOLVED", {item.code for item in report.issues})

    def test_interactive_commit_is_blocked_until_latest_recovery_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            plugin = ConfigQualityPlugin()
            context = PlatformContext(
                "run-interactive-recovery", frozenset({"structured_read"}),
            )
            run = InteractivePlatformSession(plugin.manifest).begin(
                context, str(path), "CFG-001", "1.0.0",
                JsonPlatformLedgerStore(Path(directory) / "ledger"),
            )
            item = plugin.discover(str(path), context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            proposal = ConfigurationDecisionProvider().decide(
                (packet,), plugin.manifest.checks[0], context,
            )[0]
            receipt = CommitReceipt(
                "commit-interactive-recovery", item.work_item_id,
                proposal.check_id, proposal.check_version,
                proposal.result, "durable",
            )
            run.record_discovery((item,))
            run.record_investigation(packet)
            run.record_recovery(item.work_item_id, "uncertain")
            with self.assertRaises(PlatformContractError) as blocked:
                run.record_commit(proposal, receipt)
            run.record_recovery(item.work_item_id, "restored")
            run.record_commit(proposal, receipt)
        self.assertEqual(blocked.exception.code, "RECOVERY_INVALID")

    def test_failed_result_suppresses_formal_outcomes_but_retains_ledger_history(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_result(directory)
        terminal = replace(result.ledger.events[-1], outcome="failed")
        failed_ledger = replace(result.ledger, status="failed", events=result.ledger.events[:-1] + (terminal,))
        failed = replace(
            result, status="failed", decisions=(), receipts=(), ledger=failed_ledger,
        )
        report = inspect_result_conformance(failed)
        self.assertTrue(report.passed, report.as_dict())
        self.assertTrue(failed.ledger.decisions)
        self.assertFalse(failed.decisions)


if __name__ == "__main__":
    unittest.main()
