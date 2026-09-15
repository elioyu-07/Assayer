"""Deterministic evaluator used by compiler-generated zero-Python Policy Packs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .contract import PlatformContractError
from .simple import Candidate, Document, Support, Unknown


def _terms(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PlatformContractError("INVALID_POLICY_PACK", f"{label} must be a string array")
    terms = tuple(str(item).strip() for item in value)
    if not terms or any(not item for item in terms):
        raise PlatformContractError("INVALID_POLICY_PACK", f"{label} cannot be empty")
    return terms


def scan_policy(
    document: Document, rules: Sequence[Mapping[str, Any]],
) -> Iterable[Candidate | Unknown]:
    """Yield candidates from compiler-validated declarative match predicates."""
    if not isinstance(document, Document):
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT", "Policy Pack scanning requires a Host Document",
        )
    for rule in rules:
        detect = rule.get("detect")
        if not isinstance(detect, Mapping):
            continue
        contains_all = _terms(detect["contains_all"], "contains_all") if "contains_all" in detect else ()
        contains_any = _terms(detect["contains_any"], "contains_any") if "contains_any" in detect else ()
        absent_all = _terms(detect["absent_all"], "absent_all") if "absent_all" in detect else ()
        if contains_all and not all(document.contains(term) for term in contains_all):
            continue
        if contains_any and not document.contains_any(*contains_any):
            continue
        support: Support | Unknown | None = None
        if absent_all:
            if document.contains_any(*absent_all):
                continue
            support = document.absence(absent_all, scope=document.full_scope)
            if isinstance(support, Unknown):
                yield support
                continue
            if not isinstance(support, Support):
                continue
        else:
            matched = next(
                (term for term in (*contains_all, *contains_any) if document.contains(term)),
                None,
            )
            if matched is None:
                continue
            matches = document.search(matched, limit=1)
            if not matches:
                continue
            support = matches[0]
        yield Candidate(
            rule=str(rule["id"]),
            subject=str(rule["title"]),
            message=str(rule["description"]),
            support=support,
            severity=str(rule["default_severity"]),
            recommendation=str(rule["recommendation"]),
        )


__all__ = ["scan_policy"]
