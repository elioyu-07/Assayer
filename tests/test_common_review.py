"""Tests for the Host-owned common review value model."""

from __future__ import annotations

import unittest

from assayer_platform.common_review import (
    Applicability,
    CandidateDisposition,
    CommonReviewDecision,
    CommonReviewSubmission,
    Confidence,
    DimensionVerdict,
    Escalation,
    EvidenceSupport,
    Finding,
    RelationshipVerdict,
    Unknown,
)
from assayer_platform.contract import PlatformContractError


class CommonReviewValueTests(unittest.TestCase):
    def test_values_normalize_to_one_stable_json_shape(self):
        self.assertEqual(Applicability("applicable").as_dict(), {"state": "applicable"})
        self.assertEqual(
            Confidence("medium", "  Evidence is indirect.  ").as_dict(),
            {"level": "medium", "reason": "Evidence is indirect."},
        )
        self.assertEqual(
            EvidenceSupport(("R1", "R2"), "Two source ranges.").as_dict(),
            {"refs": ["R1", "R2"], "reason": "Two source ranges."},
        )
        self.assertEqual(
            Unknown("The relevant section is unavailable.", ("permission section",)).as_dict(),
            {
                "reason": "The relevant section is unavailable.",
                "missingInformation": ["permission section"],
            },
        )
        self.assertEqual(
            Escalation("Owner is unknown.", "Ask the service owner.").as_dict()["requiredAction"],
            "Ask the service owner.",
        )

    def test_unknown_states_require_explanations(self):
        invalid = (
            lambda: Applicability("unknown"),
            lambda: Applicability("not_applicable"),
            lambda: Confidence("unknown"),
            lambda: Unknown(""),
            lambda: Escalation("Needs follow-up.", ""),
        )
        for factory in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises(PlatformContractError) as rejected:
                    factory()
                self.assertEqual(rejected.exception.code, "INVALID_COMMON_REVIEW")


class CommonReviewVerdictTests(unittest.TestCase):
    support = EvidenceSupport(("R1",), "Direct source support.")

    def test_common_verdicts_have_stable_typed_projections(self):
        dimension = DimensionVerdict(
            "permissions",
            "violated",
            "Denial behavior is missing.",
            confidence=Confidence("high"),
            support=self.support,
        )
        finding = Finding(
            "Permission behavior is incomplete",
            "Roles are described without denial behavior.",
            "P2",
            "Define data scope and denial responses.",
            self.support,
            ("permissions",),
        )
        dimension = DimensionVerdict(
            "permissions",
            "violated",
            "Denial behavior is missing.",
            confidence=Confidence("high"),
            support=self.support,
            findings=(finding,),
        )
        candidate = CandidateDisposition(
            "confirmed",
            "The candidate is supported.",
            support=self.support,
            finding=finding,
        )
        relationship = RelationshipVerdict(
            "role-to-data-scope",
            "confirmed",
            "The relationship is explicit.",
            support=self.support,
        )

        self.assertEqual(dimension.as_dict()["verdict"], "violated")
        self.assertEqual(
            dimension.as_dict()["findings"][0]["affectedDimensions"],
            ["permissions"],
        )
        self.assertEqual(candidate.as_dict()["finding"]["severity"], "P2")
        self.assertEqual(relationship.as_dict()["relationship"], "role-to-data-scope")

    def test_relational_invariants_are_declared_once_in_the_types(self):
        invalid = (
            lambda: DimensionVerdict(
                "permissions", "violated", "Missing.", support=EvidenceSupport(),
            ),
            lambda: DimensionVerdict(
                "permissions", "unresolved", "Unknown.",
            ),
            lambda: DimensionVerdict(
                "permissions", "satisfied", "Satisfied.", support=self.support,
                findings=(Finding(
                    "Unexpected issue", "A problem was asserted.", "P2",
                    "Correct the problem.", self.support, ("permissions",),
                ),),
            ),
            lambda: DimensionVerdict(
                "permissions", "violated", "Missing.", support=self.support,
                findings=(Finding(
                    "Wrong dimension", "A problem was asserted.", "P2",
                    "Correct the problem.", self.support, ("acceptance",),
                ),),
            ),
            lambda: CandidateDisposition(
                "confirmed", "Confirmed without a finding.", support=self.support,
            ),
            lambda: CandidateDisposition(
                "merged", "Merged without a target.",
            ),
            lambda: CandidateDisposition(
                "needs_review", "No unresolved explanation.",
            ),
            lambda: RelationshipVerdict(
                "role-to-data-scope", "unknown", "Unknown without details.",
            ),
            lambda: RelationshipVerdict(
                "role-to-data-scope", "not_applicable", "N/A.",
            ),
        )
        for factory in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises(PlatformContractError) as rejected:
                    factory()
                self.assertEqual(rejected.exception.code, "INVALID_COMMON_REVIEW")

    def test_unknown_and_escalation_are_first_class_candidate_outcomes(self):
        unknown = CandidateDisposition(
            "needs_review",
            "Source coverage is incomplete.",
            unknown=Unknown("Permission section was truncated.", ("full permission section",)),
        )
        escalation = CandidateDisposition(
            "needs_review",
            "A human owner must confirm the policy.",
            escalation=Escalation("Ownership is unclear.", "Ask the policy owner."),
        )

        self.assertIn("unknown", unknown.as_dict())
        self.assertIn("escalation", escalation.as_dict())


