"""Tests for the Host-owned incremental semantic-review core."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform.contract import PlatformContractError
from assayer_platform.incremental_review import (
    BatchVerdict,
    CoverageLedger,
    JsonCoverageLedgerStore,
    ReviewAtom,
    adapt_legacy_domain_result,
    append_legacy_domain_result,
    plan_legacy_domain_results,
)


class IncrementalReviewPlanningTests(unittest.TestCase):
    @staticmethod
    def shared_context(size: int = 20_000) -> dict[str, object]:
        return {
            "facts": [{
                "name": "document-context",
                "value": "x" * size,
                "supportIds": ["evidence:context"],
            }],
            "unknowns": [],
        }

    def test_atom_identity_is_stable_and_source_bound(self):
        values = {
            "run_id": "run:planning",
            "work_item_id": "work:document",
            "kind": "candidate",
            "source_anchor": "line:42",
            "rule_id": "PERM-001",
            "payload": {"message": "权限行为缺少拒绝定义"},
        }
        first = ReviewAtom.create(**values)
        second = ReviewAtom.create(**values)
        moved = ReviewAtom.create(**{**values, "source_anchor": "line:43"})

        self.assertEqual(first.atom_id, second.atom_id)
        self.assertNotEqual(first.atom_id, moved.atom_id)

    def test_multi_batch_plan_is_bounded_deterministic_and_stably_ordered(self):
        atoms = tuple(
            ReviewAtom(
                f"review-atom:{index}",
                "work:large-document",
                "candidate",
                {"index": index, "message": "x" * 80},
            )
            for index in range(257)
        )

        first = CoverageLedger.plan(
            "run:large-document",
            atoms,
            max_batch_items=41,
            max_batch_bytes=8 * 1024,
        )
        second = CoverageLedger.plan(
            "run:large-document",
            atoms,
            max_batch_items=41,
            max_batch_bytes=8 * 1024,
        )

        planned_ids = tuple(
            atom_id for batch in first.batches for atom_id in batch.atom_ids
        )
        self.assertEqual(planned_ids, tuple(atom.atom_id for atom in atoms))
        self.assertEqual(len(planned_ids), len(set(planned_ids)))
        self.assertTrue(all(len(batch.atom_ids) <= 41 for batch in first.batches))
        self.assertTrue(all(batch.payload_bytes <= 8 * 1024 for batch in first.batches))
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_one_atom_larger_than_the_task_limit_is_rejected(self):
        atom = ReviewAtom(
            "review-atom:oversized",
            "work:large-document",
            "candidate",
            {"message": "x" * 2_000},
        )

        with self.assertRaises(PlatformContractError) as rejected:
            CoverageLedger.plan(
                "run:large-document", (atom,), max_batch_bytes=256,
            )

        self.assertEqual(rejected.exception.code, "REVIEW_ATOM_TOO_LARGE")
        self.assertEqual(rejected.exception.work_item_id, "work:large-document")

    def test_shared_context_is_cataloged_once_and_charged_once_per_batch(self):
        context = self.shared_context()
        atoms = tuple(
            ReviewAtom(
                f"review-atom:context-{index}", "work:document", "dimension",
                {
                    "dimension": f"dimension-{index}",
                    "instruction": "review it",
                    "supportIds": ["evidence:item"],
                    "context": context,
                },
            )
            for index in range(2)
        )

        ledger = CoverageLedger.plan(
            "run:context-catalog", atoms, max_batch_bytes=30_000,
        )

        self.assertEqual(len(ledger.contexts), 1)
        self.assertEqual(len(ledger.batches), 1)
        self.assertTrue(all("context" not in atom.payload for atom in ledger.atoms))
        self.assertTrue(all("contextRef" in atom.payload for atom in ledger.atoms))
        self.assertLess(ledger.batches[0].payload_bytes, 30_000)
        restored = CoverageLedger.from_dict(json.loads(ledger.canonical_bytes()))
        self.assertEqual(restored.canonical_bytes(), ledger.canonical_bytes())

    def test_v1_inline_context_snapshot_migrates_to_context_catalog(self):
        atom = ReviewAtom(
            "review-atom:legacy-context", "work:document", "dimension",
            {
                "dimension": "quality",
                "instruction": "review it",
                "supportIds": ["evidence:item"],
                "context": self.shared_context(20),
            },
        )
        # Construct a valid pre-catalog ledger using its historical inline shape.
        planned = CoverageLedger.plan("run:legacy-context", (atom,))
        raw = planned.as_dict()
        raw["schemaVersion"] = "1.0.0"
        raw.pop("contexts")
        raw["atoms"] = [atom.as_dict()]
        raw["batches"][0]["payloadBytes"] = len(json.dumps(
            {"atoms": [atom.as_dict()]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))

        restored = CoverageLedger.from_dict(raw)

        self.assertEqual(restored.as_dict()["schemaVersion"], "1.1.0")
        self.assertEqual(len(restored.contexts), 1)
        self.assertIn("contextRef", restored.atoms[0].payload)
        self.assertNotIn("context", restored.atoms[0].payload)

    def test_review_payload_is_deeply_detached_and_rejects_non_finite_json(self):
        source = {"nested": {"value": 1}}
        atom = ReviewAtom("review-atom:frozen", "work:document", "candidate", source)
        source["nested"]["value"] = 2

        self.assertEqual(atom.payload["nested"]["value"], 1)
        with self.assertRaises(TypeError):
            atom.payload["nested"]["value"] = 3
        with self.assertRaises(PlatformContractError) as rejected:
            ReviewAtom(
                "review-atom:not-finite",
                "work:document",
                "candidate",
                {"confidence": float("nan")},
            )
        self.assertEqual(rejected.exception.code, "INVALID_REVIEW_VALUE")


class IncrementalReviewStateTests(unittest.TestCase):
    @staticmethod
    def ledger() -> CoverageLedger:
        return CoverageLedger.plan(
            "run:state",
            (
                ReviewAtom("review-atom:one", "work:document", "candidate", {}),
                ReviewAtom("review-atom:two", "work:document", "candidate", {}),
            ),
        )

    def test_offered_batch_accepts_complete_verdict_and_replay_is_idempotent(self):
        offered = self.ledger().offer()
        self.assertIs(offered.offer(offered.batches[0].batch_id), offered)
        batch = offered.batches[0]
        verdict = BatchVerdict(
            "submission:first",
            batch.batch_id,
            tuple({"atomId": atom_id, "disposition": "confirmed"} for atom_id in batch.atom_ids),
        )

        accepted = offered.accept(verdict)

        self.assertEqual(accepted.batches[0].status, "accepted")
        self.assertTrue(all(entry.status == "accepted" for entry in accepted.entries))
        self.assertTrue(all(
            entry.effective_submission_id == verdict.submission_id
            for entry in accepted.entries
        ))
        self.assertIs(accepted.accept(verdict), accepted)

    def test_submission_must_cover_the_offered_batch_exactly_once(self):
        offered = self.ledger().offer()
        batch = offered.batches[0]
        with self.assertRaises(PlatformContractError) as missing:
            offered.accept(BatchVerdict(
                "submission:missing",
                batch.batch_id,
                ({"atomId": batch.atom_ids[0], "disposition": "confirmed"},),
            ))
        self.assertEqual(missing.exception.code, "INVALID_BATCH_VERDICT")

        with self.assertRaises(PlatformContractError) as unknown:
            offered.accept(BatchVerdict(
                "submission:unknown",
                batch.batch_id,
                (
                    {"atomId": batch.atom_ids[0], "disposition": "confirmed"},
                    {"atomId": "review-atom:unknown", "disposition": "confirmed"},
                ),
            ))
        self.assertEqual(unknown.exception.code, "UNKNOWN_REVIEW_ATOM")

        with self.assertRaises(PlatformContractError) as duplicate:
            BatchVerdict(
                "submission:duplicate",
                batch.batch_id,
                (
                    {"atomId": batch.atom_ids[0], "disposition": "confirmed"},
                    {"atomId": batch.atom_ids[0], "disposition": "suppressed"},
                ),
            )
        self.assertEqual(duplicate.exception.code, "INVALID_BATCH_VERDICT")

    def test_reused_submission_identity_with_different_content_conflicts(self):
        offered = self.ledger().offer()
        batch = offered.batches[0]
        accepted = offered.accept(BatchVerdict(
            "submission:stable",
            batch.batch_id,
            tuple({"atomId": atom_id, "disposition": "confirmed"} for atom_id in batch.atom_ids),
        ))

        with self.assertRaises(PlatformContractError) as rejected:
            accepted.accept(BatchVerdict(
                "submission:stable",
                batch.batch_id,
                tuple({"atomId": atom_id, "disposition": "suppressed"} for atom_id in batch.atom_ids),
            ))

        self.assertEqual(rejected.exception.code, "REVIEW_SUBMISSION_CONFLICT")

    def test_correction_appends_history_and_rejects_a_stale_parent(self):
        offered = self.ledger().offer()
        batch = offered.batches[0]
        first = BatchVerdict(
            "submission:first",
            batch.batch_id,
            tuple({"atomId": atom_id, "disposition": "confirmed"} for atom_id in batch.atom_ids),
        )
        accepted = offered.accept(first)
        correction = BatchVerdict(
            "submission:second",
            batch.batch_id,
            tuple({"atomId": atom_id, "disposition": "suppressed"} for atom_id in batch.atom_ids),
            supersedes=first.submission_id,
        )

        corrected = accepted.accept(correction)

        self.assertEqual(
            tuple(verdict.status for verdict in corrected.verdicts),
            ("superseded", "accepted"),
        )
        self.assertTrue(all(
            entry.revision_ids == (first.submission_id, correction.submission_id)
            and entry.effective_submission_id == correction.submission_id
            for entry in corrected.entries
        ))
        with self.assertRaises(PlatformContractError) as stale:
            corrected.accept(BatchVerdict(
                "submission:stale",
                batch.batch_id,
                tuple({"atomId": atom_id, "disposition": "needs_review"} for atom_id in batch.atom_ids),
                supersedes=first.submission_id,
            ))
        self.assertEqual(stale.exception.code, "REVIEW_CORRECTION_CONFLICT")

    def test_partial_terminal_requires_every_unaccepted_batch_to_be_blocked(self):
        ledger = CoverageLedger.plan(
            "run:partial",
            (
                ReviewAtom("review-atom:one", "work:document", "candidate", {}),
                ReviewAtom("review-atom:two", "work:document", "candidate", {}),
            ),
            max_batch_items=1,
        ).offer()
        first_batch = ledger.batches[0]
        ledger = ledger.accept(BatchVerdict(
            "submission:partial",
            first_batch.batch_id,
            ({"atomId": first_batch.atom_ids[0], "disposition": "confirmed"},),
        ))
        with self.assertRaises(PlatformContractError) as incomplete:
            ledger.finalize("partial")
        self.assertEqual(incomplete.exception.code, "INCOMPLETE_REVIEW_COVERAGE")

        ledger = ledger.block(ledger.batches[1].batch_id, "source unavailable")
        terminal = ledger.finalize("partial")

        self.assertEqual(terminal.terminal_status, "partial")
        self.assertTrue(all(batch.status == "terminal" for batch in terminal.batches))
        self.assertIs(terminal.finalize("partial"), terminal)

    def test_terminal_coverage_cannot_be_changed(self):
        terminal = self.ledger().finalize("failed")
        batch = terminal.batches[0]
        operations = (
            lambda: terminal.offer(batch.batch_id),
            lambda: terminal.block(batch.batch_id, "late failure"),
            lambda: terminal.accept(BatchVerdict(
                "submission:late",
                batch.batch_id,
                tuple({"atomId": atom_id, "disposition": "confirmed"} for atom_id in batch.atom_ids),
            )),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(PlatformContractError) as rejected:
                    operation()
                self.assertEqual(rejected.exception.code, "REVIEW_TERMINAL")


class IncrementalReviewSerializationTests(unittest.TestCase):
    @staticmethod
    def corrected_terminal_ledger() -> CoverageLedger:
        ledger = CoverageLedger.plan(
            "run:restore",
            (ReviewAtom(
                "review-atom:restore",
                "work:document",
                "candidate",
                {"message": "权限行为缺少拒绝定义"},
            ),),
        ).offer()
        batch = ledger.batches[0]
        ledger = ledger.accept(BatchVerdict(
            "submission:before",
            batch.batch_id,
            ({"atomId": batch.atom_ids[0], "disposition": "confirmed"},),
        ))
        ledger = ledger.accept(BatchVerdict(
            "submission:after",
            batch.batch_id,
            ({"atomId": batch.atom_ids[0], "disposition": "suppressed"},),
            supersedes="submission:before",
        ))
        return ledger.finalize("completed")

    def test_corrected_terminal_ledger_round_trips_byte_for_byte(self):
        ledger = self.corrected_terminal_ledger()

        restored = CoverageLedger.from_dict(json.loads(ledger.canonical_bytes()))

        self.assertEqual(restored.canonical_bytes(), ledger.canonical_bytes())
        self.assertEqual(restored.terminal_status, "completed")
        self.assertEqual(
            restored.entries[0].effective_submission_id,
            "submission:after",
        )

    def test_changed_verdict_content_fails_digest_validation(self):
        value = json.loads(self.corrected_terminal_ledger().canonical_bytes())
        value["verdicts"][1]["decisions"][0]["disposition"] = "confirmed"

        with self.assertRaises(PlatformContractError) as rejected:
            CoverageLedger.from_dict(value)

        self.assertEqual(rejected.exception.code, "COVERAGE_LEDGER_DIGEST_MISMATCH")

    def test_changed_batch_byte_accounting_is_rejected(self):
        value = json.loads(self.corrected_terminal_ledger().canonical_bytes())
        value["batches"][0]["payloadBytes"] += 1

        with self.assertRaises(PlatformContractError) as rejected:
            CoverageLedger.from_dict(value)

        self.assertEqual(rejected.exception.code, "INVALID_COVERAGE_LEDGER")

    def test_restore_rejects_coerced_types_and_unknown_record_fields(self):
        value = json.loads(self.corrected_terminal_ledger().canonical_bytes())
        value["limits"]["maxBatchItems"] = True
        with self.assertRaises(PlatformContractError) as invalid_type:
            CoverageLedger.from_dict(value)
        self.assertEqual(invalid_type.exception.code, "INVALID_COVERAGE_LEDGER")

        value = json.loads(self.corrected_terminal_ledger().canonical_bytes())
        value["atoms"][0]["unexpected"] = "value"
        with self.assertRaises(PlatformContractError) as unknown_field:
            CoverageLedger.from_dict(value)
        self.assertEqual(unknown_field.exception.code, "INVALID_COVERAGE_LEDGER")


class IncrementalReviewPersistenceTests(unittest.TestCase):
    def test_json_store_atomically_saves_and_restores_the_exact_ledger(self):
        ledger = IncrementalReviewSerializationTests.corrected_terminal_ledger()
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCoverageLedgerStore(Path(directory))

            destination = store.save(ledger)
            restored = store.load(ledger.run_id)

            self.assertTrue(destination.is_file())
            self.assertFalse(destination.with_name(destination.name + ".tmp").exists())
            self.assertIsNotNone(restored)
            self.assertEqual(restored.canonical_bytes(), ledger.canonical_bytes())
            self.assertIsNone(store.load("run:missing"))

    def test_legacy_domain_result_becomes_one_deterministic_accepted_batch(self):
        result = {
            "decisions": [{"candidate_id": "candidate:one", "status": "CONFIRMED"}],
            "reason": "The candidate is supported.",
        }

        first = adapt_legacy_domain_result(
            run_id="run:legacy",
            work_item_id="work:document",
            check_id="CFG-001",
            domain_result=result,
        )
        second = adapt_legacy_domain_result(
            run_id="run:legacy",
            work_item_id="work:document",
            check_id="CFG-001",
            domain_result=result,
        )

        self.assertEqual(len(first.atoms), 1)
        self.assertEqual(len(first.batches), 1)
        self.assertEqual(len(first.entries), 1)
        self.assertEqual(len(first.verdicts), 1)
        self.assertEqual(first.batches[0].status, "accepted")
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(
            first.verdicts[0].decisions[0]["domainResult"]["reason"],
            result["reason"],
        )

    def test_legacy_results_append_one_single_item_batch_per_work_item(self):
        ledger = append_legacy_domain_result(
            None,
            run_id="run:legacy-many",
            work_item_id="work:one",
            check_id="CFG-001",
            domain_result={"reason": "first"},
        )
        ledger = append_legacy_domain_result(
            ledger,
            run_id="run:legacy-many",
            work_item_id="work:two",
            check_id="CFG-001",
            domain_result={"reason": "second"},
        )
        replayed = append_legacy_domain_result(
            ledger,
            run_id="run:legacy-many",
            work_item_id="work:two",
            check_id="CFG-001",
            domain_result={"reason": "second"},
        )

        self.assertEqual(len(ledger.batches), 2)
        self.assertTrue(all(len(batch.atom_ids) == 1 for batch in ledger.batches))
        self.assertTrue(all(batch.status == "accepted" for batch in ledger.batches))
        self.assertEqual(len(ledger.verdicts), 2)
        self.assertIs(replayed, ledger)

    def test_legacy_plan_exists_before_results_are_submitted(self):
        ledger = plan_legacy_domain_results(
            run_id="run:legacy-plan",
            work_item_ids=("work:one", "work:two"),
            check_id="CFG-001",
        )

        self.assertEqual([batch.status for batch in ledger.batches], ["planned", "planned"])
        ledger = append_legacy_domain_result(
            ledger,
            run_id="run:legacy-plan",
            work_item_id="work:one",
            check_id="CFG-001",
            domain_result={"reason": "first"},
        )

        self.assertEqual([batch.status for batch in ledger.batches], ["accepted", "planned"])
        self.assertEqual(len(ledger.verdicts), 1)


if __name__ == "__main__":
    unittest.main()
