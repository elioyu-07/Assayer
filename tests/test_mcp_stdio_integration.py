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
POLICY_PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "policy-pack"
PLUGIN_ID = "test.policy-pack"

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


def _review_submission(task: dict) -> dict:
    decisions = []
    for item in task["items"]:
        support = {"refs": item["support"]}
        if item["kind"] == "dimension":
            value = {
                "dimension": item["dimension"],
                "verdict": "violated",
                "reason": "The required overview is absent from the frozen document.",
                "applicability": {"state": "applicable"},
                "confidence": {"level": "high"},
                "support": support,
            }
        elif item["kind"] == "candidate":
            value = {
                "disposition": "confirmed",
                "reason": "The candidate is confirmed by the complete document snapshot.",
                "support": support,
                "finding": {
                    "title": item["subject"],
                    "message": item["message"],
                    "severity": item["severity"],
                    "recommendation": item["recommendation"],
                    "support": support,
                },
            }
        else:
            value = {
                "relationship": item["relationship"],
                "verdict": "rejected",
                "reason": "The declared relationship is not satisfied.",
                "applicability": {"state": "applicable"},
                "confidence": {"level": "high"},
                "support": support,
            }
        decisions.append({
            "itemRef": item["itemRef"], "kind": item["kind"], "value": value,
        })
    return {"decisions": decisions}


class PluginLifecycleStdioIntegrationTest(unittest.TestCase):
    """Drive ``assayer-mcp`` over a real stdio MCP connection."""

    @unittest.skipUnless(_MCP_AVAILABLE, "MCP optional dependency is not installed")
    def test_install_run_uninstall_over_one_stdio_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / "store")
            output_root = str(Path(directory) / "out")
            sample = Path(directory) / "sample.md"
            sample.write_text(
                "# Delivery notes\n\nImplementation details without an introductory summary.\n",
                encoding="utf-8",
            )
            policy = Path(directory) / "policy"
            shutil.copytree(POLICY_PACKAGE, policy)
            asyncio.run(self._journey(store, output_root, sample, policy))

    async def _journey(
        self, store: str, output_root: str, sample: Path, policy: Path,
    ) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", _SERVER_BOOTSTRAP, "--store", store, "--output-root", output_root],
            env=_server_env(),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await self._drive(session, sample, policy)

    async def _drive(
        self, session: ClientSession, sample: Path, policy: Path,
    ) -> None:
        listed_tools = (await session.list_tools()).tools
        tools = {tool.name for tool in listed_tools}
        self.assertTrue(
            {
                "verify_plugin_source", "list_plugins", "get_plugin_info",
                "plan_plugin_change", "execute_plugin_change",
            } <= tools,
        )
        annotations = {tool.name: tool.annotations for tool in listed_tools}
        self.assertTrue(annotations["plan_plugin_change"].readOnlyHint)
        self.assertFalse(annotations["plan_plugin_change"].destructiveHint)
        self.assertFalse(annotations["execute_plugin_change"].readOnlyHint)
        self.assertTrue(annotations["execute_plugin_change"].destructiveHint)
        self.assertFalse(annotations["apply_plugin_change"].readOnlyHint)
        self.assertFalse(annotations["verify_plugin_source"].readOnlyHint)
        self.assertFalse(annotations["verify_plugin_source"].destructiveHint)

        verified = _envelope(await session.call_tool(
            "verify_plugin_source", {"source": str(policy)},
        ))
        verification = verified["structuredContent"]["result"]
        self.assertEqual(verification["status"], "passed", verification)
        self.assertEqual(verification["pluginId"], "test.policy-pack")
        self.assertTrue(Path(verification["wheel"]).is_file())

        async def plugin_ids():
            listing = _envelope(await session.call_tool("list_plugins", {}))
            return {
                plugin["pluginId"]
                for plugin in listing["structuredContent"]["result"]["plugins"]
            }

        self.assertNotIn(PLUGIN_ID, await plugin_ids())

        # plan -> confirm -> execute: compile and install the verified Policy Pack.
        planned = _envelope(await session.call_tool(
            "plan_plugin_change", {"operation": "install", "plugin": str(policy)},
        ))
        self.assertEqual(planned["structuredContent"]["result"]["plan"]["status"], "ready")
        token = planned["structuredContent"]["result"]["token"]

        executed = _envelope(await session.call_tool(
            "execute_plugin_change", {"token": token, "confirmed": True},
        ))
        self.assertEqual(executed["structuredContent"]["result"]["resultingState"], "installed")

        # Same connection now sees the freshly installed plugin.
        self.assertIn(PLUGIN_ID, await plugin_ids())
        templates = (await session.list_resource_templates()).resourceTemplates
        self.assertIn(
            "assayer://plugins/{plugin_id}/checks/{check_id}/{check_version}/semantic-instructions",
            {str(item.uriTemplate) for item in templates},
        )

        # The freshly installed plugin is runnable in this same connection.
        started = await session.call_tool("start_plugin_run", {
            "pluginId": PLUGIN_ID, "checkId": "POLICY-001",
            "scope": {"files": [{"path": str(sample)}]},
        })
        self.assertFalse(started.isError)
        started_result = _envelope(started)["structuredContent"]["result"]

        # Drive the same public workflow Codex uses through a terminal result.
        advanced = _envelope(await session.call_tool("advance_plugin_run", {}))
        advanced_result = advanced["structuredContent"]["result"]
        self.assertEqual(advanced_result["status"], "awaiting_agent_decision")
        task = advanced_result["result"]["semanticTask"]
        self.assertEqual(task["kind"], "common_review")
        self.assertNotIn("workItemId", task)
        self.assertEqual(
            advanced_result["result"]["workflow"]["requiredNextStep"],
            "submit_common_review",
        )
        semantic_uri = task["semanticInstructions"]["uri"]
        self.assertEqual(
            semantic_uri,
            f"assayer://plugins/{PLUGIN_ID}/checks/POLICY-001/1.0.0/semantic-instructions",
        )
        semantic_resource = await session.read_resource(semantic_uri)
        self.assertEqual(len(semantic_resource.contents), 1)
        self.assertEqual(
            semantic_resource.contents[0].text,
            (policy / "semantic-review.md").read_text(
                encoding="utf-8",
            ),
        )
        terminal = _envelope(await session.call_tool("advance_plugin_run", {
            "reviewSubmission": _review_submission(task),
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
        package_v2 = sample.parent / "policy-pack-1.1.0"
        _copy_policy_version(policy, package_v2, "1.1.0")
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
            "rerunAuthorization": {
                "previousRunId": started_result["runId"], "userConfirmed": True,
            },
        })
        self.assertTrue(rejected.isError)
        self.assertIn("not registered", rejected.content[0].text)


def _copy_policy_version(source: Path, destination: Path, version: str) -> None:
    """Create a business-version variant of an ordinary Policy Pack."""
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".assayer"))
    declaration = destination / "plugin.yaml"
    declaration.write_text(
        declaration.read_text(encoding="utf-8").replace(
            "version: 1.0.0", f"version: {version}",
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