class CommonReviewSubmissionTests(unittest.TestCase):
    def submission(self) -> CommonReviewSubmission:
        support = EvidenceSupport(("R1",), "Direct source support.")
        return CommonReviewSubmission((
            CommonReviewDecision("I1", DimensionVerdict(
                "permissions", "satisfied", "The rule is explicit.", support=support,
            )),
            CommonReviewDecision("I2", RelationshipVerdict(
                "role-to-data-scope", "confirmed", "The mapping is explicit.", support=support,
            )),
        ))

    def test_submission_round_trips_through_the_strict_parser(self):
        submission = self.submission()

        restored = CommonReviewSubmission.from_dict(submission.as_dict())
        restored.validate_task_membership(
            expected_item_refs=("I1", "I2"), allowed_evidence_refs=("R1",),
        )

        self.assertEqual(restored.as_dict(), submission.as_dict())

    def test_task_membership_is_exactly_once_and_evidence_is_task_local(self):
        submission = self.submission()
        cases = (
            (("I1", "I2", "I3"), ("R1",), "INCOMPLETE_REVIEW_SUBMISSION"),
            (("I1",), ("R1",), "UNKNOWN_REVIEW_ITEM"),
            (("I1", "I2"), ("R2",), "UNKNOWN_REVIEW_EVIDENCE"),
        )
        for expected, evidence, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(PlatformContractError) as rejected:
                    submission.validate_task_membership(
                        expected_item_refs=expected,
                        allowed_evidence_refs=evidence,
                    )
                self.assertEqual(rejected.exception.code, code)

    def test_platform_identity_and_unknown_fields_are_not_part_of_submission(self):
        value = self.submission().as_dict()
        value["runId"] = "run:forbidden"
        with self.assertRaises(PlatformContractError) as root_field:
            CommonReviewSubmission.from_dict(value)
        self.assertEqual(root_field.exception.code, "INVALID_COMMON_REVIEW")

        value = self.submission().as_dict()
        value["decisions"][0]["atomId"] = "review-atom:forbidden"
        with self.assertRaises(PlatformContractError) as decision_field:
            CommonReviewSubmission.from_dict(value)
        self.assertEqual(decision_field.exception.code, "INVALID_COMMON_REVIEW")

    def test_evidence_and_missing_information_cannot_contain_duplicates(self):
        for factory in (
            lambda: EvidenceSupport(("R1", "R1")),
            lambda: Unknown("Missing.", ("owner", "owner")),
        ):
            with self.subTest(factory=factory):
                with self.assertRaises(PlatformContractError) as rejected:
                    factory()
                self.assertEqual(rejected.exception.code, "INVALID_COMMON_REVIEW")


if __name__ == "__main__":
    unittest.main()
