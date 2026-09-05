"""Spec-quality plugin evaluation helpers."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import tempfile
from typing import Any

from ... import PlatformContext
from ...evaluation import (
    load_evaluation_corpus as _load_evaluation_corpus,
    validate_evaluation_corpus,
)
from .runtime import SpecQualityPlugin


_ROOT = Path(__file__).parent
_CORPUS_PATH = _ROOT / "evaluation" / "corpus.json"
_REVIEW_STATUSES = {"CONFIRMED", "SUPPRESSED", "MERGED", "UNVERIFIED"}
_READINESS_STATUSES = {"READY", "REWORK", "ESCALATE", "UNVERIFIED"}


def _array(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} must be an array")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _cluster(signature: tuple[tuple[str, ...], str]) -> dict[str, Any]:
    return {"candidateIds": list(signature[0]), "severity": signature[1]}


def _semantic_signature(
    relationship_id: str, finding: Mapping[str, Any], *, expected: bool,
    expected_status: str | None = None,
) -> tuple[str, str, str, str | None, tuple[str, ...], str]:
    semantic_key = "semanticType" if expected else "semantic_type"
    semantic_type = _identifier(
        finding.get(semantic_key),
        "cross-document semantic type",
    )
    if semantic_type not in {
        "ambiguity", "conflict", "contradiction", "drift", "unverified_dependency",
    }:
        raise ValueError("Cross-document semantic type is unsupported")
    status = expected_status or _identifier(
        finding.get("status"), "cross-document finding status",
    )
    if status not in {"CONFIRMED", "UNVERIFIED"}:
        raise ValueError("Cross-document finding status is unsupported")
    severity = finding.get("severity")
    if severity is not None and severity not in {"P1", "P2", "P3"}:
        raise ValueError("Cross-document finding severity is unsupported")
    document_key = "documentIds" if expected else "document_ids"
    document_ids = tuple(sorted(
        _identifier(value, "cross-document finding document ID")
        for value in _array(finding.get(document_key), document_key)
    ))
    owner_key = "resolutionOwner" if expected else "resolution_owner"
    owner = _identifier(finding.get(owner_key), "cross-document resolution owner")
    return relationship_id, semantic_type, status, severity, document_ids, owner


def _semantic_signature_document(
    signature: tuple[str, str, str, str | None, tuple[str, ...], str],
) -> dict[str, Any]:
    return {
        "relationshipId": signature[0],
        "semanticType": signature[1],
        "status": signature[2],
        "severity": signature[3],
        "documentIds": list(signature[4]),
        "resolutionOwner": signature[5],
    }


def load_evaluation_corpus(path: Path | None = None) -> dict[str, Any]:
    corpus_path = (path or _CORPUS_PATH).resolve()
    corpus = _load_evaluation_corpus(corpus_path)
    fixture_root = corpus_path.parent
    for case in corpus["cases"]:
        for document in case["documents"]:
            if "path" not in document:
                continue
            fixture = (fixture_root / document["path"]).resolve()
            if not fixture.is_relative_to(fixture_root) or not fixture.is_file():
                raise ValueError(f"Evaluation document is unavailable: {document['documentId']}")
    return corpus


def inspect_evaluation_case(
    case: Mapping[str, Any], *, fixture_root: Path | None = None,
) -> dict[str, Any]:
    """Run deterministic inspection without claiming semantic outcomes."""
    documents = case["documents"]
    if case.get("category") == "cross_document":
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            resolved: dict[str, Path] = {}
            for document in documents:
                document_id = str(document["documentId"])
                if "content" in document:
                    safe_name = "".join(
                        character if character.isalnum() or character in "-_" else "-"
                        for character in document_id
                    )
                    path = temporary_root / f"{safe_name}.md"
                    path.write_text(str(document["content"]), encoding="utf-8")
                else:
                    path = (fixture_root or _CORPUS_PATH.parent) / str(document["path"])
                resolved[document_id] = path.resolve()
            anchor_id = str(case["anchorDocumentId"])
            anchor_document = next(
                document for document in documents if document["documentId"] == anchor_id
            )
            anchor_scope: dict[str, Any] = {
                "documentId": anchor_id,
                "path": str(resolved[anchor_id]),
            }
            if anchor_document.get("profile"):
                anchor_scope["profile"] = anchor_document["profile"]
            scope = {
                "anchor": anchor_scope,
                "relatedDocuments": [{
                    "documentId": document["documentId"],
                    "path": str(resolved[str(document["documentId"])]),
                    "selectionReason": "Selected by the fixed cross-document evaluation Case.",
                } for document in documents if document["documentId"] != anchor_id],
                "relationships": [dict(relationship) for relationship in case["relationships"]],
            }
            plugin = SpecQualityPlugin()
            context = PlatformContext("evaluation-corpus", frozenset({"structured_read"}))
            items = plugin.discover(scope, context)
            packet = plugin.inspect(items, plugin.manifest.checks[0], context)[0]
            payload = packet.evidence[0].payload
            cross_packet = payload["crossDocumentPacket"]
            cross_evidence = cross_packet["evidence"]
            return {
                "caseId": case["caseId"],
                "status": "semantic_review_required",
                "candidateIds": [],
                "ruleIds": [],
                "workItemCount": len(items),
                "anchorDocumentId": cross_packet["scope"]["anchor_document_id"],
                "relationshipIds": sorted({
                    str(item["relationship_id"]) for item in cross_evidence
                }),
                "documentIds": sorted({str(item["document_id"]) for item in cross_evidence}),
                "sourceDigests": {
                    str(item["document_id"]): str(item["source_digest"])
                    for item in cross_evidence
                },
                "evidenceItemCount": len(cross_evidence),
            }
    if len(documents) != 1:
        return {"caseId": case["caseId"], "status": "semantic_review_required", "candidateIds": [], "ruleIds": []}
    document = documents[0]
    if "path" not in document:
        return {"caseId": case["caseId"], "status": "semantic_review_required", "candidateIds": [], "ruleIds": []}
    path = (fixture_root or _CORPUS_PATH.parent) / document["path"]
    plugin = SpecQualityPlugin()
    context = PlatformContext("evaluation-corpus", frozenset({"structured_read"}))
    config: dict[str, Any] = {"path": str(path)}
    if document.get("profile"):
        config["profile"] = document["profile"]
    item = plugin.discover({"files": [config]}, context)[0]
    packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
    payload = packet.evidence[0].payload
    candidates = payload["candidates"]
    raw_document_context = payload.get("documentContext", {})
    raw_source_facts = payload.get("sourceFacts", {})
    raw_identifiers = raw_source_facts.get("identifiers", {})
    identifiers = {
        str(kind): [str(item["id"]) for item in values if isinstance(item, Mapping) and item.get("id")]
        for kind, values in raw_identifiers.items()
        if isinstance(values, (tuple, list))
    } if isinstance(raw_identifiers, Mapping) else {}
    statement_kinds = sorted({
        str(kind)
        for statement in raw_source_facts.get("explicitStatements", ())
        if isinstance(statement, Mapping)
        for kind in statement.get("kinds", ())
    }) if isinstance(raw_source_facts, Mapping) else []
    boundaries = raw_document_context.get("responsibilityBoundaries", {})
    if not isinstance(boundaries, Mapping):
        boundaries = {}
    return {
        "caseId": case["caseId"], "status": "inspected",
        "candidates": [{
            "candidateId": candidate["candidateId"],
            "ruleId": candidate["ruleId"],
        } for candidate in candidates],
        "candidateIds": [candidate["candidateId"] for candidate in candidates],
        "ruleIds": [candidate["ruleId"] for candidate in candidates],
        "documentContext": {
            "documentType": raw_document_context.get("documentType"),
            "classificationConfidence": raw_document_context.get("classificationConfidence"),
            "delegatedResponsibilityCount": len(boundaries.get("delegated", ())),
            "excludedResponsibilityCount": len(boundaries.get("excluded", ())),
        },
        "sourceFacts": {
            "identifiers": identifiers,
            "explicitStatementKinds": statement_kinds,
        },
    }


def evaluate_semantic_review_case(
    case: Mapping[str, Any], actual_review: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare one canonical reviewed outcome with a corpus semantic oracle.

    The evaluator intentionally compares structural decisions rather than
    prose: candidate coverage, suppressions, unverified dispositions,
    root-cause grouping with severity, and readiness. It does not pretend that
    string equality can judge whether an Agent's explanation is semantically
    sound.
    """
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError("Evaluation case requires expected semantic outcomes")
    raw_reviewed = _array(actual_review.get("reviewed_findings"), "actual_review.reviewed_findings")
    raw_readiness = actual_review.get("readiness")
    if not isinstance(raw_readiness, Mapping):
        raise ValueError("actual_review.readiness must be an object")
    actual_readiness = _identifier(raw_readiness.get("status"), "actual_review.readiness.status")
    if actual_readiness not in _READINESS_STATUSES:
        raise ValueError("actual_review.readiness.status is unsupported")

    confirmed: dict[str, dict[str, Any]] = {}
    merged: list[tuple[str, tuple[str, ...]]] = []
    actual_suppressions: set[str] = set()
    actual_unverified: set[str] = set()
    handled: list[str] = []
    for index, raw_item in enumerate(raw_reviewed):
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"actual_review.reviewed_findings[{index}] must be an object")
        finding_id = _identifier(raw_item.get("finding_id"), f"reviewed_findings[{index}].finding_id")
        status = _identifier(raw_item.get("status"), f"reviewed_findings[{index}].status")
        if status not in _REVIEW_STATUSES:
            raise ValueError(f"reviewed_findings[{index}].status is unsupported")
        candidate_ids = tuple(
            _identifier(value, f"reviewed_findings[{index}].candidate_ids")
            for value in _array(raw_item.get("candidate_ids"), f"reviewed_findings[{index}].candidate_ids")
        )
        handled.extend(candidate_ids)
        if status == "CONFIRMED":
            severity = _identifier(raw_item.get("severity"), f"reviewed_findings[{index}].severity")
            if severity not in {"P1", "P2", "P3"}:
                raise ValueError(f"reviewed_findings[{index}].severity is unsupported")
            if finding_id in confirmed:
                raise ValueError(f"Duplicate confirmed finding_id: {finding_id}")
            confirmed[finding_id] = {
                "severity": severity, "candidateIds": set(candidate_ids),
            }
        elif status == "MERGED":
            target = _identifier(raw_item.get("merged_into"), f"reviewed_findings[{index}].merged_into")
            merged.append((target, candidate_ids))
        elif status == "SUPPRESSED":
            actual_suppressions.update(candidate_ids)
        else:
            actual_unverified.update(candidate_ids)
    if len(handled) != len(set(handled)):
        raise ValueError("A candidate has multiple actual semantic dispositions")
    for target, candidate_ids in merged:
        if target not in confirmed:
            raise ValueError(f"Merged review references unknown confirmed finding: {target}")
        confirmed[target]["candidateIds"].update(candidate_ids)

    expected_candidate_ids = {
        _identifier(item.get("candidateId"), "expected candidateId")
        for item in _array(expected.get("candidates"), "expected.candidates")
        if isinstance(item, Mapping)
    }
    actual_candidate_ids = set(handled)
    expected_suppressions = {
        _identifier(item.get("candidateId"), "expected suppression candidateId")
        for item in _array(expected.get("suppressions"), "expected.suppressions")
        if isinstance(item, Mapping)
    }
    expected_unverified = {
        _identifier(item.get("candidateId"), "expected unverified candidateId")
        for item in _array(expected.get("unverified"), "expected.unverified")
        if isinstance(item, Mapping)
    }
    expected_clusters = Counter(
        (
            tuple(sorted(
                _identifier(value, "expected finding candidateId")
                for value in _array(item.get("candidateIds"), "expected finding candidateIds")
            )),
            _identifier(item.get("severity"), "expected finding severity"),
        )
        for item in _array(expected.get("findings"), "expected.findings")
        if isinstance(item, Mapping) and not (
            case.get("category") == "cross_document"
            and item.get("semanticType") == "unverified_dependency"
        )
    )
    actual_clusters = Counter(
        (tuple(sorted(value["candidateIds"])), str(value["severity"]))
        for value in confirmed.values()
    )
    expected_readiness = _identifier(expected.get("readiness"), "expected.readiness")

    def id_check(expected_ids: set[str], actual_ids: set[str]) -> dict[str, Any]:
        return {
            "passed": expected_ids == actual_ids,
            "expectedIds": sorted(expected_ids),
            "actualIds": sorted(actual_ids),
            "missingIds": sorted(expected_ids - actual_ids),
            "unexpectedIds": sorted(actual_ids - expected_ids),
        }

    finding_check = {
        "passed": expected_clusters == actual_clusters,
        "expected": [_cluster(item) for item in sorted(expected_clusters.elements())],
        "actual": [_cluster(item) for item in sorted(actual_clusters.elements())],
        "missing": [_cluster(item) for item in sorted((expected_clusters - actual_clusters).elements())],
        "unexpected": [_cluster(item) for item in sorted((actual_clusters - expected_clusters).elements())],
    }
    checks = {
        "candidateCoverage": id_check(expected_candidate_ids, actual_candidate_ids),
        "suppressions": id_check(expected_suppressions, actual_suppressions),
        "unverified": id_check(expected_unverified, actual_unverified),
        "findingClusters": finding_check,
        "readiness": {
            "passed": expected_readiness == actual_readiness,
            "expected": expected_readiness,
            "actual": actual_readiness,
        },
    }
    schema_version = "1.0.0"
    metrics = {
        "expectedCandidateCount": len(expected_candidate_ids),
        "reviewedCandidateCount": len(actual_candidate_ids),
        "expectedFindingCount": sum(expected_clusters.values()),
        "actualFindingCount": sum(actual_clusters.values()),
    }
    limitations = [
        "Reason wording and semantic explanation quality require a model or human judge.",
        "Candidate matching does not prove that the scanner found every semantic defect.",
    ]
    context_assertions = expected.get("contextAssertions")
    if isinstance(context_assertions, Mapping):
        schema_version = "1.2.0"
        raw_actual_context = actual_review.get("document_context")
        actual_context = raw_actual_context if isinstance(raw_actual_context, Mapping) else {}
        expected_document_type = _identifier(
            context_assertions.get("documentType"), "expected context documentType",
        )
        actual_document_type = actual_context.get("document_type")
        document_context_check = {
            "passed": actual_document_type == expected_document_type,
            "expected": expected_document_type,
            "actual": actual_document_type if isinstance(actual_document_type, str) else None,
        }
        expected_rows = {
            (
                _identifier(item.get("checkId"), "expected checklist checkId"),
                _identifier(item.get("applicability"), "expected checklist applicability"),
                _identifier(item.get("status"), "expected checklist status"),
            )
            for item in _array(
                context_assertions.get("checklistResults"),
                "expected.contextAssertions.checklistResults",
            )
            if isinstance(item, Mapping)
        }
        required_check_ids = {item[0] for item in expected_rows}
        raw_actual_checklist = actual_review.get("checklist_review", ())
        actual_rows = {
            (
                _identifier(item.get("check_id"), "actual checklist check_id"),
                _identifier(item.get("applicability"), "actual checklist applicability"),
                _identifier(item.get("status"), "actual checklist status"),
            )
            for item in _array(raw_actual_checklist, "actual_review.checklist_review")
            if isinstance(item, Mapping) and item.get("check_id") in required_check_ids
        }

        def checklist_row(value: tuple[str, str, str]) -> dict[str, str]:
            return {"checkId": value[0], "applicability": value[1], "status": value[2]}

        checklist_check = {
            "passed": expected_rows == actual_rows,
            "expected": [checklist_row(item) for item in sorted(expected_rows)],
            "actual": [checklist_row(item) for item in sorted(actual_rows)],
            "missing": [checklist_row(item) for item in sorted(expected_rows - actual_rows)],
            "unexpected": [checklist_row(item) for item in sorted(actual_rows - expected_rows)],
        }
        checks.update({
            "documentContext": document_context_check,
            "checklistExpectations": checklist_check,
        })
        limitations = [
            "The corpus checks document classification and selected applicability outcomes, while explanation quality still requires a model or human judge.",
            "Source-fact extraction proves that cited identifiers and explicit statements were visible; it does not decide their semantic sufficiency.",
        ]
    if case.get("category") == "cross_document":
        schema_version = "1.1.0"
        expected_findings = {
            _identifier(item.get("findingId"), "expected cross-document finding ID"): item
            for item in _array(expected.get("findings"), "expected.findings")
            if isinstance(item, Mapping)
        }
        expected_reviews = _array(
            expected.get("relationshipReviews"), "expected.relationshipReviews",
        )
        actual_reviews = _array(
            actual_review.get("cross_document_review"),
            "actual_review.cross_document_review",
        )
        expected_relationship_ids: list[str] = []
        actual_relationship_ids: list[str] = []
        expected_outcomes: list[str] = []
        actual_outcomes: list[str] = []
        expected_signatures: Counter[tuple[str, str, str, str | None, tuple[str, ...], str]] = Counter()
        actual_signatures: Counter[tuple[str, str, str, str | None, tuple[str, ...], str]] = Counter()
        for index, raw_review in enumerate(expected_reviews):
            if not isinstance(raw_review, Mapping):
                raise ValueError(f"expected.relationshipReviews[{index}] must be an object")
            relationship_id = _identifier(
                raw_review.get("relationshipId"), "expected relationship review ID",
            )
            outcome = _identifier(raw_review.get("outcome"), "expected relationship outcome")
            expected_relationship_ids.append(relationship_id)
            expected_outcomes.append(f"{relationship_id}:{outcome}")
            for finding_id in _array(raw_review.get("findingIds"), "expected relationship finding IDs"):
                finding_id = _identifier(finding_id, "expected relationship finding ID")
                if finding_id not in expected_findings:
                    raise ValueError(f"Expected relationship review references unknown finding: {finding_id}")
                expected_signatures[_semantic_signature(
                    relationship_id, expected_findings[finding_id], expected=True,
                    expected_status=(
                        "CONFIRMED" if outcome == "FINDING" else "UNVERIFIED"
                    ),
                )] += 1
        for index, raw_review in enumerate(actual_reviews):
            if not isinstance(raw_review, Mapping):
                raise ValueError(f"actual_review.cross_document_review[{index}] must be an object")
            relationship_id = _identifier(
                raw_review.get("relationship_id"), "actual relationship review ID",
            )
            outcome = _identifier(raw_review.get("outcome"), "actual relationship outcome")
            actual_relationship_ids.append(relationship_id)
            actual_outcomes.append(f"{relationship_id}:{outcome}")
            for decision in _array(raw_review.get("decisions"), "actual relationship decisions"):
                if not isinstance(decision, Mapping):
                    raise ValueError("Actual cross-document decisions must be objects")
                actual_signatures[_semantic_signature(
                    relationship_id, decision, expected=False,
                )] += 1
        relationship_coverage = id_check(
            set(expected_relationship_ids), set(actual_relationship_ids),
        )
        relationship_coverage["passed"] = (
            relationship_coverage["passed"]
            and len(expected_relationship_ids) == len(set(expected_relationship_ids))
            and len(actual_relationship_ids) == len(set(actual_relationship_ids))
        )
        semantic_check = {
            "passed": expected_signatures == actual_signatures,
            "expected": [
                _semantic_signature_document(item)
                for item in sorted(expected_signatures.elements(), key=repr)
            ],
            "actual": [
                _semantic_signature_document(item)
                for item in sorted(actual_signatures.elements(), key=repr)
            ],
            "missing": [
                _semantic_signature_document(item)
                for item in sorted((expected_signatures - actual_signatures).elements(), key=repr)
            ],
            "unexpected": [
                _semantic_signature_document(item)
                for item in sorted((actual_signatures - expected_signatures).elements(), key=repr)
            ],
        }
        checks.update({
            "relationshipCoverage": relationship_coverage,
            "relationshipOutcomes": id_check(set(expected_outcomes), set(actual_outcomes)),
            "semanticFindings": semantic_check,
        })
        metrics.update({
            "expectedRelationshipCount": len(expected_relationship_ids),
            "actualRelationshipCount": len(actual_relationship_ids),
            "expectedFindingCount": sum(expected_signatures.values()),
            "actualFindingCount": sum(actual_signatures.values()),
        })
        limitations = [
            "Cross-document explanation quality and the correctness of the cited interpretation still require a model or human judge.",
            "Structural relationship coverage does not prove that every external conflict was in the declared scope.",
        ]
    return {
        "schemaVersion": schema_version,
        "caseId": _identifier(case.get("caseId"), "case.caseId"),
        "passed": all(check["passed"] for check in checks.values()),
        "metrics": metrics,
        "checks": checks,
        "limitations": limitations,
    }


