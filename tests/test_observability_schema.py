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

    def test_public_progress_requires_honest_terminal_state(self):
        validator = load_validator("public-progress.schema.json")
        progress = {
            "phase": "discovering", "status": "running",
            "message": "Inspecting the current page.",
            "nextStep": "Inspect the next object.", "terminal": False,
            "counts": {
                "pagesVisited": 1, "objectsDiscovered": 2, "objectsVerified": 1,
                "decisionsCommitted": 0, "entrypointsProcessed": 1, "entrypointsRemaining": 3,
            },
        }
        validator.validate(progress)
        with self.assertRaises(Exception):
            validator.validate({**progress, "status": "failed", "terminal": False})

    def test_performance_bill_allows_unexposed_model_latency(self):
        validator = load_validator("performance-bill.schema.json")
        bill = {
            "schemaVersion": "1.0.0", "scanId": "scan-001", "runId": "run-001",
            "status": "completed",
            "measurement": {
                "startedAt": "2026-09-02T00:00:00Z", "endedAt": "2026-09-02T00:00:10Z",
                "totalDurationMs": 10000, "hostOperationDurationMs": 400,
                "browserOperationDurationMs": 200, "transportDurationMs": 450,
                "modelDurationMs": None, "outsideHostDurationMs": 9600,
                "modelTelemetryStatus": "not_exposed",
            },
            "activity": {
                "toolCalls": 2, "agentTurnsObserved": 2,
                "publicToolCallsObserved": 2,
                "publicToolCallsByName": {"start_audit": 1, "complete_audit": 1},
                "toolCallsByName": {"start_audit": 1, "complete_audit": 1},
                "requestBytes": 100, "responseBytes": 200,
                "transportRequestsMeasured": 1, "requestSizesMeasured": 1,
                "responseSizesMeasured": 1, "pagesVisited": 1,
                "physicalEntrypoints": 5, "logicalEntrypoints": 3,
                "duplicateEntrypoints": 2, "duplicateEntrypointRatio": 0.4,
                "objectsDiscovered": 1, "objectsVerified": 1,
                "decisionsCommitted": 1, "screenshotsCaptured": 1,
                "uniqueScreenshotDigests": 1, "screenshotBytes": 1024,
                "rejectedOperations": 0, "modelRetries": 0,
            },
            "operationGroups": [{"name": "lifecycle", "callCount": 2, "durationMs": 400}],
            "largestOperations": [{
                "operationId": "operation-001", "tool": "complete_audit",
                "operationKind": "lifecycle", "status": "succeeded", "durationMs": 300,
            }],
            "limitations": ["Model telemetry is not exposed."],
        }
        validator.validate(bill)
        with self.assertRaises(Exception):
            validator.validate({**bill, "activity": {**bill["activity"], "duplicateEntrypointRatio": 1.1}})

    def test_platform_plugin_manifest_accepts_domain_neutral_contract(self):
        validator = load_validator("plugin-manifest.schema.json")
        manifest = {
            "pluginId": "example.config-quality", "version": "1.0.0", "platformApiVersion": "1.0.0",
            "domains": ["configuration-quality"],
            "subjectKinds": ["configuration_file"],
            "checks": [{
                "checkId": "CFG-001", "version": "1.0.0",
                "subjectKinds": ["configuration_file"],
                "dimensions": ["required_keys", "value_types"],
                "decisionStates": ["issue_found", "scanned_no_issue", "needs_review"],
                "requiredEvidenceKinds": ["structured"],
                "requiredCapabilities": ["structured_read"],
                "capabilityMissingOutcome": "needs_review",
                "invalidationSignals": ["source_digest"],
            }],
            "executionProfile": {
                "discoverBatching": "allowed", "inspectBatching": "allowed",
                "decisionBatching": "allowed", "parallelism": "forbidden",
                "cacheReuse": "allowed", "checkpoint": "required",
            },
        }
        validator.validate(manifest)
        with self.assertRaises(Exception):
            validator.validate({**manifest, "executionProfile": {
                **manifest["executionProfile"], "parallelism": "unsafe"
            }})

    def test_platform_ledger_schema_accepts_generic_runtime_ledger(self):
        validator = load_validator("platform-ledger.schema.json")
        ledger = {
            "run": {
                "run_id": "run-001", "plugin_id": "example.plugin", "plugin_version": "1.0.0",
                "check_id": "CFG-001", "check_version": "1.0.0", "scope_digest": "a" * 64,
                "started_at": "2026-09-02T00:00:00Z",
            },
            "status": "completed", "operations": [], "events": [], "receipts": [], "artifacts": [],
            "work_items": [], "investigations": [], "decisions": [], "failures": [], "decision_authority": "platform",
        }
        validator.validate(ledger)


if __name__ == "__main__":
    unittest.main()
