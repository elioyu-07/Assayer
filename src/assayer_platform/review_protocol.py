"""Domain-neutral Agent review task and submission boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contract import PlatformContractError


REVIEW_DISPOSITIONS = frozenset({"confirmed", "suppressed", "merged", "needs_review"})


def build_review_task(
    *, work_item_id: str, check_id: str, check_version: str,
    collection_id: str, item_ids: Sequence[str], evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a compact, domain-neutral review task envelope."""
    ids = tuple(str(item) for item in item_ids)
    if not work_item_id or not check_id or not check_version or not collection_id:
        raise PlatformContractError("INVALID_REVIEW_TASK", "Review task identity fields are required")
    if not ids or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise PlatformContractError("INVALID_REVIEW_TASK", "Review task item IDs must be unique nonempty strings")
    refs = tuple(str(item) for item in evidence_refs)
    if len(refs) != len(set(refs)) or any(not item for item in refs):
        raise PlatformContractError("INVALID_REVIEW_TASK", "Review task Evidence references must be unique nonempty strings")
    return {
        "workItemId": work_item_id, "checkId": check_id, "checkVersion": check_version,
        "collectionId": collection_id, "itemIds": list(ids), "evidenceRefs": list(refs),
    }


def validate_review_submission(
    submission: Mapping[str, Any], *, expected_item_ids: Sequence[str],
    allowed_evidence_refs: Sequence[str] = (),
) -> tuple[Mapping[str, Any], ...]:
    """Validate generic candidate coverage without interpreting domain meaning."""
    if not isinstance(submission, Mapping):
        raise PlatformContractError("INVALID_REVIEW_SUBMISSION", "Review submission must be an object")
    raw = submission.get("decisions")
    if not isinstance(raw, (tuple, list)) or not raw:
        raise PlatformContractError("INVALID_REVIEW_SUBMISSION", "Review submission requires decisions")
    expected = {str(item) for item in expected_item_ids}
    if not expected or len(expected) != len(tuple(expected_item_ids)):
        raise PlatformContractError("INVALID_REVIEW_SUBMISSION", "Expected review item IDs must be unique and nonempty")
    allowed = {str(item) for item in allowed_evidence_refs}
    handled: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    for index, decision in enumerate(raw):
        if not isinstance(decision, Mapping):
            raise PlatformContractError("INVALID_REVIEW_SUBMISSION", f"Decision {index} must be an object")
        ids = decision.get("candidateIds", decision.get("candidate_ids"))
        if not isinstance(ids, (tuple, list)) or not ids:
            raise PlatformContractError("INVALID_REVIEW_SUBMISSION", f"Decision {index} requires candidate IDs")
        disposition = str(decision.get("disposition", ""))
        if disposition not in REVIEW_DISPOSITIONS:
            raise PlatformContractError("INVALID_REVIEW_SUBMISSION", f"Decision {index} has an unsupported disposition")
        candidate_ids = [str(item) for item in ids]
        handled.extend(candidate_ids)
        refs = decision.get("evidenceRefs", decision.get("evidence_refs", ()))
        if not isinstance(refs, (tuple, list)) or any(str(item) not in allowed for item in refs):
            raise PlatformContractError("INVALID_REVIEW_SUBMISSION", f"Decision {index} cites Evidence outside the task")
        normalized.append(decision)
    if set(handled) - expected:
        raise PlatformContractError("INVALID_REVIEW_SUBMISSION", "Review submission references an unknown candidate")
    if len(handled) != len(set(handled)) or set(handled) != expected:
        raise PlatformContractError("INCOMPLETE_REVIEW_SUBMISSION", "Review submission must handle every candidate exactly once")
    return tuple(normalized)


__all__ = ["REVIEW_DISPOSITIONS", "build_review_task", "validate_review_submission"]
