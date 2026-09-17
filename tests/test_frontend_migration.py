"""Frontend is an ordinary data-only plugin with no private runtime."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
from assayer_platform.declaration_compiler import compile_plugin_contract


class FrontendDataOnlyMigrationTests(unittest.TestCase):
    def test_frontend_source_and_compiled_artifact_contain_no_python(self):
        source = Path("plugins/frontend-audit")
        self.assertEqual(tuple(source.rglob("*.py")), ())
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            compile_plugin_contract(source, generated)
            self.assertEqual(tuple(generated.rglob("*.py")), ())
            self.assertEqual(
                {path.name for path in generated.iterdir()},
                {"compiled-plugin.json"},
            )

    def test_frontend_contract_contains_only_domain_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), generated)
            contract = load_compiled_plugin_contract(generated)

        self.assertEqual(contract.plugin_id, "assayer.frontend-audit")
        self.assertEqual(contract.input_kind, "browser_snapshot")
        self.assertEqual(contract.payload["checks"][0]["id"], "FUA-01")
        self.assertNotIn("registration", contract.payload)
        self.assertNotIn("runtime", contract.payload)
        self.assertNotIn("domainResultContract", contract.payload)


if __name__ == "__main__":
    unittest.main()
