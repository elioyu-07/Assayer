from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assayer_platform.compiled_plugin_lifecycle import (
    CompiledPluginLifecycleManager,
    load_installed_compiled_plugin,
)
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_plugin_sdk.contract import PlatformContractError


class CompiledPluginLifecycleTests(unittest.TestCase):
    def test_install_discover_and_uninstall_without_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifact"
            compile_plugin_contract(Path("tests/fixtures/plugins/policy-pack"), artifact_dir)
            store = PluginInstallationStore(root / "store")
            manager = CompiledPluginLifecycleManager(store)

            installed = manager.install(artifact_dir / "compiled-plugin.json")
            self.assertEqual(installed["status"], "completed")
            loaded = load_installed_compiled_plugin(store, "test.policy-pack")
            self.assertEqual(loaded.plugin_id, "test.policy-pack")
            package = store.package_dir("test.policy-pack", "1.0.0")
            self.assertEqual(tuple(package.rglob("*.py")), ())
            self.assertEqual(manager.list()[0]["artifactType"], "compiled-plugin-contract")

            removed = manager.uninstall("test.policy-pack")
            self.assertEqual(removed["status"], "completed")
            with self.assertRaises(PlatformContractError):
                load_installed_compiled_plugin(store, "test.policy-pack")


if __name__ == "__main__":
    unittest.main()
