import unittest

from assayer_host.locale_terms import inject_action_labels


class LocaleTermTests(unittest.TestCase):
    def test_probe_placeholders_are_replaced_with_explicit_locale_data(self):
        script = "const reset = __ASSAYER_RESET_ACTION_LABELS__; const query = __ASSAYER_QUERY_ACTION_LABELS__;"
        rendered = inject_action_labels(script)
        self.assertNotIn("__ASSAYER_", rendered)
        self.assertIn("\\u91cd\\u7f6e", rendered)
        self.assertIn("\\u67e5\\u8be2", rendered)


if __name__ == "__main__":
    unittest.main()
