"""Immutable, domain-neutral entities shared by the platform and plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


DECISION_STATES = frozenset({
    "issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise",
})
FINDING_STATES = frozenset({"satisfied", "violated", "unresolved", "blocked", "conflicted"})
RECOVERY_STATES = frozenset({"restored", "not_required", "uncertain", "failed"})
PLATFORM_API_VERSION = "1.0.0"


class PlatformContractError(ValueError):
    """A plugin or Agent result violated the platform contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return _freeze(dict(value or {}))


@dataclass(frozen=True)
class ExecutionProfile:
    discover_batching: str
    inspect_batching: str
    decision_batching: str
    parallelism: str
    cache_reuse: str
    checkpoint: str
    max_batch_size: int = 1
    ordering: str = "independent"
    failure_splitting: str = "forbidden"

    @property
    def inspect_batch_size(self) -> int:
        return self.max_batch_size if self.inspect_batching == "allowed" else 1

    @property
    def can_split_failed_inspection(self) -> bool:
        return (
            self.inspect_batching == "allowed"
            and self.failure_splitting == "allowed"
            and self.ordering == "independent"
        )


@dataclass(frozen=True)
class CheckContract:
    check_id: str
    version: str
    subject_kinds: tuple[str, ...]
    dimensions: tuple[str, ...]
    decision_states: tuple[str, ...]
    required_evidence_kinds: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    capability_missing_outcome: str
    invalidation_signals: tuple[str, ...]

    @property
    def ref(self) -> tuple[str, str]:
        return self.check_id, self.version


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    version: str
    platform_api_version: str
    domains: tuple[str, ...]
    subject_kinds: tuple[str, ...]
    checks: tuple[CheckContract, ...]
    execution_profile: ExecutionProfile


@dataclass(frozen=True)
class CapabilityProfile:
    capabilities: frozenset[str]
    limits: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "limits", _mapping(self.limits))


@dataclass(frozen=True)
class PlatformContext:
    run_id: str
    capabilities: frozenset[str]

    @property
    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(self.capabilities)


@dataclass(frozen=True)
class PlatformRun:
    run_id: str
    plugin_id: str
    plugin_version: str
    check_id: str
    check_version: str
    scope_digest: str
    started_at: str = ""


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    kind: str
    identity: str
    state_digest: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.work_item_id, self.kind, self.identity, self.state_digest,
        )):
            raise PlatformContractError("INVALID_WORK_ITEM", "WorkItem identity fields must be nonempty strings")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True)
class InvestigationCase:
    case_id: str
    work_item_id: str
    check_id: str
    check_version: str
    operation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Operation:
    operation_id: str
    kind: str
    work_item_id: str
    check_id: str
    status: str
    attempt: int = 1
    error_code: str | None = None
    receipt_id: str | None = None
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class PlatformEvent:
    event_id: str
    sequence: int
    run_id: str
    name: str
    phase: str
    outcome: str
    occurred_at: str
    operation_id: str | None = None
    work_item_id: str | None = None
    check_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 1 or not self.event_id or not self.run_id or not self.name:
            raise PlatformContractError("INVALID_PLATFORM_EVENT", "Platform event identity and sequence are required")
        object.__setattr__(self, "details", _mapping(self.details))


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    work_item_id: str
    check_id: str
    check_version: str
    kind: str
    source_identity: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _mapping(self.payload))


@dataclass(frozen=True)
class DimensionObservation:
    name: str
    observations: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    candidate_status: str

    def __post_init__(self) -> None:
        if self.candidate_status not in FINDING_STATES:
            raise PlatformContractError("INVALID_FINDING", "Dimension candidate status is not supported")


@dataclass(frozen=True)
class InvestigationPacket:
    work_item: WorkItem
    check_id: str
    check_version: str
    dimensions: tuple[DimensionObservation, ...]
    evidence: tuple[EvidenceRecord, ...]
    recovery_status: str
    case_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.recovery_status not in RECOVERY_STATES:
            raise PlatformContractError("INVALID_RECOVERY", "Investigation recovery status is not supported")
        object.__setattr__(self, "metadata", _mapping(self.metadata))

    @property
    def check_ref(self) -> tuple[str, str]:
        return self.check_id, self.check_version


@dataclass(frozen=True)
class Finding:
    dimension: str
    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in FINDING_STATES:
            raise PlatformContractError("INVALID_FINDING", "Finding status is not supported")
        if not self.reason:
            raise PlatformContractError("INVALID_FINDING", "Finding reason must be nonempty")


@dataclass(frozen=True)
class DecisionProposal:
    work_item_id: str
    check_id: str
    check_version: str
    result: str
    findings: tuple[Finding, ...]
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.result not in DECISION_STATES:
            raise PlatformContractError("INVALID_DECISION", "Decision result is not supported")
        if not self.reason:
            raise PlatformContractError("INVALID_DECISION", "Decision reason must be nonempty")
        object.__setattr__(self, "details", _mapping(self.details))


