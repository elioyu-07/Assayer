"""Durable JSON storage for the generic platform ledger."""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .contract import PlatformContractError, PlatformLedger
from .canonical_result import canonical_ledger_bytes, render_canonical_result
from .platform_performance import render_platform_performance_bill


class PlatformLedgerStore(Protocol):
    def save(self, ledger: PlatformLedger) -> Path | None: ...

    def load(self, run_id: str) -> dict[str, Any] | None: ...


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def _diary_text(value: Any, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"(?i)\b(authorization|cookie|password|token|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:limit] or "unknown"


def _state_label(state: str) -> str:
    return {
        "running": "Running deterministic platform work",
        "awaiting_agent_decision": "Waiting for Agent decision",
        "ready_to_finish": "Ready to finalize",
        "blocked": "Blocked pending recovery",
        "completed": "Completed",
        "partial": "Completed with partial coverage",
        "failed": "Failed",
    }.get(state, state.replace("_", " ").title())


def _next_action(value: Any) -> str:
    return {
        "discover_work_items": "Discover the next bounded WorkItem batch.",
        "inspect_work_items": "Inspect the next bounded WorkItem batch.",
        "checkpoint_review": "Review the next evidence batch and save a durable checkpoint.",
        "submit_decisions": "Submit the evidence-backed decision for the current WorkItem.",
        "advance_plugin_run": "Continue the Run with the requested Agent review or decision.",
        "advance_plugin_run_or_recover_work_item": (
            "Continue the Run after resolving the reported WorkItem failure."
        ),
        "recover_work_item": "Recover or classify the failed WorkItem before continuing.",
        "finish_plugin_run": "Validate coverage and publish the terminal result.",
    }.get(value, "No further action is required." if value is None else _diary_text(value))


def workflow_progress(
    workflow: Mapping[str, Any], *, discovered: int, inspected: int,
    decisions: int, checkpoints: int, entered_at: str | None = None,
) -> dict[str, Any]:
    """Build one compact, domain-neutral user and Agent progress block."""
    state = str(workflow.get("state", "running"))
    phase = str(workflow.get("phase", "discovery"))
    remaining = workflow.get("remaining") if isinstance(workflow.get("remaining"), Mapping) else {}
    messages = {
        "running": f"Assayer is performing bounded {phase.replace('_', ' ')} work.",
        "awaiting_agent_decision": "Durable evidence is ready; semantic Agent judgment is required for this boundary.",
        "ready_to_finish": "All required WorkItems are decided and coverage is ready for final validation.",
        "blocked": "The Run cannot advance until the reported recovery work is resolved.",
        "completed": "The declared scope completed and the formal result is available.",
        "partial": "The Run closed with valid partial results and disclosed unfinished scope.",
        "failed": "The Run failed and cannot support a complete formal conclusion.",
    }
    waiting_on = {
        "awaiting_agent_decision": "agent",
        "blocked": "recovery",
        "running": "platform",
    }.get(state, "none")
    return {
        "phase": phase,
        "state": state,
        "message": messages.get(state, f"Assayer workflow state is {_state_label(state)}."),
        "waitingOn": waiting_on,
        "enteredAt": entered_at,
        "completed": {
            "workItemsDiscovered": discovered,
            "workItemsInspected": inspected,
            "decisionsCommitted": decisions,
            "reviewCheckpoints": checkpoints,
        },
        "remaining": {
            "workItemsToInspect": int(remaining.get("workItemsToInspect", 0) or 0),
            "workItemsToDecide": int(remaining.get("workItemsToDecide", 0) or 0),
            "reviewItems": int(remaining.get("reviewItems", 0) or 0),
            "failures": int(remaining.get("failures", 0) or 0),
        },
        "requiredNextStep": workflow.get("requiredNextStep"),
        "nextAction": _next_action(workflow.get("requiredNextStep")),
        "terminal": state in {"completed", "partial", "failed"},
    }


def _operation_message(operation: Any, phase: str) -> str:
    action = {
        "discover": "WorkItem discovery",
        "inspect": "WorkItem inspection",
        "inspect_batch": "inspection batch split",
        "review_checkpoint": "semantic-review checkpoint",
        "commit": "evidence-backed decision commit",
        "recover": "WorkItem recovery",
        "publish": "artifact publication",
    }.get(operation.kind, operation.kind.replace("_", " "))
    if phase == "start":
        return f"Started {action}."
    if operation.status in {"failed", "uncertain"}:
        reason = f" Error: {operation.error_code}." if operation.error_code else ""
        return f"{action.capitalize()} did not complete.{reason}"
    return f"Completed {action}."


def _event_message(event: Any, operations: Mapping[str, Any]) -> tuple[str, str]:
    operation = operations.get(event.operation_id)
    if event.name == "platform.run.started":
        return "STARTED", "Run started and the plugin, Check, and business scope were frozen."
    if event.name == "platform.run.terminal":
        return event.outcome.upper(), f"Run reached terminal state: {_state_label(event.outcome)}."
    if event.name in {"operation.started", "operation.finished"} and operation is not None:
        label = "STARTED" if event.name.endswith("started") else operation.status.upper()
        return label, _operation_message(operation, event.phase)
    if event.name == "platform.workflow.transition":
        next_step = event.details.get("requiredNextStep")
        return "PHASE", f"Workflow changed to {_state_label(event.outcome)}. Next: {_next_action(next_step)}"
    if event.name == "review.checkpoint.saved":
        count = int(event.details.get("itemCount", 0) or 0)
        return "SAVED", f"Saved a durable semantic-review checkpoint for {count} evidence item(s)."
    if event.name == "inspection.batch.split":
        before = int(event.details.get("failedBatchSize", 0) or 0)
        after = int(event.details.get("nextBatchSize", 0) or 0)
        return "RETRY", f"Inspection batch of {before} failed safely; the next batch size is {after}."
    if event.name == "inspection.batch.summary":
        attempts = int(event.details.get("attempts", 0) or 0)
        failures = int(event.details.get("failures", 0) or 0)
        return "BATCH", f"Inspection batching finished with {attempts} attempt(s) and {failures} failure(s)."
    if event.name == "inspection.parallel.planned":
        workers = int(event.details.get("workerCount", 1) or 1)
        tasks = int(event.details.get("taskCount", 0) or 0)
        reason = _diary_text(event.details.get("reason", "unknown"))
        if event.outcome == "parallel":
            return "PARALLEL", f"Planned {tasks} independent inspection task(s) across {workers} worker(s)."
        return "SERIAL", f"Kept {tasks} inspection task(s) serial because {reason.replace('_', ' ')}."
    if event.name == "inspection.parallel.measured":
        elapsed = int(event.details.get("parallelWallMs", 0) or 0)
        estimate = int(event.details.get("estimatedWaitReductionMs", 0) or 0)
        return (
            "MEASURED",
            f"Parallel inspection elapsed {elapsed} ms; scheduler wait reduction is estimated at {estimate} ms.",
        )
    if event.name == "inspection.item.failed":
        code = _diary_text(event.details.get("errorCode", "INSPECTION_FAILED"))
        return "FAILED", f"A WorkItem inspection failed with {code}."
    if event.name == "recovery.finished":
        return event.outcome.upper(), f"WorkItem recovery finished as {event.outcome}."
    if event.name == "artifact.published":
        return "PUBLISHED", "Published a receipt-bound result artifact."
    return event.outcome.upper(), event.name.replace(".", " ").capitalize() + "."


def _render_platform_diary(ledger: PlatformLedger) -> str:
    workflow = ledger.workflow if isinstance(ledger.workflow, Mapping) else {}
    state = str(workflow.get("state") or ledger.status)
    phase = str(workflow.get("phase") or ("finished" if ledger.status != "running" else "running"))
    remaining = workflow.get("remaining") if isinstance(workflow.get("remaining"), Mapping) else {}
    pending_decisions = max(0, len(ledger.investigations) - len(ledger.decisions))
    operations = {item.operation_id: item for item in ledger.operations}
    boundary_event = next((
        event for event in reversed(ledger.events)
        if event.name in {"platform.workflow.transition", "platform.run.terminal"}
    ), None)
    progress = workflow_progress(
        workflow,
        discovered=len(ledger.work_items),
        inspected=len(ledger.investigations),
        decisions=len(ledger.decisions),
        checkpoints=len(ledger.review_checkpoints),
        entered_at=boundary_event.occurred_at if boundary_event else ledger.run.started_at,
    )
    lines = [
        "Assayer Platform Run Diary",
        "==========================",
        f"Run: {_diary_text(ledger.run.run_id)}",
        f"Plugin: {_diary_text(ledger.run.plugin_id)}@{_diary_text(ledger.run.plugin_version)}",
        f"Check: {_diary_text(ledger.run.check_id)}@{_diary_text(ledger.run.check_version)}",
        f"Status: {_state_label(state)}",
        f"Phase: {phase.replace('_', ' ')}",
        f"Started: {_diary_text(ledger.run.started_at)}",
        "",
        "Progress",
        "--------",
        f"WorkItems: {len(ledger.work_items)} discovered, {len(ledger.investigations)} inspected, "
        f"{len(ledger.decisions)} decided, {pending_decisions} awaiting decision",
        f"Review: {len(ledger.review_checkpoints)} checkpoint record(s)",
        f"Failures: {len(ledger.failures)}",
        "",
        "Timeline",
        "--------",
    ]
    for event in sorted(ledger.events, key=lambda item: item.sequence):
        label, message = _event_message(event, operations)
        subject = f" WorkItem {_diary_text(event.work_item_id)}." if event.work_item_id else ""
        lines.append(
            f"{event.sequence:04d} {_diary_text(event.occurred_at)} | {label:<9} | "
            f"{message}{subject} [{event.name}]"
        )

    if ledger.decisions:
        lines.extend(["", "Decisions", "---------"])
        for decision in ledger.decisions:
            lines.append(
                f"- {_diary_text(decision.work_item_id)}: {decision.result} — "
                f"{_diary_text(decision.reason)}"
            )
    if ledger.failures:
        lines.extend(["", "Failures", "--------"])
        for failure in ledger.failures:
            lines.append(
                f"- {_diary_text(failure.work_item_id)}: {_diary_text(failure.code)} — "
                f"{_diary_text(failure.message)}"
            )

    lines.extend([
        "",
        "Current Position",
        "----------------",
        f"State: {_state_label(state)}",
        f"Since: {_diary_text(progress['enteredAt'])}",
        f"Remaining: {int(remaining.get('workItemsToInspect', 0) or 0)} to inspect, "
        f"{int(remaining.get('workItemsToDecide', pending_decisions) or 0)} to decide, "
        f"{int(remaining.get('reviewItems', 0) or 0)} review item(s), "
        f"{int(remaining.get('failures', len(ledger.failures)) or 0)} failure(s)",
        f"Next action: {progress['nextAction']}",
    ])
    if state == "awaiting_agent_decision":
        lines.append(
            "Waiting is expected here: durable platform state is saved, and the next step requires semantic Agent judgment."
        )
    lines.append("")
    return "\n".join(lines)


def render_platform_artifacts(ledger: PlatformLedger) -> dict[str, bytes]:
    """Render the canonical platform ledger and readable event timelines."""
    performance_json, performance_markdown, _bill = render_platform_performance_bill(ledger)
    ledger_json = canonical_ledger_bytes(ledger)
    canonical_json, _canonical = render_canonical_result(
        ledger, ledger_bytes=ledger_json,
    ) if ledger.status in {"completed", "partial", "failed"} else (None, None)
    return {
        "platform-ledger.json": ledger_json,
        "platform-events.jsonl": "".join(
            json.dumps(_plain(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in ledger.events
        ).encode("utf-8"),
        "platform-run.log": _render_platform_diary(ledger).encode("utf-8"),
        "platform-performance-bill.json": performance_json,
        "platform-performance-bill.md": performance_markdown,
        **({"canonical-result.json": canonical_json} if canonical_json is not None else {}),
    }


def write_platform_artifacts(
    ledger: PlatformLedger, root: str | Path, *, include_canonical_result: bool = True,
) -> tuple[Path, ...]:
    """Atomically write platform trace files into a scan output directory."""
    destination_root = Path(root).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    written = []
    rendered = render_platform_artifacts(ledger)
    if not include_canonical_result:
        rendered.pop("canonical-result.json", None)
    for name, content in rendered.items():
        destination = destination_root / name
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        written.append(destination)
    return tuple(written)


class JsonPlatformLedgerStore:
    """Persist the canonical ledger plus machine and human event timelines."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{2,127}", run_id):
            raise PlatformContractError("INVALID_RUN_ID", "Run ID is not safe for ledger persistence")
        return self.root / f"{run_id}.platform-ledger.json"

    def save(self, ledger: PlatformLedger) -> Path:
        destination = self._path(ledger.run.run_id)
        rendered = render_platform_artifacts(ledger)
        self._atomic_write(destination, rendered["platform-ledger.json"].decode("utf-8"))
        events_path = self.root / f"{ledger.run.run_id}.platform-events.jsonl"
        self._atomic_write(events_path, rendered["platform-events.jsonl"].decode("utf-8"))
        journal_path = self.root / f"{ledger.run.run_id}.platform-run.log"
        self._atomic_write(journal_path, rendered["platform-run.log"].decode("utf-8"))
        performance_path = self.root / f"{ledger.run.run_id}.platform-performance-bill.json"
        self._atomic_write(
            performance_path,
            rendered["platform-performance-bill.json"].decode("utf-8"),
        )
        performance_markdown_path = self.root / f"{ledger.run.run_id}.platform-performance-bill.md"
        self._atomic_write(
            performance_markdown_path,
            rendered["platform-performance-bill.md"].decode("utf-8"),
        )
        canonical = rendered.get("canonical-result.json")
        if canonical is not None:
            canonical_path = self.root / f"{ledger.run.run_id}.canonical-result.json"
            self._atomic_write(canonical_path, canonical.decode("utf-8"))
        return destination

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(destination: Path, text: str) -> None:
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(destination)
