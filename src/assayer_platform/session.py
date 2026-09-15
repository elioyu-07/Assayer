"""Interactive platform gates for Agent-driven plugin sessions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .contract import (
    CheckContract, CommitReceipt, DecisionProposal, DimensionObservation,
    EvidenceRecord, Finding, InvestigationPacket, Operation, PlatformContext,
    PlatformContractError, PlatformEvent, PlatformLedger, PlatformRun,
    PlatformRunResult, PluginManifest, ProviderEvidenceExpectation, WorkFailure, WorkItem,
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
        self.failures: list[WorkFailure] = []
        self.operations: list[Operation] = []
        self.events: list[PlatformEvent] = []
        self.inspection_batches = 0
        self.batch_splits = 0
        self.inspection_failures = 0
        self.semantic_task_builds = 0
        self.semantic_task_build_ms = 0
        self.semantic_task_bytes = 0
        self.agent_wait_ms = 0
        self.agent_wait_samples = 0
        self.transport_ms = 0
        self.transport_samples = 0
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
        restored.semantic_task_builds = int(ledger.get("metrics", {}).get("semanticTaskBuilds", 0) or 0)
        restored.semantic_task_build_ms = int(ledger.get("metrics", {}).get("semanticTaskBuildMs", 0) or 0)
        restored.semantic_task_bytes = int(ledger.get("metrics", {}).get("semanticTaskBytes", 0) or 0)
        restored.agent_wait_ms = int(ledger.get("metrics", {}).get("agentWaitMs", 0) or 0)
        restored.agent_wait_samples = int(ledger.get("metrics", {}).get("agentWaitSamples", 0) or 0)
        restored.transport_ms = int(ledger.get("metrics", {}).get("transportMs", 0) or 0)
        restored.transport_samples = int(ledger.get("metrics", {}).get("transportSamples", 0) or 0)
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

    def record_investigation(
        self, packet: InvestigationPacket, *,
        provider_evidence_expectation: ProviderEvidenceExpectation | None = None,
        issued_provider_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        self._require_running()
        from .kernel import PlatformKernel
        item = self.work_items.get(packet.work_item.work_item_id)
        if item is None:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Investigation references an undiscovered WorkItem")
        PlatformKernel._validate_packets(
            (packet,), (item,), self.check,
            provider_evidence_expectation=provider_evidence_expectation,
            issued_provider_evidence=issued_provider_evidence,
        )
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
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Record a rejected request without changing semantic conclusions."""
        self._require_running()
        if not isinstance(error_code, str) or not error_code:
            raise PlatformContractError("INVALID_OBSERVABILITY", "A Host rejection requires an error code")
        self._emit(
            "host.request.rejected", "finish", "rejected",
            operation_id=operation_id, work_item_id=work_item_id,
            details={
                "errorCode": error_code,
                **({"message": message} if message else {}),
                **dict(details or {}),
            },
        )
        self._save()

    def record_semantic_task_metrics(self, *, duration_ms: int, payload_bytes: int) -> None:
        """Record Host compilation cost for one bounded semantic task."""
        self._require_running()
        self.semantic_task_builds += 1
        self.semantic_task_build_ms += max(0, int(duration_ms))
        self.semantic_task_bytes = max(0, int(payload_bytes))
        self._emit(
            "semantic.task.compiled", "finish", "captured",
            details={
                "durationMs": max(0, int(duration_ms)),
                "payloadBytes": max(0, int(payload_bytes)),
            },
        )
        self._save()

    def record_agent_wait(self, *, duration_ms: int) -> None:
        """Record elapsed time between a semantic task and its next turn."""
        self._require_running()
        self.agent_wait_samples += 1
        self.agent_wait_ms += max(0, int(duration_ms))
        self._emit(
            "agent.wait.finished", "finish", "captured",
            details={
                "durationMs": max(0, int(duration_ms)),
                "sample": self.agent_wait_samples,
            },
        )
        self._save()

    def record_transport_timing(self, *, duration_ms: int, persist: bool = True) -> None:
        """Record one successful Host transport request for this Run."""
        self._require_running()
        self.transport_samples += 1
        self.transport_ms += max(0, int(duration_ms))
        self._emit(
            "transport.request.finished", "finish", "captured",
            details={
                "durationMs": max(0, int(duration_ms)),
                "sample": self.transport_samples,
            },
        )
        if persist:
            self._save()

    def record_recovery(
        self, work_item_id: str, status: str, details: Mapping[str, object] | None = None,
    ) -> None:
        """Record a domain-neutral recovery outcome.

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
            "semanticTaskBuilds": self.semantic_task_builds,
            "semanticTaskBuildMs": self.semantic_task_build_ms,
            "semanticTaskBytes": self.semantic_task_bytes,
            "agentWaitMs": self.agent_wait_ms,
            "agentWaitSamples": self.agent_wait_samples,
            "transportMs": self.transport_ms,
            "transportSamples": self.transport_samples,
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
            tuple(self.failures), authority,
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
