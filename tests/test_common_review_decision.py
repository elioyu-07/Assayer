"""Tests for Host-owned common-review Decision assembly."""

from __future__ import annotations

import unittest

from assayer_platform.common_review import (
    CandidateDisposition,
    CommonReviewDecision,
    CommonReviewSubmission,
    DimensionVerdict,
    EvidenceSupport,
    Finding,
    Unknown,
)
from assayer_platform.common_review_decision import assemble_common_review_decisions
from assayer_platform.contract import PlatformContractError
from assayer_platform.incremental_review import CoverageLedger, ReviewAtom
from assayer_platform.review_binding import ReviewTaskBinding


class CommonReviewDecisionAssemblyTests(unittest.TestCase):
    @staticmethod
    def ledger() -> CoverageLedger:
        return CoverageLedger.plan(
            "run:assembly",
            (
                ReviewAtom("review-atom:dimension", "work:one", "dimension", {
                    "dimension": "permissions",
                    "instruction": "Assess permissions.",
                    "supportIds": ["evidence:one"],
                }),
                ReviewAtom("review-atom:dimension-two", "work:two", "dimension", {
                    "dimension": "configuration",
                    "instruction": "Assess configuration behavior.",
                    "supportIds": ["evidence:two"],
                }),
                ReviewAtom("review-atom:candidate", "work:two", "candidate", {
                    "rule": "PERM-001",
                    "subject": "denial behavior",
                    "message": "Denial behavior may be missing.",
                    "severity": "P2",
                    "recommendation": "Define denial behavior.",
                    "supportIds": ["evidence:two"],
                }),
            ),
            max_batch_items=1,
        )

    def test_accepted_common_values_map_to_one_decision_per_work_item(self):
        ledger = self.ledger().offer()
        first = ledger.batches[0]
        first_binding = ReviewTaskBinding.from_ledger(
            ledger, first.batch_id, evidence_bindings={"R1": "evidence:one"},
        )
        ledger = ledger.accept(first_binding.bind(CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "permissions", "satisfied", "Explicit.",
                support=EvidenceSupport(("R1",)),
            )),
        ))))
        ledger = ledger.offer()
        second = ledger.batches[1]
        second_binding = ReviewTaskBinding.from_ledger(
            ledger, second.batch_id, evidence_bindings={"R2": "evidence:two"},
        )
        support = EvidenceSupport(("R2",))
        ledger = ledger.accept(second_binding.bind(CommonReviewSubmission((
            CommonReviewDecision("I2", DimensionVerdict(
                "configuration", "violated", "The candidate confirms a violation.",
                support=support,
            )),
        ))))
        ledger = ledger.offer()
        third = ledger.batches[2]
        third_binding = ReviewTaskBinding.from_ledger(
            ledger, third.batch_id, evidence_bindings={"R2": "evidence:two"},
        )
        ledger = ledger.accept(third_binding.bind(CommonReviewSubmission((
            CommonReviewDecision("I3", CandidateDisposition(
                "confirmed", "Confirmed.", support=support,
                finding=Finding(
                    "Permission denial is undefined", "Denial behavior is missing.",
                    "P2", "Define denial behavior.", support,
                ),
            )),
        ))))

        first_decision, second_decision = assemble_common_review_decisions(
            ledger, check_id="SPEC-001", check_version="1.0.0",
        )

        self.assertEqual(first_decision["result"], "scanned_no_issue")
        self.assertEqual(first_decision["findings"][0]["status"], "satisfied")
        self.assertEqual(second_decision["result"], "issue_found")
        self.assertEqual(second_decision["findings"][0]["dimension"], "configuration")
        self.assertEqual(second_decision["details"]["evidenceRefs"], ["evidence:two"])
        self.assertNotIn("atomId", str(second_decision))

    def test_unknown_value_maps_to_needs_review(self):
        ledger = CoverageLedger.plan(
            "run:unknown",
            (ReviewAtom("review-atom:unknown", "work:one", "dimension", {
                "dimension": "permissions",
                "instruction": "Assess permissions.",
                "supportIds": ["evidence:one"],
            }),),
        ).offer()
        binding = ReviewTaskBinding.from_ledger(ledger, ledger.batches[0].batch_id)
        ledger = ledger.accept(binding.bind(CommonReviewSubmission((
            CommonReviewDecision(
                "I1", Unknown("The permission section is unavailable."),
            ),
        ))))

        decision = assemble_common_review_decisions(
            ledger, check_id="SPEC-001", check_version="1.0.0",
        )[0]

        self.assertEqual(decision["result"], "needs_review")
        self.assertEqual(decision["findings"][0]["status"], "unresolved")

    def test_reviewer_origin_finding_is_preserved_without_a_candidate(self):
        ledger = CoverageLedger.plan(
            "run:reviewer-origin",
            (
                ReviewAtom("review-atom:consistency", "work:one", "dimension", {
                    "dimension": "consistency",
                    "instruction": "Assess consistency.",
                    "supportIds": ["evidence:one"],
                }),
                ReviewAtom("review-atom:recovery", "work:one", "dimension", {
                    "dimension": "recovery",
                    "instruction": "Assess recovery.",
                    "supportIds": ["evidence:one"],
                }),
            ),
        ).offer()
        binding = ReviewTaskBinding.from_ledger(
            ledger, ledger.batches[0].batch_id,
            evidence_bindings={"R1": "evidence:one"},
        )
        support = EvidenceSupport(("R1",), "The two statements conflict.")
        ledger = ledger.accept(binding.bind(CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "consistency", "violated", "The policy is contradictory.",
                support=support,
                findings=(Finding(
                    "Failure handling is contradictory",
                    "One section forbids retries while another requires them.",
                    "P2", "Define one authoritative retry policy.", support,
                    ("consistency", "recovery"),
                ),),
            )),
            CommonReviewDecision("I2", DimensionVerdict(
                "recovery", "violated", "Recovery behavior is contradictory.",
                support=support,
            )),
        ))))

        decision = assemble_common_review_decisions(
            ledger, check_id="SPEC-001", check_version="1.0.0",
        )[0]

        self.assertEqual(decision["result"], "issue_found")
        review = decision["details"]["commonReview"]
        reviewer_finding = review[0]["value"]["findings"][0]
        self.assertEqual(reviewer_finding["severity"], "P2")
        self.assertEqual(
            reviewer_finding["support"]["refs"], ["evidence:one"],
        )
        self.assertEqual(
            reviewer_finding["affectedDimensions"],
            ["consistency", "recovery"],
        )
        self.assertNotIn("findingId", reviewer_finding)

    def test_incomplete_coverage_cannot_be_assembled(self):
        with self.assertRaises(PlatformContractError) as rejected:
            assemble_common_review_decisions(
                self.ledger(), check_id="SPEC-001", check_version="1.0.0",
            )
        self.assertEqual(rejected.exception.code, "INCOMPLETE_REVIEW_COVERAGE")


if __name__ == "__main__":
    unittest.main()
