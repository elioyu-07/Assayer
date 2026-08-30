import unittest

from assayer_host import ActionSafetyPolicy, NetworkRequest


class ActionSafetyPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = ActionSafetyPolicy()
        self.origin = "https://test.example.com"

    def classify(self, **overrides):
        values = {"method": "GET", "url": f"{self.origin}/api/orders"}
        values.update(overrides)
        return self.policy.classify_request(NetworkRequest(**values), self.origin)

    def test_same_origin_read_is_allowed(self):
        self.assertEqual(self.classify().outcome, "allowed")

    def test_write_methods_are_blocked(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.classify(method=method).outcome, "blocked")

    def test_mutation_multipart_and_active_transports_are_blocked(self):
        checks = (
            {"graphql_operation_type": "mutation"},
            {"content_type": "multipart/form-data"},
            {"transport": "beacon"},
            {"transport": "websocket"},
            {"transport": "sse"},
            {"transport": "service_worker"},
        )
        for values in checks:
            with self.subTest(values=values):
                self.assertEqual(self.classify(**values).outcome, "blocked")

    def test_cross_origin_is_blocked(self):
        decision = self.classify(url="https://other.example.com/api/orders")
        self.assertEqual(decision.code, "CROSS_ORIGIN_BLOCKED")

    def test_unattributable_request_is_unknown(self):
        self.assertEqual(self.classify(attributable=False).outcome, "unknown")
        self.assertEqual(self.classify(transport="service_worker", attributable=False).outcome, "unknown")

    def test_already_sent_request_is_never_treated_as_blocked(self):
        self.assertEqual(self.classify(method="POST", sent=True).outcome, "already_sent")
        self.assertEqual(self.classify(method="GET", sent=True).outcome, "unknown")

    def test_selector_and_script_parameters_are_blocked(self):
        for parameters in ({"selector": "#save"}, {"script": "click()"}):
            with self.subTest(parameters=parameters):
                self.assertEqual(self.policy.action_decision("focus", "观察", parameters).outcome, "blocked")
        self.assertEqual(self.policy.action_decision("focus", "观察", {"nested": {"selector": "#save"}}).outcome, "blocked")

    def test_declared_origin_cannot_hide_cross_origin_url(self):
        decision = self.classify(url="https://other.example.com/api/orders", origin=self.origin)
        self.assertEqual(decision.code, "CROSS_ORIGIN_BLOCKED")


if __name__ == "__main__":
    unittest.main()
