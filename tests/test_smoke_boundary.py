import unittest

from assayer_host.browser_runtime import BrowserHostRuntime


class SmokeBoundaryTest(unittest.TestCase):
    def test_runtime_has_no_rule_evaluation_engine_or_audit_method(self):
        self.assertFalse(hasattr(BrowserHostRuntime, "audit"))
        self.assertFalse(hasattr(BrowserHostRuntime, "_rule_engine"))


if __name__ == "__main__":
    unittest.main()
