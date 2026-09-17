"""Host-owned incremental semantic-review state.

This module is deliberately not part of :mod:`assayer_plugin_sdk`.  It turns
an ordered set of small review atoms into bounded Agent batches and keeps the
append-only coverage history needed for resume, replay, and correction.
Plugins supply domain facts and candidates; they never manage this ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .contract import PlatformContractError


BATCH_STATES = frozenset({"planned", "offered", "accepted", "blocked", "terminal"})
VERDICT_STATES = frozenset({"accepted", "superseded"})
LEDGER_TERMINAL_STATES = frozenset({"completed", "partial", "failed"})
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,255}$")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PlatformContractError(
                "INVALID_REVIEW_VALUE", "Incremental review object keys must be strings",
            )
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlatformContractError(
            "INVALID_REVIEW_VALUE", "Incremental review values must be finite JSON values",
        ) from error


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is not None and not isinstance(value, Mapping):
        raise PlatformContractError(
            "INVALID_REVIEW_VALUE", "Incremental review object value must be a mapping",
        )
    detached = json.loads(_json_bytes(dict(value or {})).decode("utf-8"))
    return _freeze_json(detached)


def _stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_json_bytes(value)).hexdigest()
    return f"{prefix}:{digest[:32]}"


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise PlatformContractError(
            "INVALID_REVIEW_ID", f"{label} must be a safe, nonempty platform identity",
        )


@dataclass(frozen=True)
class ReviewAtom:
    """The smallest independently reviewable unit known to the Host."""

    atom_id: str
    work_item_id: str
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.atom_id, "ReviewAtom ID")
        _require_id(self.work_item_id, "ReviewAtom WorkItem ID")
        if not isinstance(self.kind, str) or not self.kind:
            raise PlatformContractError("INVALID_REVIEW_ATOM", "ReviewAtom kind is required")
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))

    @classmethod
    def create(
        cls, *, run_id: str, work_item_id: str, kind: str,
        source_anchor: str, rule_id: str, payload: Mapping[str, Any] | None = None,
    ) -> "ReviewAtom":
        """Create a deterministic identity from Host-frozen source coordinates."""
        for value, label in (
            (run_id, "Run ID"), (work_item_id, "WorkItem ID"),
            (source_anchor, "source anchor"), (rule_id, "rule ID"),
        ):
            if not isinstance(value, str) or not value:
                raise PlatformContractError("INVALID_REVIEW_ATOM", f"{label} is required")
        atom_id = _stable_id("review-atom", {
            "runId": run_id, "workItemId": work_item_id, "kind": kind,
            "sourceAnchor": source_anchor, "ruleId": rule_id,
        })
        return cls(atom_id, work_item_id, kind, payload or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "atomId": self.atom_id,
            "workItemId": self.work_item_id,
            "kind": self.kind,
            "payload": _plain(self.payload),
        }


@dataclass(frozen=True)
class ReviewBatch:
    """One Host-planned Agent task with hard item and byte bounds."""

    batch_id: str
    sequence: int
    atom_ids: tuple[str, ...]
    payload_bytes: int
    status: str = "planned"
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.batch_id, "ReviewBatch ID")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise PlatformContractError("INVALID_REVIEW_BATCH", "ReviewBatch sequence is invalid")
        if not self.atom_ids:
            raise PlatformContractError("INVALID_REVIEW_BATCH", "ReviewBatch sequence and atoms are required")
        if len(self.atom_ids) != len(set(self.atom_ids)):
            raise PlatformContractError("INVALID_REVIEW_BATCH", "ReviewBatch atoms must be unique")
        for atom_id in self.atom_ids:
            _require_id(atom_id, "ReviewAtom ID")
        if (
            not isinstance(self.payload_bytes, int)
            or isinstance(self.payload_bytes, bool)
            or self.payload_bytes < 1
        ):
            raise PlatformContractError(
                "INVALID_REVIEW_BATCH", "ReviewBatch byte size must be positive",
            )
        if self.status not in BATCH_STATES:
            raise PlatformContractError(
                "INVALID_REVIEW_BATCH", "ReviewBatch status is unsupported",
            )
        if self.status == "blocked" and not self.blocked_reason:
            raise PlatformContractError(
                "INVALID_REVIEW_BATCH", "A blocked ReviewBatch requires a reason",
            )
        if self.status != "blocked" and self.blocked_reason is not None:
            raise PlatformContractError(
                "INVALID_REVIEW_BATCH", "Only a blocked ReviewBatch may have a reason",
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "sequence": self.sequence,
            "atomIds": list(self.atom_ids),
            "payloadBytes": self.payload_bytes,
            "status": self.status,
            **({"blockedReason": self.blocked_reason} if self.blocked_reason else {}),
        }


@dataclass(frozen=True)
class BatchVerdict:
    """One append-only submission covering exactly one ReviewBatch."""

    submission_id: str
    batch_id: str
    decisions: tuple[Mapping[str, Any], ...]
    supersedes: str | None = None
    status: str = "accepted"

    def __post_init__(self) -> None:
        _require_id(self.submission_id, "BatchVerdict submission ID")
        _require_id(self.batch_id, "BatchVerdict batch ID")
        if not self.decisions:
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT", "BatchVerdict decisions are required",
            )
        normalized: list[Mapping[str, Any]] = []
        atom_ids: list[str] = []
        for decision in self.decisions:
            if not isinstance(decision, Mapping):
                raise PlatformContractError(
                    "INVALID_BATCH_VERDICT", "Every batch decision must be an object",
                )
            atom_id = decision.get("atomId")
            _require_id(atom_id, "BatchVerdict atom ID")
            atom_ids.append(atom_id)
            normalized.append(_frozen_mapping(decision))
        if len(atom_ids) != len(set(atom_ids)):
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT", "A BatchVerdict may decide each atom only once",
            )
        object.__setattr__(self, "decisions", tuple(normalized))
        if self.supersedes is not None:
            _require_id(self.supersedes, "Superseded submission ID")
            if self.supersedes == self.submission_id:
                raise PlatformContractError(
                    "INVALID_BATCH_VERDICT", "A verdict cannot supersede itself",
                )
        if self.status not in VERDICT_STATES:
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT", "BatchVerdict status is unsupported",
            )

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(_json_bytes({
            "submissionId": self.submission_id,
            "batchId": self.batch_id,
            "decisions": self.decisions,
            "supersedes": self.supersedes,
        })).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "submissionId": self.submission_id,
            "batchId": self.batch_id,
            "decisions": _plain(self.decisions),
            "supersedes": self.supersedes,
            "status": self.status,
            "contentDigest": f"sha256:{self.content_digest}",
        }


@dataclass(frozen=True)
class CoverageEntry:
    """Coverage and effective-verdict pointer for one ReviewAtom."""

    atom_id: str
    batch_id: str
    status: str = "planned"
    effective_submission_id: str | None = None
    revision_ids: tuple[str, ...] = ()
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.atom_id, "Coverage atom ID")
        _require_id(self.batch_id, "Coverage batch ID")
        if self.status not in BATCH_STATES:
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY", "Coverage status is unsupported",
            )
        if len(self.revision_ids) != len(set(self.revision_ids)):
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY", "Coverage revisions must be unique",
            )
        for revision_id in self.revision_ids:
            _require_id(revision_id, "Coverage revision ID")
        if self.effective_submission_id is not None:
            _require_id(self.effective_submission_id, "Effective submission ID")
            if self.effective_submission_id not in self.revision_ids:
                raise PlatformContractError(
                    "INVALID_COVERAGE_ENTRY",
                    "Effective submission must occur in revision history",
                )
        if self.status in {"accepted", "terminal"} and self.effective_submission_id is None:
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY",
                "Accepted coverage requires an effective submission",
            )
        if self.status in {"planned", "offered"} and self.revision_ids:
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY",
                "Unreviewed coverage cannot contain revision history",
            )
        if self.status == "blocked" and not self.blocked_reason:
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY", "Blocked coverage requires a reason",
            )
        if self.status != "blocked" and self.blocked_reason is not None:
            raise PlatformContractError(
                "INVALID_COVERAGE_ENTRY", "Only blocked coverage may have a reason",
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "atomId": self.atom_id,
            "batchId": self.batch_id,
            "status": self.status,
            "effectiveSubmissionId": self.effective_submission_id,
            "revisionIds": list(self.revision_ids),
            **({"blockedReason": self.blocked_reason} if self.blocked_reason else {}),
        }


def _batch_payload_size(
    atoms: Sequence[ReviewAtom], contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> int:
    payload: dict[str, Any] = {"atoms": [atom.as_dict() for atom in atoms]}
    if contexts:
        refs = tuple(dict.fromkeys(
            str(atom.payload["contextRef"])
            for atom in atoms if "contextRef" in atom.payload
        ))
        if refs:
            payload["contexts"] = [{
                "contextRef": ref, "value": contexts[ref],
            } for ref in refs]
    return len(_json_bytes(payload))


def _catalog_atom_contexts(
    atoms: Sequence[ReviewAtom],
) -> tuple[tuple[ReviewAtom, ...], Mapping[str, Mapping[str, Any]]]:
    catalog: dict[str, Mapping[str, Any]] = {}
    normalized: list[ReviewAtom] = []
    for atom in atoms:
        payload = dict(atom.payload)
        inline = payload.pop("context", None)
        if inline is not None:
            if "contextRef" in payload or not isinstance(inline, Mapping):
                raise PlatformContractError(
                    "INVALID_REVIEW_CONTEXT",
                    "ReviewAtom context must be one generated context object",
                    work_item_id=atom.work_item_id,
                )
            detached = _frozen_mapping(inline)
            context_ref = _stable_id("review-context", detached)
            catalog.setdefault(context_ref, detached)
            payload["contextRef"] = context_ref
        normalized.append(ReviewAtom(
            atom.atom_id, atom.work_item_id, atom.kind, payload,
        ))
    return tuple(normalized), MappingProxyType(catalog)


@dataclass(frozen=True)
class CoverageLedger:
    """Immutable Host plan for bounded, exactly-once review coverage."""

    run_id: str
    atoms: tuple[ReviewAtom, ...]
    batches: tuple[ReviewBatch, ...]
    entries: tuple[CoverageEntry, ...]
    verdicts: tuple[BatchVerdict, ...] = ()
    max_batch_items: int = 64
    max_batch_bytes: int = 24 * 1024
    terminal_status: str | None = None
    contexts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.run_id, "CoverageLedger Run ID")
        normalized_contexts: dict[str, Mapping[str, Any]] = {}
        if not isinstance(self.contexts, Mapping):
            raise PlatformContractError(
                "INVALID_REVIEW_CONTEXT", "Coverage context catalog must be an object",
            )
        for context_ref, context in self.contexts.items():
            if not isinstance(context_ref, str) or not isinstance(context, Mapping):
                raise PlatformContractError(
                    "INVALID_REVIEW_CONTEXT", "Coverage context catalog entry is malformed",
                )
            frozen = _frozen_mapping(context)
            if context_ref != _stable_id("review-context", frozen):
                raise PlatformContractError(
                    "INVALID_REVIEW_CONTEXT", "Coverage context identity is not content-addressed",
                )
            normalized_contexts[context_ref] = frozen
        object.__setattr__(self, "contexts", MappingProxyType(normalized_contexts))
        if (
            not isinstance(self.max_batch_items, int)
            or isinstance(self.max_batch_items, bool)
            or not isinstance(self.max_batch_bytes, int)
            or isinstance(self.max_batch_bytes, bool)
            or self.max_batch_items < 1
            or self.max_batch_bytes < 1
        ):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Review batch limits must be positive",
            )
        atom_ids = tuple(atom.atom_id for atom in self.atoms)
        if len(atom_ids) != len(set(atom_ids)):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "ReviewAtom identities must be unique",
            )
        batch_ids = tuple(batch.batch_id for batch in self.batches)
        if len(batch_ids) != len(set(batch_ids)):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "ReviewBatch identities must be unique",
            )
        if tuple(batch.sequence for batch in self.batches) != tuple(
            range(1, len(self.batches) + 1)
        ):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "ReviewBatch sequences must be contiguous",
            )
        atom_by_id = {atom.atom_id: atom for atom in self.atoms}
        context_refs = tuple(
            str(atom.payload["contextRef"])
            for atom in self.atoms if "contextRef" in atom.payload
        )
        if any(ref not in self.contexts for ref in context_refs):
            raise PlatformContractError(
                "INVALID_REVIEW_CONTEXT", "ReviewAtom references an unknown coverage context",
            )
        if set(self.contexts) != set(context_refs):
            raise PlatformContractError(
                "INVALID_REVIEW_CONTEXT", "Coverage context catalog contains an unused entry",
            )
        planned_ids: list[str] = []
        atom_batch: dict[str, str] = {}
        for batch in self.batches:
            if len(batch.atom_ids) > self.max_batch_items:
                raise PlatformContractError(
                    "REVIEW_BATCH_LIMIT_EXCEEDED", "ReviewBatch exceeds the item limit",
                )
            if any(atom_id not in atom_by_id for atom_id in batch.atom_ids):
                raise PlatformContractError(
                    "UNKNOWN_REVIEW_ATOM", "ReviewBatch references an unknown ReviewAtom",
                )
            batch_atoms = tuple(atom_by_id[atom_id] for atom_id in batch.atom_ids)
            if batch.payload_bytes != _batch_payload_size(batch_atoms, self.contexts):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "ReviewBatch byte accounting is inconsistent",
                )
            if batch.payload_bytes > self.max_batch_bytes:
                raise PlatformContractError(
                    "REVIEW_BATCH_LIMIT_EXCEEDED", "ReviewBatch exceeds the byte limit",
                )
            planned_ids.extend(batch.atom_ids)
            atom_batch.update({atom_id: batch.batch_id for atom_id in batch.atom_ids})
        if tuple(planned_ids) != atom_ids:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER",
                "Every ReviewAtom must occur exactly once in stable plan order",
            )
        entry_by_atom = {entry.atom_id: entry for entry in self.entries}
        if len(entry_by_atom) != len(self.entries) or set(entry_by_atom) != set(atom_ids):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage entries must match ReviewAtoms exactly",
            )
        if any(entry_by_atom[atom_id].batch_id != batch_id for atom_id, batch_id in atom_batch.items()):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage entry batch ownership is inconsistent",
            )
        if self.terminal_status is not None:
            if self.terminal_status not in LEDGER_TERMINAL_STATES:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Coverage terminal status is unsupported",
                )
            if any(batch.status != "terminal" for batch in self.batches):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Terminal coverage requires terminal batches",
                )
            entry_states = {entry.status for entry in self.entries}
            if self.terminal_status == "completed" and entry_states - {"accepted"}:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Completed coverage contains an unfinished atom",
                )
            if self.terminal_status == "partial" and entry_states - {"accepted", "blocked"}:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Partial coverage contains an unclassified atom",
                )
        elif any(batch.status == "terminal" for batch in self.batches):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Active coverage cannot contain terminal batches",
            )
        for batch in self.batches:
            if batch.status == "terminal":
                continue
            statuses = {entry.status for entry in self.entries if entry.batch_id == batch.batch_id}
            if statuses != {batch.status}:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Batch and atom coverage statuses are inconsistent",
                )
        verdict_ids = tuple(verdict.submission_id for verdict in self.verdicts)
        if len(verdict_ids) != len(set(verdict_ids)):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "BatchVerdict submission identities must be unique",
            )
        verdict_by_id = {verdict.submission_id: verdict for verdict in self.verdicts}
        batch_by_id = {batch.batch_id: batch for batch in self.batches}
        seen_verdicts: dict[str, BatchVerdict] = {}
        verdicts_by_batch: dict[str, list[BatchVerdict]] = {}
        for verdict in self.verdicts:
            batch = batch_by_id.get(verdict.batch_id)
            if batch is None:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "BatchVerdict references an unknown batch",
                )
            decision_ids = tuple(str(item["atomId"]) for item in verdict.decisions)
            if set(decision_ids) != set(batch.atom_ids):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Persisted verdict does not cover its batch exactly",
                )
            previous = seen_verdicts.get(verdict.supersedes) if verdict.supersedes else None
            prior_for_batch = verdicts_by_batch.setdefault(verdict.batch_id, [])
            if (not prior_for_batch) != (verdict.supersedes is None):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "BatchVerdict revision chain is discontinuous",
                )
            if verdict.supersedes is not None and (
                previous is None or previous.batch_id != verdict.batch_id
            ):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "BatchVerdict supersedes an invalid revision",
                )
            prior_for_batch.append(verdict)
            seen_verdicts[verdict.submission_id] = verdict
        for batch_verdicts in verdicts_by_batch.values():
            if any(item.status != "superseded" for item in batch_verdicts[:-1]):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Only the latest batch revision may remain accepted",
                )
            if batch_verdicts[-1].status != "accepted":
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "The latest batch revision must be accepted",
                )
        for entry in self.entries:
            if any(revision_id not in verdict_by_id for revision_id in entry.revision_ids):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Coverage history references an unknown verdict",
                )
            if entry.effective_submission_id is not None:
                verdict = verdict_by_id[entry.effective_submission_id]
                if verdict.batch_id != entry.batch_id or verdict.status != "accepted":
                    raise PlatformContractError(
                        "INVALID_COVERAGE_LEDGER", "Effective coverage verdict is inconsistent",
                    )
            expected_revisions = tuple(
                verdict.submission_id
                for verdict in verdicts_by_batch.get(entry.batch_id, ())
            )
            if entry.revision_ids != expected_revisions:
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", "Coverage revision history is inconsistent",
                )

    @classmethod
    def plan(
        cls, run_id: str, atoms: Sequence[ReviewAtom], *,
        max_batch_items: int = 64, max_batch_bytes: int = 24 * 1024,
    ) -> "CoverageLedger":
        """Greedily build stable batches without exceeding either hard limit."""
        ordered = tuple(atoms)
        if any(not isinstance(atom, ReviewAtom) for atom in ordered):
            raise PlatformContractError(
                "INVALID_REVIEW_ATOM", "Coverage planning accepts only ReviewAtom values",
            )
        if (
            not isinstance(max_batch_items, int)
            or isinstance(max_batch_items, bool)
            or not isinstance(max_batch_bytes, int)
            or isinstance(max_batch_bytes, bool)
            or max_batch_items < 1
            or max_batch_bytes < 1
        ):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Review batch limits must be positive",
            )
        ordered, contexts = _catalog_atom_contexts(ordered)
        groups: list[tuple[ReviewAtom, ...]] = []
        current: list[ReviewAtom] = []
        for atom in ordered:
            if _batch_payload_size((atom,), contexts) > max_batch_bytes:
                raise PlatformContractError(
                    "REVIEW_ATOM_TOO_LARGE", "One ReviewAtom cannot fit in a bounded Agent task",
                    work_item_id=atom.work_item_id,
                )
            proposed = (*current, atom)
            if current and (
                len(proposed) > max_batch_items
                or _batch_payload_size(proposed, contexts) > max_batch_bytes
            ):
                groups.append(tuple(current))
                current = [atom]
            else:
                current.append(atom)
        if current:
            groups.append(tuple(current))
        batches = tuple(
            ReviewBatch(
                _stable_id("review-batch", {
                    "runId": run_id,
                    "sequence": sequence,
                    "atomIds": [atom.atom_id for atom in group],
                }),
                sequence,
                tuple(atom.atom_id for atom in group),
                _batch_payload_size(group, contexts),
            )
            for sequence, group in enumerate(groups, 1)
        )
        batch_by_atom = {
            atom_id: batch.batch_id for batch in batches for atom_id in batch.atom_ids
        }
        entries = tuple(
            CoverageEntry(atom.atom_id, batch_by_atom[atom.atom_id]) for atom in ordered
        )
        return cls(
            run_id, ordered, batches, entries,
            max_batch_items=max_batch_items, max_batch_bytes=max_batch_bytes,
            contexts=contexts,
        )

    def atoms_for(self, batch_id: str) -> tuple[ReviewAtom, ...]:
        offset = 0
        for batch in self.batches:
            width = len(batch.atom_ids)
            if batch.batch_id == batch_id:
                # ``__post_init__`` proves that batch atom IDs are exactly the
                # concatenated atom order, so this slice needs no global map.
                return self.atoms[offset:offset + width]
            offset += width
        raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "ReviewBatch is not in this ledger")

    def _transitioned(self, **changes: Any) -> "CoverageLedger":
        """Construct a locally proven state transition without a global rescan.

        Every public transition below starts from a fully validated immutable
        ledger and checks the complete delta it applies.  Re-running
        ``__post_init__`` after each small delta makes a large Run quadratic;
        deserialization and external construction still perform the full
        invariant pass.
        """
        ledger = object.__new__(CoverageLedger)
        values = {
            "run_id": self.run_id,
            "atoms": self.atoms,
            "batches": self.batches,
            "entries": self.entries,
            "verdicts": self.verdicts,
            "max_batch_items": self.max_batch_items,
            "max_batch_bytes": self.max_batch_bytes,
            "terminal_status": self.terminal_status,
            "contexts": self.contexts,
            **changes,
        }
        for name, value in values.items():
            object.__setattr__(ledger, name, value)
        return ledger

    def as_dict(self) -> dict[str, Any]:
        """Return the complete, detached side-ledger representation."""
        return {
            "schemaVersion": "1.1.0",
            "runId": self.run_id,
            "limits": {
                "maxBatchItems": self.max_batch_items,
                "maxBatchBytes": self.max_batch_bytes,
            },
            "atoms": [atom.as_dict() for atom in self.atoms],
            "contexts": [{
                "contextRef": context_ref,
                "value": _plain(context),
            } for context_ref, context in self.contexts.items()],
            "batches": [batch.as_dict() for batch in self.batches],
            "entries": [entry.as_dict() for entry in self.entries],
            "verdicts": [verdict.as_dict() for verdict in self.verdicts],
            "terminalStatus": self.terminal_status,
        }

    def canonical_bytes(self) -> bytes:
        """Serialize deterministically for persistence and replay comparison."""
        return (
            json.dumps(
                self.as_dict(), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CoverageLedger":
        """Restore a side ledger while re-running all structural invariants."""
        if not isinstance(value, Mapping):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage ledger must be an object",
            )
        expected_keys_v1 = {
            "schemaVersion", "runId", "limits", "atoms", "batches",
            "entries", "verdicts", "terminalStatus",
        }
        expected_keys_v11 = expected_keys_v1 | {"contexts"}
        version = value.get("schemaVersion")
        if not (
            version == "1.0.0" and set(value) == expected_keys_v1
            or version == "1.1.0" and set(value) == expected_keys_v11
        ):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage ledger shape or schema version is unsupported",
            )
        limits = value.get("limits")
        if not isinstance(limits, Mapping) or set(limits) != {"maxBatchItems", "maxBatchBytes"}:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage ledger limits are malformed",
            )

        def records(
            name: str, required: set[str], optional: set[str] | None = None,
        ) -> tuple[Mapping[str, Any], ...]:
            raw = value.get(name)
            if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", f"Coverage ledger {name} must be an object array",
                )
            allowed = required | (optional or set())
            if any(not required.issubset(item) or set(item) - allowed for item in raw):
                raise PlatformContractError(
                    "INVALID_COVERAGE_LEDGER", f"Coverage ledger {name} record shape is invalid",
                )
            return tuple(raw)

        try:
            atoms = tuple(ReviewAtom(
                raw["atomId"], raw["workItemId"], raw["kind"], raw["payload"],
            ) for raw in records(
                "atoms", {"atomId", "workItemId", "kind", "payload"},
            ))
            batches = tuple(ReviewBatch(
                raw["batchId"], raw["sequence"], tuple(raw["atomIds"]),
                raw["payloadBytes"], raw["status"], raw.get("blockedReason"),
            ) for raw in records(
                "batches", {"batchId", "sequence", "atomIds", "payloadBytes", "status"},
                {"blockedReason"},
            ))
            entries = tuple(CoverageEntry(
                raw["atomId"], raw["batchId"], raw["status"],
                raw.get("effectiveSubmissionId"), tuple(raw["revisionIds"]),
                raw.get("blockedReason"),
            ) for raw in records(
                "entries",
                {"atomId", "batchId", "status", "effectiveSubmissionId", "revisionIds"},
                {"blockedReason"},
            ))
            verdicts: list[BatchVerdict] = []
            for raw in records("verdicts", {
                "submissionId", "batchId", "decisions", "supersedes",
                "status", "contentDigest",
            }):
                verdict = BatchVerdict(
                    raw["submissionId"], raw["batchId"], tuple(raw["decisions"]),
                    raw.get("supersedes"), raw["status"],
                )
                if raw.get("contentDigest") != f"sha256:{verdict.content_digest}":
                    raise PlatformContractError(
                        "COVERAGE_LEDGER_DIGEST_MISMATCH",
                        "Persisted BatchVerdict content does not match its digest",
                    )
                verdicts.append(verdict)
            terminal = value.get("terminalStatus")
            contexts: dict[str, Mapping[str, Any]] = {}
            if version == "1.1.0":
                for raw in records("contexts", {"contextRef", "value"}):
                    if not isinstance(raw["value"], Mapping):
                        raise PlatformContractError(
                            "INVALID_REVIEW_CONTEXT", "Coverage context value is malformed",
                        )
                    contexts[str(raw["contextRef"])] = raw["value"]
            restored = cls(
                value["runId"], atoms, batches, entries, tuple(verdicts),
                limits["maxBatchItems"], limits["maxBatchBytes"], terminal,
                contexts,
            )
            if version == "1.0.0":
                migrated_atoms, migrated_contexts = _catalog_atom_contexts(restored.atoms)
                migrated_by_id = {atom.atom_id: atom for atom in migrated_atoms}
                migrated_batches = tuple(replace(
                    batch,
                    payload_bytes=_batch_payload_size(
                        tuple(migrated_by_id[atom_id] for atom_id in batch.atom_ids),
                        migrated_contexts,
                    ),
                ) for batch in restored.batches)
                return cls(
                    restored.run_id, migrated_atoms, migrated_batches, restored.entries,
                    restored.verdicts, restored.max_batch_items,
                    restored.max_batch_bytes, restored.terminal_status,
                    migrated_contexts,
                )
            return restored
        except PlatformContractError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage ledger contains malformed fields",
            ) from error

    def offer(self, batch_id: str | None = None) -> "CoverageLedger":
        """Mark the next planned batch as the bounded task visible to an Agent."""
        if self.terminal_status is not None:
            raise PlatformContractError("REVIEW_TERMINAL", "Terminal review coverage cannot be offered")
        batch = next((item for item in self.batches if item.batch_id == batch_id), None)
        if batch_id is None:
            batch = next((item for item in self.batches if item.status == "planned"), None)
        if batch is None:
            raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "No planned ReviewBatch is available")
        if batch.status == "offered":
            return self
        if batch.status != "planned":
            raise PlatformContractError(
                "INVALID_REVIEW_TRANSITION", "Only a planned ReviewBatch can be offered",
            )
        batches = tuple(
            replace(item, status="offered") if item.batch_id == batch.batch_id else item
            for item in self.batches
        )
        entries = tuple(
            replace(entry, status="offered") if entry.batch_id == batch.batch_id else entry
            for entry in self.entries
        )
        return self._transitioned(batches=batches, entries=entries)

    def accept(self, verdict: BatchVerdict) -> "CoverageLedger":
        """Accept one complete batch submission, replaying identical input safely."""
        if self.terminal_status is not None:
            raise PlatformContractError("REVIEW_TERMINAL", "Terminal review coverage cannot change")
        if not isinstance(verdict, BatchVerdict) or verdict.status != "accepted":
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT", "Coverage accepts only a new accepted BatchVerdict",
            )
        existing = next(
            (item for item in self.verdicts if item.submission_id == verdict.submission_id), None,
        )
        if existing is not None:
            if existing.content_digest == verdict.content_digest:
                return self
            raise PlatformContractError(
                "REVIEW_SUBMISSION_CONFLICT",
                "The submission identity is already bound to different content",
            )
        batch = next((item for item in self.batches if item.batch_id == verdict.batch_id), None)
        if batch is None:
            raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "Verdict references an unknown ReviewBatch")
        submitted_ids = tuple(str(item["atomId"]) for item in verdict.decisions)
        unknown = set(submitted_ids) - set(batch.atom_ids)
        if unknown:
            raise PlatformContractError(
                "UNKNOWN_REVIEW_ATOM", "BatchVerdict references an atom outside its batch",
            )
        if set(submitted_ids) != set(batch.atom_ids):
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT",
                "BatchVerdict must handle every offered atom exactly once",
            )
        batch_entries = tuple(entry for entry in self.entries if entry.batch_id == batch.batch_id)
        effective_ids = {
            entry.effective_submission_id
            for entry in batch_entries
            if entry.effective_submission_id is not None
        }
        if verdict.supersedes is None:
            if batch.status != "offered" or effective_ids:
                raise PlatformContractError(
                    "INVALID_REVIEW_TRANSITION",
                    "Only an offered, undecided ReviewBatch can accept an initial verdict",
                )
            verdicts = (*self.verdicts, verdict)
            revision_ids = (verdict.submission_id,)
            batches = tuple(
                replace(item, status="accepted") if item.batch_id == batch.batch_id else item
                for item in self.batches
            )
        else:
            if batch.status != "accepted" or effective_ids != {verdict.supersedes}:
                raise PlatformContractError(
                    "REVIEW_CORRECTION_CONFLICT",
                    "Correction must supersede the batch's current effective verdict",
                )
            previous = next(
                (item for item in self.verdicts if item.submission_id == verdict.supersedes),
                None,
            )
            if previous is None or previous.batch_id != batch.batch_id or previous.status != "accepted":
                raise PlatformContractError(
                    "REVIEW_CORRECTION_CONFLICT",
                    "Correction target is not the current accepted verdict for this batch",
                )
            verdicts = tuple(
                replace(item, status="superseded")
                if item.submission_id == verdict.supersedes else item
                for item in self.verdicts
            ) + (verdict,)
            revision_ids = (*batch_entries[0].revision_ids, verdict.submission_id)
            batches = self.batches
        entries = tuple(
            replace(
                entry,
                status="accepted",
                effective_submission_id=verdict.submission_id,
                revision_ids=revision_ids,
            ) if entry.batch_id == batch.batch_id else entry
            for entry in self.entries
        )
        return self._transitioned(
            batches=batches, entries=entries, verdicts=verdicts,
        )

    def block(self, batch_id: str, reason: str) -> "CoverageLedger":
        """Close one unreviewable batch with an explicit, durable reason."""
        if self.terminal_status is not None:
            raise PlatformContractError("REVIEW_TERMINAL", "Terminal review coverage cannot change")
        if not isinstance(reason, str) or not reason.strip():
            raise PlatformContractError("INVALID_REVIEW_BLOCK", "A blocked batch requires a reason")
        batch = next((item for item in self.batches if item.batch_id == batch_id), None)
        if batch is None:
            raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "ReviewBatch is not in this ledger")
        if batch.status == "blocked":
            if batch.blocked_reason == reason:
                return self
            raise PlatformContractError(
                "REVIEW_BLOCK_CONFLICT", "ReviewBatch is already blocked for a different reason",
            )
        if batch.status not in {"planned", "offered"}:
            raise PlatformContractError(
                "INVALID_REVIEW_TRANSITION", "Accepted ReviewBatch cannot become blocked",
            )
        batches = tuple(
            replace(item, status="blocked", blocked_reason=reason)
            if item.batch_id == batch_id else item
            for item in self.batches
        )
        entries = tuple(
            replace(entry, status="blocked", blocked_reason=reason)
            if entry.batch_id == batch_id else entry
            for entry in self.entries
        )
        return self._transitioned(batches=batches, entries=entries)

    def finalize(self, status: str) -> "CoverageLedger":
        """Freeze coverage after enforcing the requested terminal semantics."""
        if status not in LEDGER_TERMINAL_STATES:
            raise PlatformContractError("INVALID_REVIEW_TERMINAL", "Terminal status is unsupported")
        if self.terminal_status is not None:
            if self.terminal_status == status:
                return self
            raise PlatformContractError(
                "REVIEW_TERMINAL", "Coverage is already frozen with another terminal status",
            )
        batch_states = {batch.status for batch in self.batches}
        if status == "completed" and batch_states - {"accepted"}:
            raise PlatformContractError(
                "INCOMPLETE_REVIEW_COVERAGE",
                "Completed review requires every batch to be accepted",
            )
        if status == "partial" and batch_states - {"accepted", "blocked"}:
            raise PlatformContractError(
                "INCOMPLETE_REVIEW_COVERAGE",
                "Partial review requires every batch to be accepted or explicitly blocked",
            )
        batches = tuple(
            replace(batch, status="terminal", blocked_reason=None) for batch in self.batches
        )
        return self._transitioned(batches=batches, terminal_status=status)


class JsonCoverageLedgerStore:
    """Persist coverage as a frozen snapshot plus an append-only transition journal.

    ``save`` remains the compatibility path for legacy callers that require a
    materialized latest-state JSON document.  New incremental runtimes use
    ``initialize`` and the typed transition methods, so steady-state writes
    are proportional to one transition rather than to the whole Run.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._journal_heads: dict[str, tuple[int, str]] = {}
        self._current_ledgers: dict[str, CoverageLedger] = {}

    def _path(self, run_id: str) -> Path:
        _require_id(run_id, "CoverageLedger Run ID")
        return self.root / f"{run_id}.review-coverage.json"

    def _journal_path(self, run_id: str) -> Path:
        _require_id(run_id, "CoverageLedger Run ID")
        return self.root / f"{run_id}.review-coverage.journal.jsonl"

    @staticmethod
    def _digest(value: bytes) -> str:
        return f"sha256:{hashlib.sha256(value).hexdigest()}"

    def save(self, ledger: CoverageLedger) -> Path:
        if not isinstance(ledger, CoverageLedger):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage store accepts only CoverageLedger values",
            )
        destination = self._path(ledger.run_id)
        journal = self._journal_path(ledger.run_id)
        if journal.exists():
            raise PlatformContractError(
                "COVERAGE_STORE_MODE_CONFLICT",
                "A journaled coverage Run cannot be replaced through the legacy snapshot API",
            )
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            temporary.write_bytes(ledger.canonical_bytes())
            temporary.replace(destination)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        self._journal_heads[ledger.run_id] = (
            0, self._digest(ledger.canonical_bytes()),
        )
        self._current_ledgers[ledger.run_id] = ledger
        return destination

    def initialize(self, ledger: CoverageLedger) -> Path:
        """Write the immutable base snapshot for a new incremental Run."""
        if not isinstance(ledger, CoverageLedger):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Coverage store accepts only CoverageLedger values",
            )
        destination = self._path(ledger.run_id)
        journal = self._journal_path(ledger.run_id)
        if destination.exists() or journal.exists():
            raise PlatformContractError(
                "REVIEW_RUN_EXISTS", "Coverage persistence already exists for this Run",
            )
        saved = self.save(ledger)
        return saved

    @staticmethod
    def _verdict_from_event(value: object) -> BatchVerdict:
        if not isinstance(value, Mapping) or set(value) != {
            "submissionId", "batchId", "decisions", "supersedes",
            "status", "contentDigest",
        }:
            raise PlatformContractError(
                "INVALID_COVERAGE_JOURNAL", "Journal verdict shape is malformed",
            )
        decisions = value.get("decisions")
        if not isinstance(decisions, list):
            raise PlatformContractError(
                "INVALID_COVERAGE_JOURNAL", "Journal verdict decisions are malformed",
            )
        try:
            verdict = BatchVerdict(
                value["submissionId"], value["batchId"], tuple(decisions),
                value.get("supersedes"), value["status"],
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, PlatformContractError):
                raise
            raise PlatformContractError(
                "INVALID_COVERAGE_JOURNAL", "Journal verdict is malformed",
            ) from error
        if value.get("contentDigest") != f"sha256:{verdict.content_digest}":
            raise PlatformContractError(
                "COVERAGE_LEDGER_DIGEST_MISMATCH",
                "Journal verdict content does not match its digest",
            )
        return verdict

    @classmethod
    def _apply_operation(
        cls, ledger: CoverageLedger, operation: object,
    ) -> CoverageLedger:
        if not isinstance(operation, Mapping) or not isinstance(operation.get("kind"), str):
            raise PlatformContractError(
                "INVALID_COVERAGE_JOURNAL", "Coverage journal operation is malformed",
            )
        kind = operation["kind"]
        if kind == "offer" and set(operation) == {"kind", "batchId"}:
            return ledger.offer(operation["batchId"])
        if kind == "accept" and set(operation) == {"kind", "verdict"}:
            return ledger.accept(cls._verdict_from_event(operation["verdict"]))
        if kind == "block" and set(operation) == {"kind", "batchId", "reason"}:
            return ledger.block(operation["batchId"], operation["reason"])
        if kind == "finalize" and set(operation) == {"kind", "status"}:
            return ledger.finalize(operation["status"])
        raise PlatformContractError(
            "INVALID_COVERAGE_JOURNAL", "Coverage journal operation is unsupported",
        )

    def _append_operation(
        self, ledger: CoverageLedger, operation: Mapping[str, Any],
    ) -> CoverageLedger:
        persisted = self._current_ledgers.get(ledger.run_id)
        if persisted is not ledger:
            if persisted is None:
                persisted = self.load(ledger.run_id)
            if persisted is None or persisted.canonical_bytes() != ledger.canonical_bytes():
                raise PlatformContractError(
                    "COVERAGE_STORE_CONFLICT",
                    "Coverage transition is based on stale persisted state",
                )
        transitioned = self._apply_operation(ledger, operation)
        sequence, previous_digest = self._journal_heads[ledger.run_id]
        event = {
            "schemaVersion": "1.0.0",
            "sequence": sequence + 1,
            "previousDigest": previous_digest,
            "operation": _plain(operation),
        }
        event_digest = self._digest(_json_bytes(event))
        record = {**event, "eventDigest": event_digest}
        encoded = _json_bytes(record) + b"\n"
        journal = self._journal_path(ledger.run_id)
        try:
            with journal.open("ab") as handle:
                offset = handle.tell()
                try:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    handle.seek(offset)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                    raise
        except OSError:
            raise
        self._journal_heads[ledger.run_id] = (sequence + 1, event_digest)
        self._current_ledgers[ledger.run_id] = transitioned
        return transitioned

    def offer(self, ledger: CoverageLedger, batch_id: str) -> CoverageLedger:
        return self._append_operation(
            ledger, {"kind": "offer", "batchId": batch_id},
        )

    def accept(self, ledger: CoverageLedger, verdict: BatchVerdict) -> CoverageLedger:
        if not isinstance(verdict, BatchVerdict):
            raise PlatformContractError(
                "INVALID_BATCH_VERDICT", "Coverage journal requires a BatchVerdict",
            )
        return self._append_operation(
            ledger, {"kind": "accept", "verdict": verdict.as_dict()},
        )

    def block(self, ledger: CoverageLedger, batch_id: str, reason: str) -> CoverageLedger:
        return self._append_operation(
            ledger, {"kind": "block", "batchId": batch_id, "reason": reason},
        )

    def finalize(self, ledger: CoverageLedger, status: str) -> CoverageLedger:
        return self._append_operation(
            ledger, {"kind": "finalize", "status": status},
        )

    def load(self, run_id: str) -> CoverageLedger | None:
        source = self._path(run_id)
        if not source.is_file():
            return None
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Persisted coverage ledger cannot be decoded",
            ) from error
        ledger = CoverageLedger.from_dict(value)
        sequence = 0
        head = self._digest(ledger.canonical_bytes())
        journal = self._journal_path(run_id)
        if journal.is_file():
            try:
                journal_bytes = journal.read_bytes()
            except OSError as error:
                raise PlatformContractError(
                    "INVALID_COVERAGE_JOURNAL", "Coverage journal cannot be read",
                ) from error
            if journal_bytes and not journal_bytes.endswith(b"\n"):
                raise PlatformContractError(
                    "INVALID_COVERAGE_JOURNAL", "Coverage journal ends with a partial event",
                )
            lines = journal_bytes.splitlines()
            for raw in lines:
                try:
                    record = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise PlatformContractError(
                        "INVALID_COVERAGE_JOURNAL", "Coverage journal cannot be decoded",
                    ) from error
                if not isinstance(record, Mapping) or set(record) != {
                    "schemaVersion", "sequence", "previousDigest", "operation", "eventDigest",
                }:
                    raise PlatformContractError(
                        "INVALID_COVERAGE_JOURNAL", "Coverage journal event shape is malformed",
                    )
                event = {
                    "schemaVersion": record["schemaVersion"],
                    "sequence": record["sequence"],
                    "previousDigest": record["previousDigest"],
                    "operation": record["operation"],
                }
                if (
                    record["schemaVersion"] != "1.0.0"
                    or record["sequence"] != sequence + 1
                    or record["previousDigest"] != head
                    or record["eventDigest"] != self._digest(_json_bytes(event))
                ):
                    raise PlatformContractError(
                        "COVERAGE_JOURNAL_DIGEST_MISMATCH",
                        "Coverage journal sequence or digest chain is invalid",
                    )
                ledger = self._apply_operation(ledger, record["operation"])
                sequence += 1
                head = record["eventDigest"]
        self._journal_heads[run_id] = (sequence, head)
        self._current_ledgers[run_id] = ledger
        return ledger


__all__ = [
    "BatchVerdict", "CoverageEntry", "CoverageLedger", "JsonCoverageLedgerStore",
    "ReviewAtom", "ReviewBatch",
]
