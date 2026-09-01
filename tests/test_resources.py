from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_host.resources import default_resource_root


class RuntimeResourceTests(unittest.TestCase):
    def test_explicit_resource_root_must_be_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ASSAYER_RESOURCE_ROOT": tmp}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "does not contain"):
                    default_resource_root()

    def test_explicit_resource_root_is_relocatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "schemas").mkdir()
            (root / "rules").mkdir()
            (root / "rules" / "registry.json").write_text("{}")
            with patch.dict(os.environ, {"ASSAYER_RESOURCE_ROOT": tmp}, clear=False):
                self.assertEqual(default_resource_root(), root.resolve())


if __name__ == "__main__":
    unittest.main()
