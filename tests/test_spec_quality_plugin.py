import json
import tempfile
import unittest
from pathlib import Path

from assayer_host import HostError, InteractivePlatformMcpToolTransport, ProductMcpToolTransport
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
    def test_spec_plugin_handles_structural_tables_without_header_name_error(self):
        """A table-bearing Spec must produce candidates instead of crashing inspection."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(
                """# Example

## State Model
The state model is documented here.

| State | Description |
| --- | --- |
| draft | Initial state |

## Dependencies and Assumptions
The API dependency is documented here.

| Dependency | Requirement | Fallback |
| --- | --- | --- |
| API | Data | Return an error |

## Key Decisions
The ADR decision is documented here.

| Decision ID | Outcome |
| --- | --- |
| D-01 | Use the API |

## Functional Requirements
FR-001 defines the API behavior.
AC-001 defines the outcome.
CASE-001 covers the normal path.

The ABC acronym is defined by context.
""",
                encoding="utf-8",
            )
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-spec-table-regression", frozenset({"structured_read"}))
            item = plugin.discover({"files": [{"path": str(path)}]}, context)[0]

            packets = plugin.inspect((item,), plugin.manifest.checks[0], context)

            self.assertEqual(len(packets), 1)
            self.assertGreater(len(packets[0].evidence), 0)

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
            inspected = transport.call_tool("inspect_work_items", {"includeEvidence": True})
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
                advanced = transport.call_tool("advance_plugin_run", {})
                result = advanced["structuredContent"]["result"]
                self.assertEqual(result["status"], "awaiting_agent_decision")
                self.assertEqual(result["result"]["workflow"]["requiredNextStep"], "advance_plugin_run")
                self.assertEqual(result["result"]["semanticTask"]["kind"], "review_evidence_items")
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
            inspected = transport.call_tool("inspect_work_items", {"includeEvidence": True})
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
            self.assertEqual(result["artifacts"], ["result-summary.json"])
            result_path = Path(directory) / "output" / run_id / result["artifacts"][0]
            self.assertTrue(result_path.is_file())
            complete_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(complete_result["result"]["summary"]["review"]["candidateCount"], summary["review"]["candidateCount"])
            self.assertEqual(complete_result["sourceDigest"], result["resultDelivery"]["sourceDigest"])
            self.assertFalse((Path(directory) / "output" / run_id / "report.html").exists())

    def test_spec_candidates_use_generic_summary_first_collection_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Example\n\nThe system should be good.\n", encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=builtin_plugin_registry(),
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            result = inspected["structuredContent"]["result"]["result"]
            packet = result["investigations"][0]
            self.assertEqual(packet["evidence"], [])
            work_item_id = packet["workItem"]["workItemId"]
            collection = result["evidenceCollectionIndex"][work_item_id][0]
            self.assertEqual(collection["collectionId"], "candidate-findings")
            self.assertEqual(collection["itemCount"], packet["metadata"]["candidateCount"])
            self.assertEqual(collection["groupBy"], ["rule_id"])

            seen_ids = []
            for group in collection["groups"]:
                cursor = None
                while True:
                    arguments = {
                        "workItemId": work_item_id,
                        "collectionId": "candidate-findings",
                        "groupKey": group["groupKey"],
                        "pageSize": 2,
                    }
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    page = transport.call_tool(
                        "expand_evidence_collection", arguments,
                    )["structuredContent"]["result"]["result"]
                    self.assertTrue(all(
                        item["rule_id"] == group["values"]["rule_id"] for item in page["items"]
                    ))
                    seen_ids.extend(page["itemIds"])
                    cursor = page["nextCursor"]
                    if cursor is None:
                        break
            self.assertEqual(len(seen_ids), collection["itemCount"])
            self.assertEqual(len(set(seen_ids)), collection["itemCount"])
            transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_spec_review_checkpoints_assemble_without_resending_all_candidates(self):
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
            result = inspected["structuredContent"]["result"]["result"]
            packet = result["investigations"][0]
            self.assertEqual(packet["evidence"], [])
            work_item_id = packet["workItem"]["workItemId"]
            collection = result["evidenceCollectionIndex"][work_item_id][0]
            self.assertGreater(collection["itemCount"], 0)
            checkpoint_ids = []
            reviewed_items = 0
            for group in collection["groups"]:
                cursor = None
                while True:
                    arguments = {
                        "workItemId": work_item_id,
                        "collectionId": "candidate-findings",
                        "groupKey": group["groupKey"],
                        "pageSize": 3,
                    }
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    page = transport.call_tool(
                        "expand_evidence_collection", arguments,
                    )["structuredContent"]["result"]["result"]
                    reviewed_items += len(page["itemIds"])
                    checkpoint = transport.call_tool("checkpoint_review", {
                        "workItemId": work_item_id,
                        "collectionId": "candidate-findings",
                        "itemIds": page["itemIds"],
                        "payload": {"decisions": [{
                            "finding_id": f"checkpoint-{len(checkpoint_ids) + 1}",
                            "status": "SUPPRESSED",
                            "candidate_ids": page["itemIds"],
                            "review_note": "The reviewed scanner signals are not material Spec findings.",
                        }]},
                    })["structuredContent"]["result"]["result"]
                    checkpoint_ids.append(checkpoint["checkpointId"])
                    cursor = page["nextCursor"]
                    if cursor is None:
                        break
            self.assertEqual(reviewed_items, collection["itemCount"])
            checklist = [
                {"check_id": f"CHK-{index:02d}", "status": "PASS", "note": "Reviewed and satisfied."}
                for index in range(1, 19)
            ]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id,
                "result": "scanned_no_issue",
                "reason": "Every candidate page and checklist dimension was reviewed.",
                "findings": [
                    {"dimension": item["check_id"], "status": "satisfied", "reason": item["note"]}
                    for item in checklist
                ],
                "reviewCheckpointIds": checkpoint_ids,
                "finalization": {"readiness_context": {
                    "mandatory_dimensions_checked": True,
                    "unresolved_blockers": False,
                    "escalations": [],
                    "checklist_review": checklist,
                }},
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            summary = finished["structuredContent"]["result"]["result"]["summary"]
            self.assertEqual(summary["phase"], "REVIEWED")
            self.assertEqual(summary["readiness"]["status"], "READY")
            self.assertEqual(summary["review"]["handledCandidateCount"], collection["itemCount"])

    def test_invalid_spec_checkpoint_never_enters_the_durable_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {"includeEvidence": True})[
                "structuredContent"
            ]["result"]["result"]
            packet = inspected["investigations"][0]
            work_item_id = packet["workItem"]["workItemId"]
            candidate = next(
                item for item in packet["evidence"][0]["payload"]["candidateFindings"]
                if item.get("evidence")
            )
            candidate_id = candidate["candidate_id"]

            invalid_payloads = (
                {"decisions": [{
                    "finding_id": "finding-wrong-candidate", "status": "SUPPRESSED",
                    "candidate_ids": ["candidate:not-in-page"],
                    "review_note": "This invalid reference must be rejected before persistence.",
                }]},
                {"decisions": [{
                    "finding_id": "finding-untraceable", "status": "CONFIRMED",
                    "severity": "P2", "object_id": candidate.get("object_id") or "Spec section",
                    "dimension": "traceability", "gap": "A concrete requirement is missing.",
                    "impact": "Implementation could diverge.",
                    "recommendation": "Add the missing requirement.",
                    "closure_evidence": "A direct requirement is present.",
                    "candidate_ids": [candidate_id], "merged_into": None,
                    "evidence": ["This text is not candidate source evidence."],
                    "review_note": None,
                }]},
            )
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(HostError) as rejected:
                        transport.call_tool("checkpoint_review", {
                            "workItemId": work_item_id,
                            "collectionId": "candidate-findings",
                            "itemIds": [candidate_id],
                            "payload": payload,
                        })
                    self.assertEqual(rejected.exception.code, "SPEC_REVIEW_INVALID")
                    ledger = json.loads(
                        (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(ledger["review_checkpoints"], [])
                    self.assertFalse(any(
                        item["kind"] == "review_checkpoint" for item in ledger["operations"]
                    ))

            accepted = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_id,
                "collectionId": "candidate-findings",
                "itemIds": [candidate_id],
                "payload": {"decisions": [{
                    "finding_id": "finding-valid", "status": "SUPPRESSED",
                    "candidate_ids": [candidate_id],
                    "review_note": "The candidate was reviewed and is not a material finding.",
                }]},
            })["structuredContent"]["result"]["result"]
            self.assertFalse(accepted["replayed"])
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 1)

            second_candidate = next(
                item for item in packet["evidence"][0]["payload"]["candidateFindings"]
                if item["candidate_id"] != candidate_id
            )
            with self.assertRaises(HostError) as duplicate_finding:
                transport.call_tool("checkpoint_review", {
                    "workItemId": work_item_id,
                    "collectionId": "candidate-findings",
                    "itemIds": [second_candidate["candidate_id"]],
                    "payload": {"decisions": [{
                        "finding_id": "finding-valid", "status": "SUPPRESSED",
                        "candidate_ids": [second_candidate["candidate_id"]],
                        "review_note": "A duplicate Finding identity must not become durable.",
                    }]},
                })
            self.assertEqual(duplicate_finding.exception.code, "SPEC_REVIEW_INVALID")
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 1)

    def test_advance_rejects_bad_checkpoint_and_reoffers_the_same_review_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            task = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            candidate = next(item for item in task["items"] if item.get("evidence"))
            candidate_id = candidate["candidate_id"]

            with self.assertRaises(HostError) as rejected:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": task["workItemId"],
                        "collectionId": task["collectionId"],
                        "itemIds": [candidate_id],
                        "payload": {"decisions": [{
                            "finding_id": "finding-invalid-evidence", "status": "CONFIRMED",
                            "severity": "P2", "object_id": candidate.get("object_id") or "Spec section",
                            "dimension": "traceability", "gap": "A requirement is missing.",
                            "impact": "Implementation could diverge.",
                            "recommendation": "Add the requirement.",
                            "closure_evidence": "The requirement is present.",
                            "candidate_ids": [candidate_id], "merged_into": None,
                            "evidence": ["Not a direct excerpt from this candidate."],
                            "review_note": None,
                        }]},
                    },
                })
            self.assertEqual(rejected.exception.code, "SPEC_REVIEW_INVALID")

            resumed = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            self.assertEqual(resumed["kind"], "review_evidence_items")
            self.assertEqual(resumed["workItemId"], task["workItemId"])
            self.assertEqual(resumed["collectionId"], task["collectionId"])
            self.assertIn(candidate_id, resumed["itemIds"])
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger["review_checkpoints"], [])

    def test_spec_review_resumes_from_checkpoint_after_transport_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            output = Path(directory) / "output"
            first_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            started = first_transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })["structuredContent"]["result"]
            task = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            candidate = task["items"][0]
            accepted = first_transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": task["workItemId"], "collectionId": task["collectionId"],
                    "itemIds": [candidate["candidate_id"]],
                    "payload": {"decisions": [{
                        "finding_id": "resume-reviewed-candidate", "status": "SUPPRESSED",
                        "candidate_ids": [candidate["candidate_id"]],
                        "review_note": "The scanner signal is not a material Spec finding.",
                    }]},
                },
            })["structuredContent"]["result"]

            resumed_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            resumed = resumed_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(resumed["runId"], started["runId"])
            self.assertEqual(resumed["runRevision"], accepted["runRevision"])
            self.assertNotIn(candidate["candidate_id"], resumed["result"]["semanticTask"]["itemIds"])
            ledger = json.loads(
                (output / started["runId"] / f"{started['runId']}.platform-ledger.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 1)

    def test_spec_checkpoint_correction_can_reuse_replaced_finding_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path)}]},
            })["structuredContent"]["result"]
            task = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            candidate_id = task["itemIds"][0]
            original = transport.call_tool("checkpoint_review", {
                "workItemId": task["workItemId"],
                "collectionId": task["collectionId"],
                "itemIds": [candidate_id],
                "payload": {"decisions": [{
                    "finding_id": "finding-correctable",
                    "status": "SUPPRESSED",
                    "candidate_ids": [candidate_id],
                    "review_note": "The first semantic review suppressed this candidate.",
                }]},
            })["structuredContent"]["result"]["result"]
            correction = transport.call_tool("checkpoint_review", {
                "workItemId": task["workItemId"],
                "collectionId": task["collectionId"],
                "itemIds": [candidate_id],
                "payload": {"decisions": [{
                    "finding_id": "finding-correctable",
                    "status": "UNVERIFIED",
                    "candidate_ids": [candidate_id],
                    "review_note": "The corrected review retains the identity but changes its disposition.",
                }]},
                "supersedesCheckpointId": original["checkpointId"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(
                correction["supersedesCheckpointId"], original["checkpointId"],
            )
            ledger = json.loads(
                (output / started["runId"] / f"{started['runId']}.platform-ledger.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 2)
            self.assertEqual(
                ledger["review_checkpoints"][1]["payload"]["decisions"][0]["finding_id"],
                "finding-correctable",
            )

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
            inspected = transport.call_tool("inspect_work_items", {"includeEvidence": True})
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
            self.assertEqual(len(result["artifacts"]), 1)
            self.assertEqual(result["artifacts"][0], "result-summary.json")
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
