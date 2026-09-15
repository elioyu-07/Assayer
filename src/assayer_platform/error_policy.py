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
    "DOMAIN_RESULT_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_domain_result",
    ),
    "DOMAIN_EVIDENCE_REFERENCE_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_domain_result",
    ),
    "PLUGIN_SEMANTIC_INPUT_INVALID": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_agent_input",
    ),
    "INVALID_COMMON_REVIEW": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "DOMAIN_INVARIANT_VIOLATION": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "INCOMPLETE_REVIEW_SUBMISSION": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "UNKNOWN_REVIEW_ITEM": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "UNKNOWN_REVIEW_EVIDENCE": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "REVIEW_KIND_MISMATCH": BoundaryErrorPolicy(
        "agent_input", "agent_correction", "correct_common_review",
    ),
    "AGENT_SEMANTIC_TASK_STALE": BoundaryErrorPolicy(
        "contract_state", "refresh_boundary", "refresh_semantic_boundary",
    ),
    "STALE_EVIDENCE_HANDLE": BoundaryErrorPolicy(
        "contract_state", "refresh_boundary", "refresh_semantic_boundary",
    ),
    "CROSS_TASK_EVIDENCE_HANDLE": BoundaryErrorPolicy(
        "contract_state", "refresh_boundary", "refresh_semantic_boundary",
    ),
    "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLUGIN_CONTRACT_VIOLATION": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLUGIN_RUNTIME_FAILURE": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "INVARIANT_EXECUTION_FAILED": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "INVALID_INVARIANT_RESULT": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "INVALID_COMPILED_REVIEW_PLAN": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "COMPILED_REVIEW_EVIDENCE_OUT_OF_SCOPE": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLUGIN_REPORT_FAILED": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLUGIN_SUMMARY_FAILED": BoundaryErrorPolicy(
        "plugin", "none", "read_terminal_result", True,
    ),
    "PLATFORM_CONTRACT_STATE_INVALID": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "PLATFORM_INTERNAL_ERROR": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "INVALID_COMMON_REVIEW_TASK": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "INVALID_REVIEW_BINDING": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "INVALID_BATCH_VERDICT": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "UNKNOWN_REVIEW_ATOM": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "UNKNOWN_REVIEW_BATCH": BoundaryErrorPolicy(
        "platform", "none", "read_terminal_result", True,
    ),
    "AGENT_CORRECTION_BUDGET_EXHAUSTED": BoundaryErrorPolicy(
        "agent_input", "none", "read_terminal_result", True,
    ),
    "RERUN_USER_CONFIRMATION_REQUIRED": BoundaryErrorPolicy(
        "agent_input", "none", "request_user_confirmation",
    ),
    "RUN_RESTART_REQUIRED": BoundaryErrorPolicy(
        "contract_state", "none", "start_new_run",
    ),
    "UNSUPPORTED_PROTOCOL": BoundaryErrorPolicy(
        "agent_input", "none", "stop", True,
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
        return BoundaryErrorPolicy(
            "agent_input", "none", "read_terminal_result", True,
        )
    if code in _PLUGIN_STOP_CODES:
        return BoundaryErrorPolicy(
            "plugin", "none", "read_terminal_result", True,
        )
    return _POLICIES.get(code, BoundaryErrorPolicy("platform", "none", "stop"))


__all__ = [
    "BoundaryErrorPolicy", "DEFAULT_AGENT_CORRECTION_BUDGET",
    "boundary_error_policy",
]
