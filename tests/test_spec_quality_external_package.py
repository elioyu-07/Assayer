"""The external Spec-quality distribution satisfies the M4 release and
lifecycle gates: static package validation, isolated install + fixture
execution, and durable install / discover / upgrade / rollback / uninstall
without touching platform source."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    PlatformContractError,
    load_plugin_manifest,
)
from assayer_platform.conformance import inspect_plugin_package
from assayer_platform.installation_conformance import inspect_plugin_installation
from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import (
    PluginLifecycleManager,
    discover_plugin_registry,
    load_registration,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins" / "spec-quality"


def _bumped_copy(package: Path, destination: Path, version: str) -> Path:
    shutil.copytree(package, destination)
    descriptor = json.loads((destination / "assayer-plugin-release.json").read_text(encoding="utf-8"))
    descriptor["pluginVersion"] = version
    (destination / "assayer-plugin-release.json").write_text(json.dumps(descriptor), encoding="utf-8")
    manifest_path = destination / "src" / "assayer_spec_quality" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata = destination / "pyproject.toml"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace(
        'version = "1.0.0"', f'version = "{version}"',
    ))
    return destination


class SpecQualityExternalPackageTest(unittest.TestCase):
    def test_static_package_gate_passes_without_importing_plugin_code(self):
        report = inspect_plugin_package(PACKAGE)
        self.assertTrue(report.passed, report.as_dict())
        self.assertEqual(report.plugin_id, "assayer.spec-quality")

    def test_registration_loads_and_matches_packaged_manifest_and_scope(self):
        registration = load_registration(PACKAGE)
        self.assertEqual(registration.manifest.plugin_id, "assayer.spec-quality")
        self.assertEqual(registration.manifest.version, "1.0.0")
        self.assertEqual(
            sorted(registration.execution_modes), ["interactive"],
        )
        self.assertEqual(
            sorted(registration.capabilities), ["structured_read"],
        )
        self.assertIn("evidence_graph", registration.result_features)
        packaged_manifest = load_plugin_manifest(
            PACKAGE / "src" / "assayer_spec_quality" / "manifest.json",
        )
        self.assertEqual(registration.manifest, packaged_manifest)
        packaged_scope = json.loads(
            (PACKAGE / "src" / "assayer_spec_quality" / "scope.schema.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(dict(registration.scope_schema), packaged_scope)

    def test_lifecycle_install_discover_upgrade_rollback_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(Path(directory))
            manager = PluginLifecycleManager(store)

            installed = manager.install(PACKAGE)
            self.assertEqual(installed["status"], "completed")
            self.assertEqual(installed["pluginId"], "assayer.spec-quality")
            self.assertEqual(installed["version"], "1.0.0")

            registry = discover_plugin_registry(store)
            self.assertEqual(
                [item.manifest.plugin_id for item in registry.list()],
                ["assayer.spec-quality"],
            )

            with tempfile.TemporaryDirectory() as bumped:
                upgraded_source = _bumped_copy(PACKAGE, Path(bumped) / "v110", "1.1.0")
                upgraded = manager.upgrade(upgraded_source)
            self.assertEqual(upgraded["version"], "1.1.0")
            self.assertEqual(upgraded["previousVersion"], "1.0.0")

            rolled_back = manager.rollback("assayer.spec-quality")
            self.assertEqual(rolled_back["version"], "1.0.0")
            self.assertEqual(rolled_back["previousVersion"], "1.1.0")

            uninstalled = manager.uninstall("assayer.spec-quality")
            self.assertEqual(uninstalled["status"], "completed")
            self.assertEqual(manager.list(), [])

    def test_install_rejects_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginLifecycleManager(PluginInstallationStore(Path(directory)))
            manager.install(PACKAGE)
            with self.assertRaises(PlatformContractError) as rejected:
                manager.install(PACKAGE)
            self.assertEqual(rejected.exception.code, "PLUGIN_CONFLICT")

    def test_isolated_install_gate_passes_and_runs_deterministic_fixture(self):
        report = inspect_plugin_installation(PACKAGE)
        self.assertTrue(report.passed, report.as_dict())


if __name__ == "__main__":
    unittest.main()
