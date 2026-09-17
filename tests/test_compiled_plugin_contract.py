from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform.compiled_plugin_contract import (
    contract_digest,
    load_compiled_plugin_contract,
    validate_compiled_plugin_contract,
)
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_plugin_sdk.contract import PlatformContractError


class CompiledPluginContractTests(unittest.TestCase):
    def test_frontend_contract_is_immutable_and_self_authenticating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            contract = load_compiled_plugin_contract(root)

        self.assertEqual(contract.plugin_id, "assayer.frontend-audit")
        self.assertEqual(contract.digest, contract_digest(contract.payload))
        with self.assertRaises(TypeError):
            contract.payload["plugin"]["id"] = "changed"

    def test_contract_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            path = root / "compiled-plugin.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["plugin"]["name"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PlatformContractError) as rejected:
                load_compiled_plugin_contract(path)

        self.assertEqual(rejected.exception.code, "COMPILED_PLUGIN_CONTRACT_INVALID")

    def test_schema_rejects_executable_registration_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compile_plugin_contract(Path("plugins/frontend-audit"), root)
            payload = json.loads((root / "compiled-plugin.json").read_text(encoding="utf-8"))
        payload["registration"] = "plugin:registration"
        payload["contractDigest"] = contract_digest(payload)

        issues = validate_compiled_plugin_contract(payload)

        self.assertTrue(any("registration" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
