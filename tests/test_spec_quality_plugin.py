import tempfile
import unittest
from pathlib import Path

from assayer_host import InteractivePlatformMcpToolTransport, ProductMcpToolTransport
from assayer_platform import PlatformContext
from assayer_platform.builtin_plugins import builtin_plugin_registry
from assayer_platform.builtin_plugins.spec_quality import SpecQualityDecisionCommitter, SpecQualityPlugin
from assayer_platform import DecisionProposal, Finding


COMPLETE_SPEC = """# Product Spec: Example

## 1. Module Definition
The module provides an example capability.

## 2. State Model
The example has draft and active states.

## 3. Functional Requirements
FR-001 defines the observable example behavior.
AC-FR001-01 defines a successful observable result.
CASE-01: Given a valid example, when it runs, then the result is returned.

## 4. Key Entities
The Example entity owns the example identity.

## 5. Data Fields
The identifier is a required string with a maximum length of 64 characters.

## 6. Non-functional Requirements
NFR-GEN-001 is adopted and verified by contract tests.

## 7. Success Criteria
SC-01 requires every accepted example to return one result.

## 8. References and Compliance
The project governance document is the selected authority.

## 9. Key Decisions
D-01 selects one result per accepted example.

## 10. Dependencies and Assumptions
The input source must be available; otherwise the operation fails explicitly.

## 11. Stage Differences
The first stage includes FR-001.

## 12. Revision History
Version 1.0 was created for the initial review.
"""


