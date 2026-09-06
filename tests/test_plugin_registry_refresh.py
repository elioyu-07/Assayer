"""The interactive MCP transport reloads its plugin registry when the durable
store changes, so a plugin installed in the same MCP process becomes runnable
without a restart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import PluginLifecycleManager
from assayer_host.errors import HostError
from assayer_host.transport import InteractivePlatformMcpToolTransport

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
PLUGIN_ID = "test-minimal"


class PluginRegistryRefreshTest(unittest.TestCase):
    def _installed_ids(self, transport):
        return [r.manifest.plugin_id for r in transport._controller.registry.list()]

    def test_start_plugin_run_refreshes_registry_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(Path(directory))
            transport = InteractivePlatformMcpToolTransport(
                output_root=Path(directory) / "out",
                store_root=store.root,
            )
            self.assertNotIn(PLUGIN_ID, self._installed_ids(transport))

            PluginLifecycleManager(store).install(PACKAGE)

            # start_plugin_run refreshes before selection; the non-interactive
            # fixture then fails on execution mode, but only after the freshly
            # installed plugin has become selectable in the same process.
            with self.assertRaises(HostError):
                transport.call_tool("start_plugin_run", {
                    "pluginId": PLUGIN_ID, "checkId": "TST-001", "scope": {},
                })

            self.assertIn(PLUGIN_ID, self._installed_ids(transport))

    def test_refresh_is_noop_without_store_root(self):
        transport = InteractivePlatformMcpToolTransport(
            output_root=tempfile.mkdtemp(),
        )
        before = self._installed_ids(transport)
        transport._refresh_registry()
        self.assertEqual(self._installed_ids(transport), before)

    def test_refresh_keeps_controller_run_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(Path(directory))
            transport = InteractivePlatformMcpToolTransport(
                output_root=Path(directory) / "out",
                store_root=store.root,
            )
            PluginLifecycleManager(store).install(PACKAGE)
            transport._refresh_registry()
            self.assertIsNone(transport._active_run_id)
            self.assertIn(PLUGIN_ID, self._installed_ids(transport))

    def test_start_plugin_run_rejects_tampered_package(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(Path(directory))
            transport = InteractivePlatformMcpToolTransport(
                output_root=Path(directory) / "out",
                store_root=store.root,
            )
            PluginLifecycleManager(store).install(PACKAGE)
            transport._refresh_registry()
            self.assertIn(PLUGIN_ID, self._installed_ids(transport))

            # Modify a package file without touching index.json: the store
            # digest now covers package content, so the next start must refuse
            # to keep running the modified plugin.
            package_dir = Path(directory) / "packages" / PLUGIN_ID / "1.0.0"
            (package_dir / "semantic-review.md").write_text("# tampered", encoding="utf-8")

            with self.assertRaises(HostError):
                transport.call_tool("start_plugin_run", {
                    "pluginId": PLUGIN_ID, "checkId": "TST-001", "scope": {},
                })
            self.assertNotIn(PLUGIN_ID, self._installed_ids(transport))


if __name__ == "__main__":
    unittest.main()
