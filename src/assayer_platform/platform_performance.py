"""Domain-neutral performance accounting derived from a platform ledger."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .contract import PlatformLedger
from .registry import _schema_root


_NOT_EXPOSED = {
    "agentWait": "The Agent client did not expose between-turn waiting telemetry.",
    "transport": "The calling transport did not expose request timing telemetry.",
    "model": "The Agent client did not expose model timing telemetry.",
}


def _captured(duration_ms: int, aggregation: str) -> dict[str, Any]:
    return {
        "status": "captured",
        "durationMs": max(0, int(duration_ms)),
        "aggregation": aggregation,
    }


def _not_exposed(reason: str) -> dict[str, str]:
    return {"status": "not_exposed", "reason": reason}


def _parallel_plan(ledger: PlatformLedger) -> dict[str, Any]:
    event = next((
        item for item in reversed(ledger.events)
        if item.name == "inspection.parallel.planned"
    ), None)
    if event is None:
        return {
            "mode": "unavailable",
            "reason": "planning_not_reached",
            "taskCount": 0,
            "workerCount": 0,
        }
    return {
        "mode": event.outcome,
        "reason": str(event.details.get("reason", "unknown")),
        "taskCount": int(event.details.get("taskCount", 0) or 0),
        "workerCount": int(event.details.get("workerCount", 1) or 1),
    }


@lru_cache(maxsize=1)
def _bill_validator() -> Draft202012Validator:
    root = _schema_root()
    schema = json.loads((root / "platform-performance-bill.schema.json").read_text(encoding="utf-8"))
    common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema,
        resolver=RefResolver(
            schema["$id"], schema,
            store={common["$id"]: common, "common.schema.json": common},
        ),
    )


def build_platform_performance_bill(ledger: PlatformLedger) -> dict[str, Any]:
    """Build diagnostic timing facts without changing Run conclusions."""
    metrics = ledger.metrics if isinstance(ledger.metrics, Mapping) else {}
    plan = _parallel_plan(ledger)
    wall_clock = (
        _captured(int(metrics["wallClockMs"]), "elapsed")
        if "wallClockMs" in metrics
        else _not_exposed("The Run predates platform wall-clock measurement.")
    )
    host_operations = _captured(
        sum(max(0, int(item.duration_ms)) for item in ledger.operations),
        "summed_work",
    )

    provider_bound = any(
        evidence.provider_bound
        for packet in ledger.investigations
        for evidence in packet.evidence
    )
    if int(metrics.get("providerTimingAvailable", 0) or 0) == 1:
        provider: dict[str, Any] = {
            **_captured(int(metrics.get("providerRequestDurationMs", 0) or 0), "summed_work"),
            "requestCount": int(metrics.get("providerRequestCount", 0) or 0),
        }
    elif provider_bound:
        provider = {
            "status": "partial",
            "reason": (
                "Provider-bound Evidence exists, but the provider did not expose isolated request timing."
            ),
        }
    else:
        provider = _not_exposed(
            "No isolated provider timing source was attached to this Run."
        )

    if plan["mode"] == "parallel":
        inspection_window = _captured(
            int(metrics.get("parallelWallMs", 0) or 0), "elapsed",
        )
        summed_task_work = _captured(
            int(metrics.get("parallelTaskDurationMs", 0) or 0), "summed_work",
        )
        estimated_reduction: dict[str, Any] = {
            "status": "estimated",
            "durationMs": int(metrics.get("parallelEstimatedWaitSavedMs", 0) or 0),
            "basis": "summed_task_work_minus_parallel_elapsed_window",
        }
    else:
        reason = (
            "Inspection did not run in a parallel execution window."
            if plan["mode"] == "serial"
            else "Parallel planning was not reached in this Run."
        )
        inspection_window = _not_exposed(reason)
        summed_task_work = _not_exposed(reason)
        estimated_reduction = {"status": "not_applicable", "reason": reason}

    limitations = [
        *_NOT_EXPOSED.values(),
        (
            "Run wall clock covers platform execution through the recorded boundary; "
            "later report publication and unavailable client work are outside it."
        ),
    ]
    if provider["status"] != "captured":
        limitations.append(str(provider["reason"]))
    if plan["mode"] == "parallel":
        limitations.append(
            "Estimated wait reduction is a scheduler diagnostic, not measured end-to-end user time saved."
        )
        limitations.append(
            "Summed Host and task work may exceed wall clock because concurrent work overlaps."
        )

    bill = {
        "schemaVersion": "1.0.0",
        "runId": ledger.run.run_id,
        "status": ledger.status,
        "source": {
            "ledgerKind": "platform-ledger",
            "eventCount": len(ledger.events),
            "operationCount": len(ledger.operations),
        },
        "measurement": {
            "wallClock": wall_clock,
            "hostOperations": host_operations,
            "provider": provider,
            "agentWait": _not_exposed(_NOT_EXPOSED["agentWait"]),
            "transport": _not_exposed(_NOT_EXPOSED["transport"]),
            "model": _not_exposed(_NOT_EXPOSED["model"]),
        },
        "parallelism": {
            **plan,
            "inspectionWindow": inspection_window,
            "summedTaskWork": summed_task_work,
            "estimatedWaitReduction": estimated_reduction,
        },
        "limitations": limitations,
    }
    _bill_validator().validate(bill)
    return bill


def _timing_text(value: Mapping[str, Any]) -> str:
    status = value["status"]
    if status == "captured":
        suffix = " summed work" if value.get("aggregation") == "summed_work" else " elapsed"
        return f"{value['durationMs']} ms ({suffix.strip()})"
    if status == "partial":
        return f"partial — {value['reason']}"
    return f"not exposed — {value['reason']}"


def render_platform_performance_bill(
    ledger: PlatformLedger,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Render the machine bill and a summary-first human reading view."""
    bill = build_platform_performance_bill(ledger)
    parallel = bill["parallelism"]
    estimate = parallel["estimatedWaitReduction"]
    if estimate["status"] == "estimated":
        estimate_text = (
            f"{estimate['durationMs']} ms (estimate only; not end-to-end speedup)"
        )
    else:
        estimate_text = f"not applicable — {estimate['reason']}"
    lines = [
        "# Assayer Platform Performance Bill",
        "",
        f"Run: `{bill['runId']}`  ",
        f"Status: **{bill['status']}**",
        "",
        "## Summary",
        "",
        f"- Execution mode: **{parallel['mode']}** ({parallel['reason'].replace('_', ' ')})",
        f"- Tasks and workers: {parallel['taskCount']} task(s), {parallel['workerCount']} worker(s)",
        f"- Run wall clock: {_timing_text(bill['measurement']['wallClock'])}",
        f"- Host operation work: {_timing_text(bill['measurement']['hostOperations'])}",
        f"- Provider work: {_timing_text(bill['measurement']['provider'])}",
        f"- Parallel inspection window: {_timing_text(parallel['inspectionWindow'])}",
        f"- Parallel task work: {_timing_text(parallel['summedTaskWork'])}",
        f"- Estimated wait reduction: {estimate_text}",
        "",
        "## External Telemetry",
        "",
        f"- Agent wait: {_timing_text(bill['measurement']['agentWait'])}",
        f"- Transport: {_timing_text(bill['measurement']['transport'])}",
        f"- Model: {_timing_text(bill['measurement']['model'])}",
        "",
        "## Limitations",
        "",
        *(f"- {item}" for item in bill["limitations"]),
        "",
    ]
    json_bytes = (
        json.dumps(bill, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    return json_bytes, "\n".join(lines).encode("utf-8"), bill


__all__ = ["build_platform_performance_bill", "render_platform_performance_bill"]
