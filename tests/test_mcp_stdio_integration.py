"""Real MCP stdio integration for the plugin lifecycle.

These tests launch the actual ``assayer-mcp`` server as a subprocess and drive
it with the official MCP SDK over stdio, proving the same single connection can
install, run, and uninstall a plugin without a restart.  Unlike the
transport-level tests (``test_plugin_lifecycle_mcp.py`` and
``test_plugin_registry_refresh.py``), this exercises the real ``.mcp`` entry
point, FastMCP schema registration, and stdio transport together.

The fast profile excludes this module because it requires the optional MCP SDK
and spawns a subprocess server.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _MCP_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
PLUGIN_ID = "test-minimal"

_SERVER_BOOTSTRAP = (
    "import sys; from assayer_host.transport import mcp_main; sys.exit(mcp_main())"
)


def _server_env() -> dict:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SRC), pythonpath) if part
    )
    return env


def _envelope(result) -> dict:
    """Decode the host envelope FastMCP serialized into the text content block."""
    return json.loads(result.content[0].text)


class PluginLifecycleStdioIntegrationTest(unittest.TestCase):
    """Drive ``assayer-mcp`` over a real stdio MCP connection."""

    @unittest.skipUnless(_MCP_AVAILABLE, "MCP optional dependency is not installed")
    def test_install_run_uninstall_over_one_stdio_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / "store")
            output_root = str(Path(directory) / "out")
            sample = Path(directory) / "sample.json"
            sample.write_text(json.dumps({"name": "sample"}), encoding="utf-8")
            asyncio.run(self._journey(store, output_root, sample))

    async def _journey(self, store: str, output_root: str, sample: Path) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", _SERVER_BOOTSTRAP, "--store", store, "--output-root", output_root],
            env=_server_env(),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await self._drive(session, sample)

    async def _drive(self, session: ClientSession, sample: Path) -> None:
        tools = {tool.name for tool in (await session.list_tools()).tools}
        self.assertTrue(
            {"list_plugins", "get_plugin_info", "plan_plugin_change", "execute_plugin_change"} <= tools,
        )

        async def plugin_ids():
            listing = _envelope(await session.call_tool("list_plugins", {}))
            return {
                plugin["pluginId"]
                for plugin in listing["structuredContent"]["result"]["plugins"]
            }

        self.assertNotIn(PLUGIN_ID, await plugin_ids())

        # plan -> confirm -> execute: install a local package.
        planned = _envelope(await session.call_tool(
            "plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)},
        ))
        self.assertEqual(planned["structuredContent"]["result"]["plan"]["status"], "ready")
        token = planned["structuredContent"]["result"]["token"]

        executed = _envelope(await session.call_tool(
            "execute_plugin_change", {"token": token, "confirmed": True},
        ))
        self.assertEqual(executed["structuredContent"]["result"]["resultingState"], "installed")

        # Same connection now sees the freshly installed plugin.
        self.assertIn(PLUGIN_ID, await plugin_ids())

        # The freshly installed plugin is runnable in this same connection.
        started = await session.call_tool("start_plugin_run", {
            "pluginId": PLUGIN_ID, "checkId": "TST-001",
            "scope": {"files": [{"path": str(sample), "requiredKeys": ["name"]}]},
        })
        self.assertFalse(started.isError)

        # Drive the same public workflow Codex uses through a terminal result.
        advanced = _envelope(await session.call_tool("advance_plugin_run", {}))
        advanced_result = advanced["structuredContent"]["result"]
        self.assertEqual(advanced_result["status"], "awaiting_agent_decision")
        task = advanced_result["result"]["semanticTask"]
        self.assertEqual(task["kind"], "decide_work_item")
        terminal = _envelope(await session.call_tool("advance_plugin_run", {
            "decision": {
                "workItemId": task["workItemId"],
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "required_keys",
                    "status": "satisfied",
                    "reason": "The requested key is present.",
                }],
                "reason": "The reviewed configuration satisfies the check.",
            },
        }))
        terminal_result = terminal["structuredContent"]["result"]
        self.assertEqual(terminal_result["status"], "completed")
        overview = terminal_result["result"]["resultOverview"]
        self.assertEqual(overview["status"], "completed")
        self.assertTrue(overview["coverage"]["complete"])
        decisions_section = terminal_result["result"]["decisions"]["sectionId"]
        result_page = _envelope(await session.call_tool("get_plugin_result", {
            "sectionId": decisions_section,
        }))
        self.assertEqual(result_page["structuredContent"]["result"]["result"]["page"]["total"], 1)

        # Upgrade and roll back without restarting the MCP server. The package
        # is local here so the test remains deterministic and offline.
        package_v2 = sample.parent / "test-minimal-1.1.0"
        _copy_fixture_version(package_v2, "1.1.0")
        upgraded_plan = _envelope(await session.call_tool("plan_plugin_change", {
            "operation": "upgrade", "plugin": str(package_v2),
        }))
        self.assertEqual(upgraded_plan["structuredContent"]["result"]["plan"]["targetVersion"], "1.1.0")
        upgraded = _envelope(await session.call_tool("execute_plugin_change", {
            "token": upgraded_plan["structuredContent"]["result"]["token"], "confirmed": True,
        }))
        upgraded_result = upgraded["structuredContent"]["result"]
        self.assertEqual(upgraded_result["activeVersion"], "1.1.0")
        self.assertEqual(upgraded_result["previousVersion"], "1.0.0")
        self.assertIn(PLUGIN_ID, await plugin_ids())

        rollback_plan = _envelope(await session.call_tool("plan_plugin_change", {
            "operation": "rollback", "pluginId": PLUGIN_ID,
        }))
        self.assertEqual(rollback_plan["structuredContent"]["result"]["plan"]["targetVersion"], "1.0.0")
        rolled_back = _envelope(await session.call_tool("execute_plugin_change", {
            "token": rollback_plan["structuredContent"]["result"]["token"], "confirmed": True,
        }))
        rollback_result = rolled_back["structuredContent"]["result"]
        self.assertEqual(rollback_result["activeVersion"], "1.0.0")
        self.assertEqual(rollback_result["previousVersion"], "1.1.0")

        # plan -> confirm -> execute: uninstall, all on the same connection.
        unplanned = _envelope(await session.call_tool(
            "plan_plugin_change", {"operation": "uninstall", "pluginId": PLUGIN_ID},
        ))
        uninstall_token = unplanned["structuredContent"]["result"]["token"]
        unexecuted = _envelope(await session.call_tool(
            "execute_plugin_change", {"token": uninstall_token, "confirmed": True},
        ))
        self.assertEqual(unexecuted["structuredContent"]["result"]["resultingState"], "absent")
        self.assertNotIn(PLUGIN_ID, await plugin_ids())

        # After uninstall the plugin is no longer runnable in the same connection.
        rejected = await session.call_tool("start_plugin_run", {
            "pluginId": PLUGIN_ID, "checkId": "TST-001",
            "scope": {"files": [{"path": str(sample)}]},
        })
        self.assertTrue(rejected.isError)
        self.assertIn("not registered", rejected.content[0].text)


def _copy_fixture_version(destination: Path, version: str) -> None:
    """Create a descriptor/manifest-consistent local release for lifecycle tests."""
    shutil.copytree(PACKAGE, destination)
    descriptor_path = destination / "assayer-plugin-release.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["pluginVersion"] = version
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    manifest_path = destination / "src" / "minimal_plugin" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata = destination / "pyproject.toml"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace(
        'version = "1.0.0"', f'version = "{version}"',
    ), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
