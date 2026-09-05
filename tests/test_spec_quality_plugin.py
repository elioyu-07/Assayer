import copy
import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from assayer_host import HostCore, HostError, InteractivePlatformMcpToolTransport, ProductMcpToolTransport
from assayer_platform import PlatformContext, PlatformContractError
from assayer_platform.builtin_plugins import builtin_plugin_registry
from assayer_platform.builtin_plugins.spec_quality import (
    SPEC_QUALITY_SCOPE_SCHEMA, SpecQualityDecisionCommitter, SpecQualityPlugin,
)
from assayer_platform.builtin_plugins.spec_quality.runtime import _actionable_result_delivery
from assayer_platform.builtin_plugins.spec_quality.review import (
    evaluate_review, validate_review_decisions,
)
from assayer_platform import DecisionProposal, Finding
from jsonschema import Draft202012Validator


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
    def test_inspection_exposes_document_context_and_source_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared.md"
            path.write_text(
                """> Document positioning: This global shared contract defines common asset rules.
> Child Specs define concrete feature requirements.

# Asset Model

## Shared Functional Requirements
FR-G01 defines the shared identifier rule.
FR-G08 defines transaction consistency.

## Assumptions
AS-006: The system starts from zero; no historical data migration is needed.
""",
                encoding="utf-8",
            )
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-context-facts", frozenset({"structured_read"}))
            item = plugin.discover(str(path), context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            payload = packet.evidence[0].payload
            document_context = payload["documentContext"]
            facts = payload["sourceFacts"]

        self.assertEqual(document_context["documentType"], "shared_contract")
        self.assertEqual(document_context["classificationConfidence"], "high")
        self.assertTrue(document_context["classificationEvidence"])
        self.assertTrue(document_context["responsibilityBoundaries"]["delegated"])
        identifiers = facts["identifiers"]
        self.assertEqual(
            {item["id"] for item in identifiers["functional_requirements"]},
            {"FR-G01", "FR-G08"},
        )
        migration = [
            item for item in facts["explicitStatements"] if "no_migration" in item["kinds"]
        ]
        self.assertEqual(len(migration), 1)
        self.assertEqual(migration[0]["line"], 11)

    def _strict_review_fixture(self, path: Path):
        plugin = SpecQualityPlugin()
        context = PlatformContext("run-strict-review", frozenset({"structured_read"}))
        item = plugin.discover(str(path), context)[0]
        packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
        payload = packet.evidence[0].payload
        candidates = payload["candidateFindings"]
        candidate_ids = [candidate["candidate_id"] for candidate in candidates]
        source_chunk = payload["sourceChunks"][0]
        evidence_ref = {
            key: source_chunk[key]
            for key in ("source_chunk_id", "document_path", "source_digest", "start_line", "end_line")
        }
        finding = {
            "finding_id": "strict-finding",
            "status": "CONFIRMED",
            "severity": "P2",
            "object_id": "FR-001",
            "dimension": "CHK-12",
            "gap": "The acceptance contract does not define a failure expectation for FR-001.",
            "observed_fact": "FR-001 has a success example but no failure expectation.",
            "impact": "A failing implementation can pass review without a defined boundary.",
            "recommendation": "Add a failure CASE with inputs and expected output.",
            "closure_evidence": "The failure CASE is mapped to FR-001 and can be executed.",
            "candidate_ids": [],
            "merged_into": None,
            "evidence": ["FR-001 defines the observable example behavior."],
            "review_note": None,
            "affected_elements": ["FR-001"],
            "owner": "Spec owner",
            "next_action": "Add the failure CASE.",
            "confidence": "high",
            "evidence_refs": [evidence_ref],
        }
        candidate_decision = {
            "finding_id": "strict-candidates",
            "status": "SUPPRESSED",
            "severity": None,
            "object_id": None,
            "dimension": None,
            "gap": "",
            "impact": "",
            "recommendation": "",
            "closure_evidence": None,
            "candidate_ids": candidate_ids,
            "merged_into": None,
            "evidence": [],
            "review_note": "Scanner candidates were reviewed and are not standalone findings.",
        }
        checklist = []
        for number in range(1, 19):
            check_id = f"CHK-{number:02d}"
            is_rework = check_id == "CHK-12"
            checklist.append({
                "check_id": check_id,
                "status": "REWORK" if is_rework else "PASS",
                "note": "A concrete source-bound gap was identified." if is_rework else "Positive evidence supports this dimension.",
                "applicability": "APPLICABLE",
                "observation": "The reviewed source contains the relevant contract." if not is_rework else "The source defines FR-001 but not its failure expectation.",
                "gap": "The acceptance contract does not define a failure expectation for FR-001." if is_rework else "No material gap was observed for this dimension.",
                "impact": "An implementation boundary remains undecidable." if is_rework else "No material impact was identified.",
                "recommendation": "Add a failure CASE with expected output." if is_rework else "Retain the current evidence.",
                "finding_refs": ["strict-finding"] if is_rework else [],
                "owner": "Spec owner",
                "next_action": "Add the failure CASE." if is_rework else "No action required.",
                "confidence": "high",
                "evidence_refs": [evidence_ref],
            })
        review = {
            "review_schema_version": "1.3.0",
            "document_context": {
                "document_type": "feature_spec",
                "scope_statement": "The document defines the example feature behavior and its acceptance contract.",
                "responsibility_boundaries": {"owned": ["feature behavior"], "delegated": [], "excluded": []},
                "related_documents": [],
                "selected_profile": "product-spec",
                "classification_evidence": [evidence_ref],
                "classification_confidence": "high",
                "unresolved_classification_questions": [],
            },
            "readiness_context": {
                "mandatory_dimensions_checked": True,
                "unresolved_blockers": False,
                "escalations": [],
                "checklist_review": checklist,
            },
            "decisions": [candidate_decision, finding],
        }
        proposal = DecisionProposal(
            item.work_item_id, packet.check_id, packet.check_version, "issue_found",
            tuple(Finding(
                f"CHK-{number:02d}", "violated" if number == 12 else "satisfied",
                "A concrete gap was found." if number == 12 else "The dimension is satisfied.",
            ) for number in range(1, 19)),
            "The Spec requires one acceptance-contract correction.",
            {"review": review},
        )
        return proposal, packet

    def test_v13_review_requires_finding_for_rework_and_preserves_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            proposal, packet = self._strict_review_fixture(path)
            report = evaluate_review(proposal, packet)
        self.assertEqual(report["document_context"]["document_type"], "feature_spec")
        self.assertEqual(report["readiness"]["status"], "REWORK")
        self.assertEqual(tuple(report["readiness_context"]["checklist_review"][11]["finding_refs"]), ("strict-finding",))

    def test_v13_review_rejects_orphan_rework(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            proposal, packet = self._strict_review_fixture(path)
            def mutable(value):
                if isinstance(value, Mapping):
                    return {key: mutable(item) for key, item in value.items()}
                if isinstance(value, (tuple, list)):
                    return [mutable(item) for item in value]
                return value

            review = mutable(proposal.details["review"])
            review["readiness_context"]["checklist_review"][11]["finding_refs"] = []
            invalid = DecisionProposal(
                proposal.work_item_id, proposal.check_id, proposal.check_version,
                proposal.result, proposal.findings, proposal.reason, {"review": review},
            )
            with self.assertRaisesRegex(PlatformContractError, "REWORK requires at least one finding_ref"):
                evaluate_review(invalid, packet)

    def test_v13_review_rejects_absence_claim_contradicted_by_source_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict.md"
            path.write_text(COMPLETE_SPEC, encoding="utf-8")
            proposal, packet = self._strict_review_fixture(path)

            def mutable(value):
                if isinstance(value, Mapping):
                    return {key: mutable(item) for key, item in value.items()}
                if isinstance(value, (tuple, list)):
                    return [mutable(item) for item in value]
                return value

            review = mutable(proposal.details["review"])
            review["decisions"][1]["observed_fact"] = "No stable FR identifiers are present in the Spec."
            invalid = DecisionProposal(
                proposal.work_item_id, proposal.check_id, proposal.check_version,
                proposal.result, proposal.findings, proposal.reason, {"review": review},
            )
            with self.assertRaises(PlatformContractError) as error:
                evaluate_review(invalid, packet)
            self.assertEqual(error.exception.code, "INVALID_SEMANTIC_CLAIM")

    def test_confirmed_missing_case_projects_a_bounded_absence_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-case.md"
            path.write_text("# Example\n\nFR-001 is described.\nAC-001 is observable.\n", encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-absence-claim", frozenset({"structured_read"}))
            item = plugin.discover(str(path), context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            checklist = [
                {"check_id": f"CHK-{number:02d}", "status": "REWORK" if number == 12 else "PASS"}
                for number in range(1, 19)
            ]
            finding = {
                "finding_id": "cases-missing", "status": "CONFIRMED", "severity": "P1",
                "object_id": "FR-001", "dimension": "CHK-12",
                "gap": "Acceptance criteria have no executable CASE scenarios.",
                "impact": "Testers must invent inputs and expected outcomes.",
                "recommendation": "Map each critical AC to a CASE.",
                "closure_evidence": "Each critical AC has an executable CASE.",
                "candidate_ids": [], "evidence": ["Acceptance criteria have no executable CASE scenarios."],
                "resolution_owner": None, "next_action": "Add CASE-001.",
            }
            delivery = _actionable_result_delivery([finding], checklist, packet)
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["evidenceClaims"][0]["kind"], "absence")
        self.assertEqual(delivery["evidenceClaims"][0]["scope"]["startLine"], 1)
        self.assertGreaterEqual(delivery["evidenceClaims"][0]["scope"]["endLine"], 1)
        self.assertEqual(delivery["remediations"][0]["claimRefs"], ["claim:cases-missing"])

    @staticmethod
    def _cross_document_packet_and_finding(root: Path):
        anchor = root / "anchor.md"
        related = root / "related.md"
        anchor.write_text(
            "# Retention Spec\n\nApproved records are retained for 30 days.\n",
            encoding="utf-8",
        )
        related.write_text(
            "# Retention Contract\n\nApproved records are retained for 90 days.\n",
            encoding="utf-8",
        )
        scope = {
            "anchor": {
                "documentId": "retention-anchor", "path": str(anchor), "profile": "speckit",
            },
            "relatedDocuments": [{
                "documentId": "retention-related", "path": str(related),
            }],
            "relationships": [{
                "relationshipId": "retention-contract-relation",
                "fromDocumentId": "retention-anchor",
                "toDocumentId": "retention-related",
                "kind": "implements",
                "evidence": "The related contract explicitly implements the anchor Spec.",
                "resolutionOwner": "Compliance owner",
            }],
        }
        plugin = SpecQualityPlugin()
        context = PlatformContext("run-cross-review", frozenset({"structured_read"}))
        item = plugin.discover(scope, context)[0]
        packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
        cross_evidence = packet.evidence[0].payload["crossDocumentPacket"]["evidence"]
        selected = [
            next(
                evidence for evidence in cross_evidence
                if evidence["document_id"] == document_id and marker in evidence["excerpt"]
            )
            for document_id, marker in (
                ("retention-anchor", "30 days"),
                ("retention-related", "90 days"),
            )
        ]
        refs = [{
            key: evidence[key] for key in (
                "document_id", "source_chunk_id", "document_path", "source_digest",
                "start_line", "end_line", "excerpt",
            )
        } for evidence in selected]
        finding = {
            "finding_id": "retention-contradiction",
            "status": "CONFIRMED",
            "severity": "P1",
            "object_id": "Approved-record retention period",
            "dimension": "CHK-05",
            "gap": "The related contract prescribes a different retention period.",
            "impact": "Implementations may retain regulated records for the wrong duration.",
            "recommendation": "Approve one retention period and update both documents.",
            "closure_evidence": "Both documents state the same approved retention period.",
            "candidate_ids": [],
            "merged_into": None,
            "evidence": [ref["excerpt"] for ref in refs],
            "review_note": None,
            "semantic_type": "contradiction",
            "relationship_id": "retention-contract-relation",
            "document_ids": ["retention-anchor", "retention-related"],
            "affected_elements": ["retention period"],
            "resolution_owner": "Compliance owner",
            "next_action": "The compliance owner must approve 30 or 90 days.",
            "evidence_refs": refs,
        }
        return packet, finding

    def test_cross_document_scope_schema_is_explicit_and_exclusive(self):
        scope = {
            "anchor": {"documentId": "anchor-spec", "path": "/tmp/anchor.md"},
            "relatedDocuments": [{
                "documentId": "related-contract", "path": "/tmp/contract.md",
                "selectionReason": "The contract implements the selected Spec.",
            }],
            "relationships": [{
                "relationshipId": "anchor-to-contract",
                "fromDocumentId": "anchor-spec", "toDocumentId": "related-contract",
                "kind": "implements",
                "evidence": "The contract declares the selected Spec as its source.",
                "resolutionOwner": "Product owner",
            }],
        }
        validator = Draft202012Validator(SPEC_QUALITY_SCOPE_SCHEMA)

        self.assertEqual(list(validator.iter_errors(scope)), [])
        self.assertTrue(list(validator.iter_errors({**scope, "files": ["/tmp/anchor.md"]})))
        missing_relation = dict(scope)
        missing_relation.pop("relationships")
        self.assertTrue(list(validator.iter_errors(missing_relation)))

    def test_cross_document_scope_creates_one_anchor_work_item_with_bilateral_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor_path = root / "anchor.md"
            related_path = root / "contract.md"
            anchor_text = "# Retention Spec\n\nApproved records are retained for 30 days.\n"
            related_text = "# Retention Contract\n\nApproved records are retained for 90 days.\n"
            anchor_path.write_text(anchor_text, encoding="utf-8")
            related_path.write_text(related_text, encoding="utf-8")
            scope = {
                "anchor": {
                    "documentId": "retention-anchor", "path": str(anchor_path),
                    "profile": "speckit",
                },
                "relatedDocuments": [{
                    "documentId": "retention-related", "path": str(related_path),
                    "selectionReason": "The contract implements the anchor Spec.",
                }],
                "relationships": [{
                    "relationshipId": "retention-contract-relation",
                    "fromDocumentId": "retention-anchor",
                    "toDocumentId": "retention-related",
                    "kind": "implements",
                    "evidence": "The related contract explicitly implements the anchor Spec.",
                    "resolutionOwner": "Compliance owner",
                }],
            }
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-cross-document", frozenset({"structured_read"}))

            items = plugin.discover(scope, context)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].metadata["scopeKind"], "cross-spec")
            packet = plugin.inspect(items, plugin.manifest.checks[0], context)[0]
            payload = packet.evidence[0].payload

            cross_packet = payload["crossDocumentPacket"]
            self.assertEqual(cross_packet["scope"]["anchor_document_id"], "retention-anchor")
            evidence = list(cross_packet["evidence"])
            self.assertEqual({item["document_id"] for item in evidence}, {
                "retention-anchor", "retention-related",
            })
            self.assertEqual({item["document_role"] for item in evidence}, {"anchor", "related"})
            self.assertTrue(any("retained for 30 days" in item["excerpt"] for item in evidence))
            self.assertTrue(any("retained for 90 days" in item["excerpt"] for item in evidence))
            self.assertTrue(all(len(item["source_digest"]) == 64 for item in evidence))
            self.assertTrue(all(item["start_line"] <= item["end_line"] for item in evidence))
            collections = {
                item["collectionId"]: item for item in packet.metadata["evidenceCollections"]
            }
            self.assertFalse(collections["cross-document-evidence"]["reviewRequired"])
            self.assertEqual(
                collections["cross-document-evidence"]["groupBy"],
                ("relationship_id", "document_id"),
            )
            self.assertEqual(payload["evidenceManifest"]["scope"]["kind"], "cross-spec")

            def plain(value):
                if isinstance(value, dict) or hasattr(value, "items"):
                    return {key: plain(item) for key, item in value.items()}
                if isinstance(value, (tuple, list)):
                    return [plain(item) for item in value]
                return value

            evidence_schema = json.loads(
                Path(__file__).parents[1].joinpath(
                    "src/assayer_platform/builtin_plugins/spec_quality/cross-document-evidence.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(evidence_schema).validate(plain(cross_packet))

    def test_cross_document_scope_rejects_unrelated_or_duplicate_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / "anchor.md"
            related = root / "related.md"
            anchor.write_text("Anchor", encoding="utf-8")
            related.write_text("Related", encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-cross-invalid", frozenset({"structured_read"}))
            base = {
                "anchor": {"documentId": "anchor", "path": str(anchor)},
                "relatedDocuments": [{"documentId": "related", "path": str(related)}],
                "relationships": [{
                    "relationshipId": "relation", "fromDocumentId": "anchor",
                    "toDocumentId": "related", "kind": "compares_with",
                    "evidence": "The user explicitly selected this comparison.",
                    "resolutionOwner": "Product owner",
                }],
            }
            duplicate = dict(base)
            duplicate["relatedDocuments"] = [{
                "documentId": "anchor", "path": str(related),
            }]
            with self.assertRaisesRegex(Exception, "Duplicate.*documentId"):
                plugin.discover(duplicate, context)

            unknown = dict(base)
            unknown["relationships"] = [{
                **base["relationships"][0], "toDocumentId": "outside-scope",
            }]
            with self.assertRaisesRegex(Exception, "unknown related document"):
                plugin.discover(unknown, context)

    def test_related_document_change_invalidates_anchor_investigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / "anchor.md"
            related = root / "related.md"
            anchor.write_text("Anchor content", encoding="utf-8")
            related.write_text("Related baseline", encoding="utf-8")
            scope = {
                "anchor": {"documentId": "anchor", "path": str(anchor)},
                "relatedDocuments": [{"documentId": "related", "path": str(related)}],
                "relationships": [{
                    "relationshipId": "relation", "fromDocumentId": "anchor",
                    "toDocumentId": "related", "kind": "depends_on",
                    "evidence": "The anchor declares this dependency.",
                    "resolutionOwner": "Product owner",
                }],
            }
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-cross-drift", frozenset({"structured_read"}))
            items = plugin.discover(scope, context)
            related.write_text("Related changed after discovery", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed after discovery"):
                plugin.inspect(items, plugin.manifest.checks[0], context)

    def test_cross_document_evidence_is_pageable_without_related_work_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / "anchor.md"
            related = root / "related.md"
            anchor.write_text("# Anchor\n\nThe limit is 30 days.\n", encoding="utf-8")
            related.write_text("# Related\n\nThe limit is 90 days.\n", encoding="utf-8")
            scope = {
                "anchor": {"documentId": "anchor", "path": str(anchor), "profile": "speckit"},
                "relatedDocuments": [{"documentId": "related", "path": str(related)}],
                "relationships": [{
                    "relationshipId": "retention-relation", "fromDocumentId": "anchor",
                    "toDocumentId": "related", "kind": "compares_with",
                    "evidence": "Both documents govern the same retention limit.",
                    "resolutionOwner": "Compliance owner",
                }],
            }
            transport = InteractivePlatformMcpToolTransport(
                root / "output", plugin_registry=builtin_plugin_registry(),
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001", "scope": scope,
            })
            discovered = transport.call_tool("discover_work_items", {})[
                "structuredContent"
            ]["result"]["result"]
            self.assertEqual(len(discovered["workItems"]), 1)
            inspected = transport.call_tool("inspect_work_items", {})[
                "structuredContent"
            ]["result"]["result"]
            work_item_id = inspected["investigations"][0]["workItem"]["workItemId"]
            index = {
                item["collectionId"]: item
                for item in inspected["evidenceCollectionIndex"][work_item_id]
            }
            cross_collection = index["cross-document-evidence"]
            self.assertFalse(cross_collection["reviewRequired"])
            self.assertEqual(cross_collection["groupCount"], 2)
            page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "cross-document-evidence",
                "pageSize": 20,
            })["structuredContent"]["result"]["result"]
            self.assertEqual({item["document_id"] for item in page["items"]}, {
                "anchor", "related",
            })
            transport.close()

    def test_product_mcp_can_page_bilateral_evidence_at_relationship_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / "anchor.md"
            related = root / "related.md"
            anchor.write_text(COMPLETE_SPEC, encoding="utf-8")
            related.write_text(
                "# Related Contract\n\nFR-001 returns one observable result.\n",
                encoding="utf-8",
            )
            scope = {
                "anchor": {"documentId": "anchor", "path": str(anchor)},
                "relatedDocuments": [{"documentId": "related", "path": str(related)}],
                "relationships": [{
                    "relationshipId": "anchor-related", "fromDocumentId": "anchor",
                    "toDocumentId": "related", "kind": "implements",
                    "evidence": "The related contract implements FR-001.",
                    "resolutionOwner": "Product owner",
                }],
            }
            transport = ProductMcpToolTransport(
                HostCore(), output_root=root / "output",
                plugin_registry=builtin_plugin_registry(),
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001", "scope": scope,
            })
            task = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            while task["collectionId"] == "candidate-findings":
                accepted = transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": task["workItemId"],
                        "collectionId": task["collectionId"],
                        "itemIds": task["itemIds"],
                        "payload": {"decisions": [{
                            "finding_id": f"suppressed-{candidate_id}",
                            "status": "SUPPRESSED",
                            "candidate_ids": [candidate_id],
                            "review_note": "This scanner signal is not a material issue.",
                        } for candidate_id in task["itemIds"]]},
                    },
                })["structuredContent"]["result"]
                task = accepted["result"]["semanticTask"]

            self.assertEqual(task["collectionId"], "cross-document-relationships")
            evidence_index = {
                item["collectionId"]: item
                for item in task["referenceCollectionIndex"]
            }
            groups = evidence_index["cross-document-evidence"]["groups"]
            relationship_groups = [
                group for group in groups
                if group["values"]["relationship_id"] == "anchor-related"
            ]
            self.assertEqual(len(relationship_groups), 2)
            evidence_documents = set()
            for group in relationship_groups:
                page = transport.call_tool("expand_evidence_collection", {
                    "workItemId": task["workItemId"],
                    "collectionId": "cross-document-evidence",
                    "groupKey": group["groupKey"],
                    "pageSize": 10,
                })["structuredContent"]["result"]["result"]
                evidence_documents.update(item["document_id"] for item in page["items"])
                self.assertTrue(all(item["excerpt"] for item in page["items"]))
            self.assertEqual(evidence_documents, {"anchor", "related"})
            transport.close()

    def test_host_requires_each_cross_document_relationship_before_checklist_and_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / "anchor.md"
            related = root / "related.md"
            anchor.write_text(COMPLETE_SPEC, encoding="utf-8")
            related.write_text(
                "# Related Contract\n\nFR-001 returns one observable result.\n",
                encoding="utf-8",
            )
            scope = {
                "anchor": {"documentId": "anchor", "path": str(anchor)},
                "relatedDocuments": [{"documentId": "related", "path": str(related)}],
                "relationships": [{
                    "relationshipId": "anchor-related", "fromDocumentId": "anchor",
                    "toDocumentId": "related", "kind": "implements",
                    "evidence": "The related contract implements FR-001.",
                    "resolutionOwner": "Product owner",
                }],
            }
            output = root / "output"
            first_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            started = first_transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001", "scope": scope,
            })["structuredContent"]["result"]
            task = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            checkpoint_ids = []
            while task["collectionId"] == "candidate-findings":
                accepted = first_transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": task["workItemId"],
                        "collectionId": task["collectionId"],
                        "itemIds": task["itemIds"],
                        "payload": {"decisions": [{
                            "finding_id": f"suppressed-{candidate_id}",
                            "status": "SUPPRESSED",
                            "candidate_ids": [candidate_id],
                            "review_note": "This scanner signal is not a material issue.",
                        } for candidate_id in task["itemIds"]]},
                    },
                })["structuredContent"]["result"]
                checkpoint_ids.append(accepted["operationId"])
                task = accepted["result"]["semanticTask"]
            self.assertEqual(task["collectionId"], "cross-document-relationships")
            self.assertEqual(task["itemIds"], ["anchor-related"])

            first_transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            resumed = resumed_transport.call_tool("resume_plugin_run", {
                "runId": started["runId"],
            })["structuredContent"]["result"]
            task = resumed["result"]["semanticTask"]
            self.assertEqual(task["collectionId"], "cross-document-relationships")

            with self.assertRaises(HostError) as bypassed:
                resumed_transport.call_tool("submit_decisions", {"decisions": [{
                    "workItemId": task["workItemId"],
                    "result": "scanned_no_issue",
                    "reason": "Relationship review cannot be bypassed.",
                    "findings": [{
                        "dimension": f"CHK-{index:02d}", "status": "satisfied",
                        "reason": "Not yet reviewed.",
                    } for index in range(1, 19)],
                    "reviewCheckpointIds": checkpoint_ids,
                    "finalization": {"readiness_context": {
                        "mandatory_dimensions_checked": True,
                        "unresolved_blockers": False,
                        "escalations": [],
                    }},
                }]})
            self.assertEqual(bypassed.exception.code, "REVIEW_CHECKPOINT_INCOMPLETE")

            accepted = resumed_transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": task["workItemId"],
                    "collectionId": "cross-document-relationships",
                    "itemIds": task["itemIds"],
                    "payload": {"cross_document_review": [{
                        "relationship_id": "anchor-related",
                        "outcome": "COMPATIBLE",
                        "note": "The related contract is compatible with anchor FR-001.",
                        "decisions": [],
                    }]},
                },
            })["structuredContent"]["result"]
            self.assertEqual(
                accepted["result"]["semanticTask"]["collectionId"],
                "checklist-dimensions",
            )
            resumed_transport.close()

    def test_cross_document_reviewer_origin_finding_requires_bilateral_frozen_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            packet, finding = self._cross_document_packet_and_finding(Path(directory))

            reviewed = validate_review_decisions(
                [finding], packet, expected_candidate_ids=(),
            )

            self.assertEqual(reviewed[0]["semantic_type"], "contradiction")
            self.assertEqual(len(reviewed[0]["evidence_refs"]), 2)

            invalid_cases = {
                "one source": {
                    "evidence_refs": finding["evidence_refs"][:1],
                    "evidence": finding["evidence"][:1],
                },
                "forged digest": {
                    "evidence_refs": [
                        {**finding["evidence_refs"][0], "source_digest": "0" * 64},
                        finding["evidence_refs"][1],
                    ],
                },
                "unknown relationship": {"relationship_id": "unknown-relation"},
                "wrong owner": {"resolution_owner": "Unrelated owner"},
                "wrong documents": {"document_ids": ["retention-anchor", "outside-scope"]},
                "unconfirmed contradiction": {
                    "status": "UNVERIFIED", "severity": None,
                    "review_note": "The contradiction was not confirmed.",
                },
            }
            for label, changes in invalid_cases.items():
                with self.subTest(label=label):
                    invalid = copy.deepcopy(finding)
                    invalid.update(changes)
                    with self.assertRaisesRegex(Exception, "Cross-document|cross-document"):
                        validate_review_decisions(
                            [invalid], packet, expected_candidate_ids=(),
                        )

    def test_cross_document_unverified_dependency_remains_non_final(self):
        with tempfile.TemporaryDirectory() as directory:
            packet, finding = self._cross_document_packet_and_finding(Path(directory))
            finding.update({
                "finding_id": "retention-authority-unverified",
                "status": "UNVERIFIED",
                "severity": None,
                "semantic_type": "unverified_dependency",
                "closure_evidence": None,
                "review_note": "The supplied relationship does not establish source precedence.",
            })

            reviewed = validate_review_decisions(
                [finding], packet, expected_candidate_ids=(),
            )

            self.assertEqual(reviewed[0]["status"], "UNVERIFIED")

    def test_cross_document_finding_drives_formal_readiness_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            packet, finding = self._cross_document_packet_and_finding(Path(directory))
            payload = packet.evidence[0].payload
            source = next(
                chunk for chunk in payload["sourceChunks"]
                if chunk["document_id"] == "retention-anchor"
            )
            checklist_ref = {
                key: source[key] for key in (
                    "source_chunk_id", "document_path", "source_digest",
                    "start_line", "end_line",
                )
            }
            checklist = [{
                "check_id": f"CHK-{index:02d}",
                "status": "REWORK" if index == 5 else "PASS",
                "note": (
                    "The related contract contradicts the anchor retention period."
                    if index == 5 else "The reviewed evidence satisfies this dimension."
                ),
                "evidence_refs": [checklist_ref],
            } for index in range(1, 19)]
            candidate_decisions = [{
                "finding_id": f"suppressed-{candidate['candidate_id']}",
                "status": "SUPPRESSED",
                "candidate_ids": [candidate["candidate_id"]],
                "review_note": "This scanner signal is not the cross-document root cause.",
            } for candidate in payload["candidateFindings"]]
            proposal = DecisionProposal(
                packet.work_item.work_item_id,
                packet.check_id,
                packet.check_version,
                "issue_found",
                tuple(Finding(
                    f"CHK-{index:02d}", "violated" if index == 5 else "satisfied",
                    "Cross-document consistency was reviewed." if index == 5
                    else "The dimension was reviewed.",
                ) for index in range(1, 19)),
                "The anchor and related contract require one retention decision.",
                {"review": {
                    "review_schema_version": "1.2.0",
                    "readiness_context": {
                        "mandatory_dimensions_checked": True,
                        "unresolved_blockers": False,
                        "escalations": [],
                        "checklist_review": checklist,
                    },
                    "decisions": [*candidate_decisions, finding],
                    "cross_document_review": [{
                        "relationship_id": "retention-contract-relation",
                        "outcome": "FINDING",
                        "note": "The two documents prescribe incompatible retention periods.",
                        "decisions": [finding],
                    }],
                }},
            )
            report = evaluate_review(proposal, packet)
            receipt = SpecQualityDecisionCommitter().commit(
                proposal, packet, SpecQualityPlugin.manifest.checks[0],
                PlatformContext("run-cross-review", frozenset({"structured_read"})),
            )
            summary = SpecQualityPlugin().summarize(
                (packet.work_item,), (packet,), (proposal,), "completed",
            )

            self.assertEqual(report["readiness"]["status"], "REWORK")
            self.assertEqual(
                report["cross_document_scope"]["anchor_document_id"], "retention-anchor",
            )
            self.assertTrue(receipt.metadata["reviewed"])
            self.assertEqual(receipt.metadata["readiness"]["status"], "REWORK")
            confirmed = summary["review"]["confirmedFindings"][0]
            self.assertEqual(confirmed["semantic_type"], "contradiction")
            self.assertEqual(confirmed["relationship_id"], "retention-contract-relation")
            self.assertEqual(len(confirmed["evidence_refs"]), 2)
            remediation = summary["review"]["remediations"][0]
            self.assertEqual(remediation["dimensions"], ["CHK-05"])
            self.assertEqual(remediation["owner"], {
                "status": "assigned", "identity": "Compliance owner",
            })

            def mutable(value):
                if isinstance(value, Mapping):
                    return {key: mutable(item) for key, item in value.items()}
                if isinstance(value, (tuple, list)):
                    return [mutable(item) for item in value]
                return value

            invalid_review = mutable(proposal.details["review"])
            invalid_review["readiness_context"]["checklist_review"][4]["status"] = "PASS"
            invalid_review["readiness_context"]["checklist_review"][4]["note"] = (
                "The consistency dimension was incorrectly marked satisfied."
            )
            invalid_proposal = DecisionProposal(
                proposal.work_item_id, proposal.check_id, proposal.check_version,
                proposal.result,
                tuple(Finding(
                    item.dimension,
                    "satisfied" if item.dimension == "CHK-05" else item.status,
                    item.reason,
                ) for item in proposal.findings),
                proposal.reason,
                {"review": invalid_review},
            )
            with self.assertRaisesRegex(
                PlatformContractError, "must map to a REWORK or ESCALATE",
            ):
                evaluate_review(invalid_proposal, packet)

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
            self.assertFalse(any(candidate["ruleId"] == "CHAPTER-001" for candidate in evidence["candidates"]))
            self.assertTrue(any(candidate["ruleId"] == "FR-006" for candidate in evidence["candidates"]))
            self.assertEqual(len(evidence["checklistResults"]), 18)
            self.assertTrue(all(result["status"] != "PASS" for result in evidence["checklistResults"]))
            self.assertIn("unresolved", {dimension.candidate_status for dimension in packet.dimensions})

    def test_strict_profile_retains_twelve_chapter_structure_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Example\n\nFR-001 returns an observable result.\n", encoding="utf-8")
            plugin = SpecQualityPlugin()
            context = PlatformContext("run-spec-strict-structure", frozenset({"structured_read"}))
            item = plugin.discover({"files": [{"path": str(path), "profile": "strict-12-chapter"}]}, context)[0]

            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            candidates = packet.evidence[0].payload["candidates"]

            self.assertEqual(sum(candidate["ruleId"] == "CHAPTER-001" for candidate in candidates), 12)
            self.assertEqual(packet.evidence[0].payload["profile"], "strict-12-chapter")

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
            self.assertEqual(payload["authorityVersion"], "1.3.0")
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
            self.assertEqual(set(result["artifacts"]), {
                "result-summary.json", f"{run_id}.canonical-result.json",
            })
            result_path = Path(directory) / "output" / run_id / "result-summary.json"
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

    def test_navigation_review_strategy_makes_structure_the_review_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "navigation-spec.md"
            path.write_text("# Overview\n\nA requirement with an unclear target.\n\n## Fields\n\n| Field | Required |\n| --- | --- |\n| code | NOT NULL |\n", encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=builtin_plugin_registry(),
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.spec-quality", "checkId": "SPEC-001",
                "scope": {"files": [{"path": str(path), "reviewStrategy": "navigation"}]},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            result = inspected["structuredContent"]["result"]["result"]
            packet = result["investigations"][0]
            work_item_id = packet["workItem"]["workItemId"]
            collections = {item["collectionId"]: item for item in result["evidenceCollectionIndex"][work_item_id]}
            self.assertFalse(collections["candidate-findings"]["reviewRequired"])
            self.assertTrue(collections["document-navigation"]["reviewRequired"])
            # Navigation is a complete structural index (headings, prose, tables,
            # lists, and code blocks), while the legacy scanner only emits a
            # subset of heuristic candidates.  They intentionally need not have
            # the same cardinality.
            self.assertGreater(collections["document-navigation"]["itemCount"], 0)
            self.assertGreaterEqual(
                collections["document-navigation"]["itemCount"],
                collections["candidate-findings"]["itemCount"],
            )
            boundary = transport.call_tool("advance_plugin_run", {})["structuredContent"]["result"]["result"]["semanticTask"]
            self.assertEqual(boundary["collectionId"], "document-navigation")
            self.assertIn("reviewContext", boundary)
            self.assertIn("documentContext", boundary["reviewContext"])
            self.assertIn("sourceFacts", boundary["reviewContext"])
            navigation_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "document-navigation",
                "pageSize": len(boundary["itemIds"]),
                "groupKey": boundary["group"]["groupKey"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(boundary["itemIds"], navigation_page["itemIds"])

            reviewed_unit_ids = []
            checkpoint_ids = []
            current_boundary = boundary
            while (
                current_boundary["kind"] == "review_evidence_items"
                and current_boundary["collectionId"] == "document-navigation"
            ):
                item_ids = current_boundary["itemIds"]
                reviewed_unit_ids.extend(item_ids)
                accepted_response = transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": work_item_id,
                        "collectionId": "document-navigation",
                        "itemIds": item_ids,
                        "payload": {"decisions": [{
                            "finding_id": f"navigation-review-{len(checkpoint_ids) + 1}",
                            "status": "SUPPRESSED",
                            "candidate_ids": item_ids,
                            "review_note": "The source units were semantically reviewed and do not form a standalone finding.",
                        }]},
                    },
                })["structuredContent"]["result"]
                checkpoint_ids.append(accepted_response["operationId"])
                current_boundary = accepted_response["result"]["semanticTask"]

            self.assertEqual(len(reviewed_unit_ids), collections["document-navigation"]["itemCount"])
            self.assertEqual(len(set(reviewed_unit_ids)), len(reviewed_unit_ids))
            full_navigation_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "document-navigation",
                "pageSize": 1000,
            })["structuredContent"]["result"]["result"]
            all_navigation_ids = set(full_navigation_page["itemIds"])
            self.assertEqual(set(reviewed_unit_ids), all_navigation_ids)

            source_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "source-sections",
                "pageSize": 1,
            })["structuredContent"]["result"]["result"]
            source_chunk = source_page["items"][0]
            evidence_ref = {
                "source_chunk_id": source_chunk["source_chunk_id"],
                "document_path": source_chunk["document_path"],
                "source_digest": source_chunk["source_digest"],
                "start_line": source_chunk["start_line"],
                "end_line": source_chunk["end_line"],
            }
            checklist = []
            while current_boundary["kind"] == "review_evidence_items":
                self.assertEqual(current_boundary["collectionId"], "checklist-dimensions")
                batch = [{
                    "check_id": check_id,
                    "status": "PASS",
                    "note": "The dimension was reviewed against the complete navigation index.",
                    "evidence_refs": [evidence_ref],
                    "applicability": "APPLICABLE",
                    "observation": "The declared dimension was checked against the source evidence.",
                    "gap": "No material gap was observed for this dimension.",
                    "impact": "No impact is established by the reviewed evidence.",
                    "recommendation": "Keep the current evidence-backed definition.",
                    "owner": "Spec owner",
                    "next_action": "Retain the evidence reference on the next revision.",
                    "confidence": "high",
                } for check_id in current_boundary["itemIds"]]
                checklist.extend(batch)
                accepted_response = transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": work_item_id,
                        "collectionId": "checklist-dimensions",
                        "itemIds": current_boundary["itemIds"],
                        "payload": {"checklist_review": batch},
                    },
                })["structuredContent"]["result"]
                checkpoint_ids.append(accepted_response["operationId"])
                current_boundary = accepted_response["result"]["semanticTask"]

            self.assertEqual(current_boundary["kind"], "finalize_decision")
            self.assertEqual(len(checklist), 18)
            submitted = transport.call_tool("advance_plugin_run", {"decision": {
                "workItemId": work_item_id,
                "result": "scanned_no_issue",
                "reason": "Every Markdown unit and Spec quality dimension was semantically reviewed.",
                "findings": [{
                    "dimension": item["check_id"],
                    "status": "satisfied",
                    "reason": item["note"],
                } for item in checklist],
                "reviewCheckpointIds": current_boundary["reviewCheckpointIds"],
                "finalization": {"readiness_context": {
                    "mandatory_dimensions_checked": True,
                    "unresolved_blockers": False,
                    "escalations": [],
                }},
            }})["structuredContent"]["result"]
            self.assertEqual(submitted["status"], "completed")
            self.assertEqual(submitted["result"]["summary"]["phase"], "REVIEWED")
            self.assertEqual(submitted["result"]["summary"]["readiness"]["status"], "READY")

    def test_layout_neutral_source_evidence_is_bounded_and_grouped_by_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text(
                """# Service Contract

## Integrations

The billing API is a confirmed dependency owned by Finance Platform. If it
times out, the operation fails without retry and emits dependency_unavailable.

## Assumptions Under Review

ASSUMPTION-01 remains OPEN until Finance Platform verifies API version 3.
""",
                encoding="utf-8",
            )
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
            work_item_id = packet["workItem"]["workItemId"]
            collections = {
                item["collectionId"]: item
                for item in result["evidenceCollectionIndex"][work_item_id]
            }

            self.assertTrue(collections["candidate-findings"]["reviewRequired"])
            self.assertTrue(collections["checklist-dimensions"]["reviewRequired"])
            self.assertEqual(collections["checklist-dimensions"]["itemCount"], 18)
            self.assertEqual(collections["checklist-dimensions"]["groupCount"], 4)
            self.assertFalse(collections["dimension-evidence"]["reviewRequired"])
            self.assertFalse(collections["source-sections"]["reviewRequired"])
            self.assertFalse(collections["document-navigation"]["reviewRequired"])
            self.assertEqual(collections["dimension-evidence"]["groupCount"], 18)
            self.assertGreater(collections["document-navigation"]["itemCount"], 0)

            dependency_group = next(
                group for group in collections["dimension-evidence"]["groups"]
                if group["values"] == {"check_id": "CHK-17"}
            )
            dependency_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "dimension-evidence",
                "groupKey": dependency_group["groupKey"],
                "pageSize": 4,
            })["structuredContent"]["result"]["result"]
            self.assertTrue(any(item["mapping_status"] == "candidate" for item in dependency_page["items"]))
            self.assertTrue(all(item["check_id"] == "CHK-17" for item in dependency_page["items"]))
            self.assertTrue(all(item["source_digest"] for item in dependency_page["items"] if item["mapping_status"] == "candidate"))

            source_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "source-sections",
                "pageSize": 20,
            })["structuredContent"]["result"]["result"]
            self.assertTrue(source_page["items"])
            self.assertTrue(all(len(item["excerpt"]) <= 2400 for item in source_page["items"]))
            self.assertTrue(all(item["start_line"] <= item["end_line"] for item in source_page["items"]))
            navigation_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "document-navigation",
                "pageSize": 20,
            })["structuredContent"]["result"]["result"]
            self.assertTrue(navigation_page["items"])
            self.assertTrue(all(item["unitId"] and item["excerpt"] for item in navigation_page["items"]))
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
            source_page = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id,
                "collectionId": "source-sections",
                "pageSize": 1,
            })["structuredContent"]["result"]["result"]
            source_chunk = source_page["items"][0]
            evidence_ref = {
                "source_chunk_id": source_chunk["source_chunk_id"],
                "document_path": source_chunk["document_path"],
                "source_digest": source_chunk["source_digest"],
                "start_line": source_chunk["start_line"],
                "end_line": source_chunk["end_line"],
            }
            checklist = []
            checklist_boundary = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            self.assertEqual(checklist_boundary["kind"], "review_evidence_items")
            self.assertEqual(checklist_boundary["collectionId"], "checklist-dimensions")
            with self.assertRaises(HostError) as incomplete_collections:
                transport.call_tool("submit_decisions", {"decisions": [{
                    "workItemId": work_item_id,
                    "result": "scanned_no_issue",
                    "reason": "Checklist checkpoints cannot be bypassed.",
                    "findings": [{
                        "dimension": f"CHK-{index:02d}",
                        "status": "satisfied",
                        "reason": "This projection must not bypass persisted review.",
                    } for index in range(1, 19)],
                    "reviewCheckpointIds": checkpoint_ids,
                    "finalization": {"readiness_context": {}},
                }]})
            self.assertEqual(
                incomplete_collections.exception.code, "REVIEW_CHECKPOINT_INCOMPLETE",
            )
            reference_indexes = {
                item["collectionId"]: item
                for item in checklist_boundary["referenceCollectionIndex"]
            }
            self.assertEqual(reference_indexes["dimension-evidence"]["groupCount"], 18)
            self.assertFalse(reference_indexes["dimension-evidence"]["reviewRequired"])

            invalid_ref = {
                **evidence_ref,
                "source_chunk_id": "source:not-from-this-investigation",
            }
            with self.assertRaises(HostError) as invalid_evidence:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": work_item_id,
                        "collectionId": "checklist-dimensions",
                        "itemIds": checklist_boundary["itemIds"],
                        "payload": {"checklist_review": [{
                            "check_id": check_id,
                            "status": "PASS",
                            "note": "Reviewed and satisfied.",
                            "evidence_refs": [invalid_ref],
                        } for check_id in checklist_boundary["itemIds"]]},
                    },
                })
            self.assertEqual(invalid_evidence.exception.code, "SPEC_REVIEW_INVALID")
            repeated_boundary = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            self.assertEqual(repeated_boundary["itemIds"], checklist_boundary["itemIds"])

            current_boundary = repeated_boundary
            while current_boundary["kind"] == "review_evidence_items":
                self.assertEqual(current_boundary["collectionId"], "checklist-dimensions")
                batch = [{
                    "check_id": check_id,
                    "status": "PASS",
                    "note": "Reviewed and satisfied.",
                    "evidence_refs": [evidence_ref],
                    "applicability": "APPLICABLE",
                    "observation": "The declared dimension was checked against the source evidence.",
                    "gap": "No material gap was observed for this dimension.",
                    "impact": "No impact is established by the reviewed evidence.",
                    "recommendation": "Keep the current evidence-backed definition.",
                    "owner": "Spec owner",
                    "next_action": "Retain the evidence reference on the next revision.",
                    "confidence": "high",
                } for check_id in current_boundary["itemIds"]]
                checklist.extend(batch)
                current_boundary = transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": work_item_id,
                        "collectionId": "checklist-dimensions",
                        "itemIds": current_boundary["itemIds"],
                        "payload": {"checklist_review": batch},
                    },
                })["structuredContent"]["result"]["result"]["semanticTask"]

            final_boundary = current_boundary
            self.assertEqual(final_boundary["kind"], "finalize_decision")
            self.assertEqual(len(checklist), 18)
            self.assertEqual(len(final_boundary["reviewCheckpointIds"]), len(checkpoint_ids) + 4)
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id,
                "result": "scanned_no_issue",
                "reason": "Every candidate page and checklist dimension was reviewed.",
                "findings": [
                    {"dimension": item["check_id"], "status": "satisfied", "reason": item["note"]}
                    for item in checklist
                ],
                "reviewCheckpointIds": final_boundary["reviewCheckpointIds"],
                "finalization": {"readiness_context": {
                    "mandatory_dimensions_checked": True,
                    "unresolved_blockers": False,
                    "escalations": [],
                }},
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            summary = finished["structuredContent"]["result"]["result"]["summary"]
            self.assertEqual(summary["phase"], "REVIEWED")
            self.assertEqual(summary["readiness"]["status"], "READY")
            self.assertEqual(summary["review"]["handledCandidateCount"], collection["itemCount"])

    def test_spec_checklist_review_resumes_after_a_persisted_batch(self):
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
            first_transport.call_tool("discover_work_items", {})
            inspected = first_transport.call_tool("inspect_work_items", {
                "includeEvidence": True,
            })["structuredContent"]["result"]["result"]
            packet = inspected["investigations"][0]
            work_item_id = packet["workItem"]["workItemId"]
            payload = packet["evidence"][0]["payload"]
            candidate_ids = [item["candidate_id"] for item in payload["candidateFindings"]]
            first_transport.call_tool("checkpoint_review", {
                "workItemId": work_item_id,
                "collectionId": "candidate-findings",
                "itemIds": candidate_ids,
                "payload": {"decisions": [{
                    "finding_id": "resume-candidate-review",
                    "status": "SUPPRESSED",
                    "candidate_ids": candidate_ids,
                    "review_note": "The candidate signals are not material findings.",
                }]},
            })
            first_chunk = payload["sourceChunks"][0]
            evidence_ref = {
                "source_chunk_id": first_chunk["source_chunk_id"],
                "document_path": first_chunk["document_path"],
                "source_digest": first_chunk["source_digest"],
                "start_line": first_chunk["start_line"],
                "end_line": first_chunk["end_line"],
            }
            first_batch = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            self.assertEqual(first_batch["collectionId"], "checklist-dimensions")
            accepted = first_transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": work_item_id,
                    "collectionId": "checklist-dimensions",
                    "itemIds": first_batch["itemIds"],
                    "payload": {"checklist_review": [{
                        "check_id": check_id,
                        "status": "PASS",
                        "note": "Reviewed and satisfied.",
                        "evidence_refs": [evidence_ref],
                    } for check_id in first_batch["itemIds"]]},
                },
            })["structuredContent"]["result"]
            next_batch_ids = accepted["result"]["semanticTask"]["itemIds"]

            first_transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            resumed = resumed_transport.call_tool("resume_plugin_run", {
                "runId": started["runId"],
            })["structuredContent"]["result"]

            task = resumed["result"]["semanticTask"]
            self.assertEqual(task["collectionId"], "checklist-dimensions")
            self.assertEqual(task["itemIds"], next_batch_ids)
            self.assertTrue(set(task["itemIds"]).isdisjoint(first_batch["itemIds"]))
            resumed_transport.close()

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

            first_transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=builtin_plugin_registry(),
            )
            resumed = resumed_transport.call_tool("resume_plugin_run", {
                "runId": started["runId"],
            })[
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
            self.assertEqual(set(result["artifacts"]), {
                "result-summary.json", result["canonicalResult"],
            })
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
