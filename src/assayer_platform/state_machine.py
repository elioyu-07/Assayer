"""Domain-neutral Run and WorkItem lifecycle rules.

The state machine is deliberately independent from transports and plugins.  It
validates the public workflow boundary while the interactive controller derives
the next state from durable facts.
"""

from __future__ import annotations

from collections.abc import Mapping

from .contract import PlatformContractError


RUN_WORKFLOW_STATES = frozenset({
    "running", "awaiting_agent_decision", "blocked", "ready_to_finish",
    "completed", "partial", "failed",
})
RUN_TERMINAL_STATES = frozenset({"completed", "partial", "failed"})
WORK_ITEM_STATES = frozenset({"discovered", "investigating", "blocked", "decided"})

_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": frozenset({"running", "awaiting_agent_decision", "blocked", "ready_to_finish", "partial", "failed"}),
    "awaiting_agent_decision": frozenset({"awaiting_agent_decision", "running", "blocked", "ready_to_finish", "partial", "failed"}),
    "blocked": frozenset({"blocked", "running", "partial", "failed"}),
    "ready_to_finish": frozenset({"ready_to_finish", "completed", "partial", "failed"}),
    "completed": frozenset({"completed"}),
    "partial": frozenset({"partial"}),
    "failed": frozenset({"failed"}),
}


def validate_workflow(workflow: Mapping[str, object]) -> None:
    """Validate the shape and invariants of one persisted workflow boundary."""
    if not isinstance(workflow, Mapping):
        raise PlatformContractError("INVALID_WORKFLOW", "Workflow boundary must be an object")
    state = workflow.get("state")
    phase = workflow.get("phase")
    if state not in RUN_WORKFLOW_STATES:
        raise PlatformContractError("INVALID_WORKFLOW_STATE", "Workflow state is not supported")
    if not isinstance(phase, str) or not phase:
        raise PlatformContractError("INVALID_WORKFLOW", "Workflow phase is required")
    can_finish = workflow.get("canFinish")
    if not isinstance(can_finish, bool):
        raise PlatformContractError("INVALID_WORKFLOW", "Workflow canFinish must be boolean")
    required_next = workflow.get("requiredNextStep")
    if state in RUN_TERMINAL_STATES:
        if phase != "finished" or can_finish is not True or required_next is not None:
            raise PlatformContractError(
                "INVALID_TERMINAL_WORKFLOW",
                "A terminal workflow must be finished, finishable, and have no next step",
            )
    elif can_finish and state != "ready_to_finish":
        raise PlatformContractError(
            "INVALID_WORKFLOW", "Only a terminal workflow or ready_to_finish state may be finishable",
        )
    elif not isinstance(required_next, str) or not required_next:
        raise PlatformContractError("INVALID_WORKFLOW", "An active workflow requires a next step")
    remaining = workflow.get("remaining")
    if remaining is not None and not isinstance(remaining, Mapping):
        raise PlatformContractError("INVALID_WORKFLOW", "Workflow remaining counts must be an object")


def validate_workflow_transition(previous: Mapping[str, object], current: Mapping[str, object]) -> None:
    """Reject an illegal Host workflow transition before persistence."""
    validate_workflow(previous)
    validate_workflow(current)
    old_state = str(previous["state"])
    new_state = str(current["state"])
    if new_state not in _RUN_TRANSITIONS[old_state]:
        raise PlatformContractError(
            "INVALID_WORKFLOW_TRANSITION",
            f"Workflow cannot transition from {old_state} to {new_state}",
        )


def validate_terminal_transition(current_state: str, terminal_state: str) -> None:
    """Validate a terminal transition independently of transport details."""
    if current_state not in RUN_WORKFLOW_STATES:
        raise PlatformContractError("INVALID_WORKFLOW_STATE", "Current workflow state is not supported")
    if terminal_state not in RUN_TERMINAL_STATES:
        raise PlatformContractError("INVALID_RUN_STATUS", "Terminal Run status is not supported")
    if terminal_state not in _RUN_TRANSITIONS[current_state]:
        raise PlatformContractError(
            "INVALID_WORKFLOW_TRANSITION",
            f"Workflow cannot finish from {current_state} as {terminal_state}",
        )


def derive_work_item_state(
    *, discovered: bool, inspected: bool, decided: bool, failed: bool,
) -> str:
    """Derive one WorkItem state from durable facts, never Agent wording."""
    if not discovered:
        raise PlatformContractError("INVALID_WORK_ITEM_STATE", "A WorkItem must be discovered before it can advance")
    if decided:
        if not inspected:
            raise PlatformContractError("INVALID_WORK_ITEM_STATE", "A decided WorkItem must have an investigation")
        return "decided"
    if failed:
        return "blocked"
    if inspected:
        return "investigating"
    return "discovered"


__all__ = [
    "RUN_TERMINAL_STATES", "RUN_WORKFLOW_STATES", "WORK_ITEM_STATES",
    "derive_work_item_state", "validate_terminal_transition",
    "validate_workflow", "validate_workflow_transition",
]
