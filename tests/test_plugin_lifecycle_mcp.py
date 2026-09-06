"""MCP facade for the plugin installation lifecycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_host.plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
PLUGIN_ID = "test-minimal"


class PluginLifecycleMcpTest(unittest.TestCase):
    def test_tool_names(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        self.assertEqual(
            [item["name"] for item in transport.list_tools()],
            [
                "list_plugins", "get_plugin_info", "install_plugin",
                "upgrade_plugin", "uninstall_plugin", "downgrade_plugin",
                "rollback_plugin",
            ],
        )

    def test_install_info_uninstall_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            installed = transport.call_tool("install_plugin", {"plugin": str(PACKAGE)})
            self.assertFalse(installed["isError"])
            self.assertEqual(installed["structuredContent"]["result"]["status"], "completed")

            info = transport.call_tool("get_plugin_info", {"pluginId": PLUGIN_ID})
            self.assertFalse(info["isError"])
            self.assertEqual(info["structuredContent"]["result"]["pluginId"], PLUGIN_ID)

            removed = transport.call_tool("uninstall_plugin", {"pluginId": PLUGIN_ID})
            self.assertFalse(removed["isError"])

    def test_install_by_name_routes_to_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            with patch(
                "assayer_host.plugin_lifecycle_ops.add_from_catalog",
                return_value={"operation": "install", "status": "completed",
                              "pluginId": "ass-spec", "version": "1.1.0"},
            ) as add:
                result = transport.call_tool("install_plugin", {"plugin": "ass-spec"})
            self.assertFalse(result["isError"])
            add.assert_called_once_with(
                "ass-spec", version=None,
                index=transport._catalog_index, store_root=transport._store_root,
            )

    def test_unknown_plugin_info_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            result = transport.call_tool("get_plugin_info", {"pluginId": "missing"})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "UNKNOWN_PLUGIN",
            )

    def test_unknown_tool_fails_closed(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        with self.assertRaises(Exception):
            transport.call_tool("nope", {})


if __name__ == "__main__":
    unittest.main()
