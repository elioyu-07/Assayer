import unittest

from assayer_host.runtime_rules import RuleEvaluationEngine


class RuntimeRuleEvaluationTest(unittest.TestCase):
    def test_unknown_rule_fails_closed_without_changing_orchestrator(self):
        result = RuleEvaluationEngine().evaluate(
            {"ruleId": "FUA-999", "version": "1.0.0"},
            {"payload": {"content": {}}},
        )
        self.assertEqual(result.result, "needs_review")
        self.assertEqual(result.blocker["code"], "RULE_EVALUATOR_UNAVAILABLE")

    def test_fua10_uses_only_structured_rule_facts(self):
        evidence = {"payload": {"content": {
            "controls": [
                {"tag": "input", "semanticAction": "other"},
                {"tag": "button", "semanticAction": "query"},
                {"tag": "button", "semanticAction": "reset"},
            ],
            "bindingSignals": {"pageListCount": 1, "sameContainerList": True},
        }}}
        result = RuleEvaluationEngine().evaluate(
            {"ruleId": "FUA-10", "version": "1.0.0"}, evidence,
        )
        self.assertEqual(result.result, "scanned_no_issue")
        self.assertIsNone(result.blocker)


if __name__ == "__main__":
    unittest.main()
