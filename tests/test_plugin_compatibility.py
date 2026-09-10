from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from assayer_platform import (
    InteractivePluginController,
    PluginCompatibility,
    PluginRegistry,
    PlatformContractError,
    PluginRegistration,
    load_plugin_manifest,
    negotiate_plugin_compatibility,
)
from tests.helpers import config_quality_registration


class PluginCompatibilityTests(unittest.TestCase):
    def test_manifest_declaration_is_loaded_before_plugin_execution(self):
        source = Path(__file__).parent / "fixtures/plugins/minimal/src/minimal_plugin/manifest.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["compatibility"] = {
            "protocolMinVersion": "1.1.0",
            "protocolMaxVersion": "1.2.0",
            "sdkMinVersion": "0.1.0",
            "sdkMaxVersion": "0.1.2",
            "capabilities": ["domain_result"],
            "domainContractVersion": "1.0.0",
        }
        registration = PluginRegistration(load_plugin_manifest(value))
        self.assertEqual(registration.protocol_min_version, "1.1.0")
        self.assertEqual(registration.protocol_capabilities, frozenset({"domain_result"}))

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

    def test_semver_minor_forms_are_normalized(self):
        result = negotiate_plugin_compatibility(
            PluginCompatibility(protocol_min_version="1.1", protocol_max_version="1.2")
        )
        self.assertEqual(result.protocol_version, "1.2.0")

    def test_supported_older_protocol_uses_host_adapter(self):
        result = negotiate_plugin_compatibility(
            PluginCompatibility(
                protocol_min_version="1.0.0",
                protocol_max_version="1.1.0",
            )
        )
        self.assertEqual(result.protocol_version, "1.1.0")
        self.assertEqual(result.adapter, "host.compat.protocol-1.1.0")

    def test_unsupported_protocol_fails_closed(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(
                PluginCompatibility(protocol_min_version="2.0.0", protocol_max_version="2.1.0")
            )
        self.assertEqual(error.exception.code, "PLUGIN_PROTOCOL_INCOMPATIBLE")

    def test_missing_host_capability_fails_closed(self):
        with self.assertRaises(PlatformContractError) as error:
            negotiate_plugin_compatibility(
                PluginCompatibility(capabilities=frozenset({"future_capability"}))
            )
        self.assertEqual(error.exception.code, "PLUGIN_CAPABILITY_INCOMPATIBLE")

    def test_controller_rejects_before_run_state_is_created(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            protocol_min_version="2.0.0",
            protocol_max_version="2.0.0",
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

    def test_new_host_starts_supported_older_plugin_through_adapter(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            protocol_min_version="1.0.0",
            protocol_max_version="1.1.0",
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text('{"enabled": true}', encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            started = controller.start(
                plugin_id=registration.manifest.plugin_id,
                check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            self.assertEqual(started["result"]["compatibility"]["protocolVersion"], "1.1.0")
            self.assertEqual(
                started["result"]["compatibility"]["adapter"],
                "host.compat.protocol-1.1.0",
            )

    def test_resume_requires_the_frozen_protocol_handshake(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
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