@dataclass(frozen=True)
class ReviewCheckpoint:
    checkpoint_id: str
    work_item_id: str
    check_id: str
    check_version: str
    collection_id: str
    item_ids: tuple[str, ...]
    payload: Mapping[str, Any]
    supersedes_checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.checkpoint_id, self.work_item_id, self.check_id,
            self.check_version, self.collection_id,
        )):
            raise PlatformContractError("INVALID_REVIEW_CHECKPOINT", "Review checkpoint identity fields are required")
        if not self.item_ids or any(not isinstance(item_id, str) or not item_id for item_id in self.item_ids):
            raise PlatformContractError("INVALID_REVIEW_CHECKPOINT", "Review checkpoint item IDs are required")
        if len(set(self.item_ids)) != len(self.item_ids):
            raise PlatformContractError("INVALID_REVIEW_CHECKPOINT", "Review checkpoint item IDs must be unique")
        if self.supersedes_checkpoint_id is not None and (
            not isinstance(self.supersedes_checkpoint_id, str)
            or not self.supersedes_checkpoint_id
            or self.supersedes_checkpoint_id == self.checkpoint_id
        ):
            raise PlatformContractError(
                "INVALID_REVIEW_CHECKPOINT",
                "Superseded checkpoint identity must be a different nonempty checkpoint ID",
            )
        object.__setattr__(self, "item_ids", tuple(self.item_ids))
        object.__setattr__(self, "payload", _mapping(self.payload))


@dataclass(frozen=True)
class CommitReceipt:
    commit_id: str
    work_item_id: str
    check_id: str
    check_version: str
    result: str
    durability: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    authority: str = "platform"

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.commit_id, self.work_item_id, self.check_id, self.check_version,
        )):
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Commit receipt identity fields must be nonempty")
        if self.result not in DECISION_STATES:
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Commit result is not supported")
        if self.durability not in {"memory", "durable"}:
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Commit durability is not supported")
        if not isinstance(self.authority, str) or not self.authority:
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Commit authority must be nonempty")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    location: str
    digest: str
    source_receipt_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.artifact_id, self.kind, self.location, self.digest)):
            raise PlatformContractError("INVALID_ARTIFACT", "Artifact identity fields must be nonempty")
        if len(set(self.source_receipt_ids)) != len(self.source_receipt_ids):
            raise PlatformContractError("INVALID_ARTIFACT", "Artifact source receipts must be unique")


@dataclass(frozen=True)
class PlatformLedger:
    run: PlatformRun
    status: str
    operations: tuple[Operation, ...] = ()
    events: tuple[PlatformEvent, ...] = ()
    receipts: tuple[CommitReceipt, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    investigations: tuple[InvestigationPacket, ...] = ()
    decisions: tuple[DecisionProposal, ...] = ()
    failures: tuple[WorkFailure, ...] = ()
    decision_authority: str = "platform"
    review_checkpoints: tuple[ReviewCheckpoint, ...] = ()
    workflow: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"running", "completed", "partial", "failed"}:
            raise PlatformContractError("INVALID_LEDGER_STATUS", "Platform ledger status is not supported")
        sequences = tuple(event.sequence for event in self.events)
        if sequences and sequences != tuple(range(1, len(sequences) + 1)):
            raise PlatformContractError("INVALID_LEDGER_SEQUENCE", "Platform event sequences must be contiguous")
        authorities = {receipt.authority for receipt in self.receipts}
        if len(authorities) > 1 or authorities and authorities != {self.decision_authority}:
            raise PlatformContractError(
                "INVALID_LEDGER_AUTHORITY", "A Run must have exactly one decision authority",
            )
        checkpoint_ids = tuple(item.checkpoint_id for item in self.review_checkpoints)
        if len(checkpoint_ids) != len(set(checkpoint_ids)):
            raise PlatformContractError("INVALID_REVIEW_CHECKPOINT", "Ledger review checkpoint IDs must be unique")
        object.__setattr__(self, "workflow", _mapping(self.workflow))


@dataclass(frozen=True)
class WorkFailure:
    work_item_id: str
    check_id: str
    code: str
    message: str


@dataclass(frozen=True)
class PlatformRunResult:
    run_id: str
    status: str
    decisions: tuple[DecisionProposal, ...]
    failures: tuple[WorkFailure, ...]
    metrics: Mapping[str, int]
    receipts: tuple[CommitReceipt, ...] = ()
    ledger: PlatformLedger | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "partial", "failed"}:
            raise PlatformContractError("INVALID_RUN_STATUS", "Platform Run status is not supported")
        object.__setattr__(self, "metrics", _mapping(self.metrics))


# Concise platform vocabulary for callers that do not need the storage-layer
# suffixes used by the immutable transport records.
Check = CheckContract
Evidence = EvidenceRecord
Decision = DecisionProposal
