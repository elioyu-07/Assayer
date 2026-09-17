from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from assayer_platform.compiled_interactive import CompiledInteractiveController
from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_platform.provider_registry import ProviderRegistry
from assayer_platform.incremental_review import JsonCoverageLedgerStore
from assayer_plugin_sdk.contract import PlatformContractError
from assayer_plugin_sdk import BrowserSnapshot
from assayer_document_navigation import markdown_registration


class _SnapshotSource:
    def observe_snapshot(self):
        return BrowserSnapshot(
            visible_text="Orders\nFilter\nQuery\nReset",
            entrypoints=({"kind": "safe_action", "intent": "query"},),
            candidates=({"kind": "filter_region", "label": "Order filters"},),
            route="/orders",
            state_kind="page",
            structure_summary={"fields": 1, "tables": 1},
            dom_digest="d" * 64,
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
        )


class CompiledInteractiveTests(unittest.TestCase):
    def _markdown_run(self, text="# Overview\n\nFirst.\n\nSecond.\n"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        document = root / "input.md"
        document.write_text(text, encoding="utf-8")
        compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), root / "compiled")
        controller = CompiledInteractiveController(
            load_compiled_plugin_contract(root / "compiled"), root / "runs",
        )
        run_id = controller.start(
            plugin_id="test.policy-pack", check_id="POLICY-001",
            scope={"files": [str(document)]},
        )["runId"]
        controller.bind_provider(run_id, provider_registry=ProviderRegistry([markdown_registration()]))
        self.addCleanup(controller.close, run_id)
        controller.plan_review_batches(run_id, max_batch_items=1)
        return controller, run_id

    def _decisions(self, controller, run_id, batch):
        return [{
            "atomId": atom.atom_id, "state": "satisfied",
            "rationale": "The supplied frozen element supports this decision.",
            "evidenceRefs": list(atom.payload["evidenceRefs"]),
        } for atom in controller._run(run_id).coverage.atoms_for(batch.batch_id)]

    def test_compiled_manifest_fails_closed_when_a_capability_is_missing(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), root / "compiled")
        controller = CompiledInteractiveController(
            load_compiled_plugin_contract(root / "compiled"), root / "runs",
        )
        check = controller.manifest.checks[0]
        self.assertEqual(check.capability_missing_outcome, "blocked")
        self.assertIn("blocked", check.decision_states)
        self.assertNotIn("needs_review", check.decision_states)

    def test_evidence_binding_rejection_leaves_memory_and_disk_unchanged(self):
        controller, run_id = self._markdown_run()
        state = controller._run(run_id)
        batch = state.coverage.batches[0]
        refs = self._decisions(controller, run_id, batch)[0]["evidenceRefs"]
        record = state.provider.issued_evidence()[refs[0]]
        store = JsonCoverageLedgerStore(controller.output_root / "coverage")
        before = store.load(run_id).canonical_bytes()
        # Simulate corrupted Provider registry records at the trust boundary;
        # real collection and the real acceptance path remain in use.
        for field, value in (
            ("run_id", "run-foreign"), ("work_item_id", "work-foreign"),
            ("source_state_digest", "sha256:" + "0" * 64),
            ("check_id", "OTHER-001"), ("check_version", "99.0.0"),
        ):
            with self.subTest(field=field):
                state.provider._issued_evidence[record.evidence_id] = replace(record, **{field: value})
                with self.assertRaises(PlatformContractError) as rejected:
                    controller.submit_review_batch(run_id, batch.batch_id, self._decisions(controller, run_id, batch))
                self.assertEqual(rejected.exception.code, "INVALID_REVIEW_DECISION")
                self.assertEqual(controller._run(run_id).coverage.canonical_bytes(), before)
                self.assertEqual(store.load(run_id).canonical_bytes(), before)
        state.provider._issued_evidence[record.evidence_id] = record
        self.assertEqual(controller.submit_review_batch(
            run_id, batch.batch_id, self._decisions(controller, run_id, batch),
        )["status"], "accepted")

    def test_foreign_run_evidence_is_rejected(self):
        controller, run_id = self._markdown_run()
        other, other_id = self._markdown_run()
        batch = controller._run(run_id).coverage.batches[0]
        decisions = self._decisions(controller, run_id, batch)
        decisions[0]["evidenceRefs"] = list(other._run(other_id).provider.issued_evidence())
        with self.assertRaises(PlatformContractError) as rejected:
            controller.submit_review_batch(run_id, batch.batch_id, decisions)
        self.assertEqual(rejected.exception.code, "INVALID_REVIEW_DECISION")

    def test_missing_and_duplicate_decisions_do_not_mutate_coverage(self):
        controller, run_id = self._markdown_run()
        ledger = controller._run(run_id).coverage
        batch = ledger.batches[0]
        decisions = self._decisions(controller, run_id, batch)
        for invalid in ([], decisions + decisions):
            with self.subTest(decisions=invalid):
                with self.assertRaises(PlatformContractError):
                    controller.submit_review_batch(run_id, batch.batch_id, invalid)
                self.assertEqual(controller._run(run_id).coverage, ledger)
                self.assertEqual(JsonCoverageLedgerStore(controller.output_root / "coverage").load(run_id), ledger)
        with self.assertRaises(PlatformContractError) as rejected:
            controller.finalize(run_id)
        self.assertEqual(rejected.exception.code, "REVIEW_NOT_CLOSED")

    def test_review_plan_cannot_be_replaced_after_planning(self):
        controller, run_id = self._markdown_run()
        before = controller._run(run_id).coverage.canonical_bytes()
        for method in (controller.discover, controller.collect, controller.plan_review_batches):
            with self.subTest(method=method.__name__):
                with self.assertRaises(PlatformContractError):
                    method(run_id)
                self.assertEqual(controller._run(run_id).coverage.canonical_bytes(), before)

    def test_terminal_result_survives_restart_with_all_five_states_and_bindings(self):
        controller, run_id = self._markdown_run("# Overview\n\nA.\n\nB.\n\nC.\n\nD.\n")
        ledger = controller._run(run_id).coverage
        states = ("satisfied", "violated", "unknown", "blocked", "not_applicable")
        self.assertEqual(len(ledger.atoms), 5)
        for batch, status in zip(ledger.batches, states):
            decisions = self._decisions(controller, run_id, batch)
            decisions[0].update(state=status, missingInformation="Missing context", blockedReason="Source access", applicabilityBasis="Outside this requirement")
            controller.submit_review_batch(run_id, batch.batch_id, decisions)
        result = controller.finalize(run_id)
        self.assertEqual(result["coverage"]["terminalStatus"], "completed")
        self.assertEqual([v["decisions"][0]["state"] for v in result["coverage"]["verdicts"]], list(states))
        self.assertEqual(len(result["coverage"]["entries"]), 5)
        self.assertTrue(all(e["status"] == "accepted" for e in result["coverage"]["entries"]))
        self.assertTrue(result["trace"]["coverageDigest"].startswith("sha256:"))
        bindings = [b for c in result["coverage"]["contexts"] for b in c["value"]["evidenceBindings"]]
        self.assertTrue(bindings)
        self.assertTrue(all(b["runId"] == run_id and b["sourceStateDigest"] for b in bindings))
        restarted = CompiledInteractiveController(controller.contract, controller.output_root)
        self.assertEqual(restarted.get_result(run_id), result)
        self.assertEqual(controller.finalize(run_id), result)
        with self.assertRaises(PlatformContractError):
            controller.submit_review_batch(run_id, ledger.batches[0].batch_id, self._decisions(controller, run_id, ledger.batches[0]))

    def test_restart_cannot_overwrite_existing_run_identity(self):
        controller, run_id = self._markdown_run()
        restarted = CompiledInteractiveController(controller.contract, controller.output_root)
        before = controller._run(run_id).coverage.canonical_bytes()
        with self.assertRaises(PlatformContractError) as rejected:
            restarted.start(plugin_id="test.policy-pack", check_id="POLICY-001",
                            scope=controller._run(run_id).scope, run_id=run_id)
        self.assertEqual(rejected.exception.code, "RUN_CONFLICT")
        self.assertEqual(JsonCoverageLedgerStore(controller.output_root / "coverage").load(run_id).canonical_bytes(), before)

    def test_result_is_unavailable_until_review_is_terminal(self):
        controller, run_id = self._markdown_run()
        for candidate in (run_id, "run-missing"):
            with self.subTest(run_id=candidate):
                with self.assertRaises(PlatformContractError) as rejected:
                    controller.get_result(candidate)
                self.assertEqual(rejected.exception.code, "RESULT_NOT_AVAILABLE")

    def test_start_consumes_contract_without_registration_or_plugin_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            contract = load_compiled_plugin_contract(root)
            controller = CompiledInteractiveController(contract, root / "runs")
            started = controller.start(
                plugin_id="assayer.frontend-audit",
                check_id="FUA-01",
                scope={"url": "https://example.test/orders"},
                capabilities=("browser_snapshot",),
            )

        self.assertEqual(started["runtime"], "compiled_plugin_contract")
        self.assertEqual(started["plugin"]["contractDigest"], contract.digest)
        self.assertEqual(started["check"]["version"], "1.0.0")

    def test_contract_scope_is_enforced_before_run_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            controller = CompiledInteractiveController(
                load_compiled_plugin_contract(root), root / "runs",
            )
            with self.assertRaises(PlatformContractError) as rejected:
                controller.start(
                    plugin_id="assayer.frontend-audit",
                    check_id="FUA-01",
                    scope={},
                )

        self.assertEqual(rejected.exception.code, "INVALID_SCOPE")

    def test_provider_required_run_fails_closed_without_installed_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            controller = CompiledInteractiveController(
                load_compiled_plugin_contract(root), root / "runs",
            )
            started = controller.start(
                plugin_id="assayer.frontend-audit",
                check_id="FUA-01",
                scope={"url": "https://example.test/orders"},
            )
            with self.assertRaises(PlatformContractError) as rejected:
                controller.bind_provider(
                    started["runId"],
                    provider_registry=ProviderRegistry(),
                )

        self.assertEqual(rejected.exception.code, "PROVIDER_NOT_FOUND")

    def test_markdown_run_discovers_and_collects_provider_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "input.md"
            document.write_text("# Overview\n\nA bounded document.\n", encoding="utf-8")
            compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), root / "compiled")
            controller = CompiledInteractiveController(
                load_compiled_plugin_contract(root / "compiled"), root / "runs",
            )
            started = controller.start(
                plugin_id="test.policy-pack", check_id="POLICY-001",
                scope={"files": [str(document)]},
            )
            run_id = started["runId"]
            try:
                controller.bind_provider(
                    run_id, provider_registry=ProviderRegistry([markdown_registration()]),
                )
                discovered = controller.discover(run_id)
                collected = controller.collect(run_id)
            finally:
                controller.close(run_id)

        self.assertEqual(len(discovered["workItems"]), 1)
        self.assertEqual(collected["status"], "collected")
        self.assertEqual(collected["collections"][0]["status"], "succeeded")
        self.assertEqual(len(collected["collections"][0]["evidence"]), 1)

    def test_markdown_review_plan_has_one_atom_per_provider_unit_and_dimension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "input.md"
            document.write_text("# Overview\n\nFirst paragraph.\n\nSecond paragraph.\n", encoding="utf-8")
            compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), root / "compiled")
            controller = CompiledInteractiveController(
                load_compiled_plugin_contract(root / "compiled"), root / "runs",
            )
            started = controller.start(
                plugin_id="test.policy-pack", check_id="POLICY-001",
                scope={"files": [str(document)]},
            )
            run_id = started["runId"]
            try:
                controller.bind_provider(
                    run_id, provider_registry=ProviderRegistry([markdown_registration()]),
                )
                planned = controller.plan_review_batches(run_id, max_batch_items=20)
                atoms = controller._run(run_id).coverage.atoms
            finally:
                controller.close(run_id)

        entries = planned["coverage"]["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            {atom.payload["element"]["kind"] for atom in atoms},
            {"heading", "paragraph"},
        )
        self.assertEqual(
            len({atom.payload["element"]["unitId"] for atom in atoms}), 3,
        )
        self.assertTrue(all(atom.payload["evidenceRefs"] for atom in atoms))

    def test_review_submission_rejects_fabricated_evidence_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "input.md"
            document.write_text("# Overview\n", encoding="utf-8")
            compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), root / "compiled")
            controller = CompiledInteractiveController(
                load_compiled_plugin_contract(root / "compiled"), root / "runs",
            )
            run_id = controller.start(
                plugin_id="test.policy-pack", check_id="POLICY-001",
                scope={"files": [str(document)]},
            )["runId"]
            try:
                controller.bind_provider(
                    run_id, provider_registry=ProviderRegistry([markdown_registration()]),
                )
                controller.plan_review_batches(run_id)
                state = controller._run(run_id)
                atom = state.coverage.atoms[0]
                batch_id = state.coverage.batches[0].batch_id
                with self.assertRaises(PlatformContractError) as rejected:
                    controller.submit_review_batch(run_id, batch_id, [{
                        "atomId": atom.atom_id,
                        "state": "satisfied",
                        "evidenceRefs": ["evidence:forged"],
                    }])
                self.assertEqual(rejected.exception.code, "INVALID_REVIEW_DECISION")
                accepted = controller.submit_review_batch(run_id, batch_id, [{
                    "atomId": atom.atom_id,
                    "state": "satisfied",
                    "evidenceRefs": list(atom.payload["evidenceRefs"]),
                }])
            finally:
                controller.close(run_id)

        self.assertEqual(accepted["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
