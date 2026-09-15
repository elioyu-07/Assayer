"""Tests for the Host-owned incremental common-review coordinator."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform.contract import PlatformContractError
from assayer_platform.incremental_review import JsonCoverageLedgerStore, ReviewAtom
from assayer_platform.incremental_review_runtime import IncrementalReviewCoordinator


def _candidate(index: int, *, support_id: str | None = None) -> ReviewAtom:
    return ReviewAtom(
        f"review-atom:{index}",
        "work:document",
        "candidate",
        {
            "rule": f"RULE-{index:05d}",
            "subject": f"subject {index}",
            "message": "Potential issue requires semantic review.",
            "severity": "P2",
            "recommendation": "Clarify the documented behavior.",
            "supportIds": [support_id or f"evidence:{index}"],
        },
    )


def _submission(task: dict[str, object], *, reason: str = "Not an issue.") -> dict[str, object]:
    return {
        "decisions": [
            {
                "itemRef": item["itemRef"],
                "kind": "candidate",
                "value": {
                    "disposition": "suppressed",
                    "reason": reason,
                    "support": {"refs": item["support"]},
                },
            }
            for item in task["items"]
        ],
    }


class IncrementalReviewCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = JsonCoverageLedgerStore(Path(self.temporary.name))

    def start(self, count: int = 2, **limits: int) -> IncrementalReviewCoordinator:
        return IncrementalReviewCoordinator.start(
            self.store,
            run_id="run:coordinator",
            atoms=tuple(_candidate(index) for index in range(1, count + 1)),
            max_batch_items=limits.get("max_batch_items", 1),
            max_batch_bytes=limits.get("max_batch_bytes", 24 * 1024),
        )

    def test_item_refs_are_run_global_and_late_submissions_fail_closed(self):
        coordinator = self.start()
        first_task = coordinator.next_task()
        self.assertEqual(first_task["items"][0]["itemRef"], "I1")
        first_submission = _submission(first_task)
        coordinator.submit(first_submission)

        second_task = coordinator.next_task()
        self.assertEqual(second_task["items"][0]["itemRef"], "I2")
        with self.assertRaises(PlatformContractError) as late:
            coordinator.submit(first_submission)
        self.assertEqual(late.exception.code, "UNKNOWN_REVIEW_ITEM")

        coordinator.submit(_submission(second_task))
        self.assertIsNone(coordinator.next_task())

    def test_offer_is_persisted_before_publication_and_restores_identically(self):
        coordinator = self.start()
        task_before_crash = coordinator.next_task()

        restored = IncrementalReviewCoordinator.restore(
            self.store, "run:coordinator",
        )
        task_after_crash = restored.next_task()

        self.assertEqual(task_after_crash, task_before_crash)
        self.assertEqual(restored.ledger.batches[0].status, "offered")

    def test_accepted_batch_is_not_repeated_after_restore(self):
        coordinator = self.start()
        first_task = coordinator.next_task()
        coordinator.submit(_submission(first_task))

        restored = IncrementalReviewCoordinator.restore(
            self.store, "run:coordinator",
        )
        second_task = restored.next_task()

        self.assertEqual(len(restored.ledger.verdicts), 1)
        self.assertEqual(second_task["items"][0]["itemRef"], "I2")
        self.assertEqual(
            [batch.status for batch in restored.ledger.batches],
            ["accepted", "offered"],
        )

    def test_evidence_refs_are_stable_and_monotonic_across_batches(self):
        coordinator = IncrementalReviewCoordinator.start(
            self.store,
            run_id="run:coordinator",
            atoms=(
                _candidate(1, support_id="evidence:shared"),
                _candidate(2, support_id="evidence:second"),
                _candidate(3, support_id="evidence:shared"),
            ),
            max_batch_items=1,
        )
        first = coordinator.next_task()
        self.assertEqual(first["items"][0]["support"], ["R1"])
        coordinator.submit(_submission(first))
        second = coordinator.next_task()
        self.assertEqual(second["items"][0]["support"], ["R2"])
        coordinator.submit(_submission(second))
        third = coordinator.next_task()
        self.assertEqual(third["items"][0]["support"], ["R1"])

    def test_correction_supersedes_the_effective_revision_without_agent_identity(self):
        coordinator = self.start(count=1)
        task = coordinator.next_task()
        coordinator.submit(_submission(task, reason="Initial decision."))
        batch_id = coordinator.ledger.batches[0].batch_id

        correction_task = coordinator.correction_task(batch_id)
        self.assertEqual(correction_task, task)
        self.assertNotIn("supersedes", json.dumps(correction_task))
        coordinator.submit(_submission(correction_task, reason="Corrected decision."))

        first, second = coordinator.ledger.verdicts
        self.assertEqual(first.status, "superseded")
        self.assertEqual(second.status, "accepted")
        self.assertEqual(second.supersedes, first.submission_id)

    def test_terminal_review_rejects_late_submission(self):
        coordinator = self.start(count=1)
        task = coordinator.next_task()
        coordinator.submit(_submission(task))
        terminal = coordinator.finish("completed")

        self.assertEqual(terminal.terminal_status, "completed")
        self.assertIsNone(coordinator.next_task())
        with self.assertRaises(PlatformContractError) as rejected:
            coordinator.submit(_submission(task))
        self.assertEqual(rejected.exception.code, "REVIEW_TERMINAL")

    def test_snapshot_is_frozen_and_journal_replays_the_exact_terminal_state(self):
        coordinator = self.start(count=1)
        snapshot = Path(self.temporary.name) / "run:coordinator.review-coverage.json"
        journal = (
            Path(self.temporary.name)
            / "run:coordinator.review-coverage.journal.jsonl"
        )
        frozen_snapshot = snapshot.read_bytes()

        task = coordinator.next_task()
        coordinator.submit(_submission(task))
        terminal = coordinator.finish("completed")

        self.assertEqual(snapshot.read_bytes(), frozen_snapshot)
        self.assertEqual(len(journal.read_bytes().splitlines()), 3)
        restored = IncrementalReviewCoordinator.restore(
            JsonCoverageLedgerStore(Path(self.temporary.name)), "run:coordinator",
        )
        self.assertEqual(
            restored.ledger.canonical_bytes(), terminal.canonical_bytes(),
        )

    def test_journal_digest_chain_rejects_tampering(self):
        coordinator = self.start(count=1)
        coordinator.next_task()
        journal = (
            Path(self.temporary.name)
            / "run:coordinator.review-coverage.journal.jsonl"
        )
        event = json.loads(journal.read_text(encoding="utf-8"))
        event["operation"]["batchId"] = "review-batch:tampered"
        journal.write_text(json.dumps(event) + "\n", encoding="utf-8")

        with self.assertRaises(PlatformContractError) as rejected:
            IncrementalReviewCoordinator.restore(
                JsonCoverageLedgerStore(Path(self.temporary.name)), "run:coordinator",
            )
        self.assertEqual(
            rejected.exception.code, "COVERAGE_JOURNAL_DIGEST_MISMATCH",
        )

    def test_blocked_tasks_can_close_as_partial(self):
        coordinator = self.start(count=1)
        coordinator.next_task()
        coordinator.block_current("Required source is unavailable")

        terminal = coordinator.finish("partial")

        self.assertEqual(terminal.terminal_status, "partial")
        self.assertEqual(terminal.entries[0].status, "blocked")

    def test_ten_thousand_candidates_still_issue_a_bounded_task(self):
        item_limit = 41
        byte_limit = 8 * 1024
        coordinator = IncrementalReviewCoordinator.start(
            self.store,
            run_id="run:coordinator",
            atoms=tuple(_candidate(index) for index in range(1, 10_001)),
            max_batch_items=item_limit,
            max_batch_bytes=byte_limit,
        )

        task = coordinator.next_task()

        self.assertLessEqual(len(task["items"]), item_limit)
        self.assertLessEqual(
            len(json.dumps(task, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            byte_limit,
        )
        self.assertEqual(task["items"][0]["itemRef"], "I1")


if __name__ == "__main__":
    unittest.main()
