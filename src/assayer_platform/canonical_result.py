"""Portable canonical result derived only from a terminal platform ledger."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .contract import PlatformContractError, PlatformLedger, PlatformRunResult
from .platform_performance import build_platform_performance_bill
from .registry import _schema_root


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


def _public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        protected = re.compile(
            r"(?i)(authorization|cookie|password|token|secret|environment|env|stdout|stderr|commandoutput)"
        )
        result = {}
        for key, item in value.items():
            name = str(key)
            public_name = _public_text(name)
            if public_name != name:
                digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
                public_name = f"redacted-field:{digest}"
            result[public_name] = (
                "[REDACTED]" if protected.search(name) else _public_value(item)
            )
        return result
    if isinstance(value, (tuple, list)):
        return [_public_value(item) for item in value]
    if isinstance(value, str):
        return _public_text(value)
    return value


def canonical_ledger_bytes(ledger: PlatformLedger) -> bytes:
    """Return the exact deterministic ledger representation used for tracing."""
    return (
        json.dumps(_plain(ledger), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    root = _schema_root()
    schemas: dict[str, Any] = {}
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas["canonical-result.schema.json"]
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


def _timing(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("status") == "captured":
        return {"status": "measured", "durationMs": int(value["durationMs"])}
    return {
        "status": "unavailable",
        "reason": str(value.get("reason") or "The timing source did not expose a measurement."),
    }


def _failure_owner(code: str) -> str:
    if code.startswith("PROVIDER_"):
        return "provider"
    if code.startswith(("PLUGIN_", "DISCOVERY_", "INSPECTION_")):
        return "plugin"
    if code.startswith(("AGENT_", "DECISION_")):
        return "agent"
    if code.startswith("TRANSPORT_"):
        return "transport"
    if code.startswith(("SOURCE_", "STALE_", "TARGET_")):
        return "target"
    if code in {"CAPABILITY_MISSING", "RESOURCE_UNAVAILABLE"}:
        return "environment"
    if code.startswith(("PLATFORM_", "RESULT_", "COMMIT_", "PUBLICATION_")):
        return "platform"
    return "unattributed"


def _applicable_ids(ledger: PlatformLedger) -> set[str]:
    declared = set(ledger.run.subject_kinds)
    if declared:
        return {
            item.work_item_id for item in ledger.work_items
            if item.kind in declared
        }
    observed = {
        item.work_item.work_item_id for item in ledger.investigations
    } | {
        item.work_item_id for item in ledger.decisions
    } | {
        item.work_item_id for item in ledger.failures if item.work_item_id != "run"
    }
    return observed or {item.work_item_id for item in ledger.work_items}


def validate_canonical_result(
    value: Mapping[str, Any],
    *,
    ledger_bytes: bytes | None = None,
    expected_run_id: str | None = None,
    expected_status: str | None = None,
) -> None:
    """Validate schema, identity, and optional exact ledger trace binding."""
    error = next(_validator().iter_errors(dict(value)), None)
    if error is not None:
        location = ".".join(str(item) for item in error.absolute_path) or "root"
        raise PlatformContractError(
            "CANONICAL_RESULT_INVALID",
            f"Canonical result validation failed at {location}: {error.message}",
        )
    run = value["run"]
    if expected_run_id is not None and run["runId"] != expected_run_id:
        raise PlatformContractError(
            "CANONICAL_RESULT_IDENTITY_MISMATCH",
            "Canonical result Run identity does not match the requested Run",
        )
    if expected_status is not None and value["status"] != expected_status:
        raise PlatformContractError(
            "CANONICAL_RESULT_IDENTITY_MISMATCH",
            "Canonical result status does not match the terminal ledger",
        )
    if ledger_bytes is not None:
        actual_digest = hashlib.sha256(ledger_bytes).hexdigest()
        if value["trace"]["ledgerDigest"] != actual_digest:
            raise PlatformContractError(
                "CANONICAL_RESULT_TRACE_MISMATCH",
                "Canonical result ledger digest does not match the persisted ledger",
            )


def build_canonical_result(
    ledger: PlatformLedger,
    *,
    ledger_bytes: bytes | None = None,
    domain_extension: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate the portable result without changing ledger facts."""
    if ledger.status not in {"completed", "partial", "failed"}:
        raise PlatformContractError(
            "CANONICAL_RESULT_NOT_TERMINAL",
            "A canonical result can be derived only from a terminal platform ledger",
        )
    from .result_conformance import inspect_result_conformance

    formal_result = PlatformRunResult(
        ledger.run.run_id,
        ledger.status,
        () if ledger.status == "failed" else ledger.decisions,
        ledger.failures,
        ledger.metrics,
        () if ledger.status == "failed" else ledger.receipts,
        ledger,
    )
    conformance = inspect_result_conformance(formal_result)
    if not conformance.passed:
        first = conformance.issues[0]
        raise PlatformContractError(
            "CANONICAL_RESULT_CONFORMANCE_FAILED",
            f"{first.message} Contract: {first.invariant}. Next action: {first.next_action}",
        )
    ledger_content = ledger_bytes if ledger_bytes is not None else canonical_ledger_bytes(ledger)
    ledger_digest = hashlib.sha256(ledger_content).hexdigest()
    applicable = _applicable_ids(ledger)
    discovered_ids = {item.work_item_id for item in ledger.work_items}
    decision_by_id = {item.work_item_id: item for item in ledger.decisions}
    receipt_by_id = {item.work_item_id: item for item in ledger.receipts}
    packet_by_id = {
        item.work_item.work_item_id: item for item in ledger.investigations
    }
    failure_ids = {
        item.work_item_id for item in ledger.failures
        if item.work_item_id in applicable
    }
    decided_ids = set(decision_by_id) & applicable
    processed_ids = decided_ids | failure_ids
    skipped_ids = discovered_ids - applicable
    unprocessed_ids = applicable - processed_ids
    formal_decisions = () if ledger.status == "failed" else ledger.decisions

    outcomes = []
    findings = []
    needs_review = []
    for decision in formal_decisions:
        receipt = receipt_by_id[decision.work_item_id]
        outcomes.append({
            "workItemId": decision.work_item_id,
            "checkId": decision.check_id,
            "checkVersion": decision.check_version,
            "result": decision.result,
            "reason": _public_text(decision.reason),
            "receiptId": receipt.commit_id,
        })
        packet = packet_by_id[decision.work_item_id]
        evidence_by_dimension = {
            item.name: list(item.evidence_refs) for item in packet.dimensions
        }
        for finding in decision.findings:
            identity = hashlib.sha256(json.dumps({
                "runId": ledger.run.run_id,
                "workItemId": decision.work_item_id,
                "checkId": decision.check_id,
                "checkVersion": decision.check_version,
                "dimension": finding.dimension,
                "status": finding.status,
                "reason": _public_text(finding.reason),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            findings.append({
                "findingId": f"finding:{identity[:24]}",
                "workItemId": decision.work_item_id,
                "checkId": decision.check_id,
                "checkVersion": decision.check_version,
                "dimension": finding.dimension,
                "status": finding.status,
                "reason": finding.reason,
                "evidenceRefs": evidence_by_dimension.get(finding.dimension, []),
            })
        if decision.result == "needs_review":
            unresolved = [
                item.dimension for item in decision.findings
                if item.status in {"unresolved", "blocked", "conflicted"}
            ]
            needs_review.append({
                "workItemId": decision.work_item_id,
                "checkId": decision.check_id,
                "reason": _public_text(decision.reason),
                "unresolvedDimensions": unresolved,
                "nextAction": (
                    "Resolve the missing or conflicting facts and start a new evidence-bound review."
                ),
            })

    if ledger.status == "failed":
        unverified_ids = sorted(applicable)
        unverified = [{
            "workItemId": work_item_id,
            "reason": "The failed Run invalidated formal conclusions for this WorkItem.",
            "missingEvidence": ["valid_terminal_conclusion"],
        } for work_item_id in unverified_ids]
    else:
        unverified = []
        for work_item_id in sorted(unprocessed_ids):
            missing = (
                "authoritative_decision"
                if work_item_id in packet_by_id
                else "investigation_packet"
            )
            unverified.append({
                "workItemId": work_item_id,
                "reason": "The WorkItem has no committed formal Decision in this Run.",
                "missingEvidence": [missing],
            })

    performance_bill = build_platform_performance_bill(ledger)
    result: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "run": {
            "runId": ledger.run.run_id,
            "pluginId": ledger.run.plugin_id,
            "pluginVersion": ledger.run.plugin_version,
            "checkId": ledger.run.check_id,
            "checkVersion": ledger.run.check_version,
        },
        "status": ledger.status,
        "conclusionValidity": "invalidated" if ledger.status == "failed" else "valid",
        "coverage": {
            "discovered": len(discovered_ids),
            "processed": len(processed_ids),
            "skipped": len(skipped_ids),
            "unprocessed": len(unprocessed_ids),
            "complete": ledger.status == "completed" and not unprocessed_ids,
            "note": (
                "Non-applicable WorkItem kinds are counted as skipped; failures remain processed but incomplete."
            ),
        },
        "outcomes": outcomes,
        "findings": findings if ledger.status != "failed" else [],
        "needsReview": needs_review if ledger.status != "failed" else [],
        "unverified": unverified,
        "failures": [{
            "workItemId": item.work_item_id,
            "code": item.code,
            "message": _public_text(item.message),
            "owner": _failure_owner(item.code),
            "retryable": False,
        } for item in ledger.failures],
        "performance": {
            "wallClock": _timing(performance_bill["measurement"]["wallClock"]),
            "agentWait": _timing(performance_bill["measurement"]["agentWait"]),
            "transport": _timing(performance_bill["measurement"]["transport"]),
            "host": _timing(performance_bill["measurement"]["hostOperations"]),
            "provider": _timing(performance_bill["measurement"]["provider"]),
            "details": {
                "parallelism": performance_bill["parallelism"],
                "model": performance_bill["measurement"]["model"],
                "limitations": performance_bill["limitations"],
            },
        },
        "trace": {
            "ledgerRef": f"{ledger.run.run_id}.platform-ledger.json",
            "ledgerDigest": ledger_digest,
            "eventsRef": f"{ledger.run.run_id}.platform-events.jsonl",
            "diagnosticsRef": f"{ledger.run.run_id}.platform-performance-bill.json",
        },
    }
    if domain_extension is not None:
        extension = dict(domain_extension)
        if _public_text(extension.get("schemaId")) != str(extension.get("schemaId", "")):
            raise PlatformContractError(
                "CANONICAL_EXTENSION_IDENTITY_UNSAFE",
                "Canonical domain extension schema identity contains private local data",
            )
        result["domainExtension"] = {
            **extension,
            "data": _public_value(_plain(extension.get("data", {}))),
        }
    validate_canonical_result(
        result,
        ledger_bytes=ledger_content,
        expected_run_id=ledger.run.run_id,
        expected_status=ledger.status,
    )
    return result


def render_canonical_result(
    ledger: PlatformLedger,
    *,
    ledger_bytes: bytes | None = None,
    domain_extension: Mapping[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    result = build_canonical_result(
        ledger, ledger_bytes=ledger_bytes, domain_extension=domain_extension,
    )
    return (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        result,
    )


__all__ = [
    "build_canonical_result",
    "canonical_ledger_bytes",
    "render_canonical_result",
    "validate_canonical_result",
]
