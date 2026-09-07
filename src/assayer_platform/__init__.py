"""Domain-neutral contracts and execution kernel for Assayer plugins."""

from .agent_contract import (
    AGENT_CONTRACT_CANONICALIZATION_VERSION,
    AGENT_CONTRACT_SCHEMA_DIALECT,
    AgentContractBundle,
)

from .contract import (
    Artifact,
    CapabilityProfile,
    CapabilityProviderDescriptor,
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
    ProviderCollectionResult,
    ProviderCapability,
    ProviderEvidenceExpectation,
    ProviderFact,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    ReviewCheckpoint,
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
from .evaluation import load_evaluation_corpus, validate_evaluation_corpus
from .plugin_registry import PluginRegistration, PluginRegistry
from .plugin_discovery import builtin_plugin_registry, installed_plugin_registry
from .plugin_installation import PluginInstallationStore
from .plugin_lifecycle import (
    PluginLifecycleManager,
    discover_plugin_registry,
    load_registration,
)
from .provider_registry import (
    ProviderRegistration,
    ProviderRegistry,
    load_provider_descriptor,
    validate_provider_descriptor,
)
from .provider_catalog import builtin_provider_registry, installed_provider_registry
from .provider_conformance import (
    ProviderConformanceIssue,
    ProviderConformanceReport,
    inspect_provider_registration,
    inspect_provider_registrations,
    require_provider_registration_conformance,
)
from .capability_negotiation import CapabilityNegotiation, CapabilityNegotiator
from .provider_execution import BoundCapabilityProvider
from .provider_package_conformance import inspect_provider_package
from .provider_installation_conformance import inspect_provider_installation
from .parallel_execution import ParallelExecutionPlan, ParallelExecutionPlanner
from .platform_performance import build_platform_performance_bill, render_platform_performance_bill
from .canonical_result import (
    build_canonical_result,
    canonical_ledger_bytes,
    render_canonical_result,
    validate_canonical_result,
)
from .result_conformance import (
    ResultConformanceIssue,
    ResultConformanceReport,
    inspect_result_conformance,
)
from .result_delivery import StagedResultDocument
from .actionable_result import (
    build_actionable_result, extract_result_delivery,
    extract_result_delivery_bundle, validate_result_delivery,
)
from .evidence_claim import validate_evidence_claims
from .conformance import (
    PluginConformanceIssue,
    PluginConformanceReport,
    inspect_plugin_registration,
    inspect_plugin_registrations,
    inspect_plugin_lifecycle,
    inspect_plugin_package,
    require_plugin_registration_conformance,
)
from .runner import PlatformRunner
from .state_machine import (
    RUN_TERMINAL_STATES, RUN_WORKFLOW_STATES, WORK_ITEM_STATES,
    derive_work_item_state, validate_terminal_transition, validate_workflow,
    validate_workflow_transition,
)
from .ownership import RunOwnership
from .observability import build_platform_observability, render_platform_observability
from .layers import (
    DeliveryObserver, EvidenceCollectionProvider, NavigationProvider,
    ReviewProtocol,
)
from .evidence_collection import EvidenceCollectionPager
from .review_protocol import REVIEW_DISPOSITIONS, build_review_task, validate_review_submission
from .delivery_observer import PlatformDeliveryObserver
from .evidence_graph import (
    CANDIDATE_DISPOSITIONS, EvidenceCandidate, EvidenceGraph, FindingRecord,
    RootCauseGroup, build_candidate_evidence_graph,
    build_candidate_envelope, canonicalize_candidate,
    candidate_dispositions_from_decisions, conservative_root_cause_groups,
    render_candidate_evidence_graph, stable_candidate_fingerprint,
    validate_candidate_evidence_graph_projection,
)
from .identity import digest_bytes, document_state_digest
from .source_chunking import SOURCE_CHUNK_LIMIT, build_source_chunks, source_ref_for_line
from .source_fact_index import build_source_fact_index

__all__ = [
    "AGENT_CONTRACT_CANONICALIZATION_VERSION",
    "AGENT_CONTRACT_SCHEMA_DIALECT",
    "AgentContractBundle",
    "Artifact",
    "ArtifactPublisher",
    "CapabilityProfile",
    "CapabilityProviderDescriptor",
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
    "ProviderCollectionResult",
    "ProviderCapability",
    "ProviderEvidenceExpectation",
    "ProviderFact",
    "ProviderFailure",
    "ProviderRequest",
    "ProviderResponse",
    "ReviewCheckpoint",
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
    "load_evaluation_corpus",
    "validate_evaluation_corpus",
    "PluginRegistration",
    "PluginRegistry",
    "builtin_plugin_registry",
    "installed_plugin_registry",
    "PluginInstallationStore",
    "PluginLifecycleManager",
    "discover_plugin_registry",
    "load_registration",
    "ProviderRegistration",
    "ProviderRegistry",
    "builtin_provider_registry",
    "installed_provider_registry",
    "load_provider_descriptor",
    "validate_provider_descriptor",
    "ProviderConformanceIssue",
    "ProviderConformanceReport",
    "inspect_provider_registration",
    "inspect_provider_registrations",
    "require_provider_registration_conformance",
    "CapabilityNegotiation",
    "CapabilityNegotiator",
    "BoundCapabilityProvider",
    "inspect_provider_package",
    "inspect_provider_installation",
    "ParallelExecutionPlan",
    "ParallelExecutionPlanner",
    "build_platform_performance_bill",
    "render_platform_performance_bill",
    "build_canonical_result",
    "canonical_ledger_bytes",
    "render_canonical_result",
    "validate_canonical_result",
    "ResultConformanceIssue",
    "ResultConformanceReport",
    "inspect_result_conformance",
    "StagedResultDocument",
    "extract_result_delivery",
    "extract_result_delivery_bundle",
    "validate_result_delivery",
    "build_actionable_result",
    "validate_evidence_claims",
    "PluginConformanceIssue",
    "PluginConformanceReport",
    "inspect_plugin_registration",
    "inspect_plugin_registrations",
    "inspect_plugin_lifecycle",
    "inspect_plugin_package",
    "require_plugin_registration_conformance",
    "PlatformRunner",
    "RUN_TERMINAL_STATES",
    "RUN_WORKFLOW_STATES",
    "WORK_ITEM_STATES",
    "derive_work_item_state",
    "validate_terminal_transition",
    "validate_workflow",
    "validate_workflow_transition",
    "RunOwnership",
    "build_platform_observability",
    "render_platform_observability",
    "NavigationProvider",
    "EvidenceCollectionProvider",
    "ReviewProtocol",
    "DeliveryObserver",
    "EvidenceCollectionPager",
    "REVIEW_DISPOSITIONS",
    "build_review_task",
    "validate_review_submission",
    "PlatformDeliveryObserver",
    "CANDIDATE_DISPOSITIONS",
    "EvidenceCandidate",
    "EvidenceGraph",
    "FindingRecord",
    "RootCauseGroup",
    "build_candidate_evidence_graph",
    "candidate_dispositions_from_decisions",
    "conservative_root_cause_groups",
    "render_candidate_evidence_graph",
    "stable_candidate_fingerprint",
    "validate_candidate_evidence_graph_projection",
    "build_candidate_envelope",
    "canonicalize_candidate",
    "digest_bytes",
    "document_state_digest",
    "SOURCE_CHUNK_LIMIT",
    "build_source_chunks",
    "source_ref_for_line",
    "build_source_fact_index",
]
