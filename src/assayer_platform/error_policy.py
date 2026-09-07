"""Central ownership and retry policy for executable Agent-boundary errors."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_AGENT_CORRECTION_BUDGET = 1


@dataclass(frozen=True)
class BoundaryErrorPolicy:
    owner: str
    retry_disposition: str
    required_next_step: str
    terminal_on_rejection: bool = False


_POLICIES = {
    "AGENT_CONTRACT_ENVELOPE_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_agent_input",
    ),
    "AGENT_CONTRACT_INPUT_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_agent_input",
    ),
    "PLUGIN_SEMANTIC_INPUT_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_agent_input",
    ),
    "AGENT_CONTRACT_STALE": BoundaryErrorPolicy(
        "contract_state", "refresh_boundary", "refresh_semantic_boundary",
    ),
    "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLUGIN_RUNTIME_FAILURE": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLATFORM_CONTRACT_STATE_INVALID": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "AGENT_CORRECTION_BUDGET_EXHAUSTED": BoundaryErrorPolicy(
        "agent_input", "none", "read_terminal_result", True,
    ),
}

_AGENT_INPUT_STOP_CODES = frozenset({
    "COMMIT_CONFLICT",
    "EVIDENCE_COLLECTION_NOT_REVIEWABLE",
    "INVALID_DECISION",
    "INVALID_FINDING",
    "INVALID_REVIEW_CHECKPOINT",
    "REVIEW_CHECKPOINT_CONFLICT",
    "REVIEW_CHECKPOINT_INCOMPLETE",
    "UNKNOWN_EVIDENCE_COLLECTION",
    "UNKNOWN_EVIDENCE_COLLECTION_ITEM",
    "UNKNOWN_INVESTIGATION",
    "UNKNOWN_REVIEW_CHECKPOINT",
    "WORKFLOW_INPUT_CONFLICT",
})

_PLUGIN_STOP_CODES = frozenset({
    "REVIEW_CHECKPOINT_ASSEMBLY_FAILED",
    "REVIEW_CHECKPOINT_UNSUPPORTED",
    "REVIEW_CHECKPOINT_VALIDATION_FAILED",
    "REVIEW_CHECKPOINT_VALIDATION_UNSUPPORTED",
})


def boundary_error_policy(code: str) -> BoundaryErrorPolicy:
    """Return a closed policy; unknown errors are never Agent-retryable."""
    if code in _AGENT_INPUT_STOP_CODES:
        return BoundaryErrorPolicy("agent_input", "none", "stop")
    if code in _PLUGIN_STOP_CODES:
        return BoundaryErrorPolicy("plugin", "none", "stop")
    return _POLICIES.get(code, BoundaryErrorPolicy("platform", "none", "stop"))


__all__ = [
    "BoundaryErrorPolicy", "DEFAULT_AGENT_CORRECTION_BUDGET",
    "boundary_error_policy",
]
