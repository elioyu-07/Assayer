"""Shared semantic conformance gates for terminal platform results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .contract import PlatformLedger, PlatformRunResult
from .registry import _schema_root


@dataclass(frozen=True)
class ResultConformanceIssue:
    """One result or recovery invariant that blocks formal publication."""

    code: str
    invariant: str
    message: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "invariant": self.invariant,
            "message": self.message,
            "nextAction": self.next_action,
        }


@dataclass(frozen=True)
class ResultConformanceReport:
    run_id: str
    issues: tuple[ResultConformanceIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "runId": self.run_id,
            "status": "passed" if self.passed else "failed",
            "issues": [item.as_dict() for item in self.issues],
        }


def _issue(
    code: str, invariant: str, message: str, next_action: str,
) -> ResultConformanceIssue:
    return ResultConformanceIssue(code, invariant, message, next_action)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


@lru_cache(maxsize=1)
def _ledger_validator() -> Draft202012Validator:
    root = _schema_root()
    schemas: dict[str, Any] = {}
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas["platform-ledger.schema.json"]
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


def latest_recovery_statuses(ledger: PlatformLedger) -> dict[str, str]:
    """Return the latest explicit recovery event for each WorkItem."""
    latest: dict[str, str] = {}
    for event in sorted(ledger.events, key=lambda item: item.sequence):
        if event.name == "recovery.finished" and event.work_item_id:
            latest[event.work_item_id] = event.outcome
    return latest


def inspect_result_conformance(result: PlatformRunResult) -> ResultConformanceReport:
    """Validate result, ledger, recovery, receipt, and publication closure."""
    ledger = result.ledger
    run_id = result.run_id
    if ledger is None:
        return ResultConformanceReport(run_id, (_issue(
            "RESULT_LEDGER_MISSING",
            "RCV1-LEDGER-IDENTITY",
            "The terminal result has no canonical platform ledger.",
            "Persist the canonical ledger before constructing or publishing a terminal result.",
        ),))

    issues: list[ResultConformanceIssue] = []
    schema_error = next(_ledger_validator().iter_errors(_plain(ledger)), None)
    if schema_error is not None:
        location = ".".join(str(item) for item in schema_error.absolute_path) or "root"
        issues.append(_issue(
            "RESULT_LEDGER_SCHEMA_INVALID",
            "RCV1-LEDGER-SCHEMA",
            f"The canonical ledger violates its schema at {location}: {schema_error.message}",
            "Correct the platform ledger structure before terminal publication.",
        ))
    if ledger.run.run_id != run_id or ledger.status != result.status:
        issues.append(_issue(
            "RESULT_LEDGER_IDENTITY_MISMATCH",
            "RCV1-LEDGER-IDENTITY",
            "The result identity or status differs from its canonical ledger.",
            "Rebuild the result only from the matching terminal ledger.",
        ))
    if result.status != "failed" and tuple(result.decisions) != tuple(ledger.decisions):
        issues.append(_issue(
            "RESULT_DECISION_MISMATCH",
            "RCV1-DECISION-CLOSURE",
            "The result Decisions differ from the canonical ledger.",
            "Derive result Decisions directly from committed ledger Decisions.",
        ))
    if tuple(result.failures) != tuple(ledger.failures):
        issues.append(_issue(
            "RESULT_FAILURE_MISMATCH",
            "RCV1-FAILURE-CLOSURE",
            "The result failures differ from the canonical ledger.",
            "Derive result failures directly from the canonical ledger.",
        ))
    terminal = ledger.events[-1] if ledger.events else None
    if (
        terminal is None
        or terminal.name != "platform.run.terminal"
        or terminal.outcome != ledger.status
    ):
        issues.append(_issue(
            "RESULT_TERMINAL_EVENT_INVALID",
            "RCV1-TERMINAL-CLOSURE",
            "The ledger does not end with a terminal event matching its status.",
            "Append the matching terminal event only after all required work is durably closed.",
        ))
    if result.status != "failed" and tuple(result.receipts) != tuple(ledger.receipts):
        issues.append(_issue(
            "RESULT_RECEIPT_MISMATCH",
            "RCV1-RECEIPT-CLOSURE",
            "The result receipts differ from the canonical ledger.",
            "Publish only the receipts persisted for this Run.",
        ))

    work_items = {item.work_item_id: item for item in ledger.work_items}
    packets = {item.work_item.work_item_id: item for item in ledger.investigations}
    decisions = {item.work_item_id: item for item in ledger.decisions}
    receipts = {item.work_item_id: item for item in ledger.receipts}
    if any(len(values) != len(set(values)) for values in (
        [item.work_item_id for item in ledger.work_items],
        [item.work_item.work_item_id for item in ledger.investigations],
        [item.work_item_id for item in ledger.decisions],
        [item.work_item_id for item in ledger.receipts],
    )):
        issues.append(_issue(
            "RESULT_DUPLICATE_IDENTITY",
            "RCV1-REFERENCE-CLOSURE",
            "The ledger contains duplicate WorkItem-bound records.",
            "Retain exactly one current Investigation, Decision, and receipt per WorkItem.",
        ))

    latest_recovery = latest_recovery_statuses(ledger)
    for work_item_id, decision in decisions.items():
        packet = packets.get(work_item_id)
        receipt = receipts.get(work_item_id)
        if work_item_id not in work_items or packet is None:
            issues.append(_issue(
                "RESULT_DECISION_UNTRACEABLE",
                "RCV1-REFERENCE-CLOSURE",
                "A Decision does not trace to one discovered and inspected WorkItem.",
                "Remove the Decision or restore its WorkItem and InvestigationPacket closure.",
            ))
            continue
        if (
            decision.check_id != packet.check_id
            or decision.check_version != packet.check_version
        ):
            issues.append(_issue(
                "RESULT_DECISION_CHECK_MISMATCH",
                "RCV1-REFERENCE-CLOSURE",
                "A Decision and its InvestigationPacket reference different Check identities.",
                "Rebuild the Decision from the current WorkItem and frozen Check version.",
            ))
        finding_dimensions = [item.dimension for item in decision.findings]
        packet_dimensions = [item.name for item in packet.dimensions]
        if (
            len(finding_dimensions) != len(set(finding_dimensions))
            or set(finding_dimensions) != set(packet_dimensions)
        ):
            issues.append(_issue(
                "RESULT_FINDING_CLOSURE_INVALID",
                "RCV1-DECISION-CLOSURE",
                "A Decision does not cover every investigated dimension exactly once.",
                "Provide one Finding for every InvestigationPacket dimension.",
            ))
        finding_statuses = {item.status for item in decision.findings}
        invalid_gate = (
            decision.result == "scanned_no_issue" and finding_statuses != {"satisfied"}
        ) or (
            decision.result == "issue_found" and "violated" not in finding_statuses
        ) or (
            decision.result == "needs_review"
            and not finding_statuses.intersection({"unresolved", "blocked", "conflicted"})
        )
        if invalid_gate:
            issues.append(_issue(
                "RESULT_DECISION_GATE_INVALID",
                "RCV1-DECISION-CLOSURE",
                "A Decision result is incompatible with its Finding states.",
                "Correct the semantic Decision before committing it.",
            ))
        evidence = {item.evidence_id: item for item in packet.evidence}
        referenced = {
            reference
            for dimension in packet.dimensions
            for reference in dimension.evidence_refs
        }
        if len(evidence) != len(packet.evidence) or not referenced.issubset(evidence):
            issues.append(_issue(
                "RESULT_EVIDENCE_CLOSURE_INVALID",
                "RCV1-EVIDENCE-CLOSURE",
                "An InvestigationPacket contains duplicate Evidence or an unresolved Evidence reference.",
                "Restore unique in-Run Evidence and close every dimension reference before Decision.",
            ))
        elif any(
            item.work_item_id != work_item_id
            or item.check_id != packet.check_id
            or item.check_version != packet.check_version
            or item.source_identity != packet.work_item.identity
            for item in packet.evidence
        ):
            issues.append(_issue(
                "RESULT_EVIDENCE_IDENTITY_MISMATCH",
                "RCV1-EVIDENCE-CLOSURE",
                "Evidence identity does not match its WorkItem, Check, or source.",
                "Collect fresh Evidence bound to the current WorkItem and frozen Check.",
            ))
        recovery_status = latest_recovery.get(work_item_id, packet.recovery_status)
        if result.status != "failed" and recovery_status not in {"restored", "not_required"}:
            issues.append(_issue(
                "RESULT_RECOVERY_UNRESOLVED",
                "RCV1-RECOVERY-BARRIER",
                "A committed Decision is downstream of unresolved or failed recovery.",
                "Complete safe recovery and start a fresh Evidence-bound Decision before publication.",
            ))
        if receipt is None or (
            receipt.check_id != decision.check_id
            or receipt.check_version != decision.check_version
            or receipt.result != decision.result
            or receipt.authority != ledger.decision_authority
        ):
            issues.append(_issue(
                "RESULT_RECEIPT_UNCLOSED",
                "RCV1-RECEIPT-CLOSURE",
                "A Decision has no matching authoritative commit receipt.",
                "Commit the validated Decision and persist its matching platform receipt.",
            ))

    orphan_receipts = set(receipts) - set(decisions)
    if orphan_receipts:
        issues.append(_issue(
            "RESULT_RECEIPT_ORPHANED",
            "RCV1-RECEIPT-CLOSURE",
            "The ledger contains a receipt without a matching Decision.",
            "Repair the atomic Decision and receipt persistence boundary.",
        ))

    if result.status == "completed":
        if ledger.failures or set(decisions) != set(packets):
            issues.append(_issue(
                "RESULT_COMPLETED_COVERAGE_INVALID",
                "RCV1-TERMINAL-COVERAGE",
                "A completed Run does not have one committed Decision for every inspected applicable WorkItem.",
                "Resolve every failure and commit every inspected WorkItem Decision, or close the Run as partial or failed.",
            ))
    if result.status == "failed" and (result.decisions or result.receipts or ledger.artifacts):
        issues.append(_issue(
            "RESULT_FAILED_PUBLICATION_INVALID",
            "RCV1-FAILED-INVALIDATION",
            "A failed Run exposes formal Decisions, receipts, or published artifacts.",
            "Suppress formal outcomes and artifacts for failed Runs while retaining diagnostic history.",
        ))
    receipt_ids = {item.commit_id for item in ledger.receipts}
    if any(
        not artifact.source_receipt_ids
        or not set(artifact.source_receipt_ids).issubset(receipt_ids)
        for artifact in ledger.artifacts
    ):
        issues.append(_issue(
            "RESULT_ARTIFACT_UNTRACEABLE",
            "RCV1-PUBLICATION-CLOSURE",
            "A published artifact is not fully bound to authoritative receipts from this Run.",
            "Republish the artifact only from committed receipt-bound Decisions.",
        ))
    return ResultConformanceReport(run_id, tuple(issues))


__all__ = [
    "ResultConformanceIssue",
    "ResultConformanceReport",
    "inspect_result_conformance",
    "latest_recovery_statuses",
]
