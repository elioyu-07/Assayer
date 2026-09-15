"""Tests for task-local common-review identity binding."""

from __future__ import annotations

import json
import unittest

from assayer_platform.common_review import (
    CommonReviewDecision,
    CommonReviewSubmission,
    DimensionVerdict,
    EvidenceSupport,
    Finding,
    RelationshipVerdict,
)
from assayer_platform.contract import PlatformContractError
from assayer_platform.incremental_review import CoverageLedger, ReviewAtom
from assayer_platform.review_binding import ReviewTaskBinding


class ReviewTaskBindingTests(unittest.TestCase):
    @staticmethod
    def offered_ledger() -> CoverageLedger:
        return CoverageLedger.plan(
            "run:common-review",
            (
                ReviewAtom("review-atom:dimension", "work:document", "dimension", {}),
                ReviewAtom("review-atom:relation", "work:document", "relationship", {}),
            ),
        ).offer()

    def test_agent_task_contains_only_task_local_identity(self):
        ledger = self.offered_ledger()
        binding = ReviewTaskBinding.from_ledger(
            ledger, ledger.batches[0].batch_id,
            evidence_bindings={"R1": "evidence:internal:one"},
        )

        task = binding.agent_task()
        encoded = json.dumps(task)

        self.assertEqual([item["itemRef"] for item in task["items"]], ["I1", "I2"])
        self.assertEqual(task["evidenceRefs"], ["R1"])
        self.assertNotIn("run:common-review", encoded)
        self.assertNotIn("work:document", encoded)
        self.assertNotIn("review-atom:", encoded)
        self.assertNotIn("review-batch:", encoded)
        self.assertNotIn("evidence:internal:", encoded)

    def test_submission_resolves_item_and_evidence_refs_then_enters_coverage(self):
        ledger = self.offered_ledger()
        batch = ledger.batches[0]
        binding = ReviewTaskBinding.from_ledger(
            ledger, batch.batch_id,
            evidence_bindings={"R1": "evidence:internal:one"},
        )
        support = EvidenceSupport(("R1",))
        submission = CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "permissions", "satisfied", "Explicit.", support=support,
            )),
            CommonReviewDecision("I2", RelationshipVerdict(
                "role-to-scope", "confirmed", "Explicit.", support=support,
            )),
        ))

        verdict = binding.bind(submission)
        accepted = ledger.accept(verdict)

        self.assertEqual(
            [item["atomId"] for item in verdict.decisions],
            list(batch.atom_ids),
        )
        self.assertEqual(
            verdict.decisions[0]["value"]["support"]["refs"],
            ("evidence:internal:one",),
        )
        self.assertTrue(all(entry.status == "accepted" for entry in accepted.entries))

    def test_unknown_evidence_and_mismatched_kind_fail_closed(self):
        ledger = self.offered_ledger()
        batch = ledger.batches[0]
        binding = ReviewTaskBinding.from_ledger(
            ledger, batch.batch_id,
            evidence_bindings={"R1": "evidence:internal:one"},
        )
        with self.assertRaises(PlatformContractError) as evidence_error:
            binding.bind(CommonReviewSubmission((
                CommonReviewDecision("I1", DimensionVerdict(
                    "permissions", "satisfied", "Explicit.",
                    support=EvidenceSupport(("R2",)),
                )),
                CommonReviewDecision("I2", RelationshipVerdict(
                    "role-to-scope", "confirmed", "Explicit.",
                    support=EvidenceSupport(("R1",)),
                )),
            )))
        self.assertEqual(evidence_error.exception.code, "UNKNOWN_REVIEW_EVIDENCE")

        with self.assertRaises(PlatformContractError) as kind_error:
            binding.bind(CommonReviewSubmission((
                CommonReviewDecision("I1", RelationshipVerdict(
                    "wrong-kind", "confirmed", "Explicit.",
                    support=EvidenceSupport(("R1",)),
                )),
                CommonReviewDecision("I2", RelationshipVerdict(
                    "role-to-scope", "confirmed", "Explicit.",
                    support=EvidenceSupport(("R1",)),
                )),
            )))
        self.assertEqual(kind_error.exception.code, "REVIEW_KIND_MISMATCH")

    def test_reviewer_finding_may_use_current_batch_evidence_across_dimensions(self):
        ledger = CoverageLedger.plan(
            "run:cross-dimension-finding",
            (
                ReviewAtom("review-atom:content", "work:document", "dimension", {}),
                ReviewAtom("review-atom:recovery", "work:document", "dimension", {}),
            ),
        ).offer()
        batch = ledger.batches[0]
        binding = ReviewTaskBinding.from_ledger(
            ledger,
            batch.batch_id,
            evidence_bindings={
                "R1": "evidence:internal:content",
                "R2": "evidence:internal:recovery",
            },
        )
        finding = Finding(
            "Failure handling is contradictory",
            "One statement forbids retry while another requires retry.",
            "P2",
            "Define one authoritative failure-handling rule.",
            EvidenceSupport(("R1", "R2")),
            ("content-consistency", "dependency-recovery"),
        )
        submission = CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "content-consistency",
                "conflicted",
                "The two frozen statements cannot both govern failure handling.",
                support=EvidenceSupport(("R1", "R2")),
                findings=(finding,),
            )),
            CommonReviewDecision("I2", DimensionVerdict(
                "dependency-recovery",
                "violated",
                "Recovery behavior is affected by the same contradiction.",
                support=EvidenceSupport(("R2",)),
            )),
        ))

        verdict = binding.bind(submission)

        self.assertEqual(
            verdict.decisions[0]["value"]["findings"][0]["support"]["refs"],
            ("evidence:internal:content", "evidence:internal:recovery"),
        )

    def test_correction_parent_is_host_bound_and_never_agent_authored(self):
        ledger = self.offered_ledger()
        batch = ledger.batches[0]
        first_binding = ReviewTaskBinding.from_ledger(
            ledger, batch.batch_id, evidence_bindings={"R1": "evidence:internal:one"},
        )
        first = CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "permissions", "satisfied", "Explicit.",
                support=EvidenceSupport(("R1",)),
            )),
            CommonReviewDecision("I2", RelationshipVerdict(
                "role-to-scope", "confirmed", "Explicit.",
                support=EvidenceSupport(("R1",)),
            )),
        ))
        accepted = ledger.accept(first_binding.bind(first))
        correction_binding = ReviewTaskBinding.from_ledger(
            accepted, batch.batch_id, evidence_bindings={"R1": "evidence:internal:one"},
        )
        correction = CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "permissions", "violated", "Denial behavior is missing.",
                support=EvidenceSupport(("R1",)),
            )),
            CommonReviewDecision("I2", RelationshipVerdict(
                "role-to-scope", "rejected", "The mapping is incomplete.",
                support=EvidenceSupport(("R1",)),
            )),
        ))

        verdict = correction_binding.bind(correction)
        corrected = accepted.accept(verdict)

        self.assertEqual(verdict.supersedes, accepted.verdicts[-1].submission_id)
        self.assertNotIn("supersedes", correction_binding.agent_task())
        self.assertEqual(
            [item.status for item in corrected.verdicts], ["superseded", "accepted"],
        )


if __name__ == "__main__":
    unittest.main()