def load_semantic_review_from_ledger(
    path: str | Path, *, work_item_id: str | None = None,
) -> dict[str, Any]:
    """Load the canonical Spec review projection from a terminal Run ledger."""
    ledger_path = Path(path).expanduser().resolve()
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Spec evaluation ledger is unavailable or invalid: {ledger_path}") from error
    if not isinstance(ledger, Mapping):
        raise ValueError("Spec evaluation ledger must be an object")
    run = ledger.get("run")
    if not isinstance(run, Mapping) or run.get("plugin_id") != "assayer.spec-quality":
        raise ValueError("Evaluation ledger does not belong to assayer.spec-quality")
    if ledger.get("status") not in {"completed", "partial"}:
        raise ValueError("Evaluation ledger must contain a valid terminal conclusion")
    decisions = [
        item for item in _array(ledger.get("decisions"), "ledger.decisions")
        if isinstance(item, Mapping)
        and (work_item_id is None or item.get("work_item_id") == work_item_id)
    ]
    if len(decisions) != 1:
        raise ValueError("Select exactly one reviewed Spec WorkItem from the evaluation ledger")
    decision = decisions[0]
    selected_work_item_id = _identifier(decision.get("work_item_id"), "ledger decision work_item_id")
    details = decision.get("details")
    review = details.get("review") if isinstance(details, Mapping) else None
    if not isinstance(review, Mapping):
        raise ValueError("Evaluation ledger Decision does not contain a canonical Spec review")
    reviewed_findings = _array(review.get("decisions"), "ledger decision review.decisions")
    if any(not isinstance(item, Mapping) for item in reviewed_findings):
        raise ValueError("Evaluation ledger review decisions must be objects")
    cross_document_review = _array(
        review.get("cross_document_review", ()),
        "ledger decision review.cross_document_review",
    )
    if any(not isinstance(item, Mapping) for item in cross_document_review):
        raise ValueError("Evaluation ledger cross-document reviews must be objects")
    receipts = [
        item for item in _array(ledger.get("receipts"), "ledger.receipts")
        if isinstance(item, Mapping) and item.get("work_item_id") == selected_work_item_id
    ]
    if len(receipts) != 1:
        raise ValueError("Evaluation ledger requires one matching Decision receipt")
    metadata = receipts[0].get("metadata")
    readiness = metadata.get("readiness") if isinstance(metadata, Mapping) else None
    if not isinstance(readiness, Mapping):
        raise ValueError("Evaluation ledger receipt does not contain reviewed readiness")
    return {
        "reviewed_findings": [dict(item) for item in reviewed_findings],
        "cross_document_review": [dict(item) for item in cross_document_review],
        "document_context": dict(review.get("document_context", {}))
        if isinstance(review.get("document_context"), Mapping) else {},
        "checklist_review": [
            dict(item) for item in _array(
                review.get("readiness_context", {}).get("checklist_review", ())
                if isinstance(review.get("readiness_context"), Mapping) else (),
                "ledger decision review.readiness_context.checklist_review",
            )
            if isinstance(item, Mapping)
        ],
        "readiness": dict(readiness),
    }


