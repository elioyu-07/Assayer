"""Domain-neutral contracts and execution kernel for Assayer plugins."""

from .contract import (
    Artifact,
    CapabilityProfile,
    Check,
    CheckContract,
    CommitReceipt,
    Decision,
    DecisionProposal,
    DimensionObservation,
    Evidence,
    EvidenceRecord,
    ExecutionProfile,
    Finding,
    InvestigationCase,
    InvestigationPacket,
    Operation,
    PlatformContext,
    PlatformContractError,
    PlatformEvent,
    PlatformLedger,
    PlatformRun,
    PlatformRunResult,
    PluginManifest,
    WorkFailure,
    WorkItem,
    PLATFORM_API_VERSION,
)
from .kernel import ArtifactPublisher, DecisionCommitter, DomainPlugin, PlatformKernel, SemanticDecisionProvider
from .ledger import JsonPlatformLedgerStore, PlatformLedgerStore
from .reporting import JsonSummaryPublisher
from .decision import validate_decision_shape
from .session import InteractivePlatformRun, InteractivePlatformSession
from .interactive import (
    INTERACTIVE_OPERATIONS,
    INTERACTIVE_PROTOCOL_VERSION,
    InteractivePluginController,
)
from .registry import load_plugin_manifest, validate_plugin_manifest
from .plugin_registry import PluginRegistration, PluginRegistry
from .runner import PlatformRunner

__all__ = [
    "Artifact",
    "ArtifactPublisher",
    "CapabilityProfile",
    "Check",
    "CheckContract",
    "CommitReceipt",
    "Decision",
    "DecisionProposal",
    "DimensionObservation",
    "DecisionCommitter",
    "DomainPlugin",
    "Evidence",
    "EvidenceRecord",
    "ExecutionProfile",
    "Finding",
    "InvestigationCase",
    "InvestigationPacket",
    "InteractivePlatformSession",
    "InteractivePlatformRun",
    "InteractivePluginController",
    "INTERACTIVE_OPERATIONS",
    "INTERACTIVE_PROTOCOL_VERSION",
    "Operation",
    "PlatformContext",
    "PlatformContractError",
    "PlatformEvent",
    "PlatformLedger",
    "PlatformKernel",
    "PlatformLedgerStore",
    "PlatformRun",
    "PlatformRunResult",
    "PluginManifest",
    "JsonPlatformLedgerStore",
    "JsonSummaryPublisher",
    "validate_decision_shape",
    "SemanticDecisionProvider",
    "WorkFailure",
    "WorkItem",
    "PLATFORM_API_VERSION",
    "load_plugin_manifest",
    "validate_plugin_manifest",
    "PluginRegistration",
    "PluginRegistry",
    "PlatformRunner",
]
