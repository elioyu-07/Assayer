"""Interactive platform gates for Agent-driven plugin sessions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .contract import (
    CheckContract, CommitReceipt, DecisionProposal, DimensionObservation,
    EvidenceRecord, Finding, InvestigationPacket, Operation, PlatformContext,
    PlatformContractError, PlatformEvent, PlatformLedger, PlatformRun,
    PlatformRunResult, PluginManifest, ReviewCheckpoint, WorkFailure, WorkItem,
)
from .decision import validate_decision_shape
from .ledger import PlatformLedgerStore
from .state_machine import validate_terminal_transition, validate_workflow, validate_workflow_transition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class InteractivePlatformSession:
    """Apply a plugin manifest to an interactive Agent workflow.

    Unlike ``PlatformKernel.run``, this object does not own model turns or
    runtime navigation. It provides the same plugin/check selection and
    decision-shape gates to a long-lived MCP session where the Agent chooses
    when to discover, inspect, and decide.
    """

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    def check(self, check_id: str, version: str) -> CheckContract:
        matches = tuple(
            check for check in self.manifest.checks
            if check.check_id == check_id and check.version == version
        )
        if len(matches) != 1:
            raise PlatformContractError("UNKNOWN_CHECK", "The plugin does not declare this Check version")
        return matches[0]

    def validate_decision(
        self,
        rule: Mapping[str, object],
        result: str,
        findings: Sequence[Mapping[str, object]],
    ) -> CheckContract:
        check_id = rule.get("ruleId")
        version = rule.get("version")
        if not isinstance(check_id, str) or not isinstance(version, str):
            raise PlatformContractError("UNKNOWN_CHECK", "Decision rule identity is incomplete")
        check = self.check(check_id, version)
        validate_decision_shape(result, findings, check.dimensions, check.decision_states)
        return check

    def begin(
        self,
        context: PlatformContext,
        scope: object,
        check_id: str,
        check_version: str,
        store: PlatformLedgerStore,
    ) -> "InteractivePlatformRun":
        return InteractivePlatformRun(
            self.manifest, self.check(check_id, check_version), context, scope, store,
        )


class InteractivePlatformRun:
    """Checkpoint an Agent-driven Check without taking over Agent turns."""

    def __init__(
        self,
        manifest: PluginManifest,
        check: CheckContract,
        context: PlatformContext,
        scope: object,
        store: PlatformLedgerStore,
    ) -> None:
        self.manifest = manifest
        self.check = check
        self.context = context
        self.store = store
        self.run = PlatformRun(
            context.run_id, manifest.plugin_id, manifest.version,
            check.check_id, check.version, _digest(scope), _now(),
            tuple(check.subject_kinds),
        )
        self.work_items: dict[str, WorkItem] = {}
        self.investigations: dict[str, InvestigationPacket] = {}
        self.decisions: dict[str, DecisionProposal] = {}
        self.receipts: dict[str, CommitReceipt] = {}
        self.review_checkpoints: dict[str, ReviewCheckpoint] = {}
        self.failures: list[WorkFailure] = []
        self.operations: list[Operation] = []
        self.events: list[PlatformEvent] = []
        self.inspection_batches = 0
        self.batch_splits = 0
        self.inspection_failures = 0
        self.adaptive_inspect_batch_size = manifest.execution_profile.inspect_batch_size
        self.discovery_complete = False
        self.status = "running"
        self.workflow: Mapping[str, object] = {
            "state": "running",
            "phase": "discovery",
            "canFinish": False,
            "requiredNextStep": "discover_work_items",
            "remaining": {
                "workItemsToInspect": 0,
                "workItemsToDecide": 0,
                "reviewItems": 0,
                "failures": 0,
            },
        }
        validate_workflow(self.workflow)
        self._emit("platform.run.started", "start", "started")
        self._save()

    @classmethod
    def restore(
        cls, manifest: PluginManifest, check: CheckContract,
        context: PlatformContext, store: PlatformLedgerStore,
        ledger: Mapping[str, object],
    ) -> "InteractivePlatformRun":
        """Hydrate one running interactive Run from its canonical ledger."""
        if ledger.get("status") != "running":
            raise PlatformContractError("RUN_TERMINAL", "Only a running ledger can be resumed")

        def work_item(value: Mapping[str, object]) -> WorkItem:
            return WorkItem(
                str(value["work_item_id"]), str(value["kind"]), str(value["identity"]),
                str(value["state_digest"]), value.get("metadata", {}),
            )

        raw_run = ledger.get("run")
        if not isinstance(raw_run, Mapping):
            raise PlatformContractError("INVALID_LEDGER", "Running ledger has no Run identity")
        restored_run = PlatformRun(
            str(raw_run["run_id"]), str(raw_run["plugin_id"]), str(raw_run["plugin_version"]),
            str(raw_run["check_id"]), str(raw_run["check_version"]),
            str(raw_run["scope_digest"]), str(raw_run.get("started_at", "")),
            tuple(str(item) for item in raw_run.get("subject_kinds", ())),
        )
        if restored_run.run_id != context.run_id:
            raise PlatformContractError("RUN_MISMATCH", "Resume descriptor and ledger Run identity differ")
        if (
            restored_run.plugin_id != manifest.plugin_id
            or restored_run.plugin_version != manifest.version
            or (restored_run.check_id, restored_run.check_version) != check.ref
        ):
            raise PlatformContractError("PLUGIN_IDENTITY_MISMATCH", "Running ledger does not match the installed plugin Check")

        restored = cls.__new__(cls)
        restored.manifest = manifest
        restored.check = check
        restored.context = context
        restored.store = store
        restored.run = restored_run
        restored.work_items = {}
        for value in ledger.get("work_items", ()):
            item = work_item(value)
            restored.work_items[item.work_item_id] = item
        restored.investigations = {}
        for value in ledger.get("investigations", ()):
            item = work_item(value["work_item"])
            evidence = tuple(EvidenceRecord(
                str(raw["evidence_id"]), str(raw["work_item_id"]), str(raw["check_id"]),
                str(raw["check_version"]), str(raw["kind"]), str(raw["source_identity"]),
                raw.get("payload", {}),
            ) for raw in value.get("evidence", ()))
            dimensions = tuple(DimensionObservation(
                str(raw["name"]), tuple(raw.get("observations", ())),
                tuple(raw.get("evidence_refs", ())), str(raw["candidate_status"]),
            ) for raw in value.get("dimensions", ()))
            packet = InvestigationPacket(
                item, str(value["check_id"]), str(value["check_version"]), dimensions,
                evidence, str(value["recovery_status"]), value.get("case_ref"),
                value.get("metadata", {}),
            )
            restored.investigations[item.work_item_id] = packet
        restored.decisions = {}
        for value in ledger.get("decisions", ()):
            proposal = DecisionProposal(
                str(value["work_item_id"]), str(value["check_id"]), str(value["check_version"]),
                str(value["result"]), tuple(Finding(
                    str(raw["dimension"]), str(raw["status"]), str(raw["reason"]),
                ) for raw in value.get("findings", ())), str(value["reason"]),
                value.get("details", {}),
            )
            restored.decisions[proposal.work_item_id] = proposal
        restored.receipts = {}
        for value in ledger.get("receipts", ()):
            receipt = CommitReceipt(
                str(value["commit_id"]), str(value["work_item_id"]), str(value["check_id"]),
                str(value["check_version"]), str(value["result"]), str(value["durability"]),
                value.get("metadata", {}), str(value.get("authority", "platform")),
            )
            restored.receipts[receipt.work_item_id] = receipt
        restored.review_checkpoints = {}
        for value in ledger.get("review_checkpoints", ()):
            checkpoint = ReviewCheckpoint(
                str(value["checkpoint_id"]), str(value["work_item_id"]), str(value["check_id"]),
                str(value["check_version"]), str(value["collection_id"]),
                tuple(value.get("item_ids", ())), value.get("payload", {}),
                value.get("supersedes_checkpoint_id"),
            )
            if checkpoint.checkpoint_id in restored.review_checkpoints:
                raise PlatformContractError(
                    "INVALID_REVIEW_CHECKPOINT", "Ledger review checkpoint IDs must be unique",
                )
            restored.review_checkpoints[checkpoint.checkpoint_id] = checkpoint
        restored._validate_review_checkpoint_history()
        restored.failures = [WorkFailure(
            str(value["work_item_id"]), str(value["check_id"]), str(value["code"]), str(value["message"]),
        ) for value in ledger.get("failures", ())]
        restored.operations = [Operation(
            str(value["operation_id"]), str(value["kind"]), str(value["work_item_id"]),
            str(value["check_id"]), str(value["status"]), int(value.get("attempt", 1)),
            value.get("error_code"), value.get("receipt_id"), str(value.get("started_at", "")),
            str(value.get("ended_at", "")), int(value.get("duration_ms", 0)),
        ) for value in ledger.get("operations", ())]
        restored.events = [PlatformEvent(
            str(value["event_id"]), int(value["sequence"]), str(value["run_id"]),
            str(value["name"]), str(value["phase"]), str(value["outcome"]),
            str(value["occurred_at"]), value.get("operation_id"), value.get("work_item_id"),
            value.get("check_id"), value.get("details", {}),
        ) for value in ledger.get("events", ())]
        restored.inspection_batches = 0
        restored.batch_splits = sum(item.kind == "inspect_batch" for item in restored.operations)
        restored.inspection_failures = sum(
            item.kind == "inspect" and item.status == "failed" for item in restored.operations
        )
        restored.adaptive_inspect_batch_size = manifest.execution_profile.inspect_batch_size
        restored.discovery_complete = any(
            item.kind == "discover" and item.status == "succeeded" for item in restored.operations
        )
        restored.status = "running"
        workflow = ledger.get("workflow")
        restored.workflow = dict(workflow) if isinstance(workflow, Mapping) else {
            "state": "running", "phase": "discovery", "canFinish": False,
            "requiredNextStep": "discover_work_items",
            "remaining": {"workItemsToInspect": 0, "workItemsToDecide": 0, "reviewItems": 0, "failures": 0},
        }
        validate_workflow(restored.workflow)
        return restored

    def record_workflow(self, workflow: Mapping[str, object]) -> None:
        """Persist the derived workflow boundary exposed to an Agent."""
        self._require_running()
        normalized = dict(workflow)
        if normalized == self.workflow:
            return
        validate_workflow_transition(self.workflow, normalized)
        previous_state = self.workflow.get("state")
        previous_phase = self.workflow.get("phase")
        self.workflow = normalized
        if (
            previous_state != normalized.get("state")
            or previous_phase != normalized.get("phase")
        ):
            self._emit(
                "platform.workflow.transition", "instant", str(normalized.get("state")),
                details={
                    "previousState": previous_state,
                    "previousPhase": previous_phase,
                    "phase": normalized.get("phase"),
                    "canFinish": bool(normalized.get("canFinish")),
                    "requiredNextStep": normalized.get("requiredNextStep"),
                },
            )
        self._save()

    def record_discovery(self, items: Sequence[WorkItem]) -> None:
        self._require_running()
        from .kernel import PlatformKernel
        PlatformKernel._validate_work_items(items, self.manifest)
        for item in items:
            existing = self.work_items.get(item.work_item_id)
            if existing is not None and existing != item:
                raise PlatformContractError("WORK_ITEM_CONFLICT", "WorkItem identity changed within the active Run")
            self.work_items[item.work_item_id] = item
        self.discovery_complete = True
        self._record_operation("discover", "succeeded")
        self._save()

    def record_investigation(self, packet: InvestigationPacket) -> None:
        self._require_running()
        from .kernel import PlatformKernel
        item = self.work_items.get(packet.work_item.work_item_id)
        if item is None:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Investigation references an undiscovered WorkItem")
        PlatformKernel._validate_packets((packet,), (item,), self.check)
        existing = self.investigations.get(item.work_item_id)
        if existing is not None and existing != packet:
            raise PlatformContractError("INVESTIGATION_CONFLICT", "Investigation changed within the active Run")
        self.investigations[item.work_item_id] = packet
        self.failures = [failure for failure in self.failures if failure.work_item_id != item.work_item_id]
        self._record_operation("inspect", "succeeded", item.work_item_id)
        self._save()

    def record_inspection_split(
        self, work_item_ids: Sequence[str], error_code: str, next_batch_size: int,
    ) -> None:
        """Checkpoint a safe batch split without turning it into a finding."""
        self._require_running()
        if not work_item_ids or any(item_id not in self.work_items for item_id in work_item_ids):
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Inspection split references an undiscovered WorkItem")
        operation_id = self._record_operation("inspect_batch", "failed", error_code=error_code)
        self._emit(
            "inspection.batch.split", "instant", "split", operation_id=operation_id,
            details={
                "workItemIds": list(work_item_ids),
                "failedBatchSize": len(work_item_ids),
                "nextBatchSize": next_batch_size,
                "errorCode": error_code,
            },
        )
        self._save()

    def record_inspection_failure(self, work_item_id: str, error_code: str, message: str) -> None:
        """Checkpoint one isolated WorkItem inspection failure."""
        self._require_running()
        if work_item_id not in self.work_items:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Inspection failure references an undiscovered WorkItem")
        self.failures = [failure for failure in self.failures if failure.work_item_id != work_item_id]
        self.failures.append(WorkFailure(work_item_id, self.check.check_id, error_code, message))
        operation_id = self._record_operation(
            "inspect", "failed", work_item_id, error_code=error_code,
        )
        self._emit(
            "inspection.item.failed", "instant", "failed", operation_id=operation_id,
            work_item_id=work_item_id, details={"errorCode": error_code},
        )
        self._save()

    def record_inspection_batch_metrics(
        self, *, attempts: int, splits: int, failures: int, effective_batch_size: int,
    ) -> None:
        """Persist transport-independent adaptive batching diagnostics."""
        self._require_running()
        self.inspection_batches += attempts
        self.batch_splits += splits
        self.inspection_failures += failures
        self.adaptive_inspect_batch_size = effective_batch_size
        self._emit(
            "inspection.batch.summary", "instant", "recorded",
            details={
                "attempts": attempts,
                "splits": splits,
                "failures": failures,
                "effectiveBatchSize": effective_batch_size,
            },
        )
        self._save()

    def record_host_rejection(
        self, error_code: str, *, work_item_id: str | None = None,
        operation_id: str | None = None, message: str | None = None,
    ) -> None:
        """Record a rejected request without changing semantic conclusions."""
        self._require_running()
        if not isinstance(error_code, str) or not error_code:
            raise PlatformContractError("INVALID_OBSERVABILITY", "A Host rejection requires an error code")
        self._emit(
            "host.request.rejected", "finish", "rejected",
            operation_id=operation_id, work_item_id=work_item_id,
            details={"errorCode": error_code, **({"message": message} if message else {})},
        )
        self._save()

    def record_recovery(
        self, work_item_id: str, status: str, details: Mapping[str, object] | None = None,
    ) -> None:
        """Record a domain-neutral recovery checkpoint.

        Recovery implementation belongs to the plugin/runtime adapter.  The
        platform still records its outcome so an Agent can reason about the
        validity of a subsequent decision and diagnostics can explain a
        blocked or uncertain path.
        """
        self._require_running()
        if work_item_id not in self.work_items:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Recovery references an undiscovered WorkItem")
        if work_item_id in self.decisions:
            raise PlatformContractError(
                "COMMIT_CONFLICT", "Recovery cannot change after the WorkItem decision is committed",
            )
        if status not in {"restored", "not_required", "uncertain", "failed"}:
            raise PlatformContractError("INVALID_RECOVERY", "Recovery status is not supported")
        operation_id = self._record_operation(
            "recover",
            "succeeded" if status in {"restored", "not_required"} else "failed",
            work_item_id,
        )
        self._emit(
            "recovery.finished", "finish", status,
            operation_id=operation_id, work_item_id=work_item_id,
            details=details or {},
        )
        self._save()

    def record_commit(
        self, proposal: DecisionProposal, receipt: CommitReceipt, *,
        operation_id: str | None = None,
    ) -> None:
        self._require_running()
        from .kernel import PlatformKernel
        packet = self.investigations.get(proposal.work_item_id)
        if packet is None:
            raise PlatformContractError("UNKNOWN_INVESTIGATION", "Decision references an uninvestigated WorkItem")
        latest_recovery = next((
            event.outcome for event in reversed(self.events)
            if event.name == "recovery.finished"
            and event.work_item_id == proposal.work_item_id
        ), packet.recovery_status)
        if latest_recovery not in {"restored", "not_required"}:
            raise PlatformContractError(
                "RECOVERY_INVALID", "The latest WorkItem recovery does not permit a Decision commit",
            )
        PlatformKernel._validate_proposals((proposal,), {proposal.work_item_id: packet}, self.check)
        PlatformKernel._validate_receipt(receipt, proposal)
        existing = self.receipts.get(proposal.work_item_id)
        if existing is not None and existing != receipt:
            raise PlatformContractError("COMMIT_CONFLICT", "A different receipt already exists for this WorkItem")
        previous_decision = self.decisions.get(proposal.work_item_id)
        previous_receipt = self.receipts.get(proposal.work_item_id)
        operation_count = len(self.operations)
        event_count = len(self.events)
        self.decisions[proposal.work_item_id] = proposal
        self.receipts[proposal.work_item_id] = receipt
        self._record_operation(
            "commit", "succeeded", proposal.work_item_id, receipt.commit_id,
            operation_id=operation_id,
        )
        try:
            self._save()
        except Exception:
            if previous_decision is None:
                self.decisions.pop(proposal.work_item_id, None)
            else:
                self.decisions[proposal.work_item_id] = previous_decision
            if previous_receipt is None:
                self.receipts.pop(proposal.work_item_id, None)
            else:
                self.receipts[proposal.work_item_id] = previous_receipt
            del self.operations[operation_count:]
            del self.events[event_count:]
            raise

    @staticmethod
    def _effective_checkpoints(
        checkpoints: Mapping[str, ReviewCheckpoint],
    ) -> tuple[ReviewCheckpoint, ...]:
        successors = {
            item.supersedes_checkpoint_id: item.checkpoint_id
            for item in checkpoints.values()
            if item.supersedes_checkpoint_id is not None
        }
        leaves: list[ReviewCheckpoint] = []
        for checkpoint in checkpoints.values():
            if checkpoint.supersedes_checkpoint_id is not None:
                continue
            leaf_id = checkpoint.checkpoint_id
            while leaf_id in successors:
                leaf_id = successors[leaf_id]
            leaves.append(checkpoints[leaf_id])
        return tuple(leaves)

    @property
    def effective_review_checkpoints(self) -> tuple[ReviewCheckpoint, ...]:
        """Return current checkpoint leaves while retaining all history in the ledger."""
        return self._effective_checkpoints(self.review_checkpoints)

    def _validate_review_checkpoint_history(self) -> None:
        staged: dict[str, ReviewCheckpoint] = {}
        for checkpoint in self.review_checkpoints.values():
            self._validate_review_checkpoint_record(checkpoint, staged)
            staged[checkpoint.checkpoint_id] = checkpoint

    def _validate_review_checkpoint_record(
        self, checkpoint: ReviewCheckpoint,
        staged: Mapping[str, ReviewCheckpoint] | None = None,
    ) -> None:
        records = staged if staged is not None else self.review_checkpoints
        if checkpoint.work_item_id not in self.investigations:
            raise PlatformContractError(
                "UNKNOWN_INVESTIGATION", "Review checkpoint references an uninspected WorkItem",
            )
        if (checkpoint.check_id, checkpoint.check_version) != self.check.ref:
            raise PlatformContractError("CHECK_MISMATCH", "Review checkpoint Check identity is not current")
        existing = records.get(checkpoint.checkpoint_id)
        if existing is not None:
            if existing != checkpoint:
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_CONFLICT", "Review checkpoint ID was reused with different content",
                )
            return
        effective = self._effective_checkpoints(records)
        target_id = checkpoint.supersedes_checkpoint_id
        target = records.get(target_id) if target_id is not None else None
        if target_id is not None:
            if target is None:
                raise PlatformContractError(
                    "UNKNOWN_REVIEW_CHECKPOINT", "Checkpoint correction references an unknown checkpoint",
                )
            if target_id not in {item.checkpoint_id for item in effective}:
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_CONFLICT", "Checkpoint correction must supersede the current effective checkpoint",
                )
            if (
                checkpoint.work_item_id != target.work_item_id
                or checkpoint.check_id != target.check_id
                or checkpoint.check_version != target.check_version
                or checkpoint.collection_id != target.collection_id
            ):
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_CONFLICT", "Checkpoint correction must retain the original review scope",
                )
            if checkpoint.item_ids != target.item_ids:
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_CONFLICT", "Checkpoint correction must cover exactly the original item IDs",
                )
        claimed = {
            (item.work_item_id, item.collection_id, item_id): item.checkpoint_id
            for item in effective if item.checkpoint_id != target_id
            for item_id in item.item_ids
        }
        overlap = [
            item_id for item_id in checkpoint.item_ids
            if (checkpoint.work_item_id, checkpoint.collection_id, item_id) in claimed
        ]
        if overlap:
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_CONFLICT", "Evidence collection item is already covered by another checkpoint",
            )

    def validate_review_checkpoint_record(self, checkpoint: ReviewCheckpoint) -> None:
        """Validate append-only checkpoint identity and correction scope without mutation."""
        self._require_running()
        if self.review_checkpoints.get(checkpoint.checkpoint_id) == checkpoint:
            return
        if checkpoint.work_item_id in self.decisions:
            raise PlatformContractError(
                "COMMIT_CONFLICT", "Review checkpoints cannot change after the WorkItem decision is committed",
            )
        self._validate_review_checkpoint_record(checkpoint)

    def record_review_checkpoints(self, checkpoints: Sequence[ReviewCheckpoint]) -> None:
        """Atomically persist idempotent checkpoint records and append-only corrections."""
        self._require_running()
        if not checkpoints:
            raise PlatformContractError("INVALID_REVIEW_CHECKPOINT", "At least one review checkpoint is required")
        staged = dict(self.review_checkpoints)
        accepted: list[ReviewCheckpoint] = []
        for checkpoint in checkpoints:
            if staged.get(checkpoint.checkpoint_id) == checkpoint:
                continue
            if checkpoint.work_item_id in self.decisions:
                raise PlatformContractError(
                    "COMMIT_CONFLICT", "Review checkpoints cannot change after the WorkItem decision is committed",
                )
            self._validate_review_checkpoint_record(checkpoint, staged)
            existing = staged.get(checkpoint.checkpoint_id)
            if existing is not None:
                continue
            staged[checkpoint.checkpoint_id] = checkpoint
            accepted.append(checkpoint)
        previous = self.review_checkpoints
        operation_count = len(self.operations)
        event_count = len(self.events)
        self.review_checkpoints = staged
        for checkpoint in accepted:
            self._record_operation(
                "review_checkpoint", "succeeded", checkpoint.work_item_id,
                operation_id=checkpoint.checkpoint_id,
            )
            self._emit(
                "review.checkpoint.saved", "instant", "succeeded",
                work_item_id=checkpoint.work_item_id,
                details={
                    "checkpointId": checkpoint.checkpoint_id,
                    "collectionId": checkpoint.collection_id,
                    "itemCount": len(checkpoint.item_ids),
                    "supersedesCheckpointId": checkpoint.supersedes_checkpoint_id,
                },
            )
        try:
            self._save()
        except Exception:
            self.review_checkpoints = previous
            del self.operations[operation_count:]
            del self.events[event_count:]
            raise

    def validate_finish(self, status: str, failures: Sequence[WorkFailure] = ()) -> None:
        """Validate terminal eligibility before plugin finalization can publish artifacts."""
        self._require_running()
        if status not in {"completed", "partial", "failed"}:
            raise PlatformContractError("INVALID_RUN_STATUS", "Interactive Run terminal status is not supported")
        if status == "completed" and not self.discovery_complete:
            raise PlatformContractError("DISCOVERY_MISSING", "Completed Run has not completed WorkItem discovery")
        unresolved_failure_ids = {failure.work_item_id for failure in (*self.failures, *failures)}
        if status == "completed" and (self.failures or failures):
            raise PlatformContractError("UNRESOLVED_FAILURE", "Completed Run cannot contain unresolved WorkItem failures")
        uninspected = set(self.work_items) - set(self.investigations) - unresolved_failure_ids
        if status == "completed" and uninspected:
            raise PlatformContractError("INVESTIGATION_MISSING", "Completed Run has an uninspected WorkItem")
        pending = set(self.investigations) - set(self.decisions)
        if status == "completed" and pending:
            raise PlatformContractError("DECISION_MISSING", "Completed Run has an uncommitted investigation")
        latest_recovery: dict[str, str] = {}
        for event in self.events:
            if event.name == "recovery.finished" and event.work_item_id:
                latest_recovery[event.work_item_id] = event.outcome
        unsafe_decisions = {
            work_item_id for work_item_id in self.decisions
            if latest_recovery.get(
                work_item_id, self.investigations[work_item_id].recovery_status,
            ) not in {"restored", "not_required"}
        }
        if status == "completed" and unsafe_decisions:
            raise PlatformContractError(
                "RECOVERY_INVALID", "Completed Run contains a Decision after unresolved recovery",
            )

    def finish(self, status: str, failures: Sequence[WorkFailure] = ()) -> PlatformRunResult:
        self.validate_finish(status, failures)
        current_state = str(self.workflow.get("state", "running"))
        # Direct diagnostic closeout may arrive before the derived workflow
        # response was persisted.  The data gates above prove readiness; make
        # that implicit boundary explicit before publishing the terminal state.
        if status == "completed" and current_state != "ready_to_finish":
            ready_workflow = {
                "state": "ready_to_finish", "phase": "closeout", "canFinish": True,
                "requiredNextStep": "finish_plugin_run",
                "remaining": {
                    "workItemsToInspect": 0, "workItemsToDecide": 0,
                    "reviewItems": 0, "failures": len(self.failures),
                },
            }
            validate_workflow_transition(self.workflow, ready_workflow)
            previous_state = self.workflow.get("state")
            previous_phase = self.workflow.get("phase")
            self.workflow = ready_workflow
            self._emit(
                "platform.workflow.transition", "instant", "ready_to_finish",
                details={
                    "previousState": previous_state, "previousPhase": previous_phase,
                    "phase": "closeout", "canFinish": True,
                    "requiredNextStep": "finish_plugin_run",
                },
            )
        validate_terminal_transition(str(self.workflow.get("state", "running")), status)
        previous_failures = list(self.failures)
        previous_status = self.status
        previous_workflow = self.workflow
        event_count = len(self.events)
        for failure in failures:
            if failure not in self.failures:
                self.failures.append(failure)
        self.status = status
        self.workflow = {
            "state": status,
            "phase": "finished",
            "canFinish": True,
            "requiredNextStep": None,
            "remaining": {
                "workItemsToInspect": 0,
                "workItemsToDecide": 0,
                "reviewItems": 0,
                "failures": len(self.failures),
            },
        }
        self._emit("platform.run.terminal", "finish", status)
        try:
            ledger = self._save()
        except Exception:
            self.failures = previous_failures
            self.status = previous_status
            self.workflow = previous_workflow
            del self.events[event_count:]
            raise
        metrics = self.metrics()
        return PlatformRunResult(
            self.run.run_id, status,
            () if status == "failed" else tuple(self.decisions.values()),
            tuple(self.failures), metrics,
            () if status == "failed" else tuple(self.receipts.values()), ledger,
        )

    def metrics(self) -> dict[str, int]:
        """Return current Run counters without changing its lifecycle state."""
        return {
            "discovered": len(self.work_items), "inspected": len(self.investigations),
            "decisionsCommitted": len(self.decisions), "operations": len(self.operations),
            "inspectionBatches": self.inspection_batches,
            "batchSplits": self.batch_splits,
            "inspectionFailures": self.inspection_failures,
            "inspectBatchSize": self.adaptive_inspect_batch_size,
            "reviewCheckpoints": len(self.effective_review_checkpoints),
            "reviewCheckpointRecords": len(self.review_checkpoints),
            "reviewItemsCheckpointed": sum(len(item.item_ids) for item in self.effective_review_checkpoints),
        }

    def _record_operation(
        self, kind: str, status: str, work_item_id: str | None = None,
        receipt_id: str | None = None, error_code: str | None = None,
        operation_id: str | None = None,
    ) -> str:
        now = _now()
        operation_id = operation_id or f"operation:{self.run.run_id}:interactive:{len(self.operations) + 1}"
        if any(item.operation_id == operation_id for item in self.operations):
            raise PlatformContractError(
                "OPERATION_CONFLICT", "Interactive operation identity is already present in the ledger",
            )
        self.operations.append(Operation(
            operation_id, kind, work_item_id or "run", self.check.check_id,
            status, error_code=error_code, receipt_id=receipt_id,
            started_at=now, ended_at=now,
        ))
        self._emit(
            "operation.finished", "finish", status, operation_id=operation_id,
            work_item_id=work_item_id, details={"kind": kind},
        )
        return operation_id

    def _emit(
        self, name: str, phase: str, outcome: str, *, operation_id: str | None = None,
        work_item_id: str | None = None, details: Mapping[str, object] | None = None,
    ) -> None:
        sequence = len(self.events) + 1
        self.events.append(PlatformEvent(
            f"event:{self.run.run_id}:interactive:{sequence}", sequence, self.run.run_id,
            name, phase, outcome, _now(), operation_id, work_item_id,
            self.check.check_id, details or {},
        ))

    def _save(self) -> PlatformLedger:
        authority = next(iter(self.receipts.values())).authority if self.receipts else "platform"
        ledger = PlatformLedger(
            self.run, self.status, tuple(self.operations), tuple(self.events),
            tuple(self.receipts.values()), (), tuple(self.work_items.values()),
            tuple(self.investigations.values()), tuple(self.decisions.values()),
            tuple(self.failures), authority, tuple(self.review_checkpoints.values()),
            self.workflow, self.metrics(),
        )
        if ledger.status in {"completed", "partial", "failed"}:
            from .result_conformance import inspect_result_conformance

            prospective = PlatformRunResult(
                self.run.run_id,
                ledger.status,
                () if ledger.status == "failed" else ledger.decisions,
                ledger.failures,
                ledger.metrics,
                () if ledger.status == "failed" else ledger.receipts,
                ledger,
            )
            conformance = inspect_result_conformance(prospective)
            if not conformance.passed:
                first = conformance.issues[0]
                raise PlatformContractError(
                    "RESULT_CONFORMANCE_FAILED",
                    f"{first.message} Contract: {first.invariant}. Next action: {first.next_action}",
                )
        self.store.save(ledger)
        exporter = getattr(self.store, "export", None)
        if callable(exporter):
            try:
                exporter(ledger)
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PLATFORM_EXPORT_FAILED", "Platform trace artifacts could not be published",
                ) from error
        return ledger

    def _require_running(self) -> None:
        if self.status != "running":
            raise PlatformContractError("RUN_TERMINAL", "Interactive platform Run is already terminal")
