"""Validation of compiler-generated Simple review items at the Host boundary.

This is generated adapter IR, not an authoring API. Ordinary plugin declarations
are compiled by the platform into this exact typed projection.
The Host validates it against the frozen InvestigationPacket before creating
internal ReviewAtoms.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any

from .contract import InvestigationPacket, PlatformContractError
from .evidence_handles import EvidenceHandleRegistry
from .incremental_review import ReviewAtom


@dataclass(frozen=True)
class CompiledReviewPlan:
    """Host-validated compiler IR, including shared typed review context."""

    atoms: tuple[ReviewAtom, ...]
    context: Mapping[str, Any] | None = None
    dimension_supports: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({}),
    )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN", f"{label} must be a nonempty string",
        )
    return value.strip()


def _support_refs(
    value: object, registry: EvidenceHandleRegistry, *, work_item_id: str,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review support must be a nonempty Evidence reference array",
            work_item_id=work_item_id,
        )
    refs = tuple(_text(item, "Compiled Evidence reference") for item in value)
    if len(refs) != len(set(refs)):
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review Evidence references must be unique",
            work_item_id=work_item_id,
        )
    if any(registry.handle_for_reference(ref) is None for ref in refs):
        raise PlatformContractError(
            "COMPILED_REVIEW_EVIDENCE_OUT_OF_SCOPE",
            "Compiled review item references Evidence outside its frozen InvestigationPacket",
            work_item_id=work_item_id,
        )
    return refs


def _json_value(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ))
    except (TypeError, ValueError) as error:
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN", f"{label} must be a finite JSON value",
        ) from error


def _compiled_context(
    value: Mapping[str, Any], registry: EvidenceHandleRegistry, *, work_item_id: str,
) -> Mapping[str, Any] | None:
    allowed = {"items", "facts", "unknowns", "dimensionSupports"}
    if set(value) - allowed or "items" not in value:
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review plan contains unsupported fields",
            work_item_id=work_item_id,
        )
    has_context = "facts" in value or "unknowns" in value
    if not has_context:
        return None
    if "facts" not in value or "unknowns" not in value:
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review facts and unknowns must be declared together",
            work_item_id=work_item_id,
        )
    raw_facts = value["facts"]
    raw_unknowns = value["unknowns"]
    if (
        not isinstance(raw_facts, Sequence) or isinstance(raw_facts, (str, bytes))
        or not isinstance(raw_unknowns, Sequence) or isinstance(raw_unknowns, (str, bytes))
    ):
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled facts and unknowns must be arrays",
            work_item_id=work_item_id,
        )
    facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, Mapping) or set(raw) != {
            "name", "value", "anchor", "supportRefs",
        }:
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Compiled Fact does not match its generated contract",
                work_item_id=work_item_id,
            )
        facts.append({
            "name": _text(raw["name"], "Fact name"),
            "value": _json_value(raw["value"], "Fact value"),
            "supportIds": list(_support_refs(
                raw["supportRefs"], registry, work_item_id=work_item_id,
            )),
        })
        _text(raw["anchor"], "Fact anchor")
    unknowns: list[dict[str, Any]] = []
    for raw in raw_unknowns:
        if not isinstance(raw, Mapping) or set(raw) != {
            "reason", "missingInformation",
        }:
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Compiled Unknown does not match its generated contract",
                work_item_id=work_item_id,
            )
        missing = raw["missingInformation"]
        if not isinstance(missing, Sequence) or isinstance(missing, (str, bytes)):
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Unknown missing information must be an array",
                work_item_id=work_item_id,
            )
        normalized_missing = tuple(
            _text(item, "Unknown missing information") for item in missing
        )
        if len(normalized_missing) != len(set(normalized_missing)):
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Unknown missing information must be unique",
                work_item_id=work_item_id,
            )
        unknowns.append({
            "reason": _text(raw["reason"], "Unknown reason"),
            "missingInformation": list(normalized_missing),
        })
    if not facts and not unknowns:
        return None
    return MappingProxyType({"facts": facts, "unknowns": unknowns})


def _compiled_dimension_supports(
    value: Mapping[str, Any], registry: EvidenceHandleRegistry, *, work_item_id: str,
) -> Mapping[str, tuple[str, ...]]:
    raw_supports = value.get("dimensionSupports", ())
    if not isinstance(raw_supports, Sequence) or isinstance(raw_supports, (str, bytes)):
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled dimension supports must be an array",
            work_item_id=work_item_id,
        )
    result: dict[str, tuple[str, ...]] = {}
    for raw in raw_supports:
        if not isinstance(raw, Mapping) or set(raw) != {"dimension", "supportRefs"}:
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Compiled dimension support does not match its generated contract",
                work_item_id=work_item_id,
            )
        dimension = _text(raw["dimension"], "Compiled dimension name")
        if dimension in result:
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Compiled dimension support names must be unique",
                work_item_id=work_item_id,
            )
        result[dimension] = _support_refs(
            raw["supportRefs"], registry, work_item_id=work_item_id,
        )
    return MappingProxyType(result)


def _context_for_support(
    context: Mapping[str, Any] | None, support_refs: Sequence[str],
) -> Mapping[str, Any] | None:
    """Select deterministic facts anchored to one generated review item."""
    if context is None:
        return None
    support = set(support_refs)
    facts = [
        fact for fact in context["facts"]
        if support.intersection(fact["supportIds"])
    ]
    unknowns = list(context["unknowns"])
    if not facts and not unknowns:
        return None
    return MappingProxyType({"facts": facts, "unknowns": unknowns})


def compile_review_items(
    value: object, *, run_id: str, packet: InvestigationPacket,
) -> CompiledReviewPlan:
    """Validate compiler IR and create deterministic Host review atoms."""
    if not isinstance(value, Mapping):
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review plan must be an object",
            work_item_id=packet.work_item.work_item_id,
        )
    registry = EvidenceHandleRegistry.from_packet(
        packet.work_item.work_item_id, packet,
    )
    context = _compiled_context(
        value, registry, work_item_id=packet.work_item.work_item_id,
    )
    dimension_supports = _compiled_dimension_supports(
        value, registry, work_item_id=packet.work_item.work_item_id,
    )
    raw_items = value["items"]
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN", "Compiled review items must be an array",
            work_item_id=packet.work_item.work_item_id,
        )
    atoms: list[ReviewAtom] = []
    for index, raw in enumerate(raw_items, 1):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("kind"), str):
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN", "Compiled review item is malformed",
                work_item_id=packet.work_item.work_item_id,
            )
        kind = raw["kind"]
        if kind == "candidate":
            required = {
                "kind", "rule", "subject", "message", "severity",
                "recommendation", "supportRefs",
            }
            if set(raw) not in (required, required | {"anchor"}):
                raise PlatformContractError(
                    "INVALID_COMPILED_REVIEW_PLAN",
                    "Compiled Candidate does not match its generated contract",
                    work_item_id=packet.work_item.work_item_id,
                )
            refs = _support_refs(
                raw["supportRefs"], registry,
                work_item_id=packet.work_item.work_item_id,
            )
            rule = _text(raw["rule"], "Candidate rule")
            item_context = _context_for_support(context, refs)
            anchor = (
                _text(raw["anchor"], "Candidate anchor")
                if "anchor" in raw else f"compiled:candidate:{index}:{'|'.join(refs)}"
            )
            atoms.append(ReviewAtom.create(
                run_id=run_id,
                work_item_id=packet.work_item.work_item_id,
                kind="candidate",
                source_anchor=anchor,
                rule_id=rule,
                payload={
                    "rule": rule,
                    "subject": _text(raw["subject"], "Candidate subject"),
                    "message": _text(raw["message"], "Candidate message"),
                    "severity": _text(raw["severity"], "Candidate severity"),
                    "recommendation": _text(
                        raw["recommendation"], "Candidate recommendation",
                    ),
                    "supportIds": list(refs),
                    **({"context": item_context} if item_context is not None else {}),
                },
            ))
            continue
        if kind == "relationship":
            required = {
                "kind", "relationship", "left", "right", "instruction", "supportRefs",
            }
            if set(raw) not in (required, required | {"anchor"}):
                raise PlatformContractError(
                    "INVALID_COMPILED_REVIEW_PLAN",
                    "Compiled Relationship does not match its generated contract",
                    work_item_id=packet.work_item.work_item_id,
                )
            refs = _support_refs(
                raw["supportRefs"], registry,
                work_item_id=packet.work_item.work_item_id,
            )
            relationship = _text(raw["relationship"], "Relationship name")
            item_context = _context_for_support(context, refs)
            anchor = (
                _text(raw["anchor"], "Relationship anchor")
                if "anchor" in raw else f"compiled:relationship:{index}:{'|'.join(refs)}"
            )
            atoms.append(ReviewAtom.create(
                run_id=run_id,
                work_item_id=packet.work_item.work_item_id,
                kind="relationship",
                source_anchor=anchor,
                rule_id=relationship,
                payload={
                    "relationship": relationship,
                    "left": _text(raw["left"], "Relationship left subject"),
                    "right": _text(raw["right"], "Relationship right subject"),
                    "instruction": _text(raw["instruction"], "Relationship instruction"),
                    "supportIds": list(refs),
                    **({"context": item_context} if item_context is not None else {}),
                },
            ))
            continue
        raise PlatformContractError(
            "INVALID_COMPILED_REVIEW_PLAN",
            "Compiled review item kind is unsupported",
            work_item_id=packet.work_item.work_item_id,
        )
    return CompiledReviewPlan(tuple(atoms), context, dimension_supports)


__all__ = ["CompiledReviewPlan", "compile_review_items"]
