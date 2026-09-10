"""Assayer Plugin SDK: the stable, platform-independent plugin contract.

This package is the only contract a domain plugin or capability provider needs.
It MUST NOT import :mod:`assayer_platform`; the platform depends on the SDK, not
the other way around. The modules that still live in ``assayer_platform`` are
re-exported from here during the SDK extraction migration.
"""

from __future__ import annotations

from .agent_contract import (
    DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION,
    DomainResultContract,
)
from .actionable_result import (  # noqa: F401
    build_actionable_result,
    extract_result_delivery,
    extract_result_delivery_bundle,
    validate_result_delivery,
)
from .contract import *  # noqa: F401,F403
from .contract import (  # noqa: F401
    DECISION_STATES,
    FINDING_STATES,
    PLATFORM_API_VERSION,
    RECOVERY_STATES,
    CheckContract,
    CommitReceipt,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    ExecutionProfile,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PluginManifest,
    ReviewCheckpoint,
    WorkItem,
)
from .evidence_graph import (  # noqa: F401
    CANDIDATE_DISPOSITIONS,
    EvidenceCandidate,
    EvidenceGraph,
    FindingRecord,
    RootCauseGroup,
    build_candidate_envelope,
    build_candidate_evidence_graph,
    canonicalize_candidate,
    candidate_dispositions_from_decisions,
    conservative_root_cause_groups,
    render_candidate_evidence_graph,
    stable_candidate_fingerprint,
    validate_candidate_evidence_graph_projection,
)
from .evidence_handles import (  # noqa: F401
    EvidenceHandle,
    EvidenceHandleRegistry,
)
from .evidence_claim import validate_evidence_claims  # noqa: F401
from .evidence_reference import (  # noqa: F401
    host_evidence_references,
    resolve_evidence_references,
    validate_domain_evidence_references,
)
from .identity import digest_bytes, document_state_digest  # noqa: F401
from .manifest import (  # noqa: F401
    load_plugin_manifest,
    validate_plugin_manifest,
)
from .evaluation import (  # noqa: F401
    load_evaluation_corpus,
    validate_evaluation_corpus,
)
from .plugin_compatibility import (  # noqa: F401
    HOST_PROTOCOL_CAPABILITIES,
    HOST_PROTOCOL_VERSION,
    HOST_SDK_VERSION,
    HOST_SUPPORTED_PROTOCOL_VERSIONS,
    CompatibilityResult,
    PluginCompatibility,
    negotiate_plugin_compatibility,
)
from .plugin_sdk import (  # noqa: F401
    ENTITY_ID_PATTERN,
    PLUGIN_CONTRACT_VIOLATION,
    JsonScalar,
    JsonValue,
    PluginContractError,
    to_json_value,
    validate_entity_id,
)
from .registration import PluginRegistration  # noqa: F401
from .review_protocol import (  # noqa: F401
    REVIEW_DISPOSITIONS,
    build_review_task,
    validate_review_submission,
)
from .source_chunking import (  # noqa: F401
    SOURCE_CHUNK_LIMIT,
    build_source_chunks,
    source_ref_for_line,
)
from .source_fact_index import build_source_fact_index  # noqa: F401
