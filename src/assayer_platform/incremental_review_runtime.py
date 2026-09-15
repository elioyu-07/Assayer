"""Host runtime for bounded, resumable common-review tasks.

The coordinator is the narrow operational bridge between the immutable
coverage ledger and Agent-visible common-review payloads.  It deliberately
keeps batch, atom, Evidence, and submission identities inside the Host.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from .common_review import CommonReviewSubmission
from .contract import PlatformContractError
from .incremental_review import CoverageLedger, JsonCoverageLedgerStore, ReviewAtom
from .review_binding import ReviewTaskBinding
from .review_projection import build_common_review_task, review_atom_support_ids


class IncrementalReviewCoordinator:
    """Plan, issue, accept, recover, and finalize incremental review batches."""

    def __init__(self, ledger: CoverageLedger, store: JsonCoverageLedgerStore):
        if not isinstance(ledger, CoverageLedger):
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER", "Review coordinator requires a CoverageLedger",
            )
        if not isinstance(store, JsonCoverageLedgerStore):
            raise PlatformContractError(
                "INVALID_COVERAGE_STORE", "Review coordinator requires a coverage store",
            )
        offered = tuple(batch for batch in ledger.batches if batch.status == "offered")
        if len(offered) > 1:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER",
                "Incremental review may have only one offered batch",
            )
        self._store = store
        self._ledger = ledger
        self._active_binding: ReviewTaskBinding | None = None
        self._evidence_refs_by_id = self._allocate_evidence_refs(
            ledger.atoms, ledger.contexts,
        )

    @classmethod
    def start(
        cls,
        store: JsonCoverageLedgerStore,
        *,
        run_id: str,
        atoms: Sequence[ReviewAtom],
        max_batch_items: int = 64,
        max_batch_bytes: int = 24 * 1024,
    ) -> "IncrementalReviewCoordinator":
        """Create and durably persist a new frozen review plan."""
        if store.load(run_id) is not None:
            raise PlatformContractError(
                "REVIEW_RUN_EXISTS", "Incremental review coverage already exists for this Run",
            )
        ledger = CoverageLedger.plan(
            run_id,
            atoms,
            max_batch_items=max_batch_items,
            max_batch_bytes=max_batch_bytes,
        )
        coordinator = cls(ledger, store)
        store.initialize(ledger)
        return coordinator

    @classmethod
    def restore(
        cls, store: JsonCoverageLedgerStore, run_id: str,
    ) -> "IncrementalReviewCoordinator":
        """Restore a persisted plan and revalidate all ledger invariants."""
        ledger = store.load(run_id)
        if ledger is None:
            raise PlatformContractError(
                "UNKNOWN_REVIEW_RUN", "No incremental review coverage exists for this Run",
            )
        return cls(ledger, store)

    @staticmethod
    def _allocate_evidence_refs(
        atoms: Sequence[ReviewAtom],
        contexts: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, str]:
        evidence_ids: list[str] = []
        seen: set[str] = set()
        for atom in atoms:
            for evidence_id in review_atom_support_ids(atom, contexts):
                if evidence_id not in seen:
                    seen.add(evidence_id)
                    evidence_ids.append(evidence_id)
        return {
            evidence_id: f"R{ordinal}"
            for ordinal, evidence_id in enumerate(evidence_ids, 1)
        }

    @property
    def ledger(self) -> CoverageLedger:
        return self._ledger

    @property
    def active_evidence_bindings(self) -> tuple[tuple[str, str], ...]:
        """Return the current task's opaque-to-internal Evidence binding."""
        return (
            self._active_binding.evidence_bindings
            if self._active_binding is not None else ()
        )

    def _binding(self, batch_id: str) -> ReviewTaskBinding:
        evidence_ids: list[str] = []
        seen: set[str] = set()
        for atom in self._ledger.atoms_for(batch_id):
            for evidence_id in review_atom_support_ids(atom, self._ledger.contexts):
                if evidence_id not in seen:
                    seen.add(evidence_id)
                    evidence_ids.append(evidence_id)
        return ReviewTaskBinding.from_ledger(
            self._ledger,
            batch_id,
            evidence_bindings=tuple(
                (self._evidence_refs_by_id[evidence_id], evidence_id)
                for evidence_id in evidence_ids
            ),
        )

    def _activate(self, batch_id: str) -> dict[str, Any]:
        binding = self._binding(batch_id)
        task = build_common_review_task(self._ledger, binding)
        task_bytes = len(json.dumps(
            task,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"))
        if task_bytes > self._ledger.max_batch_bytes:
            raise PlatformContractError(
                "REVIEW_BATCH_LIMIT_EXCEEDED",
                "Projected common-review task exceeds the Agent byte limit",
            )
        self._active_binding = binding
        return task

    def next_task(self) -> dict[str, Any] | None:
        """Durably offer and return the sole current task, or ``None`` if drained."""
        if self._ledger.terminal_status is not None:
            return None
        if self._active_binding is not None:
            return self._activate(self._active_binding.batch_id)
        offered = tuple(batch for batch in self._ledger.batches if batch.status == "offered")
        if len(offered) > 1:
            raise PlatformContractError(
                "INVALID_COVERAGE_LEDGER",
                "Incremental review may have only one offered batch",
            )
        if offered:
            return self._activate(offered[0].batch_id)
        planned = next(
            (batch for batch in self._ledger.batches if batch.status == "planned"), None,
        )
        if planned is None:
            self._active_binding = None
            return None
        self._ledger = self._store.offer(self._ledger, planned.batch_id)
        return self._activate(planned.batch_id)

    def submit(self, value: Mapping[str, Any]) -> CoverageLedger:
        """Bind and durably accept the current Agent submission exactly once."""
        if self._ledger.terminal_status is not None:
            raise PlatformContractError(
                "REVIEW_TERMINAL", "Terminal review coverage cannot accept submissions",
            )
        binding = self._active_binding
        if binding is None:
            offered = tuple(
                batch for batch in self._ledger.batches if batch.status == "offered"
            )
            if len(offered) != 1:
                raise PlatformContractError(
                    "NO_ACTIVE_REVIEW_TASK", "No unique Agent review task is active",
                )
            binding = self._binding(offered[0].batch_id)
        submission = CommonReviewSubmission.from_dict(value)
        verdict = binding.bind(submission)
        self._ledger = self._store.accept(self._ledger, verdict)
        self._active_binding = None
        return self._ledger

    def correction_task(self, batch_id: str) -> dict[str, Any]:
        """Issue a correction task whose superseded revision is Host-bound."""
        if self._ledger.terminal_status is not None:
            raise PlatformContractError(
                "REVIEW_TERMINAL", "Terminal review coverage cannot be corrected",
            )
        if any(batch.status == "offered" for batch in self._ledger.batches):
            raise PlatformContractError(
                "INVALID_REVIEW_TRANSITION",
                "Finish the currently offered task before issuing a correction",
            )
        batch = next(
            (item for item in self._ledger.batches if item.batch_id == batch_id), None,
        )
        if batch is None:
            raise PlatformContractError(
                "UNKNOWN_REVIEW_BATCH", "ReviewBatch is not in this ledger",
            )
        if batch.status != "accepted":
            raise PlatformContractError(
                "INVALID_REVIEW_TRANSITION",
                "Only an accepted review batch can be corrected",
            )
        return self._activate(batch_id)

    def block_current(self, reason: str) -> CoverageLedger:
        """Durably block the sole offered batch when its evidence is unavailable."""
        offered = tuple(batch for batch in self._ledger.batches if batch.status == "offered")
        if len(offered) != 1:
            raise PlatformContractError(
                "NO_ACTIVE_REVIEW_TASK", "No unique Agent review task is active",
            )
        self._ledger = self._store.block(
            self._ledger, offered[0].batch_id, reason,
        )
        self._active_binding = None
        return self._ledger

    def block(self, batch_id: str, reason: str) -> CoverageLedger:
        """Durably block one planned or offered batch during Host closeout."""
        self._ledger = self._store.block(self._ledger, batch_id, reason)
        if (
            self._active_binding is not None
            and self._active_binding.batch_id == batch_id
        ):
            self._active_binding = None
        return self._ledger

    def finish(self, status: str) -> CoverageLedger:
        """Enforce coverage completeness and durably freeze the review."""
        self._ledger = self._store.finalize(self._ledger, status)
        self._active_binding = None
        return self._ledger


__all__ = ["IncrementalReviewCoordinator"]
