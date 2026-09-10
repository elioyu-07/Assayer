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
from .evidence_handles import (  # noqa: F401
    EvidenceHandle,
    EvidenceHandleRegistry,
)
from .evidence_reference import (  # noqa: F401
    host_evidence_references,
    resolve_evidence_references,
    validate_domain_evidence_references,
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
