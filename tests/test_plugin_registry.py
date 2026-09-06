"""Plugin discovery, selection, and registered execution contracts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_platform import (
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PlatformKernel,
    PluginRegistration,
    PluginRegistry,
    WorkItem,
    load_plugin_manifest,
)
from assayer_platform.builtin_plugins import builtin_plugin_registry
from assayer_platform.testing import config_quality_registration
from assayer_platform.testing.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)


class InstalledFixturePlugin:
    manifest = load_plugin_manifest({
        "pluginId": "fixture.installed-plugin", "version": "1.0.0",
        "platformApiVersion": "1.0.0", "domains": ["fixture-domain"],
        "subjectKinds": ["fixture_item"],
        "checks": [{
            "checkId": "FIX-001", "version": "1.0.0",
            "subjectKinds": ["fixture_item"], "dimensions": ["present"],
            "decisionStates": ["scanned_no_issue", "needs_review"],
            "requiredEvidenceKinds": ["fixture"], "requiredCapabilities": ["fixture_read"],
            "capabilityMissingOutcome": "needs_review", "invalidationSignals": ["source_digest"],
        }],
        "executionProfile": {
            "discoverBatching": "forbidden", "inspectBatching": "forbidden",
            "decisionBatching": "forbidden", "parallelism": "forbidden",
            "cacheReuse": "forbidden", "checkpoint": "required",
        },
    })

    def discover(self, scope, context):
        del scope, context
        return (WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-digest"),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        evidence = EvidenceRecord(
            "fixture-evidence", item.work_item_id, check.check_id, check.version,
            "fixture", item.identity, {"present": True},
        )
        observation = DimensionObservation(
            "present", ("The fixture item is present.",), (evidence.evidence_id,), "satisfied",
        )
        return (InvestigationPacket(
            item, check.check_id, check.version, (observation,), (evidence,), "not_required",
        ),)


class InstalledFixtureDecisionProvider:
    def decide(self, packets, check, context):
        del context
        packet = packets[0]
        return (DecisionProposal(
            packet.work_item.work_item_id, check.check_id, check.version,
            "scanned_no_issue", (Finding("present", "satisfied", "The fixture item is present."),),
            "The independently installed fixture satisfies its Check.",
        ),)


def installed_fixture_registration():
    return PluginRegistration(
        InstalledFixturePlugin.manifest,
        plugin_factory=lambda: InstalledFixturePlugin(),
        decision_provider_factory=lambda: InstalledFixtureDecisionProvider(),
        capabilities=frozenset({"fixture_read"}),
        scope_schema={"type": "object"},
    )


class PluginRegistryTest(unittest.TestCase):
    def test_builtin_plugins_are_selected_by_domain_and_check(self):
        registry = builtin_plugin_registry()

        frontend = registry.select(domain="frontend-audit")
        self.assertEqual(frontend.manifest.plugin_id, "assayer.frontend-audit")

        with_fixture = PluginRegistry((
            *builtin_plugin_registry().list(),
            config_quality_registration(),
        ))
        config = with_fixture.select(check_ref=("CFG-001", "1.0.0"))
        self.assertEqual(config.manifest.plugin_id, "test.config-quality")

    def test_duplicate_plugin_identity_fails_closed(self):
        registration = PluginRegistration(
            ConfigQualityPlugin.manifest,
            plugin_factory=lambda: ConfigQualityPlugin(),
            decision_provider_factory=lambda: ConfigurationDecisionProvider(),
            capabilities=frozenset({"structured_read"}),
            scope_schema={"type": "object"},
        )
        registry = PluginRegistry((registration,))

        with self.assertRaisesRegex(PlatformContractError, "already registered"):
            registry.register(registration)

    def test_ambiguous_selection_fails_closed(self):
        registry = PluginRegistry((
            *builtin_plugin_registry().list(),
            config_quality_registration(),
        ))

        with self.assertRaisesRegex(PlatformContractError, "Multiple registered plugins"):
            registry.select()

    def test_registered_plugin_runs_without_kernel_importing_its_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")

            result = PlatformKernel().run_registered(
                PluginRegistry((config_quality_registration(),)),
                str(path), "CFG-001",
                PlatformContext("run-registry", frozenset({"structured_read"})),
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.decisions[0].result, "scanned_no_issue")
        self.assertEqual(result.ledger.run.plugin_id, "test.config-quality")

    def test_registered_plugin_missing_runtime_factory_is_rejected_before_run(self):
        with self.assertRaisesRegex(PlatformContractError, "PCV1-RUNTIME-FACTORY"):
            PluginRegistry((PluginRegistration(
                ConfigQualityPlugin.manifest,
                decision_provider_factory=lambda: ConfigurationDecisionProvider(),
                capabilities=frozenset({"structured_read"}),
                scope_schema={"type": "object"},
            ),))

    def test_entry_point_registration_is_loaded(self):
        registration = PluginRegistration(
            ConfigQualityPlugin.manifest,
            plugin_factory=lambda: ConfigQualityPlugin(),
            decision_provider_factory=lambda: ConfigurationDecisionProvider(),
            capabilities=frozenset({"structured_read"}),
            scope_schema={"type": "object"},
        )

        class EntryPoint:
            name = "config-quality"

            @staticmethod
            def load():
                return lambda: registration

        class EntryPoints:
            @staticmethod
            def select(*, group):
                return (EntryPoint(),) if group == "assayer.plugins" else ()

        with patch("assayer_platform.plugin_registry.metadata.entry_points", return_value=EntryPoints()):
            registry = PluginRegistry.from_entry_points()

        self.assertEqual(registry.select(plugin_id="test.config-quality"), registration)

    def test_independently_installed_distribution_is_discovered_and_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            distribution = Path(directory) / "assayer_fixture_plugin-1.0.0.dist-info"
            distribution.mkdir()
            (distribution / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: assayer-fixture-plugin\nVersion: 1.0.0\n",
                encoding="utf-8",
            )
            (distribution / "entry_points.txt").write_text(
                "[assayer.plugins]\nfixture = test_plugin_registry:installed_fixture_registration\n",
                encoding="utf-8",
            )
            with patch.object(sys, "path", [directory, *sys.path]):
                registry = PluginRegistry.from_entry_points()
                result = PlatformKernel().run_registered(
                    registry, {}, "FIX-001",
                    PlatformContext("run-installed", frozenset({"fixture_read"})),
                    plugin_id="fixture.installed-plugin",
                )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.ledger.run.plugin_id, "fixture.installed-plugin")
        self.assertEqual(result.decisions[0].result, "scanned_no_issue")

    def test_factory_type_error_is_not_misread_as_a_zero_argument_factory(self):
        def broken_factory(runtime=None):
            del runtime
            raise TypeError("plugin construction failed")

        registration = PluginRegistration(
            ConfigQualityPlugin.manifest,
            plugin_factory=broken_factory,
            decision_provider_factory=lambda: ConfigurationDecisionProvider(),
        )

        with self.assertRaisesRegex(TypeError, "plugin construction failed"):
            registration.create_plugin()

    def test_registered_factory_cannot_substitute_another_plugin_identity(self):
        registration = PluginRegistration(
            ConfigQualityPlugin.manifest,
            plugin_factory=lambda: type("Substitute", (), {
                "manifest": builtin_plugin_registry().select(
                    plugin_id="assayer.frontend-audit",
                ).manifest,
            })(),
            decision_provider_factory=lambda: ConfigurationDecisionProvider(),
            capabilities=frozenset({"structured_read"}),
            scope_schema={"type": "object"},
        )
        result = PlatformKernel().run_registered(
            PluginRegistry((registration,)), {}, "CFG-001",
            PlatformContext("run-substitute", frozenset({"structured_read"})),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failures[0].code, "PLUGIN_IDENTITY_MISMATCH")

    def test_registered_factory_failure_is_a_stable_failed_run(self):
        def fail():
            raise RuntimeError("plugin setup failed")

        registration = PluginRegistration(
            ConfigQualityPlugin.manifest,
            plugin_factory=fail,
            decision_provider_factory=lambda: ConfigurationDecisionProvider(),
            capabilities=frozenset({"structured_read"}),
            scope_schema={"type": "object"},
        )
        result = PlatformKernel().run_registered(
            PluginRegistry((registration,)), {}, "CFG-001",
            PlatformContext("run-setup-failure", frozenset({"structured_read"})),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failures[0].code, "PLUGIN_INITIALIZATION_FAILED")

    def test_manifest_requiring_newer_platform_api_is_rejected(self):
        manifest = {
            "pluginId": "example.future-plugin", "version": "1.0.0",
            "platformApiVersion": "2.0.0", "domains": ["future"],
            "subjectKinds": ["future_item"],
            "checks": [{
                "checkId": "FUT-001", "version": "1.0.0",
                "subjectKinds": ["future_item"], "dimensions": ["present"],
                "decisionStates": ["scanned_no_issue", "needs_review"],
                "requiredEvidenceKinds": ["structured"],
                "requiredCapabilities": [], "capabilityMissingOutcome": "needs_review",
                "invalidationSignals": [],
            }],
            "executionProfile": {
                "discoverBatching": "allowed", "inspectBatching": "allowed",
                "decisionBatching": "allowed", "parallelism": "forbidden",
                "cacheReuse": "allowed", "checkpoint": "required",
            },
        }

        with self.assertRaisesRegex(PlatformContractError, "platform API"):
            from assayer_platform import load_plugin_manifest
            load_plugin_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
