import unittest

from assayer_platform import (
    PlatformContractError, build_review_task, validate_review_submission,
)


class ReviewProtocolTest(unittest.TestCase):
    def test_task_and_submission_cover_candidates_once(self):
        task = build_review_task(
            work_item_id="item:1", check_id="CHECK-1", check_version="1.0.0",
            collection_id="candidates", item_ids=("candidate:1", "candidate:2"),
            evidence_refs=("evidence:1",),
        )
        self.assertEqual(task["itemIds"], ["candidate:1", "candidate:2"])
        decisions = validate_review_submission({"decisions": [
            {"candidateIds": ["candidate:1"], "disposition": "confirmed", "evidenceRefs": ["evidence:1"]},
            {"candidateIds": ["candidate:2"], "disposition": "suppressed", "evidenceRefs": ["evidence:1"]},
        ]}, expected_item_ids=task["itemIds"], allowed_evidence_refs=task["evidenceRefs"])
        self.assertEqual(len(decisions), 2)

    def test_submission_rejects_missing_or_foreign_candidate(self):
        with self.assertRaises(PlatformContractError) as incomplete:
            validate_review_submission(
                {"decisions": [{"candidateIds": ["candidate:1"], "disposition": "confirmed"}]},
                expected_item_ids=("candidate:1", "candidate:2"),
            )
        self.assertEqual(incomplete.exception.code, "INCOMPLETE_REVIEW_SUBMISSION")
        with self.assertRaises(PlatformContractError) as foreign:
            validate_review_submission(
                {"decisions": [{"candidateIds": ["candidate:x"], "disposition": "confirmed"}]},
                expected_item_ids=("candidate:1",),
            )
        self.assertEqual(foreign.exception.code, "INVALID_REVIEW_SUBMISSION")


if __name__ == "__main__":
    unittest.main()
