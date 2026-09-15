"""Host-owned assembly of common-review coverage into platform Decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import PlatformContractError
from .incremental_review import CoverageLedger


_DIMENSION_STATUS = {
    "satisfied": "satisfied",
    "violated": "violated",
    "unresolved": "unresolved",
    "blocked": "blocked",
    "conflicted": "conflicted",
}
_RELATIONSHIP_STATUS = {
    "confirmed": "satisfied",
    "rejected": "violated",
    "unknown": "unresolved",
}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _support_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    support = value.get("support")
    if not isinstance(support, Mapping) or not isinstance(support.get("refs"), (tuple, list)):
        return ()
    return tuple(str(item) for item in support["refs"])


def assemble_common_review_decisions(
    ledger: CoverageLedger, *, check_id: str, check_version: str,
) -> tuple[dict[str, Any], ...]:
    """Create one generic Decision projection per fully covered WorkItem."""
    if not isinstance(ledger, CoverageLedger):
        raise PlatformContractError(
            "INVALID_COVERAGE_LEDGER", "Common Decision assembly requires a CoverageLedger",
        )
    if any(entry.status != "accepted" for entry in ledger.entries):
        raise PlatformContractError(
            "INCOMPLETE_REVIEW_COVERAGE",
            "Common Decisions require accepted coverage for every review atom",
        )
    verdicts = {item.submission_id: item for item in ledger.verdicts}
    decisions_by_atom: dict[str, Mapping[str, Any]] = {}
    for entry in ledger.entries:
        verdict = verdicts.get(entry.effective_submission_id or "")
        if verdict is None:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Accepted coverage has no effective verdict",
            )
        decision = next(
            (item for item in verdict.decisions if item.get("atomId") == entry.atom_id), None,
        )
        if decision is None:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Effective verdict does not cover its atom",
            )
        decisions_by_atom[entry.atom_id] = decision

    work_item_ids = tuple(dict.fromkeys(atom.work_item_id for atom in ledger.atoms))
    result: list[dict[str, Any]] = []
    for work_item_id in work_item_ids:
        findings: list[dict[str, str]] = []
        review_items: list[dict[str, Any]] = []
        evidence_refs: list[str] = []
        applicability: list[str] = []
        dimension_statuses: dict[str, str] = {}
        problem_findings: list[Mapping[str, Any]] = []
        issue_signal = False
        unresolved_signal = False
        for atom in (item for item in ledger.atoms if item.work_item_id == work_item_id):
            decision = decisions_by_atom[atom.atom_id]
            kind = str(decision.get("kind") or "")
            value = decision.get("value")
            if not isinstance(value, Mapping):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Common review verdict value is malformed",
                )
            evidence_refs.extend(_support_refs(value))
            review_items.append({
                "kind": kind,
                "subject": (
                    atom.payload.get("dimension")
                    or atom.payload.get("rule")
                    or atom.payload.get("relationship")
                    or atom.kind
                ),
                "value": _plain(value),
            })
            application = value.get("applicability")
            if isinstance(application, Mapping) and isinstance(application.get("state"), str):
                applicability.append(application["state"])
            if kind == "dimension":
                dimension_name = str(
                    value.get("dimension") or atom.payload.get("dimension") or "dimension"
                )
                status = _DIMENSION_STATUS.get(str(value.get("verdict") or ""))
                dimension_statuses[dimension_name] = str(value.get("verdict") or "")
                if status is not None:
                    findings.append({
                        "dimension": dimension_name,
                        "status": status,
                        "reason": str(value.get("reason") or "Common dimension reviewed."),
                    })
                raw_findings = value.get("findings", ())
                if isinstance(raw_findings, (tuple, list)):
                    problem_findings.extend(
                        item for item in raw_findings if isinstance(item, Mapping)
                    )
                    issue_signal = issue_signal or bool(raw_findings)
            elif kind == "relationship":
                relationship_status = _RELATIONSHIP_STATUS.get(
                    str(value.get("verdict") or ""),
                )
                issue_signal = issue_signal or relationship_status == "violated"
                unresolved_signal = unresolved_signal or relationship_status == "unresolved"
            elif kind == "candidate":
                disposition = value.get("disposition")
                if disposition == "confirmed":
                    finding = value.get("finding")
                    if not isinstance(finding, Mapping):
                        raise PlatformContractError(
                            "INVALID_COVERAGE_LEDGER", "Confirmed candidate has no Finding",
                        )
                    evidence_refs.extend(_support_refs(finding))
                    problem_findings.append(finding)
                    issue_signal = True
                elif disposition == "needs_review":
                    unresolved_signal = True
            elif kind in {"unknown", "escalation"}:
                unresolved_signal = True
                if atom.kind == "dimension":
                    findings.append({
                        "dimension": str(atom.payload.get("dimension") or "dimension"),
                        "status": "unresolved",
                        "reason": str(value.get("reason") or "Review remains unresolved."),
                    })

        for finding in problem_findings:
            affected = finding.get("affectedDimensions", ())
            if not isinstance(affected, (tuple, list)):
                raise PlatformContractError(
                    "COMMON_REVIEW_DECISION_INCONSISTENT",
                    "Finding affected dimensions are malformed",
                    work_item_id=work_item_id,
                )
            unknown = set(str(item) for item in affected) - set(dimension_statuses)
            if unknown:
                raise PlatformContractError(
                    "COMMON_REVIEW_DECISION_INCONSISTENT",
                    "Finding references a dimension outside the reviewed WorkItem",
                    work_item_id=work_item_id,
                )
            non_actionable = {
                dimension for dimension in affected
                if dimension_statuses[dimension] not in {"violated", "conflicted"}
            }
            if non_actionable:
                raise PlatformContractError(
                    "COMMON_REVIEW_DECISION_INCONSISTENT",
                    "Finding may affect only violated or conflicted dimensions",
                    work_item_id=work_item_id,
                )

        statuses = {item["status"] for item in findings}
        if issue_signal and not statuses.intersection({"violated", "conflicted"}):
            raise PlatformContractError(
                "COMMON_REVIEW_DECISION_INCONSISTENT",
                "Confirmed Candidate or rejected Relationship requires a violated DimensionVerdict",
                work_item_id=work_item_id,
            )
        if unresolved_signal and not statuses.intersection({"unresolved", "blocked", "conflicted"}):
            raise PlatformContractError(
                "COMMON_REVIEW_DECISION_INCONSISTENT",
                "Unresolved Candidate or Relationship requires an unresolved DimensionVerdict",
                work_item_id=work_item_id,
            )
        if statuses & {"violated", "conflicted"}:
            decision_result = "issue_found"
            reason = "Common review confirmed one or more actionable issues."
        elif statuses & {"unresolved", "blocked"}:
            decision_result = "needs_review"
            reason = "Common review contains unresolved or blocked conclusions."
        elif applicability and set(applicability) == {"not_applicable"}:
            decision_result = "not_applicable"
            reason = "Every applicable common-review subject was not applicable."
        else:
            decision_result = "scanned_no_issue"
            reason = "Common review found no confirmed issue."
        result.append({
            "workItemId": work_item_id,
            "checkId": check_id,
            "checkVersion": check_version,
            "result": decision_result,
            "findings": findings,
            "reason": reason,
            "details": {
                "commonReview": review_items,
                "evidenceRefs": list(dict.fromkeys(evidence_refs)),
            },
        })
    return tuple(result)


__all__ = ["assemble_common_review_decisions"]
