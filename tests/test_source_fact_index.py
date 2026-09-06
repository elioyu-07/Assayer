"""Contract tests for the domain-neutral source-fact index and actionable result."""

from __future__ import annotations

import re
import unittest

from assayer_platform import (
    build_actionable_result,
    build_source_chunks,
    build_source_fact_index,
)


class SourceFactIndexTest(unittest.TestCase):
    def test_index_is_domain_neutral_with_custom_patterns(self):
        text = "# Config\n\nKEY_NAME is defined.\nKEY_PORT is defined.\n"
        chunks = build_source_chunks(text, path="config.md", source_digest="c" * 64)
        index = build_source_fact_index(
            text,
            chunks,
            identifier_patterns={"keys": re.compile(r"\bKEY[-_][A-Z0-9-]+\b", re.I)},
            explicit_patterns={},
            heading_units=[],
        )
        self.assertEqual(
            [item["id"] for item in index["identifiers"]["keys"]],
            ["KEY-NAME", "KEY-PORT"],
        )
        self.assertTrue(all(item["sourceRef"] is not None for item in index["identifiers"]["keys"]))

    def test_index_deduplicates_and_attaches_line_references(self):
        text = "FR-001 repeats FR-001 twice.\n"
        chunks = build_source_chunks(text, path="spec.md", source_digest="d" * 64)
        index = build_source_fact_index(
            text,
            chunks,
            identifier_patterns={"functional": re.compile(r"\bFR[-_][A-Z0-9-]+\b", re.I)},
            explicit_patterns={},
            heading_units=[],
        )
        self.assertEqual(len(index["identifiers"]["functional"]), 1)
        self.assertEqual(index["identifiers"]["functional"][0]["line"], 1)


class ActionableResultBuilderTest(unittest.TestCase):
    def test_builder_projects_confirmed_decisions(self):
        decisions = [{
            "status": "CONFIRMED",
            "dimension": "DIM-12",
            "affected_elements": ["FR-001"],
            "resolution_owner": "Spec owner",
            "recommendation": "Add the failure CASE.",
            "next_action": "Add the failure CASE.",
            "gap": "The acceptance contract does not define a failure expectation.",
            "finding_id": "strict-finding",
            "line": 1,
            "severity": "P2",
            "impact": "A failing implementation can pass review.",
            "closure_evidence": "The failure CASE is mapped to FR-001.",
            "evidence": "FR-001 defines the observable example behavior.",
        }]
        delivery = build_actionable_result(
            decisions,
            {"DIM-12": "REWORK"},
            {"DIM-12": ["evidence:1"]},
            evidence_id="evidence:1",
            source_chunks=({"end_line": 10},),
            work_item_identity="work:1",
            document_path="/spec.md",
            confirmed_status="CONFIRMED",
            actionable_statuses=frozenset({"REWORK", "ESCALATE"}),
            absence_pattern=re.compile(r"\bmissing\b", re.I),
        )
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["remediations"][0]["remediationId"], "strict-finding")
        self.assertEqual(delivery["remediations"][0]["dimensions"], ["DIM-12"])
        self.assertEqual(delivery["evidenceClaims"][0]["kind"], "direct")


if __name__ == "__main__":
    unittest.main()
