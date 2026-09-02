"""Domain-neutral validation for Agent decision proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contract import DECISION_STATES, FINDING_STATES, PlatformContractError


def validate_decision_shape(
    result: str,
    findings: Sequence[Mapping[str, object]],
    dimensions: Sequence[str],
    allowed_results: Sequence[str],
) -> None:
    """Validate the semantic closure before any durable write occurs.

    Evidence and object binding remain Host-owned. This function only checks
    the domain-neutral shape that every plugin must satisfy, allowing an
    interactive Agent route to use the same gates as the batch kernel.
    """
    if result not in DECISION_STATES or result not in set(allowed_results):
        raise PlatformContractError("DECISION_STATE", "Decision state is not allowed by the active Check")
    expected = tuple(dimensions)
    names = []
    statuses = set()
    for finding in findings:
        dimension = finding.get("dimension")
        status = finding.get("status")
        reason = finding.get("reasonText") or finding.get("reason")
        if not isinstance(dimension, str) or not dimension:
            raise PlatformContractError("FINDING_CLOSURE", "Every Finding must name a dimension")
        if dimension in names:
            raise PlatformContractError("FINDING_CLOSURE", "Decision Findings must contain each dimension exactly once")
        if status not in FINDING_STATES:
            raise PlatformContractError("INVALID_FINDING", "Finding status is not supported")
        if not isinstance(reason, str) or not reason.strip():
            raise PlatformContractError("INVALID_FINDING", "Finding reason must be nonempty")
        names.append(dimension)
        statuses.add(status)
    if set(names) != set(expected) or len(names) != len(expected):
        raise PlatformContractError("FINDING_CLOSURE", "Decision Findings must cover every Check dimension exactly once")
    if result == "scanned_no_issue" and statuses != {"satisfied"}:
        raise PlatformContractError("DECISION_GATE", "scanned_no_issue requires every dimension to be satisfied")
    if result == "issue_found" and "violated" not in statuses:
        raise PlatformContractError("DECISION_GATE", "issue_found requires a violated dimension")
    if result == "needs_review" and not statuses.intersection({"unresolved", "blocked", "conflicted"}):
        raise PlatformContractError("DECISION_GATE", "needs_review requires an unresolved, blocked, or conflicted dimension")
