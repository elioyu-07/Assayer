import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_host import InteractivePlatformMcpToolTransport
from assayer_platform import PluginRegistry
from assayer_touchstone import evaluation as evaluation_module
from assayer_touchstone import (
    evaluate_semantic_review_case,
    evaluate_semantic_review_corpus,
    inspect_evaluation_case,
    load_evaluation_corpus,
    load_semantic_review_from_ledger,
    registration,
    validate_evaluation_corpus,
)


def spec_registry() -> PluginRegistry:
    """Return a fresh registry containing the external Touchstone plugin."""
    return PluginRegistry((registration,))

class TouchstoneEvaluationCorpusTest(unittest.TestCase):
    @staticmethod
    def _review_for(case):
        expected = case["expected"]
        reviewed = []
        cross_document = case.get("category") == "cross_document"
        cross_status = {
            finding_id: (
                "CONFIRMED" if review["outcome"] == "FINDING" else "UNVERIFIED"
            )
            for review in expected.get("relationshipReviews", ())
            for finding_id in review["findingIds"]
        }
        for item in expected["findings"]:
            finding = {
                "finding_id": item["findingId"],
                "status": cross_status.get(item["findingId"], "CONFIRMED"),
                "severity": item["severity"],
                "candidate_ids": list(item["candidateIds"]),
            }
            if cross_document:
                finding.update({
                    "semantic_type": item["semanticType"],
                    "document_ids": list(item["documentIds"]),
                    "resolution_owner": item["resolutionOwner"],
                })
            reviewed.append(finding)
        for item in expected["suppressions"]:
            reviewed.append({
                "finding_id": f"suppressed-{item['candidateId']}",
                "status": "SUPPRESSED",
                "candidate_ids": [item["candidateId"]],
            })
        for item in expected["unverified"]:
            reviewed.append({
                "finding_id": f"unverified-{item['candidateId']}",
                "status": "UNVERIFIED",
                "candidate_ids": [item["candidateId"]],
            })
        result = {
            "reviewed_findings": reviewed,
            "readiness": {"status": expected["readiness"]},
        }
        context_assertions = expected.get("contextAssertions")
        if context_assertions:
            result["document_context"] = {
                "document_type": context_assertions["documentType"],
            }
            result["checklist_review"] = [{
                "check_id": item["checkId"],
                "applicability": item["applicability"],
                "status": item["status"],
            } for item in context_assertions["checklistResults"]]
        if cross_document:
            findings_by_id = {item["finding_id"]: item for item in reviewed}
            result["cross_document_review"] = [{
                "relationship_id": item["relationshipId"],
                "outcome": item["outcome"],
                "note": "The fixed semantic oracle supplies this relationship outcome.",
                "decisions": [findings_by_id[finding_id] for finding_id in item["findingIds"]],
            } for item in expected["relationshipReviews"]]
        return result

    def test_packaged_corpus_is_valid_and_ids_are_closed(self):
        corpus = load_evaluation_corpus()
        self.assertEqual(corpus["pluginId"], "assayer.touchstone")
        self.assertEqual(len(corpus["cases"]), 11)

    def test_single_document_cases_match_deterministic_candidate_ids(self):
        corpus = load_evaluation_corpus()
        for case in corpus["cases"]:
            if case["category"] == "cross_document":
                continue
            actual = inspect_evaluation_case(case)
            expected = [{
                "candidateId": item["candidateId"],
                "ruleId": item["ruleId"],
            } for item in case["expected"]["candidates"]]
            self.assertEqual(actual["status"], "inspected", case["caseId"])
            self.assertEqual(actual["candidates"], expected, case["caseId"])

    def test_single_document_semantic_calibration_cases_are_explicit(self):
        corpus = load_evaluation_corpus()
        cases = {case["category"]: case for case in corpus["cases"]}

        ambiguity = cases["ambiguity"]["expected"]
        self.assertEqual(ambiguity["findings"][0]["semanticType"], "ambiguity")
        self.assertEqual(ambiguity["readiness"], "REWORK")

        contradiction = cases["contradiction"]["expected"]
        self.assertEqual(contradiction["findings"][0]["semanticType"], "contradiction")
        self.assertEqual(len(contradiction["findings"][0]["candidateIds"]), 2)

        duplicate = cases["duplicate"]["expected"]
        self.assertEqual(len(duplicate["findings"]), 1)
        self.assertEqual(duplicate["merges"][0]["findingId"], duplicate["findings"][0]["findingId"])

        false_positive = cases["false_positive"]["expected"]
        self.assertEqual(false_positive["findings"], [])
        self.assertEqual(len(false_positive["suppressions"]), 1)
        self.assertEqual(false_positive["readiness"], "READY")

    def test_context_regressions_preserve_shared_contract_facts_and_real_omissions(self):
        corpus = load_evaluation_corpus()
        cases = {case["caseId"]: case for case in corpus["cases"]}

        shared = inspect_evaluation_case(cases["spec-shared-contract-context"])
        self.assertEqual(shared["documentContext"]["documentType"], "shared_contract")
        shared_assertions = cases["spec-shared-contract-context"]["expected"]["contextAssertions"]
        shared_ids = {
            identifier
            for values in shared["sourceFacts"]["identifiers"].values()
            for identifier in values
        }
        self.assertTrue(set(shared_assertions["requiredIdentifiers"]).issubset(shared_ids))
        self.assertTrue(set(shared_assertions["requiredStatementKinds"]).issubset(
            set(shared["sourceFacts"]["explicitStatementKinds"]),
        ))
        self.assertEqual(shared["sourceFacts"]["identifiers"]["assumptions"], ["AS-006"])
        self.assertEqual(shared["sourceFacts"]["identifiers"]["decisions"], [])
        self.assertGreater(shared["documentContext"]["delegatedResponsibilityCount"], 0)

        omission = inspect_evaluation_case(cases["spec-feature-real-omission"])
        self.assertEqual(omission["documentContext"]["documentType"], "feature_spec")
        self.assertIn("cases-missing", omission["candidateIds"])
        self.assertIn("scenario-coverage-missing", omission["candidateIds"])

    def test_context_semantic_evaluation_checks_classification_and_applicability(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["caseId"] == "spec-shared-contract-context")
        actual = self._review_for(case)

        result = evaluate_semantic_review_case(case, actual)

        self.assertTrue(result["passed"])
        self.assertEqual(result["schemaVersion"], "1.2.0")
        self.assertTrue(result["checks"]["documentContext"]["passed"])
        self.assertTrue(result["checks"]["checklistExpectations"]["passed"])

        actual["document_context"]["document_type"] = "feature_spec"
        actual["checklist_review"][0]["applicability"] = "APPLICABLE"
        drift = evaluate_semantic_review_case(case, actual)
        self.assertFalse(drift["passed"])
        self.assertFalse(drift["checks"]["documentContext"]["passed"])
        self.assertFalse(drift["checks"]["checklistExpectations"]["passed"])

    def test_cross_document_corpus_case_builds_one_bounded_bilateral_packet(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "cross_document")

        actual = inspect_evaluation_case(case)

        self.assertEqual(actual["status"], "semantic_review_required")
        self.assertEqual(actual["workItemCount"], 1)
        self.assertEqual(actual["anchorDocumentId"], "retention-anchor")
        self.assertEqual(actual["relationshipIds"], ["retention-contract-relation"])
        self.assertEqual(actual["documentIds"], ["retention-anchor", "retention-related"])
        self.assertEqual(actual["sourceDigests"], {
            document["documentId"]: document["contentDigest"] for document in case["documents"]
        })
        self.assertGreaterEqual(actual["evidenceItemCount"], 2)

    def test_cross_document_semantic_evaluation_checks_relationship_and_finding_shape(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "cross_document")
        actual = self._review_for(case)

        result = evaluate_semantic_review_case(case, actual)

        self.assertTrue(result["passed"])
        self.assertEqual(result["schemaVersion"], "1.1.0")
        self.assertTrue(result["checks"]["relationshipCoverage"]["passed"])
        self.assertTrue(result["checks"]["relationshipOutcomes"]["passed"])
        self.assertTrue(result["checks"]["semanticFindings"]["passed"])
        schema = json.loads(Path(evaluation_module.__file__).with_name(
            "semantic-evaluation-result.schema.json",
        ).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)

        actual["cross_document_review"][0]["decisions"][0]["semantic_type"] = "ambiguity"
        drift = evaluate_semantic_review_case(case, actual)
        self.assertFalse(drift["passed"])
        self.assertFalse(drift["checks"]["semanticFindings"]["passed"])

    def test_semantic_review_evaluator_accepts_structural_corpus_oracle(self):
        corpus = load_evaluation_corpus()
        for case in corpus["cases"]:
            if case["category"] == "cross_document":
                continue
            result = evaluate_semantic_review_case(case, self._review_for(case))
            self.assertTrue(result["passed"], case["caseId"])
            self.assertEqual(result["caseId"], case["caseId"])

    def test_semantic_review_evaluator_resolves_merged_candidates_to_root_finding(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "duplicate")
        actual = {
            "reviewed_findings": [
                {
                    "finding_id": "duplicate-root",
                    "status": "CONFIRMED",
                    "severity": "P2",
                    "candidate_ids": ["vague-32"],
                },
                {
                    "finding_id": "duplicate-symptom",
                    "status": "MERGED",
                    "candidate_ids": ["vague-33"],
                    "merged_into": "duplicate-root",
                },
            ],
            "readiness": {"status": "REWORK"},
        }

        result = evaluate_semantic_review_case(case, actual)

        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"]["findingClusters"]["actual"], [{
            "candidateIds": ["vague-32", "vague-33"], "severity": "P2",
        }])

    def test_semantic_review_evaluator_reports_readiness_and_cluster_drift(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "ambiguity")
        actual = self._review_for(case)
        actual["readiness"]["status"] = "READY"
        actual["reviewed_findings"][0]["severity"] = "P3"
        result = evaluate_semantic_review_case(case, actual)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["readiness"]["passed"])
        self.assertFalse(result["checks"]["findingClusters"]["passed"])
        self.assertEqual(result["checks"]["findingClusters"]["unexpected"][0]["severity"], "P3")

    def test_semantic_review_corpus_evaluator_reports_missing_and_extra_cases(self):
        corpus = load_evaluation_corpus()
        supplied = {
            "spec-ambiguity": self._review_for(next(
                case for case in corpus["cases"] if case["caseId"] == "spec-ambiguity"
            )),
            "unexpected-case": {"reviewed_findings": [], "readiness": {"status": "READY"}},
        }
        result = evaluate_semantic_review_corpus(corpus, supplied)

        self.assertFalse(result["passed"])
        self.assertEqual(result["metrics"]["evaluatedCaseCount"], 1)
        self.assertEqual(result["metrics"]["missingCaseCount"], 10)
        self.assertEqual(result["metrics"]["unexpectedCaseCount"], 1)
        self.assertEqual(
            next(item for item in result["cases"] if item["caseId"] == "unexpected-case")["status"],
            "unexpected",
        )

    def test_semantic_review_corpus_evaluator_accepts_complete_structural_run(self):
        corpus = load_evaluation_corpus()
        supplied = {case["caseId"]: self._review_for(case) for case in corpus["cases"]}

        result = evaluate_semantic_review_corpus(corpus, supplied)

        self.assertTrue(result["passed"])
        self.assertEqual(result["metrics"]["expectedCaseCount"], len(corpus["cases"]))
        self.assertEqual(result["metrics"]["passedCaseCount"], len(corpus["cases"]))
        self.assertEqual(result["metrics"]["missingCaseCount"], 0)

    def test_terminal_spec_ledger_can_feed_semantic_case_evaluation(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "ambiguity")
        actual = self._review_for(case)
        ledger = {
            "run": {"plugin_id": "assayer.touchstone"},
            "status": "completed",
            "decisions": [{
                "work_item_id": "spec:ambiguity",
                "details": {"review": {"decisions": actual["reviewed_findings"]}},
            }],
            "receipts": [{
                "work_item_id": "spec:ambiguity",
                "metadata": {"readiness": actual["readiness"]},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "run.platform-ledger.json"
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

            loaded = load_semantic_review_from_ledger(ledger_path)
            result = evaluate_semantic_review_case(case, loaded)

        self.assertTrue(result["passed"])

    def test_completed_host_run_ledger_feeds_semantic_corpus_evaluation(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "strong")
        document = case["documents"][0]
        fixture = Path(evaluation_module.__file__).parent / "evaluation" / document["path"]
        scope_document = {"path": str(fixture)}
        if document.get("profile"):
            scope_document["profile"] = document["profile"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=spec_registry(),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "assayer.touchstone",
                "checkId": "SPEC-001",
                "scope": {"files": [scope_document]},
            })["structuredContent"]["result"]
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {
                "includeEvidence": True,
            })["structuredContent"]["result"]["result"]
            packet = inspected["investigations"][0]
            self.assertEqual(packet["evidence"][0]["payload"]["candidateFindings"], [])
            checklist = [{
                "check_id": f"CHK-{index:02d}",
                "status": "PASS",
                "note": "The isolated strong corpus oracle marks this dimension satisfied.",
            } for index in range(1, 19)]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": packet["workItem"]["workItemId"],
                "result": "scanned_no_issue",
                "reason": "The strong corpus Case matches its isolated semantic oracle.",
                "findings": [{
                    "dimension": item["check_id"],
                    "status": "satisfied",
                    "reason": item["note"],
                } for item in checklist],
                "details": {"review": {
                    "review_schema_version": "1.0.0",
                    "readiness_context": {
                        "mandatory_dimensions_checked": True,
                        "unresolved_blockers": False,
                        "escalations": [],
                        "checklist_review": checklist,
                    },
                    "decisions": [],
                }},
            }]})
            transport.call_tool("finish_plugin_run", {"status": "completed"})
            ledger_path = output / started["runId"] / f"{started['runId']}.platform-ledger.json"

            actual = load_semantic_review_from_ledger(ledger_path)
            result = evaluate_semantic_review_case(case, actual)

        self.assertTrue(result["passed"])

    def test_semantic_review_evaluation_result_matches_its_portable_schema(self):
        corpus = load_evaluation_corpus()
        case = next(item for item in corpus["cases"] if item["category"] == "contradiction")
        result = evaluate_semantic_review_case(case, self._review_for(case))
        schema_path = Path(evaluation_module.__file__).with_name(
            "semantic-evaluation-result.schema.json",
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        Draft202012Validator(schema).validate(result)

    def test_semantic_corpus_evaluation_result_matches_its_portable_schema(self):
        corpus = load_evaluation_corpus()
        supplied = {case["caseId"]: self._review_for(case) for case in corpus["cases"]}
        result = evaluate_semantic_review_corpus(corpus, supplied)
        root = Path(evaluation_module.__file__).parent
        case_schema = json.loads(
            (root / "semantic-evaluation-result.schema.json").read_text(encoding="utf-8")
        )
        corpus_schema = json.loads(
            (root / "semantic-evaluation-corpus-result.schema.json").read_text(encoding="utf-8")
        )
        resolver = RefResolver(
            corpus_schema["$id"], corpus_schema,
            store={
                corpus_schema["$id"]: corpus_schema,
                case_schema["$id"]: case_schema,
                "semantic-evaluation-result.schema.json": case_schema,
            },
        )

        Draft202012Validator(corpus_schema, resolver=resolver).validate(result)

    def test_duplicate_case_and_candidate_ids_fail_closed(self):
        corpus = load_evaluation_corpus()
        duplicate_case = copy.deepcopy(corpus)
        duplicate_case["cases"].append(copy.deepcopy(duplicate_case["cases"][0]))
        with self.assertRaisesRegex(ValueError, "caseId"):
            validate_evaluation_corpus(duplicate_case)

        duplicate_candidate = copy.deepcopy(corpus)
        duplicate_candidate["cases"][1]["expected"]["candidates"].append(
            copy.deepcopy(duplicate_candidate["cases"][1]["expected"]["candidates"][0])
        )
        with self.assertRaisesRegex(ValueError, "candidateId"):
            validate_evaluation_corpus(duplicate_candidate)

    def test_cross_document_case_requires_one_matching_anchor(self):
        corpus = load_evaluation_corpus()
        cross_document = {
            "caseId": "cross-doc", "category": "cross_document",
            "description": "Compare an anchor against a related document.",
            "anchorDocumentId": "missing-anchor",
            "relationships": [{
                "relationshipId": "cross-relation", "fromDocumentId": "anchor-doc",
                "toDocumentId": "related-doc", "kind": "compares_with",
                "evidence": "The documents explicitly reference each other.",
                "resolutionOwner": "Product owner",
            }],
            "documents": [
                {"documentId": "anchor-doc", "role": "anchor", "contentDigest": "d332aa315934251ed5aa58edd8404054e1e62d6314dfa825d8bf9cb803013043", "content": "FR-001"},
                {"documentId": "related-doc", "role": "related", "contentDigest": "d332aa315934251ed5aa58edd8404054e1e62d6314dfa825d8bf9cb803013043", "content": "FR-001"},
            ],
            "expected": {"candidates": [], "suppressions": [], "merges": [], "unverified": [], "findings": [], "readiness": "UNVERIFIED"},
        }
        invalid = copy.deepcopy(corpus)
        invalid["cases"].append(cross_document)
        with self.assertRaisesRegex(ValueError, "anchor"):
            validate_evaluation_corpus(invalid)

    def test_cross_document_relationship_and_interruption_metadata_are_closed(self):
        corpus = load_evaluation_corpus()
        cross = next(item for item in corpus["cases"] if item["category"] == "cross_document")
        self.assertEqual(cross["anchorDocumentId"], "retention-anchor")
        self.assertEqual(cross["relationships"][0]["toDocumentId"], "retention-related")
        interrupted = next(item for item in corpus["cases"] if item["category"] == "interrupted")
        self.assertEqual(interrupted["recovery"]["resumeAction"], "resume_plugin_run")

        invalid = copy.deepcopy(corpus)
        cross_invalid = next(item for item in invalid["cases"] if item["category"] == "cross_document")
        cross_invalid["expected"]["findings"][0]["documentIds"] = ["unknown-doc"]
        with self.assertRaisesRegex(ValueError, "unknown document"):
            validate_evaluation_corpus(invalid)

        missing_review = copy.deepcopy(corpus)
        missing_cross = next(
            item for item in missing_review["cases"] if item["category"] == "cross_document"
        )
        missing_cross["expected"].pop("relationshipReviews")
        with self.assertRaisesRegex(ValueError, "relationship reviews"):
            validate_evaluation_corpus(missing_review)

    def test_packaged_document_reference_must_exist(self):
        corpus = load_evaluation_corpus()
        corpus["cases"][0]["documents"][0]["path"] = "missing.md"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            path.write_text(json.dumps(corpus), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unavailable"):
                load_evaluation_corpus(path)


if __name__ == "__main__":
    unittest.main()
