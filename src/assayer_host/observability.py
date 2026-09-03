from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def render_observability(scan: dict, events: list[dict], operations: list[dict], assessments: list[dict]) -> tuple[bytes, bytes, dict]:
    """Render the immutable event stream and its honest completeness manifest."""
    event_bytes = b"".join(
        (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for event in events
    )
    sequences = [event["sequence"] for event in events]
    operation_ids = {row["operation_id"] for row in operations}
    starts = {
        event.get("correlation", {}).get("operationId")
        for event in events if event["name"] == "operation.started"
    }
    finishes = {
        event.get("correlation", {}).get("operationId")
        for event in events if event["name"] == "operation.finished"
    }
    sequence_ok = sequences == list(range(1, len(events) + 1))
    timing_ok = all(
        row.get("accepted_at") and row.get("ended_at") and row.get("duration_ms") is not None
        for row in operations
    ) and starts == operation_ids and finishes == operation_ids
    correlation_ok = all(
        event.get("correlation", {}).get("requestId") and event.get("correlation", {}).get("operationId")
        for event in events if event["category"] == "tool"
    )
    terminal_ok = any(event["name"] == "scan.terminal" for event in events)
    decision_events = [event for event in events if event["name"] == "agent.decision.recorded"]
    decision_operation_ids = {event.get("correlation", {}).get("operationId") for event in decision_events}
    public_operation_ids = {row["operation_id"] for row in operations if row.get("tool") != "agent_supervision"}
    decision_status = "not_applicable" if not decision_events else ("passed" if decision_operation_ids == public_operation_ids else "failed")
    assessment_events = [event for event in events if event["name"] == "assessment.committed"]
    assessment_by_id = {item["assessmentId"]: item for item in assessments}
    assessment_ids = {event.get("correlation", {}).get("assessmentRef") for event in assessment_events}
    timeline_ok = bool(assessments) and assessment_ids == set(assessment_by_id) and all(
        event.get("correlation", {}).get("operationId") == assessment_by_id[event["correlation"]["assessmentRef"]]["commitOperationRef"]
        and set(event.get("correlation", {}).get("evidenceRefs", [])) == set(assessment_by_id[event["correlation"]["assessmentRef"]]["evidenceRefs"])
        for event in assessment_events if event.get("correlation", {}).get("assessmentRef") in assessment_by_id
    )
    assessment_status = "not_applicable" if not assessments else ("passed" if timeline_ok else "failed")
    lease_events = [event for event in events if event["category"] == "lease"]
    privacy_ok, redacted_count = _privacy_check(events)
    checks = [
        _check("sequence_contiguous", sequence_ok, "Event sequence must increment continuously from 1"),
        _check("operation_timing_closed", timing_ok, "Every operation must have real start/end times, duration, and paired events"),
        _check("tool_correlation_closed", correlation_ok, "Every tool event must reference requestId and operationId"),
        {"name": "agent_decisions_accounted", "status": decision_status,
         "reason": "The run did not provide a public Agent decision event" if decision_status == "not_applicable" else ("Some operations lack a public decision link" if decision_status == "failed" else None)},
        _check("terminal_event_present", terminal_ok, "Scan must have a terminal event"),
        {"name": "assessment_timeline_closed", "status": assessment_status,
         "reason": "The current Scan has no assessments" if not assessments else "An assessment.committed event is unlinked"},
        {"name": "lease_events_accounted", "status": "passed" if lease_events else "not_applicable",
         "reason": "The run did not pass through the RuntimeRouter lease supervisor" if not lease_events else None},
        _check("privacy_gate_passed", privacy_ok, "The event stream contains unsanitized sensitive fields or values"),
    ]
    for item in checks:
        if item.get("reason") is None:
            item.pop("reason", None)
    incomplete = any(item["status"] != "passed" for item in checks)
    signal_names = ("model_latency", "model_retries", "model_tokens", "transport_reconnects", "browser_process_metrics")
    limitations = [] if decision_events else ["The run did not provide a public Agent decision trace"]
    if assessments and assessment_status == "failed":
        limitations.append("The assessment end-to-end timeline contains unlinked events")
    limitations.append("Some model, transport, or browser-process extended telemetry is not exposed by the runtime")
    model_exposed = any(event["name"] == "model.call.finished" for event in events)
    signals = []
    for name in signal_names:
        captured = name in {"model_latency", "model_retries"} and model_exposed
        signal = {"name": name, "status": "captured" if captured else "not_exposed"}
        if not captured:
            signal["reason"] = "The current runtime does not expose this metric"
        signals.append(signal)
    manifest = {
        "schemaVersion": SCHEMA_VERSION, "scanId": scan["scanId"], "runId": scan["runId"], "generatedAt": _now(),
        "eventStream": {"path": "runtime-events.jsonl", "format": "jsonl", "eventCount": len(events),
                        "firstSequence": 1, "lastSequence": sequences[-1], "digest": hashlib.sha256(event_bytes).hexdigest()},
        "coreCompleteness": {"status": "incomplete" if incomplete else "complete", "checks": checks},
        "extendedTelemetry": {"status": "partial" if model_exposed else "not_exposed", "signals": signals},
        "privacy": {"policyVersion": SCHEMA_VERSION, "status": "passed" if privacy_ok else "failed", "droppedFieldCount": 0, "redactedFieldCount": redacted_count},
        "diagnosticCompleteness": "limited" if incomplete or not model_exposed else "complete", "limitations": limitations,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return event_bytes, manifest_bytes, manifest


def render_performance_bill(ledger: dict, events: list[dict], output_dir: str | Path) -> tuple[bytes, bytes, dict]:
    """Render an honest wall-clock and activity bill from durable run facts.

    Timings are deliberately presented as overlapping views. Transport and
    browser durations can be contained within Host operation durations and
    therefore must not be added together.
    """
    scan = ledger["scan"]
    operations = ledger.get("operations", [])
    started_at, ended_at = scan["startedAt"], scan["endedAt"]
    total_ms = _interval_ms(started_at, ended_at)
    host_ms = sum(max(0, int(item.get("durationMs", 0))) for item in operations)
    browser_finishes = [
        item for item in events
        if item.get("source") == "browser" and item.get("phase") == "finish"
    ]
    transport_finishes = [
        item for item in events
        if item.get("name") == "transport.request.finished" and item.get("phase") == "finish"
    ]
    model_finishes = [item for item in events if item.get("name") == "model.call.finished"]
    browser_ms = sum(max(0, int(item.get("durationMs", 0))) for item in browser_finishes)
    transport_ms = sum(max(0, int(item.get("durationMs", 0))) for item in transport_finishes)
    model_ms = sum(max(0, int(item.get("durationMs", 0))) for item in model_finishes) if model_finishes else None

    tool_calls_by_name: dict[str, int] = {}
    operation_groups: dict[str, dict] = {}
    for item in operations:
        tool = str(item.get("tool", "unknown"))
        tool_calls_by_name[tool] = tool_calls_by_name.get(tool, 0) + 1
        group_name = str(item.get("operationKind", "unknown"))
        group = operation_groups.setdefault(group_name, {"name": group_name, "callCount": 0, "durationMs": 0})
        group["callCount"] += 1
        group["durationMs"] += max(0, int(item.get("durationMs", 0)))

    entrypoints = ledger.get("entrypoints", [])
    logical_entrypoints = {
        item.get("identity", {}).get("materialDigest") or item.get("entrypointId")
        for item in entrypoints
    }
    physical_count = len(entrypoints)
    logical_count = len(logical_entrypoints)
    duplicate_count = max(0, physical_count - logical_count)

    captured_screenshots = [
        item for item in ledger.get("screenshots", [])
        if item.get("status") == "captured" and item.get("digest")
    ]
    screenshot_digests = {item["digest"] for item in captured_screenshots}
    screenshot_bytes, screenshot_files_measured = _unique_screenshot_bytes(
        captured_screenshots, Path(output_dir)
    )
    request_sizes = [
        item.get("attributes", {}).get("requestBytes") for item in events
        if item.get("name") == "transport.request.started"
        and type(item.get("attributes", {}).get("requestBytes")) is int
    ]
    response_sizes = [
        item.get("attributes", {}).get("responseBytes") for item in transport_finishes
        if type(item.get("attributes", {}).get("responseBytes")) is int
    ]
    agent_turns = {
        item["agentTurnId"] for item in operations if isinstance(item.get("agentTurnId"), str)
    }
    agent_turn_gaps = _agent_turn_gaps(operations)
    public_tool_calls_by_name: dict[str, int] = {}
    for turn_id in agent_turns:
        match = re.search(r":([a-z][a-z0-9_]{1,63})$", turn_id)
        if match:
            name = match.group(1)
            public_tool_calls_by_name[name] = public_tool_calls_by_name.get(name, 0) + 1
    retries = sum(
        max(0, int(item.get("modelTelemetry", {}).get("retryCount", 0)))
        for item in operations
    )
    limitations = [
        "Timing views overlap and must not be added together.",
        "Transport payload measurements begin after Scan binding and exclude the bootstrap request and response.",
        "Between-Agent-turn time can include model reasoning, Agent orchestration, transport scheduling, or user delay; it is not pure model latency.",
    ]
    if not model_finishes:
        limitations.append(
            "The CLI runtime did not expose model latency; outside-Host time includes model and Agent orchestration intervals."
        )
    if len(response_sizes) < len(transport_finishes):
        limitations.append("Response byte size was not exposed for every transport request.")
    if screenshot_files_measured < len(screenshot_digests):
        limitations.append("Byte size could not be read for every unique captured screenshot.")

    largest = sorted(
        (
            {
                "operationId": item["operationId"], "tool": item["tool"],
                "operationKind": item["operationKind"], "status": item["status"],
                "durationMs": max(0, int(item.get("durationMs", 0))),
            }
            for item in operations
        ),
        key=lambda item: (-item["durationMs"], item["operationId"]),
    )[:10]
    bill = {
        "schemaVersion": SCHEMA_VERSION,
        "scanId": scan["scanId"],
        "runId": scan["runId"],
        "status": scan["status"],
        "measurement": {
            "startedAt": started_at,
            "endedAt": ended_at,
            "totalDurationMs": total_ms,
            "hostOperationDurationMs": host_ms,
            "browserOperationDurationMs": browser_ms,
            "transportDurationMs": transport_ms,
            "modelDurationMs": model_ms,
            "outsideHostDurationMs": max(0, total_ms - host_ms),
            "agentTurnGapDurationMs": sum(item["durationMs"] for item in agent_turn_gaps),
            "longestAgentTurnGapMs": max((item["durationMs"] for item in agent_turn_gaps), default=0),
            "modelTelemetryStatus": "captured" if model_finishes else "not_exposed",
        },
        "activity": {
            "toolCalls": len(operations),
            "agentTurnsObserved": len(agent_turns),
            "agentTurnGapCount": len(agent_turn_gaps),
            "publicToolCallsObserved": sum(public_tool_calls_by_name.values()),
            "publicToolCallsByName": dict(sorted(public_tool_calls_by_name.items())),
            "toolCallsByName": dict(sorted(tool_calls_by_name.items())),
            "requestBytes": sum(request_sizes),
            "responseBytes": sum(response_sizes),
            "transportRequestsMeasured": len(transport_finishes),
            "requestSizesMeasured": len(request_sizes),
            "responseSizesMeasured": len(response_sizes),
            "pagesVisited": len(ledger.get("pageStates", [])),
            "physicalEntrypoints": physical_count,
            "logicalEntrypoints": logical_count,
            "duplicateEntrypoints": duplicate_count,
            "duplicateEntrypointRatio": round(duplicate_count / physical_count, 4) if physical_count else 0.0,
            "objectsDiscovered": len(ledger.get("objects", [])),
            "objectsVerified": sum(item.get("rebindStatus") == "matched" for item in ledger.get("objects", [])),
            "decisionsCommitted": len(ledger.get("assessments", [])),
            "screenshotsCaptured": len(captured_screenshots),
            "uniqueScreenshotDigests": len(screenshot_digests),
            "screenshotBytes": screenshot_bytes,
            "rejectedOperations": sum(item.get("status") != "succeeded" for item in operations),
            "modelRetries": retries,
        },
        "operationGroups": sorted(operation_groups.values(), key=lambda item: item["name"]),
        "largestOperations": largest,
        "largestAgentTurnGaps": sorted(
            agent_turn_gaps,
            key=lambda item: (-item["durationMs"], item["fromAgentTurn"], item["toAgentTurn"]),
        )[:10],
        "limitations": limitations,
    }
    json_bytes = (json.dumps(bill, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    markdown_bytes = _render_performance_markdown(bill).encode("utf-8")
    return json_bytes, markdown_bytes, bill


def _interval_ms(started_at: str, ended_at: str) -> int:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return max(0, int((parse(ended_at) - parse(started_at)).total_seconds() * 1000))


def _agent_turn_gaps(operations: list[dict]) -> list[dict]:
    """Measure idle wall-clock gaps between distinct public Agent turns."""
    spans: dict[str, dict] = {}
    for item in operations:
        turn_id = item.get("agentTurnId")
        started_at = item.get("acceptedAt")
        ended_at = item.get("endedAt")
        if not all(isinstance(value, str) and value for value in (turn_id, started_at, ended_at)):
            continue
        span = spans.setdefault(turn_id, {
            "agentTurn": turn_id,
            "startedAt": started_at,
            "endedAt": ended_at,
            "tool": _public_tool_from_turn(turn_id, item.get("tool")),
        })
        if _timestamp_key(started_at) < _timestamp_key(span["startedAt"]):
            span["startedAt"] = started_at
        if _timestamp_key(ended_at) > _timestamp_key(span["endedAt"]):
            span["endedAt"] = ended_at
    ordered = sorted(spans.values(), key=lambda item: (_timestamp_key(item["startedAt"]), item["agentTurn"]))
    gaps = []
    for previous, current in zip(ordered, ordered[1:]):
        duration = _interval_ms(previous["endedAt"], current["startedAt"])
        if duration <= 0:
            continue
        gaps.append({
            "fromAgentTurn": previous["agentTurn"],
            "toAgentTurn": current["agentTurn"],
            "fromTool": previous["tool"],
            "toTool": current["tool"],
            "durationMs": duration,
        })
    return gaps


def _public_tool_from_turn(turn_id: str, fallback: object) -> str:
    match = re.search(r":([a-z][a-z0-9_]{1,63})$", turn_id)
    return match.group(1) if match else str(fallback or "unknown")


def _timestamp_key(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _unique_screenshot_bytes(screenshots: list[dict], root: Path) -> tuple[int, int]:
    measured: dict[str, int] = {}
    for item in screenshots:
        digest, relative = item["digest"], item.get("path")
        if digest in measured or not isinstance(relative, str):
            continue
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            continue
        target = root / path
        try:
            measured[digest] = target.stat().st_size
        except OSError:
            continue
    return sum(measured.values()), len(measured)


def _render_performance_markdown(bill: dict) -> str:
    measurement, activity = bill["measurement"], bill["activity"]
    model = (
        f"{measurement['modelDurationMs']:,} ms"
        if measurement["modelDurationMs"] is not None else "not exposed"
    )
    lines = [
        "# Performance Bill", "",
        f"- Scan: `{bill['scanId']}`", f"- Status: `{bill['status']}`",
        f"- Total wall-clock time: {measurement['totalDurationMs']:,} ms",
        f"- Host operation time: {measurement['hostOperationDurationMs']:,} ms",
        f"- Outside-Host interval: {measurement['outsideHostDurationMs']:,} ms",
        f"- Between-Agent-turn time: {measurement['agentTurnGapDurationMs']:,} ms",
        f"- Longest between-Agent-turn interval: {measurement['longestAgentTurnGapMs']:,} ms",
        f"- Browser-operation view: {measurement['browserOperationDurationMs']:,} ms",
        f"- Transport view: {measurement['transportDurationMs']:,} ms",
        f"- Model latency: {model}", "", "## Activity", "",
        f"- Product tool calls observed: {activity['publicToolCallsObserved']}",
        f"- Host operations: {activity['toolCalls']}",
        f"- Agent turns observed: {activity['agentTurnsObserved']}",
        f"- Between-Agent-turn intervals: {activity['agentTurnGapCount']}",
        f"- Pages visited: {activity['pagesVisited']}",
        f"- Objects / decisions: {activity['objectsDiscovered']} / {activity['decisionsCommitted']}",
        f"- Entrypoints, physical / logical / duplicate: {activity['physicalEntrypoints']} / {activity['logicalEntrypoints']} / {activity['duplicateEntrypoints']}",
        f"- Duplicate-entrypoint ratio: {activity['duplicateEntrypointRatio']:.2%}",
        f"- Request / response bytes measured: {activity['requestBytes']:,} / {activity['responseBytes']:,}",
        f"- Screenshots, entities / unique images / bytes: {activity['screenshotsCaptured']} / {activity['uniqueScreenshotDigests']} / {activity['screenshotBytes']:,}",
        "", "## Largest Host Operations", "",
        "| Tool | Kind | Status | Duration |", "| --- | --- | --- | ---: |",
    ]
    lines.extend(
        f"| `{item['tool']}` | `{item['operationKind']}` | `{item['status']}` | {item['durationMs']:,} ms |"
        for item in bill["largestOperations"]
    )
    lines.extend(["", "## Largest Between-Agent-Turn Intervals", ""])
    if bill["largestAgentTurnGaps"]:
        lines.extend(["| From | To | Duration |", "| --- | --- | ---: |"])
        lines.extend(
            f"| `{item['fromTool']}` | `{item['toTool']}` | {item['durationMs']:,} ms |"
            for item in bill["largestAgentTurnGaps"]
        )
    else:
        lines.append("No positive interval between distinct Agent turns was observed.")
    lines.extend(["", "## Measurement Limits", ""])
    lines.extend(f"- {item}" for item in bill["limitations"])
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, reason: str) -> dict:
    value = {"name": name, "status": "passed" if passed else "failed"}
    if not passed:
        value["reason"] = reason
    return value


def _privacy_check(events: list[dict]) -> tuple[bool, int]:
    forbidden_keys = {"prompt", "messages", "chainofthought", "hiddenreasoning", "credential", "password", "secret", "cookie", "authorization", "dom", "html", "requestbody", "responsebody", "uservalue", "screenshotbase64"}
    redacted = 0

    def walk(value) -> bool:
        nonlocal redacted
        if isinstance(value, dict):
            if any(str(key).lower() in forbidden_keys for key in value):
                return False
            return all(walk(item) for item in value.values())
        if isinstance(value, list):
            return all(walk(item) for item in value)
        if isinstance(value, str):
            redacted += value.count("[REDACTED]")
            return re.search(r"(?i)(password|passwd|token|secret|cookie|authorization)\s*[=:]\s*(?!\[REDACTED\])[^\s,;]+", value) is None
        return True

    return all(walk(event) for event in events), redacted
