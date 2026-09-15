"""Task-local identity binding for common incremental review submissions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .common_review import CommonReviewSubmission
from .contract import PlatformContractError
from .incremental_review import BatchVerdict, CoverageLedger


_ITEM_REF = re.compile(r"^I[1-9][0-9]*$")
_EVIDENCE_REF = re.compile(r"^R[1-9][0-9]*$")
_COMMON_ATOM_KINDS = frozenset({"candidate", "dimension", "relationship"})


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlatformContractError(
            "INVALID_REVIEW_BINDING", "Review binding contains a non-JSON value",
        ) from error


def _resolve_support_refs(value: Any, evidence: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        resolved = {str(key): _resolve_support_refs(item, evidence) for key, item in value.items()}
        if set(value).issuperset({"refs"}) and isinstance(value.get("refs"), (tuple, list)):
            resolved["refs"] = [evidence[str(ref)] for ref in value["refs"]]
        return resolved
    if isinstance(value, (tuple, list)):
        return [_resolve_support_refs(item, evidence) for item in value]
    return value


@dataclass(frozen=True)
class ReviewTaskBinding:
    """Immutable mapping between one internal batch and task-local handles."""

    batch_id: str
    item_bindings: tuple[tuple[str, str, str], ...]
    evidence_bindings: tuple[tuple[str, str], ...] = ()
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id:
            raise PlatformContractError("INVALID_REVIEW_BINDING", "Review batch identity is required")
        item_refs: list[str] = []
        atom_ids: list[str] = []
        normalized_items: list[tuple[str, str, str]] = []
        for binding in self.item_bindings:
            if not isinstance(binding, (tuple, list)) or len(binding) != 3:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Review item binding is malformed")
            item_ref, atom_id, kind = binding
            if not isinstance(item_ref, str) or _ITEM_REF.fullmatch(item_ref) is None:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Review item ref is malformed")
            if not isinstance(atom_id, str) or not atom_id:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Review atom identity is malformed")
            if kind not in _COMMON_ATOM_KINDS:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Review atom kind is unsupported")
            item_refs.append(item_ref)
            atom_ids.append(atom_id)
            normalized_items.append((item_ref, atom_id, kind))
        if not normalized_items or len(item_refs) != len(set(item_refs)) or len(atom_ids) != len(set(atom_ids)):
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING", "Review item bindings must be nonempty and unique",
            )
        normalized_evidence: list[tuple[str, str]] = []
        evidence_refs: list[str] = []
        evidence_ids: list[str] = []
        for binding in self.evidence_bindings:
            if not isinstance(binding, (tuple, list)) or len(binding) != 2:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Evidence binding is malformed")
            evidence_ref, evidence_id = binding
            if not isinstance(evidence_ref, str) or _EVIDENCE_REF.fullmatch(evidence_ref) is None:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Evidence ref is malformed")
            if not isinstance(evidence_id, str) or not evidence_id:
                raise PlatformContractError("INVALID_REVIEW_BINDING", "Evidence identity is malformed")
            evidence_refs.append(evidence_ref)
            evidence_ids.append(evidence_id)
            normalized_evidence.append((evidence_ref, evidence_id))
        if (
            len(evidence_refs) != len(set(evidence_refs))
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING", "Evidence refs and identities must be one-to-one",
            )
        if self.supersedes is not None and (
            not isinstance(self.supersedes, str) or not self.supersedes
        ):
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING", "Correction parent identity is malformed",
            )
        object.__setattr__(self, "item_bindings", tuple(normalized_items))
        object.__setattr__(self, "evidence_bindings", tuple(normalized_evidence))

    @classmethod
    def from_ledger(
        cls, ledger: CoverageLedger, batch_id: str, *,
        evidence_bindings: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    ) -> "ReviewTaskBinding":
        batch = next((item for item in ledger.batches if item.batch_id == batch_id), None)
        if batch is None:
            raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "Review batch is not in this ledger")
        if batch.status not in {"offered", "accepted"}:
            raise PlatformContractError(
                "INVALID_REVIEW_TRANSITION", "Only an offered batch can create an Agent binding",
            )
        atoms = ledger.atoms_for(batch_id)
        first_ordinal = 1 + sum(
            len(item.atom_ids)
            for item in ledger.batches
            if item.sequence < batch.sequence
        )
        bindings = tuple(
            (f"I{first_ordinal + offset}", atom.atom_id, atom.kind)
            for offset, atom in enumerate(atoms)
        )
        evidence = (
            tuple(evidence_bindings.items())
            if isinstance(evidence_bindings, Mapping) else tuple(evidence_bindings)
        )
        effective_ids = {
            entry.effective_submission_id
            for entry in ledger.entries
            if entry.batch_id == batch.batch_id and entry.effective_submission_id is not None
        }
        if len(effective_ids) > 1:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Review batch has multiple effective verdicts",
            )
        supersedes = next(iter(effective_ids), None)
        return cls(batch.batch_id, bindings, evidence, supersedes)

    @property
    def expected_item_refs(self) -> tuple[str, ...]:
        return tuple(item_ref for item_ref, _atom_id, _kind in self.item_bindings)

    @property
    def allowed_evidence_refs(self) -> tuple[str, ...]:
        return tuple(ref for ref, _evidence_id in self.evidence_bindings)

    def agent_task(self) -> dict[str, Any]:
        """Return the identity-only portion of a public common-review task."""
        return {
            "kind": "common_review",
            "items": [
                {"itemRef": item_ref, "kind": kind}
                for item_ref, _atom_id, kind in self.item_bindings
            ],
            "evidenceRefs": list(self.allowed_evidence_refs),
        }

    def bind(self, submission: CommonReviewSubmission) -> BatchVerdict:
        """Resolve a valid task-local submission into one internal BatchVerdict."""
        if not isinstance(submission, CommonReviewSubmission):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Review binding requires a typed common submission",
            )
        submission.validate_task_membership(
            expected_item_refs=self.expected_item_refs,
            allowed_evidence_refs=self.allowed_evidence_refs,
        )
        item_map = {
            item_ref: (atom_id, kind) for item_ref, atom_id, kind in self.item_bindings
        }
        evidence_map = MappingProxyType(dict(self.evidence_bindings))
        decisions: list[dict[str, Any]] = []
        for decision in submission.decisions:
            atom_id, atom_kind = item_map[decision.item_ref]
            if decision.kind not in {atom_kind, "unknown", "escalation"}:
                raise PlatformContractError(
                    "REVIEW_KIND_MISMATCH",
                    "Common verdict kind does not match its current ReviewAtom",
                )
            decisions.append({
                "atomId": atom_id,
                "kind": decision.kind,
                "value": _resolve_support_refs(decision.value.as_dict(), evidence_map),
            })
        submission_id = "review-submission:" + hashlib.sha256(_canonical_bytes({
            "batchId": self.batch_id,
            "decisions": decisions,
            "supersedes": self.supersedes,
        })).hexdigest()[:32]
        return BatchVerdict(
            submission_id, self.batch_id, tuple(decisions), supersedes=self.supersedes,
        )


__all__ = ["ReviewTaskBinding"]
