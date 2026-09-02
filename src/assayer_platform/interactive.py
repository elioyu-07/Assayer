"""Domain-neutral interactive plugin protocol.

This module is the transport-independent controller for Agent-driven plugin
runs.  It deliberately knows only about WorkItems, InvestigationPackets and
DecisionProposals; browser concepts remain in the frontend compatibility
adapter.  MCP/CLI transports can wrap this controller without duplicating
lifecycle or validation rules.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .contract import (
    CommitReceipt,
    DecisionProposal,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PlatformRunResult,
    WorkFailure,
    WorkItem,
)
from .plugin_registry import PluginRegistry, PluginRegistration
from .session import InteractivePlatformRun, InteractivePlatformSession
from .ledger import JsonPlatformLedgerStore


INTERACTIVE_PROTOCOL_VERSION = "1.0"
INTERACTIVE_OPERATIONS = (
    "start",
    "discover",
    "inspect",
    "submit_decisions",
    "recover",
    "progress",
    "finish",
)


def _finding(value: Mapping[str, Any]) -> Finding:
    return Finding(
        value.get("dimension", ""),
        value.get("status", ""),
        value.get("reason") or value.get("reasonText") or "",
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def _proposal(value: Mapping[str, Any], check_id: str, check_version: str) -> DecisionProposal:
    return DecisionProposal(
        value.get("workItemId") or value.get("work_item_id") or "",
        value.get("checkId", check_id),
        value.get("checkVersion", check_version),
        value.get("result", ""),
        tuple(_finding(item) for item in value.get("findings", ())),
        value.get("reason") or value.get("decisionReason") or "",
        value.get("details", {}),
    )


def _work_item(item: WorkItem) -> dict[str, Any]:
    return {
        "workItemId": item.work_item_id,
        "kind": item.kind,
        "identity": item.identity,
        "stateDigest": item.state_digest,
        "metadata": _plain(item.metadata),
    }


def _packet(packet: InvestigationPacket) -> dict[str, Any]:
    return {
        "workItem": _work_item(packet.work_item),
        "checkId": packet.check_id,
        "checkVersion": packet.check_version,
        "dimensions": [{
            "name": item.name,
            "observations": list(item.observations),
            "evidenceRefs": list(item.evidence_refs),
            "candidateStatus": item.candidate_status,
        } for item in packet.dimensions],
        "evidence": [{
            "evidenceId": item.evidence_id,
            "workItemId": item.work_item_id,
            "checkId": item.check_id,
            "checkVersion": item.check_version,
            "kind": item.kind,
            "sourceIdentity": item.source_identity,
            "payload": _plain(item.payload),
        } for item in packet.evidence],
        "recoveryStatus": packet.recovery_status,
        "caseRef": packet.case_ref,
        "metadata": _plain(packet.metadata),
    }


class InteractivePluginController:
    """Run the generic interactive lifecycle for one registered plugin.

    The controller is intentionally small: it owns selection, scope
    validation, lifecycle routing and response shaping, while
    :class:`InteractivePlatformRun` remains the source of truth for gates and
    durable checkpoints.  A runtime resolver is injected by a product adapter
    (for example, a browser or API runtime); no runtime is assumed here.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        output_root: str | Path = "./assayer-output",
        *,
        runtime_resolver: Callable[[PluginRegistration, Any], Any] | None = None,
        capabilities_resolver: Callable[[PluginRegistration, Any], Sequence[str]] | None = None,
    ) -> None:
        self.registry = registry
        self.output_root = Path(output_root).expanduser().resolve()
        self.runtime_resolver = runtime_resolver
        self.capabilities_resolver = capabilities_resolver
        self._runs: dict[str, dict[str, Any]] = {}

    @property
    def operations(self) -> tuple[str, ...]:
        return INTERACTIVE_OPERATIONS

    def start(
        self,
        *,
        plugin_id: str,
        check_id: str,
        scope: Any,
        check_version: str | None = None,
        run_id: str | None = None,
        runtime: Any = None,
        capabilities: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        registration = self.registry.select(
            plugin_id=plugin_id, check_id=check_id,
            check_ref=(check_id, check_version) if check_version else None,
        )
        if "interactive" not in registration.execution_modes:
            raise PlatformContractError(
                "PLUGIN_EXECUTION_MODE_UNSUPPORTED",
                "The selected plugin does not declare interactive execution",
            )
        scope_error = next(Draft202012Validator(registration.scope_schema).iter_errors(scope), None)
        if scope_error is not None:
            raise PlatformContractError(
                "INVALID_SCOPE", "Plugin business scope does not satisfy its registered schema",
            )
        matches = tuple(item for item in registration.manifest.checks if item.check_id == check_id)
        if check_version is None:
            if len(matches) != 1:
                raise PlatformContractError("AMBIGUOUS_CHECK", f"Check version is required for: {check_id}")
            check = matches[0]
        else:
            check = self.registry.check(registration, (check_id, check_version))
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        if run_id in self._runs:
            raise PlatformContractError("RUN_CONFLICT", "An interactive Run with this ID already exists")
        resolved_runtime = runtime
        if self.runtime_resolver is not None:
            resolved_runtime = self.runtime_resolver(registration, scope)
        caps = tuple(capabilities) if capabilities is not None else tuple(
            self.capabilities_resolver(registration, scope)
            if self.capabilities_resolver is not None else registration.capabilities
        )
        context = PlatformContext(run_id, frozenset(caps))
        try:
            plugin = registration.create_plugin(resolved_runtime)
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError("PLUGIN_INITIALIZATION_FAILED", "Interactive plugin could not be initialized") from error
        if getattr(plugin, "manifest", None) != registration.manifest:
            raise PlatformContractError("PLUGIN_IDENTITY_MISMATCH", "Plugin factory returned different registered metadata")
        store = JsonPlatformLedgerStore(self.output_root / run_id)
        run = InteractivePlatformSession(registration.manifest).begin(
            context, scope, check.check_id, check.version, store,
        )
        self._runs[run_id] = {
            "registration": registration,
            "scope": scope,
            "context": context,
            "plugin": plugin,
            "run": run,
            "committer": registration.create_committer(resolved_runtime),
        }
        return self._response(run_id, "started", {"check": {"checkId": check.check_id, "version": check.version}})

    def discover(self, run_id: str) -> dict[str, Any]:
        state = self._state(run_id)
        try:
            items = tuple(state["plugin"].discover(state["scope"], state["context"]))
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError("DISCOVERY_FAILED", "Interactive plugin discovery failed") from error
        state["run"].record_discovery(items)
        return self._response(run_id, "discovered", {"workItems": [_work_item(item) for item in items]})

    def inspect(self, run_id: str, work_item_ids: Sequence[str] | None = None) -> dict[str, Any]:
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        selected_ids = tuple(work_item_ids or tuple(run.work_items))
        missing = [item_id for item_id in selected_ids if item_id not in run.work_items]
        if missing:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Inspection references an undiscovered WorkItem")
        selected = tuple(run.work_items[item_id] for item_id in selected_ids)
        try:
            packets = tuple(state["plugin"].inspect(selected, run.check, state["context"]))
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError("INSPECTION_FAILED", "Interactive plugin inspection failed") from error
        for packet in packets:
            run.record_investigation(packet)
        return self._response(run_id, "inspected", {"investigations": [_packet(packet) for packet in packets]})

    def submit_decisions(self, run_id: str, decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        proposals = tuple(_proposal(item, run.check.check_id, run.check.version) for item in decisions)
        receipts: list[CommitReceipt] = []
        for proposal in proposals:
            packet = run.investigations.get(proposal.work_item_id)
            if packet is None:
                raise PlatformContractError("UNKNOWN_INVESTIGATION", "Decision references an uninvestigated WorkItem")
            committer = state["committer"]
            if committer is None:
                receipt = CommitReceipt(
                    f"commit:{run_id}:{proposal.work_item_id}", proposal.work_item_id,
                    proposal.check_id, proposal.check_version, proposal.result, "memory",
                )
            else:
                try:
                    receipt = committer.commit(proposal, packet, run.check, state["context"])
                except PlatformContractError:
                    raise
                except Exception as error:
                    raise PlatformContractError("COMMIT_FAILED", "Interactive plugin commit failed") from error
            run.record_commit(proposal, receipt)
            receipts.append(receipt)
        return self._response(run_id, "decisions_committed", {
            "decisions": [{"workItemId": item.work_item_id, "result": item.result} for item in proposals],
            "receipts": [item.commit_id for item in receipts],
        })

    def recover(self, run_id: str, work_item_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self._state(run_id)
        restore = getattr(state["plugin"], "restore", None)
        if restore is None:
            status = "not_required"
            details = {"message": "The selected plugin does not require an explicit recovery operation."}
        else:
            try:
                result = restore(work_item_id, payload or {}, state["context"])
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError("RECOVERY_FAILED", "Interactive plugin recovery failed") from error
            if isinstance(result, Mapping):
                status = result.get("status", "uncertain")
                details = dict(result)
            else:
                status = str(result)
                details = {}
        if status not in {"restored", "not_required", "uncertain", "failed"}:
            raise PlatformContractError("INVALID_RECOVERY", "Plugin recovery returned an unsupported status")
        state["run"].record_recovery(work_item_id, status, details)
        return self._response(run_id, "recovered", {"workItemId": work_item_id, "status": status, **details})

    def progress(self, run_id: str) -> dict[str, Any]:
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        return self._response(run_id, "running", {
            "discovered": len(run.work_items),
            "inspected": len(run.investigations),
            "decisionsCommitted": len(run.decisions),
            "operations": len(run.operations),
        })

    def finish(self, run_id: str, status: str, failures: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        state = self._state(run_id)
        published_artifacts: list[str] = []
        finalize = getattr(state["plugin"], "finalize", None)
        if callable(finalize):
            try:
                paths = finalize(
                    tuple(state["run"].work_items.values()),
                    tuple(state["run"].investigations.values()),
                    tuple(state["run"].decisions.values()),
                    state["run"].store.root,
                    status,
                )
                published_artifacts = [str(path) for path in (paths or ())]
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PLUGIN_REPORT_FAILED", "Plugin report generation failed",
                ) from error
        summary: Mapping[str, Any] = {}
        summarize = getattr(state["plugin"], "summarize", None)
        if callable(summarize):
            try:
                value = summarize(
                    tuple(state["run"].work_items.values()),
                    tuple(state["run"].investigations.values()),
                    tuple(state["run"].decisions.values()),
                    status,
                )
                if value is not None:
                    if not isinstance(value, Mapping):
                        raise TypeError("plugin summary must be an object")
                    summary = dict(value)
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PLUGIN_SUMMARY_FAILED", "Plugin result summary generation failed",
                ) from error
        typed_failures = tuple(WorkFailure(
            item.get("workItemId", "run"), item.get("checkId", state["run"].check.check_id),
            item.get("code", "PLUGIN_FAILURE"), item.get("message", "Plugin failure"),
        ) for item in failures)
        result: PlatformRunResult = state["run"].finish(status, typed_failures)
        result_payload = {
            "decisions": [{"workItemId": item.work_item_id, "result": item.result} for item in result.decisions],
            "failures": [{"workItemId": item.work_item_id, "code": item.code, "message": item.message} for item in result.failures],
            "metrics": dict(result.metrics),
            "artifacts": published_artifacts,
        }
        if summary:
            result_payload["summary"] = summary
        return self._response(run_id, status, result_payload)

    def _state(self, run_id: str) -> dict[str, Any]:
        state = self._runs.get(run_id)
        if state is None:
            raise PlatformContractError("UNKNOWN_RUN", "Interactive platform Run does not exist")
        return state

    def _response(self, run_id: str, status: str, result: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": INTERACTIVE_PROTOCOL_VERSION,
            "runId": run_id,
            "status": status,
            "result": dict(result),
        }


__all__ = ["INTERACTIVE_OPERATIONS", "INTERACTIVE_PROTOCOL_VERSION", "InteractivePluginController"]
