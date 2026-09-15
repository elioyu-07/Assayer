from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from assayer_platform import (
    DomainResultContract,
    InteractivePluginController,
    PluginRegistry,
    PlatformContractError,
    PluginRegistration,
    load_plugin_manifest,
)
from assayer_plugin_sdk import PluginCompatibility, negotiate_plugin_compatibility
from tests.helpers import config_quality_registration


CONFIG_DOMAIN_RESULT_CONTRACT = DomainResultContract(
    contract_id="test.config-quality.review",
    contract_version="1.0.0",
    check_id="CFG-001",
    check_version="1.0.0",
    result_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["result", "findings", "reason"],
        "properties": {
            "result": {"enum": ["scanned_no_issue", "needs_review"]},
            "findings": {"type": "array", "minItems": 1},
            "reason": {"type": "string", "minLength": 1},
        },
    },
    semantic_instructions_path="tests/config-quality.md",
    semantic_instructions_sha256="0" * 64,
)


class PluginCompatibilityTests(unittest.TestCase):
    def test_manifest_declaration_is_loaded_before_plugin_execution(self):
        source = Path(__file__).parent / "fixtures/plugins/minimal/src/minimal_plugin/manifest.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["compatibility"] = {
            "protocolMinVersion": "1.2.0",
            "protocolMaxVersion": "1.2.0",
            "sdkMinVersion": "0.1.2",
            "sdkMaxVersion": "0.1.2",
            "capabilities": ["domain_result"],
            "domainContractVersion": "1.0.0",
        }
        registration = PluginRegistration(load_plugin_manifest(value))
        self.assertEqual(registration.compatibility.protocol_min_version, "1.2.0")
        self.assertEqual(registration.compatibility.capabilities, frozenset({"domain_result"}))

        with self.assertRaises(PlatformContractError) as mismatch:
            PluginRegistration(
                registration.manifest,
                compatibility=PluginCompatibility(
                    protocol_min_version="1.0.0",
                    protocol_max_version="1.0.0",
                ),
            )
        self.assertEqual(
            mismatch.exception.code, "PLUGIN_COMPATIBILITY_IDENTITY_MISMATCH",
        )

    def test_protocol_ranges_are_rejected(self):
        with self.assertRaises(PlatformContractError) as error:
            PluginCompatibility(protocol_min_version="1.1.0", protocol_max_version="1.2.0")
        self.assertEqual(error.exception.code, "PLUGIN_COMPATIBILITY_RANGE_UNSUPPORTED")

    def test_missing_declaration_is_rejected(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(None)
        self.assertEqual(error.exception.code, "PLUGIN_COMPATIBILITY_REQUIRED")

    def test_manifest_requires_explicit_compatibility(self):
        source = Path(__file__).parent / "fixtures/plugins/minimal/src/minimal_plugin/manifest.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value.pop("compatibility")
        with self.assertRaises(PlatformContractError) as error:
            load_plugin_manifest(value)
        self.assertEqual(error.exception.code, "PLUGIN_COMPATIBILITY_REQUIRED")

    def test_older_protocol_is_rejected_without_adapter(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(
                PluginCompatibility(protocol_min_version="1.1.0", protocol_max_version="1.1.0")
            )
        self.assertEqual(error.exception.code, "PLUGIN_PROTOCOL_INCOMPATIBLE")

    def test_unsupported_protocol_fails_closed(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(
                PluginCompatibility(protocol_min_version="2.0.0", protocol_max_version="2.0.0")
            )
        self.assertEqual(error.exception.code, "PLUGIN_PROTOCOL_INCOMPATIBLE")

    def test_missing_host_capability_fails_closed(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(
                PluginCompatibility(capabilities=frozenset({"future_capability"}))
            )
        self.assertEqual(error.exception.code, "PLUGIN_CAPABILITY_INCOMPATIBLE")

    def test_controller_rejects_before_run_state_is_created(self):
        base = config_quality_registration()
        manifest = replace(
            base.manifest,
            compatibility=PluginCompatibility(
                protocol_min_version="2.0.0",
                protocol_max_version="2.0.0",
                sdk_min_version="0.1.2",
                sdk_max_version="0.1.2",
                domain_contract_version="1.0.0",
            ),
        )
        registration = replace(
            base, manifest=manifest, execution_modes=frozenset({"interactive"}),
            compatibility=manifest.compatibility,
            domain_result_contracts=(CONFIG_DOMAIN_RESULT_CONTRACT,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text('{"enabled": true}', encoding="utf-8")
            output = Path(directory) / "output"
            controller = InteractivePluginController(PluginRegistry((registration,)), output)
            with self.assertRaises(PlatformContractError) as error:
                controller.start(
                    plugin_id=registration.manifest.plugin_id,
                    check_id="CFG-001",
                    scope={"files": [{"path": str(source)}]},
                )
            self.assertEqual(error.exception.code, "PLUGIN_PROTOCOL_INCOMPATIBLE")
            self.assertEqual(tuple(output.iterdir()), ())

    def test_new_host_rejects_older_plugin_before_run_state(self):
        base = config_quality_registration()
        manifest = replace(
            base.manifest,
            compatibility=PluginCompatibility(
                protocol_min_version="1.1.0",
                protocol_max_version="1.1.0",
                sdk_min_version="0.1.2",
                sdk_max_version="0.1.2",
                domain_contract_version="1.0.0",
            ),
        )
        registration = replace(
            base, manifest=manifest, execution_modes=frozenset({"interactive"}),
            compatibility=manifest.compatibility,
            domain_result_contracts=(CONFIG_DOMAIN_RESULT_CONTRACT,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text('{"enabled": true}', encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            with self.assertRaises(PlatformContractError) as error:
                controller.start(
                    plugin_id=registration.manifest.plugin_id,
                    check_id="CFG-001",
                    scope={"files": [{"path": str(source)}]},
                )
            self.assertEqual(error.exception.code, "PLUGIN_PROTOCOL_INCOMPATIBLE")
            self.assertEqual(tuple((Path(directory) / "output").iterdir()), ())

    def test_resume_requires_the_frozen_protocol_handshake(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(CONFIG_DOMAIN_RESULT_CONTRACT,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text('{"enabled": true}', encoding="utf-8")
            output = Path(directory) / "output"
            controller = InteractivePluginController(PluginRegistry((registration,)), output)
            started = controller.start(
                plugin_id=registration.manifest.plugin_id,
                check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            run_id = started["runId"]
            controller.close()
            descriptor_path = output / run_id / "platform-resume.json"
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            descriptor["compatibility"]["protocolVersion"] = "1.1.0"
            descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            before = ledger_path.read_bytes()
            resumed = InteractivePluginController(PluginRegistry((registration,)), output)
            with self.assertRaises(PlatformContractError) as error:
                resumed.resume(run_id)
            self.assertEqual(error.exception.code, "RUN_RESTART_REQUIRED")
            self.assertEqual(ledger_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
