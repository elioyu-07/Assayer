from __future__ import annotations

import unittest

from assayer_platform import (
    EvidenceCandidate,
    EvidenceGraph,
    FindingRecord,
    PlatformContractError,
    RootCauseGroup,
    build_candidate_evidence_graph,
    conservative_root_cause_groups,
    render_candidate_evidence_graph,
    validate_candidate_evidence_graph_projection,
    EvidenceCollectionPager,
)


class EvidenceGraphTest(unittest.TestCase):
    def candidates(self):
        return (
            EvidenceCandidate("candidate:1", "item:1", "CHECK-1", "1.0.0", "fp-a", ("evidence:1",), "confirmed"),
            EvidenceCandidate("candidate:2", "item:1", "CHECK-1", "1.0.0", "fp-a", ("evidence:1",), "merged"),
            EvidenceCandidate("candidate:3", "item:1", "CHECK-1", "1.0.0", "fp-b", ("evidence:2",), "suppressed"),
        )

    def test_conservative_grouping_requires_exact_identity_and_fingerprint(self):
        groups = conservative_root_cause_groups(self.candidates())
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].candidate_ids, ("candidate:1", "candidate:2"))

    def test_graph_preserves_candidate_coverage_and_multi_dimension_finding(self):
        group = RootCauseGroup("group:1", ("candidate:1", "candidate:2"), ("evidence:1",), ("CHK-02", "CHK-05"))
        finding = FindingRecord("finding:1", "group:1", "issue_found", ("CHK-02", "CHK-05"), ("evidence:1",), "One root cause affects two dimensions.")
        graph = EvidenceGraph(self.candidates(), (group,), (finding,), frozenset({"evidence:1", "evidence:2"}))
        self.assertTrue(graph.coverage_complete)
        self.assertEqual(graph.pending_candidate_ids, frozenset())

    def test_pending_candidate_blocks_coverage(self):
        candidate = EvidenceCandidate("candidate:pending", "item:1", "CHECK-1", "1.0.0", "fp", ("evidence:1",))
        graph = EvidenceGraph((candidate,), (), (), frozenset({"evidence:1"}))
        self.assertFalse(graph.coverage_complete)
        self.assertEqual(graph.pending_candidate_ids, frozenset({"candidate:pending"}))

    def test_group_cannot_claim_unknown_candidate(self):
        with self.assertRaises(PlatformContractError) as error:
            EvidenceGraph((), (RootCauseGroup("group:1", ("candidate:missing",)),), ())
        self.assertEqual(error.exception.code, "INVALID_ROOT_CAUSE_GROUP")

    def test_candidate_cannot_be_in_two_groups(self):
        candidate = EvidenceCandidate("candidate:1", "item:1", "CHECK-1", "1.0.0", "fp", ("evidence:1",), "merged")
        with self.assertRaises(PlatformContractError):
            EvidenceGraph((candidate,), (RootCauseGroup("group:1", ("candidate:1",)), RootCauseGroup("group:2", ("candidate:1",))), ())

    def test_mapping_adapter_preserves_members_and_projects_review_coverage(self):
        candidates = [
            {"candidate_id": "candidate:1", "rule_id": "RULE-1", "object_id": "obj",
             "line": 10, "message": "same", "evidence": "source excerpt"},
            {"candidate_id": "candidate:2", "rule_id": "RULE-1", "object_id": "obj",
             "line": 10, "message": "same", "evidence": "source excerpt"},
        ]
        decisions = [
            {"finding_id": "finding:1", "status": "CONFIRMED", "dimension": "CHK-02",
             "candidate_ids": ["candidate:1"]},
            {"finding_id": "finding:2", "status": "MERGED", "dimension": "CHK-05",
             "candidate_ids": ["candidate:2"]},
        ]
        graph = build_candidate_evidence_graph(
            candidates, work_item_id="item:1", check_id="CHECK-1", check_version="1.0.0",
            decisions=decisions,
        )
        self.assertTrue(graph.coverage_complete)
        self.assertEqual({item.candidate_id for item in graph.candidates}, {"candidate:1", "candidate:2"})
        self.assertEqual(graph.groups[0].affected_dimensions, ("CHK-02", "CHK-05"))
        self.assertEqual(render_candidate_evidence_graph(graph)["coveredCandidateCount"], 2)

    def test_mapping_adapter_keeps_unreviewed_candidates_pending(self):
        graph = build_candidate_evidence_graph(
            [{"candidateId": "candidate:pending", "ruleId": "RULE-1", "message": "signal"}],
            work_item_id="item:1", check_id="CHECK-1", check_version="1.0.0",
        )
        self.assertFalse(graph.coverage_complete)
        self.assertEqual(render_candidate_evidence_graph(graph)["pendingCandidateIds"], ["candidate:pending"])

    def test_portable_projection_validator_rejects_inconsistent_coverage(self):
        with self.assertRaises(PlatformContractError) as error:
            validate_candidate_evidence_graph_projection({
                "candidateCount": 1, "coveredCandidateCount": 1,
                "coverageComplete": True, "pendingCandidateIds": ["candidate:1"],
                "rootCauseGroups": [],
            })
        self.assertEqual(error.exception.code, "INVALID_EVIDENCE_GRAPH")

    def test_evidence_collection_pager_preserves_stable_cursor(self):
        pager = EvidenceCollectionPager({
            "itemIds": ["candidate:1", "candidate:2"],
            "items": [{"id": 1}, {"id": 2}], "groups": {},
        }, work_item_id="item:1", collection_id="candidate-findings")
        first = pager.page(page_size=1)
        second = pager.page(cursor=first["nextCursor"], page_size=1)
        self.assertEqual(first["itemIds"], ["candidate:1"])
        self.assertEqual(second["itemIds"], ["candidate:2"])
        with self.assertRaises(PlatformContractError):
            pager.page(cursor="wrong:1", page_size=1)


if __name__ == "__main__":
    unittest.main()
