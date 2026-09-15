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
    def test_start_plugin_run_refreshes_registry_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text('{}', encoding="utf-8")
            scope = {"files": [{"path": str(source)}]}
            store = PluginInstallationStore(Path(directory))
            transport = InteractivePlatformMcpToolTransport(
                output_root=Path(directory) / "out",
                store_root=store.root,
            )
            with self.assertRaises(HostError) as missing:
                transport.call_tool("start_plugin_run", {
                    "pluginId": PLUGIN_ID, "checkId": "TST-001", "scope": scope,
                })
            self.assertEqual(missing.exception.code, "UNKNOWN_PLUGIN")

            PluginLifecycleManager(store).install(PACKAGE)

            started = transport.call_tool("start_plugin_run", {
                "pluginId": PLUGIN_ID, "checkId": "TST-001", "scope": scope,
            })["structuredContent"]["result"]
            self.assertEqual(started["status"], "started")
            self.assertEqual(started["result"]["plugin"]["pluginId"], PLUGIN_ID)
            transport.close()

    def test_start_plugin_run_rejects_tampered_package(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginInstallationStore(Path(directory))
            transport = InteractivePlatformMcpToolTransport(
                output_root=Path(directory) / "out",
                store_root=store.root,
            )
            PluginLifecycleManager(store).install(PACKAGE)

            # Modify a package file without touching index.json: the store
            # digest now covers package content, so the next start must refuse
            # to keep running the modified plugin.
            package_dir = Path(directory) / "packages" / PLUGIN_ID / "1.0.0"
            (package_dir / "semantic-review.md").write_text("# tampered", encoding="utf-8")

            with self.assertRaises(HostError) as rejected:
                transport.call_tool("start_plugin_run", {
                    "pluginId": PLUGIN_ID, "checkId": "TST-001", "scope": {},
                })
            self.assertEqual(rejected.exception.code, "UNKNOWN_PLUGIN")
            transport.close()


if __name__ == "__main__":
    unittest.main()
