from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone


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