class SpecQualityPluginTest(unittest.TestCase):
    def test_incomplete_spec_returns_candidate_evidence_for_agent_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Example\n\nThe system should be good and respond as soon as possible.\n", encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-spec-inspect", frozenset({"structured_read"}))
            item = plugin.discover({"files": [{"path": str(path)}]}, context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            evidence = packet.evidence[0].payload
            self.assertTrue(any(candidate["ruleId"] == "CHAPTER-001" for candidate in evidence["candidates"]))
            self.assertTrue(any(candidate["ruleId"] == "FR-006" for candidate in evidence["candidates"]))
            self.assertEqual(len(evidence["checklistResults"]), 18)
            self.assertIn("unresolved", {dimension.candidate_status for dimension in packet.dimensions})

    def test_spec_plugin_completes_generic_interactive_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=builtin_plugin_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            decisions = [{
                "workItemId": packet["workItem"]["workItemId"],
                "result": "scanned_no_issue",
                "reason": "The reviewed Spec satisfies all required dimensions.",
                "findings": [{
                    "dimension": dimension["name"], "status": "satisfied",
                    "reason": dimension["observations"][0],
                } for dimension in packet["dimensions"]],
            }]
            transport.call_tool("submit_decisions", {"decisions": decisions})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(finished["structuredContent"]["result"]["status"], "completed")
            self.assertTrue((Path(directory) / "output" / run_id / f"{run_id}.platform-ledger.json").is_file())

    def test_product_mcp_connection_exposes_spec_interactive_tools(self):
        class UnusedFrontendCore:
            def handle(self, request):
                raise AssertionError(f"Frontend core should not be used: {request}")

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            transport = ProductMcpToolTransport(UnusedFrontendCore(), output_root=Path(directory) / "output")
            try:
                names = {item["name"] for item in transport.list_tools()}
                self.assertIn("start_plugin_run", names)
                started = transport.call_tool("start_plugin_run", {
                    "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                    "scope": {"files": [{"path": str(path)}]},
                })
                self.assertEqual(started["structuredContent"]["status"], "ok")
                discovered = transport.call_tool("discover_work_items", {})
                self.assertEqual(len(discovered["structuredContent"]["result"]["result"]["workItems"]), 1)
            finally:
                transport.close()

    def test_spec_result_summary_is_candidate_until_semantic_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Example\n\nThe system should be good.\n", encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=builtin_plugin_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            payload = packet["evidence"][0]["payload"]
            self.assertEqual(payload["authorityVersion"], "1.2.0")
            self.assertTrue(payload["candidateOnly"])
            self.assertEqual(payload["readiness"]["status"], "UNVERIFIED")
            item_id = packet["workItem"]["workItemId"]
            findings = [{"dimension": item["name"], "status": "satisfied", "reason": "Reviewed."} for item in packet["dimensions"]]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": item_id, "result": "scanned_no_issue",
                "findings": findings, "reason": "Compatibility test decision.",
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            result = finished["structuredContent"]["result"]["result"]
            summary = result["summary"]
            self.assertEqual(summary["phase"], "CANDIDATE")
            self.assertEqual(summary["readiness"]["status"], "UNVERIFIED")
            self.assertGreater(summary["review"]["candidateCount"], 0)
            self.assertEqual(summary["review"]["handledCandidateCount"], 0)
            self.assertEqual(summary["review"]["pendingCandidateCount"], summary["review"]["candidateCount"])
            self.assertEqual(result["artifacts"], [])
            self.assertFalse((Path(directory) / "output" / run_id / "report.html").exists())

    def test_spec_result_summary_presents_reviewed_findings_and_checklist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-spec-summary", frozenset({"structured_read"}))
            item = plugin.discover({"files": [{"path": str(path)}]}, context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            summary = plugin.summarize((item,), (packet,), (), "completed")
            self.assertEqual(summary["phase"], "CANDIDATE")
            self.assertEqual(summary["review"]["checklist"]["total"], 18)
            self.assertEqual(summary["review"]["confirmedFindings"], [])

    def test_interactive_finish_returns_reviewed_result_summary_without_html(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=builtin_plugin_registry(),
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            candidate_ids = [item["candidate_id"] for item in packet["evidence"][0]["payload"]["candidateFindings"]]
            review = {
                "review_schema_version": "1.0.0",
                "readiness_context": {
                    "mandatory_dimensions_checked": True,
                    "unresolved_blockers": False,
                    "escalations": [],
                    "checklist_review": [
                        {"check_id": f"CHK-{index:02d}", "status": "PASS", "note": "Reviewed and satisfied."}
                        for index in range(1, 19)
                    ],
                },
                "decisions": [
                    {
                        "finding_id": f"review-{candidate_id}", "status": "SUPPRESSED",
                        "candidate_ids": [candidate_id], "review_note": "The scanner signal is not a material Spec finding.",
                    }
                    for candidate_id in candidate_ids
                ],
            }
            item_id = packet["workItem"]["workItemId"]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": item_id, "result": "scanned_no_issue",
                "reason": "All candidate signals were reviewed and suppressed.",
                "findings": [
                    {"dimension": f"CHK-{index:02d}", "status": "satisfied", "reason": "Reviewed and satisfied."}
                    for index in range(1, 19)
                ],
                "details": {"review": review},
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            result = finished["structuredContent"]["result"]["result"]
            summary = result["summary"]
            self.assertEqual(summary["phase"], "REVIEWED")
            self.assertEqual(summary["readiness"]["status"], "READY")
            self.assertEqual(summary["review"]["statusCounts"]["SUPPRESSED"], len(candidate_ids))
            self.assertEqual(summary["review"]["checklist"]["statusCounts"]["PASS"], 18)
            self.assertEqual(summary["review"]["confirmedFindings"], [])
            self.assertEqual(result["artifacts"], [])
            self.assertFalse((Path(directory) / "output" / "report.html").exists())

    def test_formal_issue_requires_canonical_review_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Example\n", encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-spec-review", frozenset({"structured_read"}))
            item = plugin.discover({"files": [{"path": str(path)}]}, context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            proposal = DecisionProposal(
                item.work_item_id, "SPEC-001", "1.0.0", "issue_found",
                tuple(Finding(d.name, "violated", "Candidate requires review.") for d in packet.dimensions),
                "Issue found without review envelope.",
            )
            with self.assertRaisesRegex(Exception, "canonical review envelope"):
                SpecQualityDecisionCommitter().commit(proposal, packet, plugin.manifest.checks[0], context)


if __name__ == "__main__":
    unittest.main()
