from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_plugin_source_boundaries import find_violations


class PluginSourceBoundaryTests(unittest.TestCase):
    def test_current_ordinary_plugin_is_declaration_only(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(find_violations(root), ())

    def test_python_author_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plugins" / "example"
            plugin.mkdir(parents=True)
            (plugin / "plugin.yaml").write_text("id: example.plugin\n", encoding="utf-8")
            (plugin / "plugin.py").write_text("def scan(document):\n    return []\n", encoding="utf-8")
            violations = find_violations(root)
        messages = tuple(violation.message for violation in violations)
        self.assertTrue(any("undeclared author file" in message for message in messages))
        self.assertTrue(any("forbidden surface" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
