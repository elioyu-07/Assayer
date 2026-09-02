"""Interactive platform gates for Agent-driven plugin sessions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .contract import (
    CheckContract, CommitReceipt, DecisionProposal, InvestigationPacket,
    Operation, PlatformContext, PlatformContractError, PlatformEvent,
    PlatformLedger, PlatformRun, PlatformRunResult, PluginManifest,
    WorkFailure, WorkItem,
)
from .decision import validate_decision_shape
from .ledger import PlatformLedgerStore


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
        )
        self.work_items: dict[str, WorkItem] = {}
        self.investigations: dict[str, InvestigationPacket] = {}
        self.decisions: dict[str, DecisionProposal] = {}
        self.receipts: dict[str, CommitReceipt] = {}
        self.failures: list[WorkFailure] = []
        self.operations: list[Operation] = []
        self.events: list[PlatformEvent] = []
        self.status = "running"
        self._emit("platform.run.started", "start", "started")
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
        self._record_operation("inspect", "succeeded", item.work_item_id)
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
        if status not in {"restored", "not_required", "uncertain", "failed"}:
            raise PlatformContractError("INVALID_RECOVERY", "Recovery status is not supported")
        self._record_operation("recover", status, work_item_id)
        self._emit(
            "recovery.finished", "finish", status, work_item_id=work_item_id,
            details=details or {},
        )
        self._save()

    def record_commit(self, proposal: DecisionProposal, receipt: CommitReceipt) -> None:
        self._require_running()
        from .kernel import PlatformKernel
        packet = self.investigations.get(proposal.work_item_id)
        if packet is None:
            raise PlatformContractError("UNKNOWN_INVESTIGATION", "Decision references an uninvestigated WorkItem")
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
        self._record_operation("commit", "succeeded", proposal.work_item_id, receipt.commit_id)
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

    def finish(self, status: str, failures: Sequence[WorkFailure] = ()) -> PlatformRunResult:
        self._require_running()
        if status not in {"completed", "partial", "failed"}:
            raise PlatformContractError("INVALID_RUN_STATUS", "Interactive Run terminal status is not supported")
        pending = set(self.investigations) - set(self.decisions)
        if status == "completed" and pending:
            raise PlatformContractError("DECISION_MISSING", "Completed Run has an uncommitted investigation")
        self.failures.extend(failures)
        self.status = status
        self._emit("platform.run.terminal", "finish", status)
        ledger = self._save()
        metrics = {
            "discovered": len(self.work_items), "inspected": len(self.investigations),
            "decisionsCommitted": len(self.decisions), "operations": len(self.operations),
        }
        return PlatformRunResult(
            self.run.run_id, status, tuple(self.decisions.values()), tuple(self.failures),
            metrics, tuple(self.receipts.values()), ledger,
        )

    def _record_operation(
        self, kind: str, status: str, work_item_id: str | None = None,
        receipt_id: str | None = None,
    ) -> None:
        now = _now()
        operation_id = f"operation:{self.run.run_id}:interactive:{len(self.operations) + 1}"
        self.operations.append(Operation(
            operation_id, kind, work_item_id or "run", self.check.check_id,
            status, receipt_id=receipt_id, started_at=now, ended_at=now,
        ))
        self._emit(
            "operation.finished", "finish", status, operation_id=operation_id,
            work_item_id=work_item_id, details={"kind": kind},
        )

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
