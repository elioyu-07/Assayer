"""Authority-aligned semantic review admission and readiness derivation."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from assayer_platform.contract import (
    CheckContract, CommitReceipt, DecisionProposal, InvestigationPacket,
    PlatformContext, PlatformContractError,
)


_STATUSES = {"CONFIRMED", "SUPPRESSED", "MERGED", "UNVERIFIED"}
_SEVERITIES = {"P1", "P2", "P3"}
_CHECK_STATUSES = {"PASS", "REWORK", "ESCALATE", "UNVERIFIED"}
_EXPECTED_CHECKS = tuple(f"CHK-{number:02d}" for number in range(1, 19))
_REVIEW_SCHEMA_VERSIONS = {"1.0.0", "1.1.0", "1.2.0", "1.3.0"}
_STRICT_REVIEW_SCHEMA_VERSION = "1.3.0"
_APPLICABILITY_STATES = {"APPLICABLE", "NOT_APPLICABLE", "CONDITIONAL", "UNVERIFIED"}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_GENERIC_CHECKLIST_REASONS = re.compile(
    r"^(?:mandatory content dimensions are not all covered|"
    r"the spec contains multiple remediable gaps|"
    r"reviewed and requires changes)[.!]*$",
    re.I,
)
_CROSS_DOCUMENT_SEMANTIC_TYPES = {
    "ambiguity", "conflict", "contradiction", "drift", "unverified_dependency",
}
_READINESS_TO_RESULT = {
    "READY": "scanned_no_issue", "REWORK": "issue_found",
    "ESCALATE": "needs_review", "UNVERIFIED": "needs_review",
}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (tuple, list)):
        raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label} must be an array")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label} must be non-empty")
    return value


def _candidate_source_lines(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        line.strip() for line in str(candidate.get("evidence") or "").splitlines()
        if len(line.strip()) >= 4
    )


def _aggregate_object(value: str) -> bool:
    identifiers = set(re.findall(r"FR-\d{3}[A-Za-z]?", value, re.I))
    return len(identifiers) > 1 or bool(re.search(r"FR-\d{3}.*[~～,，、].*FR-\d{3}", value, re.I))


def _validate_checklist_evidence_refs(
    checklist: Sequence[Any], payload: Mapping[str, Any],
) -> None:
    chunks = _sequence(payload.get("sourceChunks"), "candidate evidence.sourceChunks")
    known: dict[str, Mapping[str, Any]] = {}
    for index, raw_chunk in enumerate(chunks):
        chunk = _mapping(raw_chunk, f"sourceChunks[{index}]")
        chunk_id = _text(chunk.get("source_chunk_id"), f"sourceChunks[{index}].source_chunk_id")
        if chunk_id in known:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"Duplicate source_chunk_id: {chunk_id}")
        known[chunk_id] = chunk
    for index, raw_item in enumerate(checklist):
        item = _mapping(raw_item, f"checklist_review[{index}]")
        refs = _sequence(item.get("evidence_refs"), f"checklist_review[{index}].evidence_refs")
        if not refs:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"checklist_review[{index}] requires at least one source evidence reference",
            )
        for ref_index, raw_ref in enumerate(refs):
            ref = _mapping(raw_ref, f"checklist_review[{index}].evidence_refs[{ref_index}]")
            chunk_id = _text(ref.get("source_chunk_id"), "source evidence reference source_chunk_id")
            chunk = known.get(chunk_id)
            if chunk is None:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", f"Unknown source evidence chunk: {chunk_id}",
                )
            start_line = ref.get("start_line")
            end_line = ref.get("end_line")
            if (
                not isinstance(start_line, int) or isinstance(start_line, bool)
                or not isinstance(end_line, int) or isinstance(end_line, bool)
                or start_line < 1 or end_line < start_line
                or start_line < int(chunk.get("start_line", 0))
                or end_line > int(chunk.get("end_line", 0))
            ):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Source evidence reference lines do not match chunk {chunk_id}",
                )
            if ref.get("source_digest") != chunk.get("source_digest"):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", f"Source evidence digest does not match chunk {chunk_id}",
                )
            if ref.get("document_path") != chunk.get("document_path"):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", f"Source evidence path does not match chunk {chunk_id}",
                )


def _validate_document_context(
    raw_context: Any, packet: InvestigationPacket,
) -> Mapping[str, Any]:
    """Validate the semantic document scope used for applicability decisions."""
    context = _mapping(raw_context, "review.document_context")
    document_type = context.get("document_type")
    if document_type not in {
        "feature_spec", "shared_contract", "data_model", "interface_contract", "rfc_prd", "other",
    }:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "document_context.document_type is invalid")
    _text(context.get("scope_statement"), "document_context.scope_statement")
    boundaries = _mapping(
        context.get("responsibility_boundaries"),
        "document_context.responsibility_boundaries",
    )
    for name in ("owned", "delegated", "excluded"):
        values = _sequence(boundaries.get(name), f"document_context.responsibility_boundaries.{name}")
        for value in values:
            _text(value, f"document_context.responsibility_boundaries.{name} item")
    related = _sequence(context.get("related_documents", ()), "document_context.related_documents")
    for value in related:
        _text(value, "document_context.related_documents item")
    _text(context.get("selected_profile"), "document_context.selected_profile")
    evidence = _sequence(context.get("classification_evidence"), "document_context.classification_evidence")
    if not evidence:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "document_context requires classification evidence")
    _text(context.get("classification_confidence"), "document_context.classification_confidence")
    if context.get("classification_confidence") not in _CONFIDENCE_LEVELS:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "document_context.classification_confidence is invalid")
    questions = _sequence(
        context.get("unresolved_classification_questions", ()),
        "document_context.unresolved_classification_questions",
    )
    for value in questions:
        _text(value, "document_context.unresolved_classification_questions item")
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    _validate_checklist_evidence_refs(
        [{"check_id": "CHK-01", "evidence_refs": evidence}], payload,
    )
    return context


def _validate_strict_checklist_items(
    checklist: Sequence[Mapping[str, Any]],
) -> None:
    """Apply the v1.3 evidence and applicability contract to checklist rows."""
    for index, item in enumerate(checklist):
        label = f"checklist_review[{index}]"
        applicability = item.get("applicability")
        if applicability not in _APPLICABILITY_STATES:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", f"{label}.applicability is invalid",
            )
        status = item.get("status")
        if applicability == "NOT_APPLICABLE" and status not in {"PASS", "UNVERIFIED"}:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"{label} marked NOT_APPLICABLE cannot be {status}",
            )
        if applicability == "UNVERIFIED" and status == "PASS":
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", f"{label} with UNVERIFIED applicability cannot PASS",
            )
        if status in {"REWORK", "ESCALATE"} and applicability not in {"APPLICABLE", "CONDITIONAL"}:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"{label} {status} requires APPLICABLE or CONDITIONAL applicability",
            )
        for field in ("observation", "gap", "impact", "recommendation", "owner", "next_action"):
            _text(item.get(field), f"{label}.{field}")
        if item.get("confidence") not in _CONFIDENCE_LEVELS:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label}.confidence is invalid")
        if _GENERIC_CHECKLIST_REASONS.fullmatch(str(item.get("gap", "")).strip()):
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", f"{label}.gap must identify a concrete source-bound gap",
            )
        refs = item.get("finding_refs", ())
        if not isinstance(refs, (tuple, list)) or any(not isinstance(value, str) or not value.strip() for value in refs):
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label}.finding_refs must be an array of IDs")
        if status == "REWORK" and not refs:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", f"{label} REWORK requires at least one finding_ref",
            )


def _validate_strict_reviewed_findings(
    reviewed: Sequence[Mapping[str, Any]], packet: InvestigationPacket,
) -> None:
    """Require actionable, source-bound fields on v1.3 confirmed findings."""
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    for index, finding in enumerate(reviewed):
        if finding.get("status") != "CONFIRMED":
            continue
        if finding.get("semantic_type") in _CROSS_DOCUMENT_SEMANTIC_TYPES:
            # Cross-document findings use bilateral evidence refs and are
            # validated by _validate_cross_document_finding below.
            continue
        label = f"decisions[{index}]"
        for field in ("observed_fact", "affected_elements", "owner", "next_action", "confidence"):
            value = finding.get(field)
            if field == "affected_elements":
                values = _sequence(value, f"{label}.{field}")
                if not values:
                    raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label}.{field} is required")
                for element in values:
                    _text(element, f"{label}.{field} item")
            elif field == "confidence":
                if value not in _CONFIDENCE_LEVELS:
                    raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label}.confidence is invalid")
            else:
                _text(value, f"{label}.{field}")
        refs = _sequence(finding.get("evidence_refs"), f"{label}.evidence_refs")
        if not refs:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"{label}.evidence_refs is required")
        _validate_checklist_evidence_refs(
            [{"check_id": "CHK-01", "evidence_refs": refs}], payload,
        )


def _validate_source_fact_consistency(
    reviewed: Sequence[Mapping[str, Any]],
    checklist: Sequence[Mapping[str, Any]],
    packet: InvestigationPacket,
) -> None:
    """Reject direct absence claims contradicted by frozen source facts."""
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    facts = payload.get("sourceFacts")
    if not isinstance(facts, Mapping):
        return
    identifiers = facts.get("identifiers", {})
    counts = {
        str(kind): len(values) if isinstance(values, (tuple, list)) else 0
        for kind, values in identifiers.items()
    } if isinstance(identifiers, Mapping) else {}
    statements = facts.get("explicitStatements", ())
    explicit_kinds = {
        str(kind)
        for statement in statements if isinstance(statement, Mapping)
        for kind in statement.get("kinds", ())
    } if isinstance(statements, (tuple, list)) else set()
    claims: list[tuple[str, str]] = []
    for item in checklist:
        for field in ("observation", "gap", "note", "recommendation"):
            if item.get(field):
                claims.append((f"{item.get('check_id')}.{field}", str(item[field])))
    for index, item in enumerate(reviewed):
        for field in ("observed_fact", "gap", "impact", "recommendation", "review_note"):
            if item.get(field):
                claims.append((f"decisions[{index}].{field}", str(item[field])))

    contradiction_patterns = {
        "functional_requirements": re.compile(
            r"\b(?:no|without|missing|absent|lacks?)\b[^.\n]{0,60}\b(?:fr|functional requirements?)\b|"
            r"\b(?:fr|functional requirements?)\b[^.\n]{0,60}\b(?:no|without|missing|absent|lacks?)\b[^.\n]{0,30}\b(?:id|identifier|number|stable)\b",
            re.I,
        ),
        "acceptance_criteria": re.compile(
            r"\b(?:no|without|missing|absent|lacks?)\b[^.\n]{0,60}\b(?:ac|acceptance criteria?)\b",
            re.I,
        ),
        "test_cases": re.compile(
            r"\b(?:no|without|missing|absent|lacks?)\b[^.\n]{0,60}\b(?:case|test cases?)\b",
            re.I,
        ),
    }
    for label, claim in claims:
        for kind, pattern in contradiction_patterns.items():
            if counts.get(kind, 0) and pattern.search(claim):
                raise PlatformContractError(
                    "INVALID_SEMANTIC_CLAIM",
                    f"{label} claims that {kind.replace('_', ' ')} are absent, but frozen source facts contain them",
                )
    if "no_migration" in explicit_kinds:
        migration_absence = re.compile(
            r"\bmigration\b[^.\n]{0,60}\b(?:unspecified|not specified|missing|absent|undefined)\b|"
            r"\b(?:unspecified|not specified|missing|absent|undefined)\b[^.\n]{0,60}\bmigration\b",
            re.I,
        )
        for label, claim in claims:
            if migration_absence.search(claim):
                raise PlatformContractError(
                    "INVALID_SEMANTIC_CLAIM",
                    f"{label} contradicts the explicit no-migration source statement",
                )


def _validate_cross_document_finding(
    item: Mapping[str, Any], finding_id: str, evidence: Sequence[str],
    payload: Mapping[str, Any],
) -> None:
    """Validate a reviewer-origin semantic claim against both frozen sources."""
    semantic_type = item.get("semantic_type")
    if semantic_type not in _CROSS_DOCUMENT_SEMANTIC_TYPES:
        return
    packet = _mapping(payload.get("crossDocumentPacket"), "cross-document evidence packet")
    scope = _mapping(packet.get("scope"), "cross-document evidence scope")
    raw_relationships = _sequence(scope.get("relationships"), "cross-document relationships")
    relationships = {
        _text(relationship.get("relationship_id"), "cross-document relationship_id"):
        _mapping(relationship, "cross-document relationship")
        for relationship in raw_relationships
    }
    relationship_id = _text(
        item.get("relationship_id"), f"cross-document finding {finding_id}.relationship_id",
    )
    relationship = relationships.get(relationship_id)
    if relationship is None:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} references an unknown relationship",
        )
    status = item.get("status")
    if semantic_type == "unverified_dependency" and status != "UNVERIFIED":
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID", "unverified_dependency findings must remain UNVERIFIED",
        )
    if semantic_type != "unverified_dependency" and status != "CONFIRMED":
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document {semantic_type} findings must be CONFIRMED",
        )
    candidate_ids = _sequence(
        item.get("candidate_ids"), f"cross-document finding {finding_id}.candidate_ids",
    )
    if candidate_ids:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            "Cross-document findings must remain reviewer-origin until the plugin emits semantic candidates",
        )
    object_id = _text(item.get("object_id"), f"cross-document finding {finding_id}.object_id")
    if _aggregate_object(object_id):
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID", f"Cross-document finding {finding_id} must identify one business object",
        )
    dimension = _text(item.get("dimension"), f"cross-document finding {finding_id}.dimension")
    if dimension not in _EXPECTED_CHECKS:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} must map to CHK-01 through CHK-18",
        )
    for field in ("gap", "impact", "recommendation", "resolution_owner", "next_action"):
        _text(item.get(field), f"cross-document finding {finding_id}.{field}")
    affected = _sequence(
        item.get("affected_elements"), f"cross-document finding {finding_id}.affected_elements",
    )
    if not affected:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} requires affected_elements",
        )
    for value in affected:
        _text(value, f"cross-document finding {finding_id}.affected_elements item")
    if item.get("resolution_owner") != relationship.get("resolution_owner"):
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} resolution owner does not match its relationship",
        )
    expected_document_ids = {
        str(relationship.get("from_document_id")), str(relationship.get("to_document_id")),
    }
    document_ids = [
        _text(value, f"cross-document finding {finding_id}.document_ids")
        for value in _sequence(
            item.get("document_ids"), f"cross-document finding {finding_id}.document_ids",
        )
    ]
    if len(document_ids) != 2 or set(document_ids) != expected_document_ids:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} must name both relationship documents exactly once",
        )

    known_evidence: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_evidence in _sequence(packet.get("evidence"), "cross-document evidence"):
        evidence_item = _mapping(raw_evidence, "cross-document evidence item")
        if evidence_item.get("relationship_id") != relationship_id:
            continue
        key = (
            _text(evidence_item.get("document_id"), "cross-document evidence document_id"),
            _text(evidence_item.get("source_chunk_id"), "cross-document evidence source_chunk_id"),
        )
        known_evidence[key] = evidence_item
    refs = _sequence(
        item.get("evidence_refs"), f"cross-document finding {finding_id}.evidence_refs",
    )
    if len(refs) < 2:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID", f"Cross-document finding {finding_id} requires bilateral evidence",
        )
    covered_documents: set[str] = set()
    for index, raw_ref in enumerate(refs):
        ref = _mapping(raw_ref, f"cross-document finding {finding_id}.evidence_refs[{index}]")
        document_id = _text(ref.get("document_id"), "cross-document evidence ref document_id")
        chunk_id = _text(ref.get("source_chunk_id"), "cross-document evidence ref source_chunk_id")
        source = known_evidence.get((document_id, chunk_id))
        if source is None:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Cross-document finding {finding_id} references unknown relationship evidence",
            )
        for field in (
            "document_path", "source_digest", "start_line", "end_line", "excerpt",
        ):
            if ref.get(field) != source.get(field):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Cross-document finding {finding_id} evidence {field} does not match its frozen source",
                )
        if source.get("excerpt") not in evidence:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Cross-document finding {finding_id} direct evidence omits a referenced excerpt",
            )
        covered_documents.add(document_id)
    if covered_documents != expected_document_ids:
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            f"Cross-document finding {finding_id} evidence must cover both relationship documents",
        )


def validate_checklist_reviews(
    raw_items: Sequence[Any], packet: InvestigationPacket, *,
    expected_check_ids: Sequence[str],
) -> list[Mapping[str, Any]]:
    """Validate one ordered, evidence-backed checklist review batch."""
    expected = tuple(expected_check_ids)
    if not expected or len(expected) != len(set(expected)) or any(item not in _EXPECTED_CHECKS for item in expected):
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Checklist review declares invalid expected CHK IDs")
    items = [_mapping(item, f"checklist_review[{index}]") for index, item in enumerate(raw_items)]
    actual = tuple(item.get("check_id") for item in items)
    if actual != expected:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Checklist review must cover its declared CHK IDs once and in order")
    for index, item in enumerate(items):
        if item.get("status") not in _CHECK_STATUSES:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"checklist_review[{index}] has an invalid status")
        _text(item.get("note"), f"checklist_review[{index}].note")
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    _validate_checklist_evidence_refs(items, payload)
    return items


def _readiness(reviewed: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> tuple[str, str]:
    checklist = _sequence(context.get("checklist_review"), "readiness_context.checklist_review")
    escalations = _sequence(context.get("escalations"), "readiness_context.escalations")
    if escalations or any(item.get("status") == "ESCALATE" for item in checklist if isinstance(item, Mapping)):
        return "ESCALATE", "A business, governance, scope, authority, or exemption owner must decide an unresolved item."
    if context.get("mandatory_dimensions_checked") is not True:
        return "UNVERIFIED", "Not every mandatory applicable dimension has been semantically reviewed."
    if any(item.get("status") == "UNVERIFIED" for item in checklist if isinstance(item, Mapping)):
        return "UNVERIFIED", "At least one checklist dimension lacks required evidence."
    if any(item.get("status") == "UNVERIFIED" for item in reviewed):
        return "UNVERIFIED", "At least one reviewed finding lacks required evidence or authority."
    if context.get("unresolved_blockers") is True:
        return "REWORK", "The Spec contains an unresolved blocker that can be addressed by rework."
    if any(item.get("status") == "REWORK" for item in checklist if isinstance(item, Mapping)):
        return "REWORK", "At least one checklist dimension requires Spec changes."
    if any(item.get("status") == "CONFIRMED" and item.get("severity") in {"P1", "P2"} for item in reviewed):
        return "REWORK", "At least one confirmed P1/P2 finding requires Spec changes."
    return "READY", "Every mandatory dimension was reviewed with no confirmed P1/P2, material unverified fact, or blocker."


def validate_review_decisions(
    raw_decisions: Sequence[Any], packet: InvestigationPacket, *,
    expected_candidate_ids: Sequence[str] | None = None,
    prior_decisions: Sequence[Mapping[str, Any]] = (),
) -> list[Mapping[str, Any]]:
    """Validate Spec review findings and their Evidence links before persistence.

    ``expected_candidate_ids`` limits validation to one checkpoint page.  When
    omitted, the complete candidate collection must be covered.  Prior
    decisions participate only in cross-checkpoint identity and merge checks;
    they are never rewritten.
    """
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    raw_candidates = _sequence(payload.get("candidateFindings"), "candidateFindings")
    candidates = {_text(item.get("candidate_id"), "candidate_id"): _mapping(item, "candidate") for item in raw_candidates}
    reviewed: list[Mapping[str, Any]] = []
    handled: list[str] = []
    prior_finding_ids = {
        _text(item.get("finding_id"), "prior decision finding_id") for item in prior_decisions
    }
    finding_ids = set(prior_finding_ids)
    for index, raw_item in enumerate(raw_decisions):
        item = _mapping(raw_item, f"decisions[{index}]")
        finding_id = _text(item.get("finding_id"), f"decisions[{index}].finding_id")
        if finding_id in finding_ids:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"Duplicate finding_id: {finding_id}")
        finding_ids.add(finding_id)
        status = item.get("status")
        if status not in _STATUSES:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"Unsupported review status: {status}")
        candidate_ids = [
            _text(value, f"decisions[{index}].candidate_ids")
            for value in _sequence(item.get("candidate_ids"), f"decisions[{index}].candidate_ids")
        ]
        cross_document_semantic = item.get("semantic_type") in _CROSS_DOCUMENT_SEMANTIC_TYPES
        if not candidate_ids and status != "CONFIRMED" and not (
            cross_document_semantic and status == "UNVERIFIED"
        ):
            raise PlatformContractError("SPEC_REVIEW_INVALID", "Only reviewer-origin CONFIRMED findings may omit candidate_ids")
        handled.extend(candidate_ids)
        if status == "CONFIRMED":
            if item.get("severity") not in _SEVERITIES:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} requires P1, P2, or P3")
            object_id = _text(item.get("object_id"), f"confirmed finding {finding_id}.object_id")
            dimension = _text(item.get("dimension"), f"confirmed finding {finding_id}.dimension")
            if dimension not in _EXPECTED_CHECKS:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Confirmed finding {finding_id} must map to CHK-01 through CHK-18",
                )
            if _aggregate_object(object_id):
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} must identify one business object")
            for field in ("gap", "impact", "recommendation", "closure_evidence"):
                _text(item.get(field), f"confirmed finding {finding_id}.{field}")
            evidence = [
                _text(value, f"confirmed finding {finding_id}.evidence")
                for value in _sequence(item.get("evidence"), f"confirmed finding {finding_id}.evidence")
            ]
            if not evidence:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} requires direct evidence")
            blob = "\n".join(evidence)
            linked = [candidates[candidate_id] for candidate_id in candidate_ids if candidate_id in candidates]
            source_objects = {str(candidate.get("object_id") or "").strip() for candidate in linked if candidate.get("object_id")}
            if len(source_objects) > 1:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} merges multiple candidate objects")
            if linked and not any(line in blob for candidate in linked for line in _candidate_source_lines(candidate)):
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} evidence does not trace to its candidates")
            if not linked:
                if cross_document_semantic:
                    _validate_cross_document_finding(item, finding_id, evidence, payload)
                else:
                    target = Path(str(packet.metadata.get("path"))).read_text(encoding="utf-8-sig")
                    if any(value not in target for value in evidence):
                        raise PlatformContractError("SPEC_REVIEW_INVALID", f"Reviewer-origin finding {finding_id} evidence is not in the target Spec")
            if item.get("merged_into") is not None:
                raise PlatformContractError("SPEC_REVIEW_INVALID", "CONFIRMED findings cannot set merged_into")
        else:
            if item.get("severity") is not None:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"{status} findings cannot carry final severity")
            _text(item.get("review_note"), f"{status} finding {finding_id}.review_note")
            if status != "MERGED" and item.get("merged_into") is not None:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"{status} findings cannot set merged_into")
            if cross_document_semantic:
                evidence = [
                    _text(value, f"cross-document finding {finding_id}.evidence")
                    for value in _sequence(
                        item.get("evidence"), f"cross-document finding {finding_id}.evidence",
                    )
                ]
                _validate_cross_document_finding(item, finding_id, evidence, payload)
        reviewed.append(item)

    expected = set(candidates) if expected_candidate_ids is None else set(expected_candidate_ids)
    actual = set(handled)
    if actual - set(candidates):
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Review references unknown candidate IDs")
    if actual - expected:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Checkpoint review references candidates outside its declared item IDs")
    if expected - actual:
        raise PlatformContractError("SPEC_REVIEW_INCOMPLETE", "Review decisions must handle every declared candidate exactly once")
    if len(handled) != len(set(handled)):
        raise PlatformContractError("SPEC_REVIEW_INVALID", "A scanner candidate is handled more than once")
    all_decisions = [*prior_decisions, *reviewed]
    confirmed_ids = {item["finding_id"] for item in all_decisions if item.get("status") == "CONFIRMED"}
    for item in reviewed:
        if item.get("status") == "MERGED" and item.get("merged_into") not in confirmed_ids:
            raise PlatformContractError("SPEC_REVIEW_INVALID", "MERGED findings must target an already validated CONFIRMED finding")
    return reviewed


def validate_cross_document_reviews(
    raw_reviews: Sequence[Any], packet: InvestigationPacket, *,
    expected_relationship_ids: Sequence[str],
    prior_decisions: Sequence[Mapping[str, Any]] = (),
) -> list[Mapping[str, Any]]:
    """Validate one or more required relationship review checkpoints."""
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    cross_packet = _mapping(payload.get("crossDocumentPacket"), "cross-document evidence packet")
    scope = _mapping(cross_packet.get("scope"), "cross-document evidence scope")
    relationships = {
        _text(item.get("relationship_id"), "cross-document relationship_id"):
        _mapping(item, "cross-document relationship")
        for item in _sequence(scope.get("relationships"), "cross-document relationships")
    }
    expected = tuple(expected_relationship_ids)
    if not expected or len(expected) != len(set(expected)) or set(expected) - set(relationships):
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID", "Cross-document review declares invalid relationship IDs",
        )
    reviews = [
        _mapping(item, f"cross_document_review[{index}]")
        for index, item in enumerate(raw_reviews)
    ]
    actual = [
        _text(item.get("relationship_id"), "cross_document_review relationship_id")
        for item in reviews
    ]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID",
            "Cross-document review must cover its declared relationships exactly once",
        )
    decisions: list[Mapping[str, Any]] = []
    for review in reviews:
        relationship_id = str(review["relationship_id"])
        outcome = review.get("outcome")
        if outcome not in {"COMPATIBLE", "FINDING", "UNVERIFIED"}:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Cross-document relationship {relationship_id} has an invalid outcome",
            )
        _text(review.get("note"), f"cross-document relationship {relationship_id}.note")
        raw_decisions = _sequence(
            review.get("decisions"),
            f"cross-document relationship {relationship_id}.decisions",
        )
        current = [
            _mapping(item, f"cross-document relationship {relationship_id} decision")
            for item in raw_decisions
        ]
        if outcome == "COMPATIBLE" and current:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Compatible relationship {relationship_id} cannot carry findings",
            )
        if outcome != "COMPATIBLE" and not current:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Cross-document relationship {relationship_id} requires a semantic decision",
            )
        for decision in current:
            if decision.get("relationship_id") != relationship_id:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Cross-document relationship {relationship_id} decision is assigned elsewhere",
                )
            if outcome == "FINDING" and (
                decision.get("status") != "CONFIRMED"
                or decision.get("semantic_type") == "unverified_dependency"
            ):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Finding relationship {relationship_id} requires confirmed semantic findings",
                )
            if outcome == "UNVERIFIED" and (
                decision.get("status") != "UNVERIFIED"
                or decision.get("semantic_type") != "unverified_dependency"
            ):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"Unverified relationship {relationship_id} requires unverified_dependency",
                )
        validate_review_decisions(
            current, packet, expected_candidate_ids=(),
            prior_decisions=[*prior_decisions, *decisions],
        )
        decisions.extend(current)
    return reviews


def evaluate_review(proposal: DecisionProposal, packet: InvestigationPacket) -> dict[str, Any]:
    details = _mapping(proposal.details, "details")
    review = _mapping(details.get("review"), "details.review")
    review_schema_version = review.get("review_schema_version")
    if review_schema_version not in _REVIEW_SCHEMA_VERSIONS:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Unsupported review_schema_version")
    context = _mapping(review.get("readiness_context"), "readiness_context")
    if not isinstance(context.get("mandatory_dimensions_checked"), bool) or not isinstance(context.get("unresolved_blockers"), bool):
        raise PlatformContractError("SPEC_REVIEW_INVALID", "Readiness context booleans are required")
    escalations = _sequence(context.get("escalations"), "readiness_context.escalations")
    for item in escalations:
        _text(item, "readiness_context.escalations item")
    checklist = _sequence(context.get("checklist_review"), "readiness_context.checklist_review")
    if len(checklist) != 18:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "checklist_review must cover CHK-01 through CHK-18")
    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    if review_schema_version in {"1.1.0", "1.2.0", _STRICT_REVIEW_SCHEMA_VERSION}:
        checklist = validate_checklist_reviews(checklist, packet, expected_check_ids=_EXPECTED_CHECKS)
    else:
        actual_ids = []
        for index, raw_item in enumerate(checklist):
            item = _mapping(raw_item, f"checklist_review[{index}]")
            actual_ids.append(item.get("check_id"))
            if item.get("status") not in _CHECK_STATUSES:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"checklist_review[{index}] has an invalid status")
            _text(item.get("note"), f"checklist_review[{index}].note")
        if tuple(actual_ids) != _EXPECTED_CHECKS:
            raise PlatformContractError("SPEC_REVIEW_INVALID", "checklist_review must preserve CHK-01 through CHK-18 order")
    if review_schema_version == _STRICT_REVIEW_SCHEMA_VERSION:
        _validate_document_context(review.get("document_context"), packet)
        _validate_strict_checklist_items(checklist)
    raw_candidates = _sequence(payload.get("candidateFindings"), "candidateFindings")
    candidates = {_text(item.get("candidate_id"), "candidate_id"): _mapping(item, "candidate") for item in raw_candidates}
    raw_decisions = _sequence(review.get("decisions"), "review decisions")
    reviewed = validate_review_decisions(raw_decisions, packet)
    if review_schema_version == _STRICT_REVIEW_SCHEMA_VERSION:
        _validate_strict_reviewed_findings(reviewed, packet)
    cross_packet = payload.get("crossDocumentPacket")
    raw_cross_reviews = review.get("cross_document_review", ())
    if isinstance(cross_packet, Mapping):
        scope = _mapping(cross_packet.get("scope"), "cross-document evidence scope")
        expected_relationship_ids = tuple(
            _text(item.get("relationship_id"), "cross-document relationship_id")
            for item in _sequence(scope.get("relationships"), "cross-document relationships")
        )
        cross_reviews = validate_cross_document_reviews(
            _sequence(raw_cross_reviews, "review.cross_document_review"), packet,
            expected_relationship_ids=expected_relationship_ids,
        )
    else:
        cross_reviews = list(_sequence(raw_cross_reviews, "review.cross_document_review"))
        if cross_reviews:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                "Single-document review cannot contain cross-document relationship results",
            )
    if review_schema_version == _STRICT_REVIEW_SCHEMA_VERSION:
        _validate_source_fact_consistency(reviewed, checklist, packet)

    checklist_by_id = {str(item["check_id"]): str(item["status"]) for item in checklist}
    for finding in reviewed:
        status = finding.get("status")
        dimension = finding.get("dimension")
        checklist_status = checklist_by_id.get(str(dimension))
        if status == "CONFIRMED" and checklist_status not in {"REWORK", "ESCALATE"}:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Confirmed finding {finding.get('finding_id')} must map to a REWORK or ESCALATE checklist dimension",
            )
        if status == "UNVERIFIED" and checklist_status not in {"UNVERIFIED", "ESCALATE"}:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                f"Unverified finding {finding.get('finding_id')} must map to an UNVERIFIED or ESCALATE checklist dimension",
            )
    for cross_review in cross_reviews:
        for decision in cross_review.get("decisions", ()):
            dimension = str(decision.get("dimension"))
            semantic_type = decision.get("semantic_type")
            checklist_status = checklist_by_id.get(dimension)
            if semantic_type == "conflict" and checklist_status != "ESCALATE":
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Cross-document conflicts require an ESCALATE checklist result",
                )
            if semantic_type == "unverified_dependency" and checklist_status not in {"UNVERIFIED", "ESCALATE"}:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Unverified cross-document dependencies must remain unresolved",
                )
            if semantic_type in {"ambiguity", "contradiction", "drift"} and checklist_status not in {"REWORK", "ESCALATE"}:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Confirmed cross-document defects require REWORK or ESCALATE",
                )

    # A checklist-level REWORK is a semantic claim, not a free-form summary.
    # It must be backed by at least one accepted semantic Finding for the same
    # dimension.  This prevents a completed-looking result from declaring many
    # defects while persisting zero actionable findings.
    if review_schema_version == _STRICT_REVIEW_SCHEMA_VERSION:
        confirmed_dimensions = {
            str(item.get("dimension")) for item in reviewed
            if item.get("status") == "CONFIRMED" and item.get("dimension")
        }
        for checklist_item in checklist:
            if checklist_item.get("status") == "REWORK" and str(checklist_item.get("check_id")) not in confirmed_dimensions:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    f"{checklist_item.get('check_id')} REWORK requires an accepted CONFIRMED finding",
                )
        reviewed_by_id = {
            str(item.get("finding_id")): item for item in reviewed
        }
        for checklist_item in checklist:
            dimension = str(checklist_item.get("check_id"))
            for finding_id in checklist_item.get("finding_refs", ()):
                finding = reviewed_by_id.get(str(finding_id))
                if finding is None:
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        f"{dimension} references an unknown finding_id: {finding_id}",
                    )
                if str(finding.get("dimension")) != dimension:
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        f"{dimension} finding_ref {finding_id} is mapped to another dimension",
                    )
                if finding.get("status") != "CONFIRMED":
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        f"{dimension} finding_ref {finding_id} is not CONFIRMED",
                    )

    readiness, reason = _readiness(reviewed, context)
    expected_result = _READINESS_TO_RESULT[readiness]
    if proposal.result != expected_result:
        raise PlatformContractError("SPEC_READINESS_MISMATCH", f"Decision result must be {expected_result} for readiness {readiness}")
    finding_status = {finding.dimension: finding.status for finding in proposal.findings}
    for item in checklist:
        expected_status = "satisfied" if item["status"] == "PASS" else "violated" if item["status"] == "REWORK" else "conflicted" if item["status"] == "ESCALATE" else "unresolved"
        if finding_status.get(item["check_id"]) != expected_status:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"Finding status for {item['check_id']} does not match checklist review")
    return {
        "report_schema_version": "1.0.0",
        "pipeline": {"candidate_source": "deterministic_scanner", "reviewed_findings_present": True, "legacy_findings_projection": "confirmed_only"},
        "policy": {"policy_id": payload.get("policyId"), "policy_version": payload.get("policyVersion"), "selected_profile": payload.get("profile"), "selected_overlays": []},
        "review_summary": {
            "candidate_count": len(candidates), "handled_candidate_count": len(candidates), "pending_candidate_count": 0,
            "status_counts": {status: sum(item.get("status") == status for item in reviewed) for status in sorted(_STATUSES)},
        },
        "candidates": list(raw_candidates), "reviewed_findings": list(reviewed),
        "cross_document_scope": (
            payload.get("crossDocumentPacket", {}).get("scope")
            if isinstance(payload.get("crossDocumentPacket"), Mapping) else None
        ),
        "cross_document_review": list(cross_reviews),
        "checklist_results": list(payload.get("checklist_results", ())),
        "document_context": review.get("document_context"),
        "readiness_context": context, "readiness": {"status": readiness, "reason": reason},
        "evidence_manifest": payload.get("evidence_manifest"),
    }


class AssSpecDecisionCommitter:
    """Admit only authority-complete Spec review decisions."""

    def commit(self, proposal: DecisionProposal, packet: InvestigationPacket,
               check: CheckContract, context: PlatformContext) -> CommitReceipt:
        # Older platform callers may submit only dimension-level proposals.
        # Preserve that API as an explicitly candidate-only receipt; it is not
        # eligible to claim a reviewed readiness result in the summary.
        if not isinstance(proposal.details, Mapping) or "review" not in proposal.details:
            if proposal.result == "issue_found":
                raise PlatformContractError(
                    "SPEC_REVIEW_REQUIRED",
                    "A formal Spec issue requires the canonical review envelope",
                )
            return CommitReceipt(
                f"commit:{context.run_id}:{proposal.work_item_id}:candidate",
                proposal.work_item_id, check.check_id, check.version, proposal.result, "memory",
                {"reviewed": False, "candidateOnly": True, "decisionOwner": "ass-spec"},
            )
        report = evaluate_review(proposal, packet)
        digest = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        return CommitReceipt(
            f"commit:{context.run_id}:{proposal.work_item_id}:{digest[:12]}",
            proposal.work_item_id, check.check_id, check.version, proposal.result, "memory",
            {"reviewed": True, "readiness": report["readiness"], "reviewDigest": digest},
            authority="platform",
        )


__all__ = [
    "AssSpecDecisionCommitter", "evaluate_review",
    "validate_checklist_reviews", "validate_cross_document_reviews",
    "validate_review_decisions",
]
