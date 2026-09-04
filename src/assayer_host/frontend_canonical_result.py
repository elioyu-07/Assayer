"""Portable canonical result adapter for the legacy frontend AuditLedger."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform.canonical_result import validate_canonical_result
from assayer_platform.contract import PlatformContractError
from assayer_platform.builtin_plugins.frontend_audit import FrontendAuditPlugin

from .resources import default_schema_root


FRONTEND_PLUGIN_ID = FrontendAuditPlugin.manifest.plugin_id
_PLUGIN_VERSION = FrontendAuditPlugin.manifest.version
_FAILED_OPERATION_STATES = {"rejected", "failed_known", "result_unknown"}


def _public_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"(?i)\b(authorization|cookie|password|token|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b([A-Z_][A-Z0-9_]{2,})\s*=\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9:])/(?:[^/\s]+/)*[^\s,;)'\"\]}]+",
        "[LOCAL_PATH]",
        text,
    )
    text = re.sub(r"\b[A-Za-z]:\\[^\s,;)'\"\]}]+", "[LOCAL_PATH]", text)
    return text[:2000] or "No public detail was supplied."


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    schemas: dict[str, Any] = {}
    for path in default_schema_root().glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    validators = {}
    for name in (
        "audit-ledger.schema.json",
        "frontend-canonical-extension.schema.json",
        "performance-bill.schema.json",
    ):
        schema = schemas[name]
        validators[name] = Draft202012Validator(
            schema, resolver=RefResolver(schema["$id"], schema, store=schemas),
        )
    return validators


def _validate(name: str, value: Mapping[str, Any]) -> None:
    error = next(_validators()[name].iter_errors(dict(value)), None)
    if error is None:
        return
    location = ".".join(str(item) for item in error.absolute_path) or "root"
    raise PlatformContractError(
        "FRONTEND_CANONICAL_SOURCE_INVALID",
        f"Frontend canonical source validation failed at {location}: {error.message}",
    )


def _failure_owner(code: str) -> str:
    if code.startswith(("BROWSER_", "ACTION_ADAPTER_", "RECOVERY_ADAPTER_", "EVIDENCE_ADAPTER_")):
        return "provider"
    if code.startswith(("TRANSPORT_", "AGENT_LEASE_", "REQUEST_RESULT_UNKNOWN")):
        return "transport"
    if code.startswith(("AGENT_", "DECISION_")):
        return "agent"
    if code.startswith(("TARGET_", "PERSISTENT_WRITE_")):
        return "target"
    if code.startswith(("CAPABILITY_", "RESOURCE_", "LOGIN_")):
        return "environment"
    if code.startswith(("RULE_", "PLUGIN_")):
        return "plugin"
    if code.startswith(("COVERAGE_", "LEDGER_", "STALE_", "COMMIT_", "PLATFORM_")):
        return "platform"
    return "unattributed"


def _failure_code(value: Any) -> str:
    code = str(value or "")
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code) else "FRONTEND_DIAGNOSTIC"


def _timing(duration: Any, unavailable_reason: str) -> dict[str, Any]:
    if type(duration) is int and duration >= 0:
        return {"status": "measured", "durationMs": duration}
    return {"status": "unavailable", "reason": unavailable_reason}


def _rule_identity(ledger: Mapping[str, Any]) -> tuple[str, str]:
    frozen = ledger["scan"]["frozenRules"]
    if len(frozen) != 1:
        raise PlatformContractError(
            "FRONTEND_CANONICAL_CHECK_AMBIGUOUS",
            "The frontend compatibility adapter requires exactly one frozen Check per Run",
        )
    return str(frozen[0]["ruleId"]), str(frozen[0]["version"])


def build_frontend_canonical_result(
    ledger: Mapping[str, Any],
    *,
    ledger_bytes: bytes | None = None,
    performance_bill: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one terminal AuditLedger and map it to the common result contract."""
    _validate("audit-ledger.schema.json", ledger)
    scan = ledger["scan"]
    status = scan["status"]
    if status not in {"completed", "partial", "failed"}:
        raise PlatformContractError(
            "FRONTEND_CANONICAL_NOT_TERMINAL",
            "A frontend canonical result requires a terminal AuditLedger",
        )
    if performance_bill is not None:
        _validate("performance-bill.schema.json", performance_bill)
        if (
            performance_bill.get("scanId") != scan["scanId"]
            or performance_bill.get("runId") != scan["runId"]
            or performance_bill.get("status") != status
        ):
            raise PlatformContractError(
                "FRONTEND_CANONICAL_PERFORMANCE_MISMATCH",
                "The frontend performance bill does not belong to the AuditLedger",
            )

    check_id, check_version = _rule_identity(ledger)
    content = ledger_bytes if ledger_bytes is not None else (
        json.dumps(dict(ledger), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    try:
        persisted_value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlatformContractError(
            "FRONTEND_CANONICAL_TRACE_INVALID",
            "The supplied AuditLedger bytes are not valid JSON",
        ) from error
    if persisted_value != dict(ledger):
        raise PlatformContractError(
            "FRONTEND_CANONICAL_TRACE_MISMATCH",
            "The supplied AuditLedger bytes do not match the mapped ledger",
        )
    digest = hashlib.sha256(content).hexdigest()
    proof = scan["coverageProof"]
    object_ids = {item["objectId"] for item in ledger["objects"]}
    entrypoint_ids = {item["entrypointId"] for item in ledger["entrypoints"]}
    evidence_ids = {item["evidenceId"] for item in ledger["evidence"]}
    findings_by_id = {item["findingId"]: item for item in ledger["dimensionFindings"]}
    valid_run = bool(scan["conclusionsValid"]) and status != "failed"
    assessments = [
        item for item in ledger["assessments"]
        if valid_run
        and item.get("conclusionValidity") == "valid"
        and item.get("rule") == {"ruleId": check_id, "version": check_version}
    ]
    valid_object_ids = {item["objectRef"] for item in assessments}

    outcomes = []
    findings = []
    needs_review = []
    for assessment in sorted(assessments, key=lambda item: item["assessmentId"]):
        outcomes.append({
            "workItemId": assessment["objectRef"],
            "checkId": check_id,
            "checkVersion": check_version,
            "result": assessment["result"],
            "reason": _public_text(assessment["reasonText"]),
            "receiptId": assessment["assessmentId"],
        })
        for finding_ref in assessment["findingRefs"]:
            finding = findings_by_id.get(finding_ref)
            if finding is None or finding.get("objectRef") != assessment["objectRef"]:
                raise PlatformContractError(
                    "FRONTEND_CANONICAL_REFERENCE_INVALID",
                    "A frontend Assessment has an invalid Finding reference",
                )
            if not set(finding["evidenceRefs"]).issubset(evidence_ids):
                raise PlatformContractError(
                    "FRONTEND_CANONICAL_REFERENCE_INVALID",
                    "A frontend Finding has an invalid Evidence reference",
                )
            findings.append({
                "findingId": finding["findingId"],
                "workItemId": assessment["objectRef"],
                "checkId": check_id,
                "checkVersion": check_version,
                "dimension": finding["dimension"],
                "status": finding["status"],
                "reason": _public_text(finding["reasonText"]),
                "evidenceRefs": list(finding["evidenceRefs"]),
            })
        if assessment["result"] == "needs_review":
            unresolved = list(assessment["coverage"].get("unresolvedDimensions", ()))
            if not unresolved:
                unresolved = ["unspecified_required_fact"]
            blocker = assessment.get("blocker", {})
            needs_review.append({
                "workItemId": assessment["objectRef"],
                "checkId": check_id,
                "reason": _public_text(blocker.get("message") or assessment["reasonText"]),
                "unresolvedDimensions": unresolved,
                "nextAction": "Resolve the named frontend fact and start a new evidence-bound review.",
            })

    processed_entrypoints = set(proof.get("processedEntrypointRefs", ())) & entrypoint_ids
    unprocessed_entrypoints = set(proof.get("unprocessedEntrypointRefs", ())) & entrypoint_ids
    skipped_entrypoints = {
        item["entrypointId"] for item in proof.get("skippedEntrypoints", ())
    } & entrypoint_ids
    # Unprocessed takes precedence for historical ledgers that also retained a
    # skip explanation for the same entrypoint.
    skipped_entrypoints -= processed_entrypoints | unprocessed_entrypoints
    unprocessed_objects = object_ids - valid_object_ids
    discovered_scope = object_ids | entrypoint_ids
    if status == "failed":
        processed_scope = (
            (set(proof.get("processedObjectRefs", ())) & object_ids)
            | processed_entrypoints
        )
        unverified_ids = sorted(discovered_scope)
    else:
        processed_scope = valid_object_ids | processed_entrypoints
        unverified_ids = sorted(unprocessed_objects | unprocessed_entrypoints)
    skipped_scope = skipped_entrypoints
    unprocessed_scope = discovered_scope - processed_scope - skipped_scope
    if status == "completed" and unprocessed_scope:
        raise PlatformContractError(
            "FRONTEND_CANONICAL_COVERAGE_INVALID",
            "A completed frontend AuditLedger contains unprocessed canonical scope",
        )

    unverified = [{
        "workItemId": item_id,
        "reason": (
            "The failed Run invalidated this frontend scope."
            if status == "failed" else
            "This frontend scope has no valid committed conclusion in the Run."
        ),
        "missingEvidence": [
            "valid_terminal_conclusion" if status == "failed" else
            ("authoritative_decision" if item_id in object_ids else "entrypoint_processing")
        ],
    } for item_id in unverified_ids]

    failures = []
    if status == "failed":
        terminal = scan["terminalReason"]
        failures.append({
            "code": terminal["code"],
            "message": _public_text(terminal["message"]),
            "owner": _failure_owner(terminal["code"]),
            "retryable": False,
        })
    for diagnostic in ledger.get("diagnostics", ()):
        if diagnostic.get("severity") not in {"error", "fatal"}:
            continue
        code = _failure_code(diagnostic.get("code"))
        candidate = {
            "code": code,
            "message": _public_text(diagnostic["message"]),
            "owner": _failure_owner(code),
            "retryable": False,
        }
        if candidate not in failures:
            failures.append(candidate)

    measurement = performance_bill.get("measurement", {}) if performance_bill else {}
    activity = performance_bill.get("activity", {}) if performance_bill else {}
    limitations = performance_bill.get("limitations", ()) if performance_bill else ()
    performance = {
        "wallClock": _timing(
            measurement.get("totalDurationMs"),
            "The frontend runtime did not publish its performance bill.",
        ),
        "agentWait": _timing(
            measurement.get("agentTurnGapDurationMs"),
            "The frontend runtime did not expose between-Agent-turn timing.",
        ),
        "transport": _timing(
            measurement.get("transportDurationMs"),
            "The frontend runtime did not expose transport timing.",
        ),
        "host": _timing(
            measurement.get("hostOperationDurationMs"),
            "The frontend runtime did not expose Host timing.",
        ),
        "provider": _timing(
            measurement.get("browserOperationDurationMs"),
            "The frontend runtime did not expose browser-provider timing.",
        ),
        "details": {
            "source": "performance-bill.json" if performance_bill else "not_exposed",
            "modelTelemetryStatus": measurement.get("modelTelemetryStatus", "not_exposed"),
            "outsideHostDurationMs": measurement.get("outsideHostDurationMs"),
            "activity": {
                key: activity[key] for key in (
                    "toolCalls", "agentTurnsObserved", "publicToolCallsObserved",
                    "pagesVisited", "physicalEntrypoints", "logicalEntrypoints",
                    "duplicateEntrypoints", "objectsDiscovered", "decisionsCommitted",
                ) if key in activity
            },
            "limitations": [_public_text(item) for item in limitations],
        },
    }

    page_ids = {item["pageStateId"] for item in ledger["pageStates"]}
    visited_pages = set(proof.get("visitedPageStateRefs", ())) & page_ids
    published_issue_count = sum(
        valid_run and item.get("conclusionValidity") == "valid"
        for item in ledger["issues"]
    )
    extension = {
        "schemaVersion": "1.0.0",
        "scanId": scan["scanId"],
        "auditLedgerRef": "audit-ledger.json",
        "issuesRef": "issues.json",
        "coverage": {
            "pages": {
                "discovered": len(ledger["pageStates"]),
                "processed": len(visited_pages),
                "unprocessed": max(0, len(ledger["pageStates"]) - len(visited_pages)),
            },
            "objects": {
                "discovered": len(object_ids),
                "processed": len(valid_object_ids) if status != "failed" else len(
                    set(proof.get("processedObjectRefs", ())) & object_ids
                ),
                "unprocessed": len(unprocessed_objects) if status != "failed" else len(object_ids),
            },
            "entrypoints": {
                "discovered": len(entrypoint_ids),
                "processed": len(processed_entrypoints),
                "skipped": len(skipped_entrypoints),
                "unprocessed": len(unprocessed_entrypoints),
            },
        },
        "issues": {
            "published": int(published_issue_count),
            "invalidated": len(ledger["issues"]) - int(published_issue_count),
        },
        "diagnostics": {
            "records": len(ledger.get("diagnostics", ())),
            "failedOperations": sum(
                item.get("status") in _FAILED_OPERATION_STATES
                for item in ledger["operations"]
            ),
        },
    }
    _validate("frontend-canonical-extension.schema.json", extension)

    result = {
        "schemaVersion": "1.0.0",
        "run": {
            "runId": scan["runId"],
            "pluginId": FRONTEND_PLUGIN_ID,
            "pluginVersion": _PLUGIN_VERSION,
            "checkId": check_id,
            "checkVersion": check_version,
        },
        "status": status,
        "conclusionValidity": "valid" if valid_run else "invalidated",
        "coverage": {
            "discovered": len(discovered_scope),
            "processed": len(processed_scope),
            "skipped": len(skipped_scope),
            "unprocessed": len(unprocessed_scope),
            "complete": status == "completed" and not unprocessed_scope,
            "note": "Frontend objects and reachable entrypoints are the canonical scope units; page coverage is preserved in the namespaced extension.",
        },
        "outcomes": outcomes if status != "failed" else [],
        "findings": findings if status != "failed" else [],
        "needsReview": needs_review if status != "failed" else [],
        "unverified": unverified,
        "failures": failures,
        "performance": performance,
        "trace": {
            "ledgerRef": "audit-ledger.json",
            "ledgerDigest": digest,
            "eventsRef": "runtime-events.jsonl",
            "diagnosticsRef": "run-diagnostics.json",
        },
        "domainExtension": {
            "schemaId": "assayer.frontend-audit/canonical-result-extension",
            "schemaVersion": "1.0.0",
            "data": extension,
        },
    }
    validate_canonical_result(
        result,
        ledger_bytes=content,
        expected_run_id=scan["runId"],
        expected_status=status,
    )
    return result


def render_frontend_canonical_result(
    ledger: Mapping[str, Any],
    *,
    ledger_bytes: bytes | None = None,
    performance_bill: Mapping[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    result = build_frontend_canonical_result(
        ledger, ledger_bytes=ledger_bytes, performance_bill=performance_bill,
    )
    return (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        result,
    )


__all__ = [
    "FRONTEND_PLUGIN_ID", "build_frontend_canonical_result",
    "render_frontend_canonical_result",
]
