import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"


def load_validator(filename):
    schemas = {}
    for path in SCHEMA_ROOT.rglob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[schema["$id"]] = schema
        schemas[path.name] = schema
    schema = schemas[filename]
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))


def event(**overrides):
    value = {
        "eventId": "event-001", "scanId": "scan-001", "runId": "run-001", "sequence": 1,
        "occurredAt": datetime.now(timezone.utc).isoformat(), "monotonicOffsetMs": 0,
        "source": "host", "category": "tool", "name": "operation.started", "phase": "start",
        "severity": "info", "outcome": "started", "summary": "Host operation started",
        "correlation": {"requestId": "request-001", "operationId": "operation-001"},
        "privacy": {"classification": "internal", "sanitizationStatus": "not_required"},
        "attributes": {},
    }
    value.update(overrides)
    return value


class ObservabilitySchemaTest(unittest.TestCase):
    def test_runtime_event_accepts_correlated_start_and_finish(self):
        validator = load_validator("runtime-event.schema.json")
        validator.validate(event())
        validator.validate(event(
            eventId="event-002", sequence=2, phase="finish", outcome="succeeded",
            durationMs=42,
        ))

    def test_runtime_event_rejects_finish_without_duration(self):
        validator = load_validator("runtime-event.schema.json")
        with self.assertRaises(Exception):
            validator.validate(event(phase="finish", outcome="succeeded"))

    def test_runtime_event_rejects_hidden_reasoning_attribute(self):
        validator = load_validator("runtime-event.schema.json")
        with self.assertRaises(Exception):
            validator.validate(event(attributes={"hiddenReasoning": "do not persist"}))

    def test_manifest_requires_all_core_checks_and_extended_signals(self):
        validator = load_validator("observability-manifest.schema.json")
        manifest = {
            "schemaVersion": "1.0.0", "scanId": "scan-001", "runId": "run-001",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "eventStream": {"path": "runtime-events.jsonl", "format": "jsonl", "eventCount": 1,
                             "firstSequence": 1, "lastSequence": 1, "digest": "a" * 64},
            "coreCompleteness": {"status": "complete", "checks": [
                {"name": name, "status": "passed"} for name in (
                    "sequence_contiguous", "operation_timing_closed", "tool_correlation_closed",
                    "agent_decisions_accounted", "terminal_event_present", "assessment_timeline_closed",
                    "lease_events_accounted", "privacy_gate_passed",
                )
            ]},
            "extendedTelemetry": {"status": "partial", "signals": [
                {"name": name, "status": "not_exposed", "reason": "Caller did not provide this metric"} for name in (
                    "model_latency", "model_retries", "model_tokens", "transport_reconnects", "browser_process_metrics",
                )
            ]},
            "privacy": {"policyVersion": "1.0.0", "status": "passed", "droppedFieldCount": 0, "redactedFieldCount": 0},
            "diagnosticCompleteness": "complete",
        }
        validator.validate(manifest)
        with self.assertRaises(Exception):
            validator.validate({**manifest, "coreCompleteness": {"status": "complete", "checks": []}})


if __name__ == "__main__":
    unittest.main()
