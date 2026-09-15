"""Typed, allowlisted projection of ReviewAtoms into Agent-visible items."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from .common_review import FINDING_SEVERITIES
from .contract import PlatformContractError
from .incremental_review import CoverageLedger, ReviewAtom
from .review_binding import ReviewTaskBinding


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", f"{label} must be a nonempty string",
        )
    return value.strip()


def _supports(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", "Typed review atom requires Evidence support identities",
        )
    normalized = tuple(_text(item, "Evidence support identity") for item in value)
    if len(normalized) != len(set(normalized)):
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", "Typed review atom Evidence support must be unique",
        )
    return normalized


def _texts(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", f"{label} must be a nonempty string array",
        )
    return tuple(_text(item, label) for item in value)


def _optional_texts(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", f"{label} must be a string array",
        )
    normalized = tuple(_text(item, label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", f"{label} must be unique",
        )
    return normalized


def _json_value(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ))
    except (TypeError, ValueError) as error:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", f"{label} must be a finite JSON value",
        ) from error


def _payload(atom: ReviewAtom, required: set[str], optional: set[str] | None = None) -> Mapping[str, Any]:
    allowed = required | (optional or set())
    if not required.issubset(atom.payload) or set(atom.payload) - allowed:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM",
            f"{atom.kind} ReviewAtom does not match the typed projection contract",
            work_item_id=atom.work_item_id,
        )
    return atom.payload


@dataclass(frozen=True)
class ReviewFact:
    name: str
    value: Any
    support_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewUnknown:
    reason: str
    missing_information: tuple[str, ...]


@dataclass(frozen=True)
class ReviewContext:
    facts: tuple[ReviewFact, ...]
    unknowns: tuple[ReviewUnknown, ...]

    @property
    def support_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            evidence_id
            for fact in self.facts
            for evidence_id in fact.support_ids
        ))

    def project(self, evidence_refs: Mapping[str, str]) -> dict[str, Any]:
        return {
            "facts": [{
                "name": fact.name,
                "value": fact.value,
                "support": [evidence_refs[item] for item in fact.support_ids],
            } for fact in self.facts],
            "unknowns": [{
                "reason": item.reason,
                "missingInformation": list(item.missing_information),
            } for item in self.unknowns],
        }


def _review_context(value: object) -> ReviewContext:
    if not isinstance(value, Mapping) or set(value) != {"facts", "unknowns"}:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", "Review context does not match its typed contract",
        )
    raw_facts = value["facts"]
    raw_unknowns = value["unknowns"]
    if (
        not isinstance(raw_facts, (tuple, list))
        or not isinstance(raw_unknowns, (tuple, list))
    ):
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", "Review facts and unknowns must be arrays",
        )
    facts: list[ReviewFact] = []
    for item in raw_facts:
        if not isinstance(item, Mapping) or set(item) != {"name", "value", "supportIds"}:
            raise PlatformContractError(
                "INVALID_REVIEW_ATOM", "Review Fact does not match its typed contract",
            )
        facts.append(ReviewFact(
            _text(item["name"], "Fact name"),
            _json_value(item["value"], "Fact value"),
            _supports(item["supportIds"]),
        ))
    unknowns: list[ReviewUnknown] = []
    for item in raw_unknowns:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"reason", "missingInformation"}
        ):
            raise PlatformContractError(
                "INVALID_REVIEW_ATOM", "Review Unknown does not match its typed contract",
            )
        unknowns.append(ReviewUnknown(
            _text(item["reason"], "Unknown reason"),
            _optional_texts(item["missingInformation"], "Unknown missing information"),
        ))
    if not facts and not unknowns:
        raise PlatformContractError(
            "INVALID_REVIEW_ATOM", "An attached Review context cannot be empty",
        )
    return ReviewContext(tuple(facts), tuple(unknowns))


@dataclass(frozen=True)
class CandidateReviewItem:
    rule: str
    subject: str
    message: str
    severity: str
    recommendation: str
    support_ids: tuple[str, ...]
    context: ReviewContext | None = None

    @classmethod
    def from_atom(cls, atom: ReviewAtom) -> "CandidateReviewItem":
        value = _payload(atom, {
            "rule", "subject", "message", "severity", "recommendation", "supportIds",
        }, {"context"})
        severity = _text(value["severity"], "Candidate severity")
        if severity not in FINDING_SEVERITIES:
            raise PlatformContractError(
                "INVALID_REVIEW_ATOM", "Candidate severity is unsupported",
                work_item_id=atom.work_item_id,
            )
        return cls(
            _text(value["rule"], "Candidate rule"),
            _text(value["subject"], "Candidate subject"),
            _text(value["message"], "Candidate message"),
            severity,
            _text(value["recommendation"], "Candidate recommendation"),
            _supports(value["supportIds"]),
            _review_context(value["context"]) if "context" in value else None,
        )

    def project(self, evidence_refs: Mapping[str, str]) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "subject": self.subject,
            "message": self.message,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "support": [evidence_refs[item] for item in self.support_ids],
        }


@dataclass(frozen=True)
class DimensionReviewItem:
    dimension: str
    instruction: str
    subject: str
    support_ids: tuple[str, ...]
    observations: tuple[str, ...] = ()
    context: ReviewContext | None = None

    @classmethod
    def from_atom(cls, atom: ReviewAtom) -> "DimensionReviewItem":
        value = _payload(
            atom,
            {"dimension", "instruction", "supportIds"},
            {"subject", "observations", "context"},
        )
        return cls(
            _text(value["dimension"], "Dimension name"),
            _text(value["instruction"], "Dimension instruction"),
            _text(value.get("subject", "document"), "Dimension subject"),
            _supports(value["supportIds"]),
            _texts(value["observations"], "Dimension observations")
            if "observations" in value else (),
            _review_context(value["context"]) if "context" in value else None,
        )

    def project(self, evidence_refs: Mapping[str, str]) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "instruction": self.instruction,
            "subject": self.subject,
            "support": [evidence_refs[item] for item in self.support_ids],
            **({"observations": list(self.observations)} if self.observations else {}),
        }


@dataclass(frozen=True)
class RelationshipReviewItem:
    relationship: str
    left: str
    right: str
    instruction: str
    support_ids: tuple[str, ...]
    context: ReviewContext | None = None

    @classmethod
    def from_atom(cls, atom: ReviewAtom) -> "RelationshipReviewItem":
        value = _payload(atom, {
            "relationship", "left", "right", "instruction", "supportIds",
        }, {"context"})
        return cls(
            _text(value["relationship"], "Relationship name"),
            _text(value["left"], "Relationship left subject"),
            _text(value["right"], "Relationship right subject"),
            _text(value["instruction"], "Relationship instruction"),
            _supports(value["supportIds"]),
            _review_context(value["context"]) if "context" in value else None,
        )

    def project(self, evidence_refs: Mapping[str, str]) -> dict[str, Any]:
        return {
            "relationship": self.relationship,
            "left": self.left,
            "right": self.right,
            "instruction": self.instruction,
            "support": [evidence_refs[item] for item in self.support_ids],
        }


ReviewItem = CandidateReviewItem | DimensionReviewItem | RelationshipReviewItem


def _resolved_atom(
    atom: ReviewAtom,
    contexts: Mapping[str, Mapping[str, Any]] | None,
) -> ReviewAtom:
    """Resolve a Host catalog reference before strict typed projection."""
    if "contextRef" not in atom.payload:
        return atom
    if "context" in atom.payload:
        raise PlatformContractError(
            "INVALID_REVIEW_CONTEXT",
            "ReviewAtom cannot contain both inline and catalog context",
            work_item_id=atom.work_item_id,
        )
    context_ref = atom.payload["contextRef"]
    if not isinstance(context_ref, str) or contexts is None or context_ref not in contexts:
        raise PlatformContractError(
            "INVALID_REVIEW_CONTEXT",
            "ReviewAtom context reference is unavailable",
            work_item_id=atom.work_item_id,
        )
    payload = dict(atom.payload)
    payload.pop("contextRef")
    payload["context"] = contexts[context_ref]
    return ReviewAtom(atom.atom_id, atom.work_item_id, atom.kind, payload)


def _typed_item(
    atom: ReviewAtom,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> ReviewItem:
    atom = _resolved_atom(atom, contexts)
    if atom.kind == "candidate":
        return CandidateReviewItem.from_atom(atom)
    if atom.kind == "dimension":
        return DimensionReviewItem.from_atom(atom)
    if atom.kind == "relationship":
        return RelationshipReviewItem.from_atom(atom)
    raise PlatformContractError(
        "INVALID_REVIEW_ATOM", "ReviewAtom kind has no typed Agent projection",
        work_item_id=atom.work_item_id,
    )


def review_atom_support_ids(
    atom: ReviewAtom,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str, ...]:
    """Return validated internal Evidence identities for one typed atom.

    This stays on the Host side of the boundary.  It lets the incremental
    runtime allocate stable public ``R<n>`` handles without inspecting raw
    plugin payload fields itself.
    """
    typed = _typed_item(atom, contexts)
    return tuple(dict.fromkeys((
        *typed.support_ids,
        *(typed.context.support_ids if typed.context is not None else ()),
    )))


def build_common_review_task(
    ledger: CoverageLedger, binding: ReviewTaskBinding,
) -> dict[str, Any]:
    """Project one bound batch without exposing internal identity or arbitrary fields."""
    atoms = {atom.atom_id: atom for atom in ledger.atoms_for(binding.batch_id)}
    evidence_refs = {evidence_id: ref for ref, evidence_id in binding.evidence_bindings}
    items: list[dict[str, Any]] = []
    contexts_by_value: dict[str, dict[str, Any]] = {}
    for item_ref, atom_id, kind in binding.item_bindings:
        atom = atoms.get(atom_id)
        if atom is None or atom.kind != kind:
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING", "Review binding does not match its frozen batch",
            )
        typed = _typed_item(atom, ledger.contexts)
        required_evidence = tuple(dict.fromkeys((
            *typed.support_ids,
            *(typed.context.support_ids if typed.context is not None else ()),
        )))
        try:
            item_evidence_refs = {
                evidence_id: evidence_refs[evidence_id]
                for evidence_id in required_evidence
            }
        except KeyError as error:
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING",
                "ReviewAtom support is not available in the current Agent task",
                work_item_id=atom.work_item_id,
            ) from error
        projected = {
            "itemRef": item_ref,
            "kind": kind,
            **typed.project(item_evidence_refs),
        }
        if typed.context is not None:
            context_value = typed.context.project(item_evidence_refs)
            context_key = json.dumps(
                context_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            context = contexts_by_value.get(context_key)
            if context is None:
                context = {
                    "contextRef": f"C{len(contexts_by_value) + 1}",
                    "itemRefs": [],
                    **context_value,
                }
                contexts_by_value[context_key] = context
            context["itemRefs"].append(item_ref)
            projected["contextRef"] = context["contextRef"]
        items.append(projected)
    task = {
        "kind": "common_review",
        "items": items,
        "evidenceRefs": list(binding.allowed_evidence_refs),
    }
    if contexts_by_value:
        task["contexts"] = list(contexts_by_value.values())
    return task


__all__ = [
    "CandidateReviewItem",
    "DimensionReviewItem",
    "RelationshipReviewItem",
    "ReviewContext",
    "build_common_review_task",
    "review_atom_support_ids",
]
