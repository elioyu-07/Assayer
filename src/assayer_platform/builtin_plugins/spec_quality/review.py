"""Authority-aligned semantic review admission and readiness derivation."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...contract import (
    CheckContract, CommitReceipt, DecisionProposal, InvestigationPacket,
    PlatformContext, PlatformContractError,
)


_STATUSES = {"CONFIRMED", "SUPPRESSED", "MERGED", "UNVERIFIED"}
_SEVERITIES = {"P1", "P2", "P3"}
_CHECK_STATUSES = {"PASS", "REWORK", "ESCALATE", "UNVERIFIED"}
_EXPECTED_CHECKS = tuple(f"CHK-{number:02d}" for number in range(1, 19))
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
        if not candidate_ids and status != "CONFIRMED":
            raise PlatformContractError("SPEC_REVIEW_INVALID", "Only reviewer-origin CONFIRMED findings may omit candidate_ids")
        handled.extend(candidate_ids)
        if status == "CONFIRMED":
            if item.get("severity") not in _SEVERITIES:
                raise PlatformContractError("SPEC_REVIEW_INVALID", f"Confirmed finding {finding_id} requires P1, P2, or P3")
            object_id = _text(item.get("object_id"), f"confirmed finding {finding_id}.object_id")
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


def evaluate_review(proposal: DecisionProposal, packet: InvestigationPacket) -> dict[str, Any]:
    details = _mapping(proposal.details, "details")
    review = _mapping(details.get("review"), "details.review")
    if review.get("review_schema_version") != "1.0.0":
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
    actual_ids = []
    for index, raw_item in enumerate(checklist):
        item = _mapping(raw_item, f"checklist_review[{index}]")
        actual_ids.append(item.get("check_id"))
        if item.get("status") not in _CHECK_STATUSES:
            raise PlatformContractError("SPEC_REVIEW_INVALID", f"checklist_review[{index}] has an invalid status")
        _text(item.get("note"), f"checklist_review[{index}].note")
    if tuple(actual_ids) != _EXPECTED_CHECKS:
        raise PlatformContractError("SPEC_REVIEW_INVALID", "checklist_review must preserve CHK-01 through CHK-18 order")

    payload = _mapping(packet.evidence[0].payload if packet.evidence else None, "candidate evidence")
    raw_candidates = _sequence(payload.get("candidateFindings"), "candidateFindings")
    candidates = {_text(item.get("candidate_id"), "candidate_id"): _mapping(item, "candidate") for item in raw_candidates}
    raw_decisions = _sequence(review.get("decisions"), "review decisions")
    reviewed = validate_review_decisions(raw_decisions, packet)

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
        "checklist_results": list(payload.get("checklist_results", ())),
        "readiness_context": context, "readiness": {"status": readiness, "reason": reason},
        "evidence_manifest": payload.get("evidence_manifest"),
    }


class SpecQualityDecisionCommitter:
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
                {"reviewed": False, "candidateOnly": True, "decisionOwner": "assayer.spec-quality"},
            )
        report = evaluate_review(proposal, packet)
        digest = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        return CommitReceipt(
            f"commit:{context.run_id}:{proposal.work_item_id}:{digest[:12]}",
            proposal.work_item_id, check.check_id, check.version, proposal.result, "memory",
            {"reviewed": True, "readiness": report["readiness"], "reviewDigest": digest},
            authority="platform",
        )


__all__ = ["SpecQualityDecisionCommitter", "evaluate_review", "validate_review_decisions"]
