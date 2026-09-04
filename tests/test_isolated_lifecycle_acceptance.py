from __future__ import annotations

import io
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "isolated_lifecycle_acceptance.py"
SPEC = importlib.util.spec_from_file_location("isolated_lifecycle_acceptance", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("isolated lifecycle acceptance script cannot be loaded")
isolated_lifecycle_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(isolated_lifecycle_acceptance)


class IsolatedLifecycleAcceptanceTests(unittest.TestCase):
    def test_full_isolated_lifecycle_uses_fake_codex_and_cleans_runtime(self):
        result = isolated_lifecycle_acceptance.run()
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["publishable"])
        self.assertEqual(
            [item["operation"] for item in result["steps"]],
            ["upgrade", "rollback", "uninstall"],
        )
        self.assertTrue(all(item["transactionStatus"] == "completed" for item in result["steps"]))
        self.assertEqual(result["cleanup"]["status"], "removed")
        self.assertEqual(result["catalog"]["installedCount"], 0)
        self.assertEqual(result["catalog"]["mutationCount"], 5)
        self.assertNotIn(tempfile.gettempdir(), json.dumps(result))

    def test_failure_output_is_sanitized(self):
        output = io.StringIO()
        with patch.object(
            isolated_lifecycle_acceptance, "run",
            side_effect=RuntimeError("secret /private/runtime"),
        ), redirect_stdout(output):
            code = isolated_lifecycle_acceptance.main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
