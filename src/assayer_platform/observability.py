"""Human-readable, domain-neutral observability derived from the ledger."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .contract import PlatformLedger
from .evidence_graph import validate_candidate_evidence_graph_projection


def _category(name: str) -> str:
    return name.split(".", 1)[0] if "." in name else name


def _duration(events: tuple[Any, ...], prefixes: tuple[str, ...]) -> tuple[int, int]:
    values = [
        int(event.details.get("durationMs", 0) or 0)
        for event in events
        if event.name.startswith(prefixes)
        and isinstance(event.details, Mapping)
        and isinstance(event.details.get("durationMs"), (int, float))
    ]
    return sum(max(0, value) for value in values), len(values)


def build_platform_observability(ledger: PlatformLedger) -> dict[str, Any]:
    """Summarize ownership, failures, retries, and exposed timing signals.

    Missing external telemetry is represented explicitly; this function never
    turns an absent Agent/model signal into a zero-duration measurement.
    """
    events = tuple(ledger.events)
    outcomes = Counter(str(event.outcome) for event in events)
    categories = Counter(_category(str(event.name)) for event in events)
    rejected = [
        event for event in events
        if str(event.outcome) in {"rejected", "blocked"}
        or str(event.name).endswith((".rejected", ".blocked"))
    ]
    retries = [
        event for event in events
        if str(event.outcome) in {"retry", "split"}
        or "retry" in str(event.name)
        or str(event.name) == "inspection.batch.split"
    ]
    failure_records = [
        {
            "event": event.name,
            "outcome": event.outcome,
            "workItemId": event.work_item_id,
            "operationId": event.operation_id,
            "errorCode": event.details.get("errorCode") if isinstance(event.details, Mapping) else None,
        }
        for event in events
        if str(event.outcome) in {"failed", "uncertain", "rejected", "blocked"}
    ]
    host_ms, host_count = _duration(events, ("host.", "operation."))
    agent_ms, agent_count = _duration(events, ("agent.",))
    model_ms, model_count = _duration(events, ("model.",))
    transport_ms, transport_count = _duration(events, ("transport.",))
    workflow = ledger.workflow if isinstance(ledger.workflow, Mapping) else {}
    checkpointed = {
        item_id for checkpoint in ledger.review_checkpoints for item_id in checkpoint.item_ids
    }
    graph_items: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    total_candidates = 0
    covered_candidates = 0
    for packet in ledger.investigations:
        payload = packet.evidence[0].payload if packet.evidence else None
        graph = payload.get("candidateGraph") if isinstance(payload, Mapping) else None
        candidates = payload.get("candidateFindings", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(graph, Mapping) or not isinstance(candidates, (tuple, list)):
            continue
        validate_candidate_evidence_graph_projection(graph)
        ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, Mapping) and item.get("candidate_id")]
        pending = [] if graph.get("coverageComplete") is True else [item_id for item_id in ids if item_id not in checkpointed]
        total_candidates += len(ids)
        covered_candidates += len(ids) - len(pending)
        pending_ids.extend(pending)
        graph_items.append({
            "workItemId": packet.work_item.work_item_id,
            "candidateCount": len(ids),
            "coveredCandidateCount": len(ids) - len(pending),
            "pendingCandidateIds": pending,
            "coverageComplete": not pending,
        })
    return {
        "schemaVersion": "1.0.0",
        "runId": ledger.run.run_id,
        "status": ledger.status,
        "currentPosition": {
            "state": workflow.get("state", ledger.status),
            "phase": workflow.get("phase", "unknown"),
            "requiredNextStep": workflow.get("requiredNextStep"),
        },
        "events": {
            "total": len(events),
            "byCategory": dict(sorted(categories.items())),
            "byOutcome": dict(sorted(outcomes.items())),
        },
        "failures": {
            "count": len(failure_records) + len(ledger.failures),
            "recorded": failure_records,
            "ledgerFailures": [
                {"workItemId": item.work_item_id, "code": item.code}
                for item in ledger.failures
            ],
        },
        "rejections": {
            "count": len(rejected),
            "items": [
                {"event": event.name, "operationId": event.operation_id,
                 "workItemId": event.work_item_id,
                 "errorCode": event.details.get("errorCode") if isinstance(event.details, Mapping) else None}
                for event in rejected
            ],
        },
        "retries": {
            "count": len(retries),
            "events": [event.name for event in retries],
        },
        "evidenceGraph": {
            "candidateCount": total_candidates,
            "coveredCandidateCount": covered_candidates,
            "pendingCandidateIds": pending_ids,
            "coverageComplete": not pending_ids,
            "workItems": graph_items,
        },
        "timing": {
            "host": {"status": "captured", "durationMs": host_ms, "eventCount": host_count},
            "agent": ({"status": "captured", "durationMs": agent_ms, "eventCount": agent_count}
                      if agent_count else {"status": "not_exposed", "reason": "Agent turn timing was not recorded."}),
            "model": ({"status": "captured", "durationMs": model_ms, "eventCount": model_count}
                      if model_count else {"status": "not_exposed", "reason": "Model timing was not recorded."}),
            "transport": ({"status": "captured", "durationMs": transport_ms, "eventCount": transport_count}
                          if transport_count else {"status": "not_exposed", "reason": "Transport timing was not recorded."}),
        },
    }


def render_platform_observability(ledger: PlatformLedger) -> tuple[bytes, bytes, dict[str, Any]]:
    value = build_platform_observability(ledger)
    timing = value["timing"]
    lines = [
        "# Assayer Platform Observability",
        "",
        f"Run: `{value['runId']}`  ",
        f"Status: **{value['status']}**  ",
        f"Position: **{value['currentPosition']['state']} / {value['currentPosition']['phase']}**  ",
        f"Next step: **{value['currentPosition']['requiredNextStep'] or 'none'}**",
        "",
        "## What happened",
        "",
        f"- Events: {value['events']['total']}",
        f"- Failures: {value['failures']['count']}",
        f"- Host rejections: {value['rejections']['count']}",
        f"- Retries or safe splits: {value['retries']['count']}",
        "",
        "## Evidence coverage",
        "",
        f"- Candidates: {value['evidenceGraph']['coveredCandidateCount']} / {value['evidenceGraph']['candidateCount']} covered",
        f"- Pending candidates: {len(value['evidenceGraph']['pendingCandidateIds'])}",
        f"- Coverage complete: {'yes' if value['evidenceGraph']['coverageComplete'] else 'no'}",
        *(["- Pending IDs: " + ", ".join(value["evidenceGraph"]["pendingCandidateIds"][:20])] if value["evidenceGraph"]["pendingCandidateIds"] else []),
        "",
        "## Timing",
        "",
        f"- Host: {timing['host']['durationMs']} ms across {timing['host']['eventCount']} event(s)",
        f"- Agent: {timing['agent'].get('durationMs', 'not exposed')}",
        f"- Model: {timing['model'].get('durationMs', 'not exposed')}",
        f"- Transport: {timing['transport'].get('durationMs', 'not exposed')}",
        "",
        "## Next action",
        "",
        f"The platform reports: **{value['currentPosition']['requiredNextStep'] or 'no further action'}**.",
        "",
    ]
    return (
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        "\n".join(lines).encode("utf-8"),
        value,
    )


__all__ = ["build_platform_observability", "render_platform_observability"]
