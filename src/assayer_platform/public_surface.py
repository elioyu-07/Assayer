"""Authoritative public SDK surface for Assayer plugins.

This module is the single source of truth for which ``assayer_platform``
modules and symbols a plugin may import. It is the enforceable form of the
Platform--Plugin Boundary Contract v1 section 5 ("Platform Public Surface").

A name becomes public only after it has a documented ownership, a version,
and a conformance test. To widen this surface, update the mapping below and
the boundary contract together, then add a conformance fixture that exercises
the newly public symbol.
"""

from __future__ import annotations

PUBLIC_SURFACE_VERSION = "1.1.0"

PUBLIC_SURFACE: dict[str, frozenset[str]] = {
    "assayer_platform": frozenset({
        "AgentContractBundle",
        "PluginRegistration",
        "PlatformContext",
    }),
    "assayer_platform.contract": frozenset({
        "CheckContract",
        "CommitReceipt",
        "DecisionProposal",
        "DimensionObservation",
        "EvidenceRecord",
        "Finding",
        "InvestigationPacket",
        "PlatformContext",
        "PlatformContractError",
        "PluginManifest",
        "ReviewCheckpoint",
        "WorkItem",
    }),
    "assayer_platform.evaluation": frozenset({
        "load_evaluation_corpus",
        "validate_evaluation_corpus",
    }),
    "assayer_platform.registry": frozenset({"load_plugin_manifest"}),
    "assayer_platform.actionable_result": frozenset({"build_actionable_result"}),
    "assayer_platform.review_protocol": frozenset({"validate_review_submission"}),
    "assayer_platform.evidence_graph": frozenset({
        "build_candidate_evidence_graph",
        "build_candidate_envelope",
        "canonicalize_candidate",
        "render_candidate_evidence_graph",
        "validate_candidate_evidence_graph_projection",
    }),
    "assayer_platform.identity": frozenset({
        "digest_bytes",
        "document_state_digest",
    }),
    "assayer_platform.source_chunking": frozenset({
        "build_source_chunks",
        "source_ref_for_line",
    }),
    "assayer_platform.source_fact_index": frozenset({"build_source_fact_index"}),
}


def is_public_module(module_name: str) -> bool:
    """Return whether ``module_name`` is a public platform module."""
    return module_name in PUBLIC_SURFACE


def public_symbols(module_name: str) -> frozenset[str]:
    """Return the public symbols of a platform module (empty if non-public)."""
    return PUBLIC_SURFACE.get(module_name, frozenset())
