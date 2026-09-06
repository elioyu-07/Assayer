"""Fail-closed persistent plugin lifecycle contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    CheckContract,
    ExecutionProfile,
    PlatformContractError,
    PluginConformanceIssue,
    PluginConformanceReport,
    PluginInstallationStore,
    PluginLifecycleManager,
    PluginManifest,
    PluginRegistration,
    discover_plugin_registry,
    load_registration,
)
from assayer_platform import installed_plugin_registry


def _manifest(plugin_id="fixture.lifecycle", version="1.0.0"):
    return PluginManifest(
        plugin_id=plugin_id, version=version, platform_api_version="1.0.0",
        domains=("fixture",), subject_kinds=("fixture_item",),
        checks=(CheckContract(
            "FIX-001", "1.0.0", ("fixture_item",), ("present",),
            ("scanned_no_issue", "needs_review"), ("structured",),
            ("structured_read",), "needs_review", ("source_digest",),
        ),),
        execution_profile=ExecutionProfile(
            "forbidden", "forbidden", "forbidden", "forbidden", "forbidden", "required",
        ),
    )


def _registration(plugin_id="fixture.lifecycle", version="1.0.0"):
    return PluginRegistration(
        _manifest(plugin_id, version),
        plugin_factory=lambda: object(),
        decision_provider_factory=lambda: object(),
        capabilities=frozenset({"structured_read"}),
        scope_schema={"type": "object"},
    )


class _FakePackage:
    def __init__(self, base: Path, plugin_id="fixture.lifecycle", version="1.0.0"):
        self.root = base / f"pkg-{plugin_id}-{version}"
        self.root.mkdir()
        (self.root / "assayer-plugin-release.json").write_text(json.dumps({
            "schemaVersion": "1.0.0",
            "pluginId": plugin_id,
            "pluginVersion": version,
            "platformApiVersion": "1.0.0",
            "registration": "fixture_lifecycle:registration",
            "manifest": "manifest.json",
            "scopeSchema": "scope.json",
            "runtimeSource": "src",
            "semanticReview": "review.md",
            "fixtures": ["fixture.json"],
            "packageMetadata": "pyproject.toml",
        }), encoding="utf-8")


def _passed_report():
    return PluginConformanceReport("fixture.lifecycle", ())


def _failed_report(code="PLUGIN_PACKAGE_INVALID"):
    return PluginConformanceReport("fixture.lifecycle", (PluginConformanceIssue(
        code, "PCV1-PACKAGE-RESOURCES", "package is broken", "fix the package",
    ),))


def _mkdir_installer(source, target):
    target.mkdir(parents=True, exist_ok=True)


def _assert_error_code(test, code, fn):
    with test.assertRaises(PlatformContractError) as caught:
        fn()
    test.assertEqual(caught.exception.code, code)


class PluginInstallationStoreTest(unittest.TestCase):
    def test_store_round_trips_index_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(directory)
            index = store.load()
            self.assertEqual(index["plugins"], {})
            store.upsert(
                index, "fixture.lifecycle", version="1.0.0",
                package_root="packages/fixture.lifecycle/1.0.0",
                installed_at=1, conformance={"status": "passed"},
            )
            store.save(index)
            reloaded = PluginInstallationStore(directory).load()
            self.assertEqual(reloaded["plugins"]["fixture.lifecycle"]["activeVersion"], "1.0.0")

    def test_upsert_appends_history_and_flips_active_version(self):
        store = PluginInstallationStore("/tmp/unused-assayer-store")
        index = store.load()
        entry = store.upsert(index, "fixture.lifecycle", version="1.0.0", package_root="p", installed_at=1, conformance={})
        entry = store.upsert(index, "fixture.lifecycle", version="2.0.0", package_root="p2", installed_at=2, conformance={})
        self.assertEqual(store.version_history(entry), ["1.0.0", "2.0.0"])
        self.assertEqual(store.active_version(entry), "2.0.0")

    def test_store_rejects_unsafe_identity(self):
        store = PluginInstallationStore("/tmp/unused-assayer-store")
        _assert_error_code(self, "PLUGIN_IDENTITY_INVALID", lambda: store.package_dir("../evil", "1.0.0"))


class PluginLifecycleManagerTest(unittest.TestCase):
    def manager(self, directory, **kwargs):
        return PluginLifecycleManager(
            PluginInstallationStore(directory),
            static_validator=lambda package: _passed_report(),
            installer=_mkdir_installer,
            clock=lambda: 1_700_000_000,
            **kwargs,
        )

    def test_install_materializes_and_activates(self):
        with tempfile.TemporaryDirectory() as directory:
            package = _FakePackage(Path(directory))
            result = self.manager(directory).install(package.root)
            self.assertEqual(result["operation"], "install")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["version"], "1.0.0")
            installed = self.manager(directory).get("fixture.lifecycle")
            self.assertEqual(installed["activeVersion"], "1.0.0")
            self.assertTrue((Path(directory) / "packages" / "fixture.lifecycle" / "1.0.0").is_dir())

    def test_install_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            package = _FakePackage(Path(directory))
            manager = self.manager(directory)
            manager.install(package.root)
            _assert_error_code(self, "PLUGIN_CONFLICT", lambda: manager.install(package.root))

    def test_install_invalid_package_quarantines(self):
        with tempfile.TemporaryDirectory() as directory:
            package = _FakePackage(Path(directory))
            manager = PluginLifecycleManager(
                PluginInstallationStore(directory),
                static_validator=lambda package: _failed_report(),
                installer=_mkdir_installer,
            )
            result = manager.install(package.root)
            self.assertEqual(result["status"], "quarantined")
            self.assertEqual(result["state"], "dirty")
            self.assertEqual(result["reason"], "PLUGIN_PACKAGE_INVALID")
            installed = manager.get("fixture.lifecycle")
            self.assertEqual(installed["state"], "dirty")
            self.assertEqual(installed["stateReason"], "PLUGIN_PACKAGE_INVALID")
            self.assertIsNone(installed["activeVersion"])

    def test_install_repairs_quarantined_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            package = _FakePackage(Path(directory))
            PluginLifecycleManager(
                PluginInstallationStore(directory),
                static_validator=lambda package: _failed_report(),
                installer=_mkdir_installer,
            ).install(package.root)
            manager = self.manager(directory)
            result = manager.install(package.root)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["recoveredFrom"], "dirty")
            installed = manager.get("fixture.lifecycle")
            self.assertEqual(installed["state"], "installed")
            self.assertIsNone(installed["stateReason"])
            self.assertEqual(installed["activeVersion"], "1.0.0")

    def test_install_unreachable_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            _assert_error_code(
                self, "PLUGIN_SOURCE_UNREACHABLE",
                lambda: manager.install(Path(directory) / "does-not-exist"),
            )

    def test_upgrade_preserves_previous_and_flips_active(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            result = manager.upgrade(_FakePackage(Path(directory), version="2.0.0").root)
            self.assertEqual(result["version"], "2.0.0")
            self.assertEqual(result["previousVersion"], "1.0.0")
            installed = manager.get("fixture.lifecycle")
            self.assertEqual(installed["history"], ["1.0.0", "2.0.0"])

    def test_upgrade_same_version_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            package = _FakePackage(Path(directory), version="1.0.0")
            manager.install(package.root)
            result = manager.upgrade(package.root)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["version"], "1.0.0")
            self.assertEqual(result["previousVersion"], "1.0.0")

    def test_upgrade_unknown_plugin_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            _assert_error_code(self, "UNKNOWN_PLUGIN", lambda: manager.upgrade(_FakePackage(Path(directory)).root))

    def test_upgrade_to_older_version_requires_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="2.0.0").root)
            _assert_error_code(
                self, "PLUGIN_DOWNGRADE_REQUIRED",
                lambda: manager.upgrade(_FakePackage(Path(directory), version="1.0.0").root),
            )

    def test_downgrade_restores_older_version_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            manager.upgrade(_FakePackage(Path(directory), version="2.0.0").root)
            manager.upgrade(_FakePackage(Path(directory), version="3.0.0").root)
            result = manager.downgrade("fixture.lifecycle", "1.0.0")
            self.assertEqual(result["version"], "1.0.0")
            self.assertEqual(result["previousVersion"], "3.0.0")
            self.assertEqual(manager.get("fixture.lifecycle")["activeVersion"], "1.0.0")
            self.assertEqual(
                manager.get("fixture.lifecycle")["history"],
                ["1.0.0", "2.0.0", "3.0.0", "1.0.0"],
            )

    def test_upgrade_after_rollback_reactivates_version(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            version_110 = _FakePackage(Path(directory), version="1.1.0").root
            manager.upgrade(version_110)
            manager.rollback("fixture.lifecycle")
            self.assertEqual(manager.get("fixture.lifecycle")["activeVersion"], "1.0.0")

            result = manager.upgrade(version_110)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["version"], "1.1.0")
            self.assertEqual(manager.get("fixture.lifecycle")["activeVersion"], "1.1.0")

    def test_downgrade_to_active_version_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            result = manager.downgrade("fixture.lifecycle", "1.0.0")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["version"], "1.0.0")
            self.assertEqual(result["previousVersion"], "1.0.0")

    def test_downgrade_unavailable_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            _assert_error_code(
                self, "PLUGIN_VERSION_UNAVAILABLE",
                lambda: manager.downgrade("fixture.lifecycle", "9.9.9"),
            )

    def test_downgrade_unknown_plugin_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            _assert_error_code(
                self, "UNKNOWN_PLUGIN",
                lambda: manager.downgrade("fixture.lifecycle", "1.0.0"),
            )

    def test_rollback_restores_previous_version(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            manager.upgrade(_FakePackage(Path(directory), version="2.0.0").root)
            result = manager.rollback("fixture.lifecycle")
            self.assertEqual(result["version"], "1.0.0")
            self.assertEqual(result["previousVersion"], "2.0.0")
            self.assertEqual(manager.get("fixture.lifecycle")["activeVersion"], "1.0.0")

    def test_rollback_without_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory)).root)
            _assert_error_code(self, "ROLLBACK_UNAVAILABLE", lambda: manager.rollback("fixture.lifecycle"))

    def test_uninstall_removes_record_and_package_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory)).root)
            result = manager.uninstall("fixture.lifecycle")
            self.assertEqual(result["operation"], "uninstall")
            self.assertIsNone(manager.get("fixture.lifecycle"))
            self.assertFalse((Path(directory) / "packages" / "fixture.lifecycle").exists())

    def test_uninstall_unknown_plugin_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            _assert_error_code(self, "UNKNOWN_PLUGIN", lambda: manager.uninstall("fixture.lifecycle"))

    def test_uninstall_leaves_builtin_platform_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory)).root)
            manager.uninstall("fixture.lifecycle")
            self.assertEqual(
                installed_plugin_registry().select(plugin_id="assayer.frontend-audit").manifest.plugin_id,
                "assayer.frontend-audit",
            )

    def test_list_and_get_reflect_installed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            self.assertEqual(manager.list(), [{
                "pluginId": "fixture.lifecycle",
                "activeVersion": "1.0.0",
                "history": ["1.0.0"],
                "state": "installed",
                "stateReason": None,
            }])

    def test_upgradable_state_when_source_knows_newer_version(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory, latest_available=lambda plugin_id: "2.0.0")
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            view = manager.get("fixture.lifecycle")
            self.assertEqual(view["state"], "upgradable")
            self.assertEqual(view["upgradeTo"], "2.0.0")
            self.assertEqual(manager.list()[0]["state"], "upgradable")

    def test_installed_state_without_source_or_with_older_source(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory, latest_available=lambda plugin_id: "0.9.0")
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            self.assertEqual(manager.get("fixture.lifecycle")["state"], "installed")
            self.assertNotIn("upgradeTo", manager.get("fixture.lifecycle"))

    def test_installed_state_when_source_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory, latest_available=lambda plugin_id: None)
            manager.install(_FakePackage(Path(directory), version="1.0.0").root)
            self.assertEqual(manager.get("fixture.lifecycle")["state"], "installed")


class DiscoveryTest(unittest.TestCase):
    def test_discover_merges_installed_into_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginLifecycleManager(
                PluginInstallationStore(directory),
                static_validator=lambda package: _passed_report(),
                installer=_mkdir_installer,
                clock=lambda: 1,
            )
            manager.install(_FakePackage(Path(directory)).root)
            registry = discover_plugin_registry(
                PluginInstallationStore(directory),
                builtins=installed_plugin_registry().list(),
                loader=lambda package: _registration(),
            )
            self.assertEqual(registry.select(plugin_id="fixture.lifecycle").manifest.plugin_id, "fixture.lifecycle")
            self.assertEqual(registry.select(plugin_id="assayer.frontend-audit").manifest.plugin_id, "assayer.frontend-audit")

    def test_discover_skips_quarantined_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginLifecycleManager(
                PluginInstallationStore(directory),
                static_validator=lambda package: _failed_report(),
                installer=_mkdir_installer,
                clock=lambda: 1,
            )
            manager.install(_FakePackage(Path(directory)).root)
            registry = discover_plugin_registry(
                PluginInstallationStore(directory),
                loader=lambda package: _registration(),
            )
            self.assertEqual(registry.list(), ())

    def test_discover_quarantines_on_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginLifecycleManager(
                PluginInstallationStore(directory),
                static_validator=lambda package: _passed_report(),
                installer=_mkdir_installer,
                clock=lambda: 1,
            )
            manager.install(_FakePackage(Path(directory)).root)
            package_dir = Path(directory) / "packages" / "fixture.lifecycle" / "1.0.0"
            (package_dir / "tampered.txt").write_text("x", encoding="utf-8")
            registry = discover_plugin_registry(
                PluginInstallationStore(directory),
                loader=lambda package: _registration(),
            )
            self.assertEqual(registry.list(), ())
            entry = manager.get("fixture.lifecycle")
            self.assertEqual(entry["state"], "dirty")
            self.assertEqual(entry["stateReason"], "PLUGIN_CHECKSUM_MISMATCH")

    def test_discover_duplicate_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(directory)
            index = store.load()
            store.upsert(
                index, "assayer.frontend-audit", version="1.0.0",
                package_root="packages/assayer.frontend-audit/1.0.0",
                installed_at=1, conformance={},
            )
            store.save(index)
            builtin = installed_plugin_registry().select(plugin_id="assayer.frontend-audit")
            _assert_error_code(
                self, "PLUGIN_CONFLICT",
                lambda: discover_plugin_registry(
                    store,
                    builtins=installed_plugin_registry().list(),
                    loader=lambda package: builtin,
                ),
            )


class LoadRegistrationTest(unittest.TestCase):
    def test_load_registration_imports_from_package_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pkg"
            src = root / "src"
            src.mkdir(parents=True)
            (src / "fixture_lifecycle.py").write_text(
                "from assayer_platform import PluginRegistration, PluginManifest, ExecutionProfile, CheckContract\n"
                "def registration():\n"
                "    manifest = PluginManifest(\n"
                "        plugin_id='fixture.lifecycle', version='1.0.0', platform_api_version='1.0.0',\n"
                "        domains=('fixture',), subject_kinds=('fixture_item',),\n"
                "        checks=(CheckContract('FIX-001', '1.0.0', ('fixture_item',), ('present',),\n"
                "            ('scanned_no_issue', 'needs_review'), ('structured',), ('structured_read',),\n"
                "            'needs_review', ('source_digest',)),),\n"
                "        execution_profile=ExecutionProfile('forbidden', 'forbidden', 'forbidden',\n"
                "            'forbidden', 'forbidden', 'required'),\n"
                "    )\n"
                "    return PluginRegistration(manifest, plugin_factory=lambda: object(),\n"
                "        decision_provider_factory=lambda: object(), capabilities=frozenset({'structured_read'}),\n"
                "        scope_schema={'type': 'object'})\n",
                encoding="utf-8",
            )
            (root / "assayer-plugin-release.json").write_text(json.dumps({
                "schemaVersion": "1.0.0",
                "pluginId": "fixture.lifecycle",
                "pluginVersion": "1.0.0",
                "registration": "fixture_lifecycle:registration",
                "runtimeSource": "src",
            }), encoding="utf-8")
            registration = load_registration(root)
            self.assertEqual(registration.manifest.plugin_id, "fixture.lifecycle")


if __name__ == "__main__":
    unittest.main()
