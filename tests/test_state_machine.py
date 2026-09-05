from __future__ import annotations

import unittest

from assayer_platform import (
    PlatformContractError,
    derive_work_item_state,
    validate_terminal_transition,
    validate_workflow_transition,
)


def workflow(state: str, *, phase: str = "discovery", can_finish: bool = False, next_step: str | None = "advance"):
    return {
        "state": state,
        "phase": "finished" if state in {"completed", "partial", "failed"} else phase,
        "canFinish": True if state in {"completed", "partial", "failed"} else can_finish,
        "requiredNextStep": None if state in {"completed", "partial", "failed"} else next_step,
        "remaining": {},
    }


class StateMachineTest(unittest.TestCase):
    def test_valid_active_transitions(self):
        validate_workflow_transition(workflow("running"), workflow("awaiting_agent_decision", phase="semantic_review"))
        validate_workflow_transition(workflow("awaiting_agent_decision"), workflow("running"))
        validate_workflow_transition(workflow("running"), workflow("ready_to_finish", phase="closeout", can_finish=True, next_step="finish_plugin_run"))
        validate_workflow_transition(workflow("ready_to_finish", phase="closeout", can_finish=True, next_step="finish_plugin_run"), workflow("completed"))

    def test_illegal_transition_is_rejected(self):
        with self.assertRaises(PlatformContractError) as error:
            validate_workflow_transition(workflow("blocked", phase="recovery", next_step="recover_work_item"), workflow("awaiting_agent_decision", phase="semantic_review"))
        self.assertEqual(error.exception.code, "INVALID_WORKFLOW_TRANSITION")

    def test_terminal_transition_requires_ready_state_for_completed(self):
        with self.assertRaises(PlatformContractError):
            validate_terminal_transition("awaiting_agent_decision", "completed")
        validate_terminal_transition("ready_to_finish", "completed")
        validate_terminal_transition("blocked", "partial")

    def test_work_item_state_is_derived_from_durable_facts(self):
        self.assertEqual(derive_work_item_state(discovered=True, inspected=False, decided=False, failed=False), "discovered")
        self.assertEqual(derive_work_item_state(discovered=True, inspected=True, decided=False, failed=False), "investigating")
        self.assertEqual(derive_work_item_state(discovered=True, inspected=True, decided=False, failed=True), "blocked")
        self.assertEqual(derive_work_item_state(discovered=True, inspected=True, decided=True, failed=False), "decided")


if __name__ == "__main__":
    unittest.main()
