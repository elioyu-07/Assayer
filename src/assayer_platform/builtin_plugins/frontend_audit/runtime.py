"""Translate the legacy frontend runtime boundary into platform contracts.

The adapter intentionally accepts a narrow runtime protocol.  The browser
session, DOM collection, visual capture, and replay implementation stay on the
other side of this boundary and are never imported by the platform kernel.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ...contract import (
    CheckContract,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PluginManifest,
    WorkItem,
)
from ...registry import load_plugin_manifest


class FrontendRuntime(Protocol):
    def discover_work_items(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem | Mapping[str, Any]]: ...

    def inspect_work_items(self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext) -> Sequence[InvestigationPacket | Mapping[str, Any]]: ...


_MANIFEST = Path(__file__).with_name("manifest.json")


def _value(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _work_item(value: WorkItem | Mapping[str, Any]) -> WorkItem:
    if isinstance(value, WorkItem):
        return value
    return WorkItem(
        _value(value, "work_item_id", "workItemId", "id"),
        _value(value, "kind", "subjectKind"),
        _value(value, "identity", "objectIdentity"),
        _value(value, "state_digest", "stateDigest", default="runtime-state"),
        _value(value, "metadata", default={}),
    )


def _packet(value: InvestigationPacket | Mapping[str, Any], check: CheckContract, items: Mapping[str, WorkItem]) -> InvestigationPacket:
    if isinstance(value, InvestigationPacket):
        return value
    item_value = _value(value, "work_item", "workItem")
    item = _work_item(item_value) if not isinstance(item_value, str) else items[item_value]
    dimensions = tuple(
        DimensionObservation(
            _value(dimension, "name", "dimension"),
            _strings(_value(dimension, "observations", "observation", default=())),
            _strings(_value(dimension, "evidence_refs", "evidenceRefs", default=())),
            _value(dimension, "candidate_status", "candidateStatus", "status"),
        ) for dimension in _value(value, "dimensions", default=())
    )
    evidence = tuple(
        EvidenceRecord(
            _value(record, "evidence_id", "evidenceId"), item.work_item_id,
            _value(record, "check_id", "checkId", default=check.check_id),
            _value(record, "check_version", "checkVersion", default=check.version),
            _value(record, "kind"), _value(record, "source_identity", "sourceIdentity", default=item.identity),
            _value(record, "payload", default={}),
        ) for record in _value(value, "evidence", "evidenceBundle", default=())
    )
    return InvestigationPacket(
        item, _value(value, "check_id", "checkId", default=check.check_id),
        _value(value, "check_version", "checkVersion", default=check.version), dimensions, evidence,
        _value(value, "recovery_status", "recoveryStatus", default="restored"),
    )


class FrontendAuditPlugin:
    """Compatibility plugin backed by an injected legacy frontend runtime."""

    manifest: PluginManifest = load_plugin_manifest(_MANIFEST)

    def __init__(self, runtime: FrontendRuntime):
        self.runtime = runtime

    def discover(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]:
        return tuple(_work_item(item) for item in self.runtime.discover_work_items(scope, context))

    def inspect(self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext) -> Sequence[InvestigationPacket]:
        by_id = {item.work_item_id: item for item in work_items}
        return tuple(_packet(item, check, by_id) for item in self.runtime.inspect_work_items(work_items, check, context))


class FrontendDecisionProvider:
    """Small deterministic provider useful for adapter and conformance tests."""

    def decide(self, packets: Sequence[InvestigationPacket], check: CheckContract, context: PlatformContext) -> Sequence[DecisionProposal]:
        del context
        proposals = []
        for packet in packets:
            findings = tuple(Finding(dimension.name, dimension.candidate_status, dimension.observations[0] if dimension.observations else "Runtime observation provided.") for dimension in packet.dimensions)
            statuses = {finding.status for finding in findings}
            result = "issue_found" if "violated" in statuses else "needs_review" if statuses.intersection({"unresolved", "blocked", "conflicted"}) else "scanned_no_issue"
            details = {}
            if result == "needs_review":
                details["blocker"] = {
                    "code": "SEMANTIC_REVIEW_REQUIRED",
                    "message": "Runtime evidence is ready, but semantic confirmation is required before publication.",
                }
            proposals.append(DecisionProposal(packet.work_item.work_item_id, check.check_id, check.version, result, findings, "Frontend dimensions evaluated from runtime evidence.", details))
        return tuple(proposals)