def evaluate_semantic_review_corpus(
    corpus: Mapping[str, Any],
    actual_reviews: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate all supplied Case reviews against a plugin-owned corpus.

    Missing Cases are reported explicitly instead of being treated as passes;
    extra Case IDs are reported as unexpected. This makes a partial model run
    distinguishable from a clean corpus pass without requiring the evaluator
    to know how the Agent produced each review.
    """
    cases = _array(corpus.get("cases"), "corpus.cases")
    expected_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"corpus.cases[{index}] must be an object")
        case_id = _identifier(raw_case.get("caseId"), f"corpus.cases[{index}].caseId")
        if case_id in expected_by_id:
            raise ValueError(f"Duplicate corpus caseId: {case_id}")
        expected_by_id[case_id] = raw_case
    provided: dict[str, Mapping[str, Any]] = {}
    for raw_case_id, actual in actual_reviews.items():
        case_id = _identifier(raw_case_id, "actual review caseId")
        if not isinstance(actual, Mapping):
            raise ValueError(f"Actual review for {case_id} must be an object")
        provided[case_id] = actual
    case_results: list[dict[str, Any]] = []
    for case_id, case in expected_by_id.items():
        actual = provided.get(case_id)
        if actual is None:
            case_results.append({"caseId": case_id, "status": "missing", "passed": False})
            continue
        result = evaluate_semantic_review_case(case, actual)
        case_results.append({
            "caseId": case_id,
            "status": "passed" if result["passed"] else "failed",
            "passed": result["passed"],
            "result": result,
        })
    expected_ids = set(expected_by_id)
    unexpected_ids = sorted(set(provided) - expected_ids)
    case_results.extend({
        "caseId": case_id, "status": "unexpected", "passed": False,
    } for case_id in unexpected_ids)
    evaluated = sum(item["status"] in {"passed", "failed"} for item in case_results)
    passed = sum(item["status"] == "passed" for item in case_results)
    failed = sum(item["status"] == "failed" for item in case_results)
    missing = sum(item["status"] == "missing" for item in case_results)
    return {
        "schemaVersion": "1.0.0",
        "corpusId": _identifier(corpus.get("corpusId"), "corpus.corpusId"),
        "pluginId": _identifier(corpus.get("pluginId"), "corpus.pluginId"),
        "passed": not missing and not unexpected_ids and failed == 0,
        "metrics": {
            "expectedCaseCount": len(expected_by_id),
            "providedCaseCount": len(provided),
            "evaluatedCaseCount": evaluated,
            "passedCaseCount": passed,
            "failedCaseCount": failed,
            "missingCaseCount": missing,
            "unexpectedCaseCount": len(unexpected_ids),
        },
        "cases": case_results,
        "limitations": [
            "Case-level explanation quality still requires a model or human judge.",
            "A corpus pass verifies only the encoded structural oracle and cannot prove scanner recall.",
        ],
    }


__all__ = [
    "evaluate_semantic_review_case", "evaluate_semantic_review_corpus",
    "inspect_evaluation_case", "load_semantic_review_from_ledger",
    "load_evaluation_corpus", "validate_evaluation_corpus",
]
