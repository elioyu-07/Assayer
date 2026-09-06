"""MCP facade for the plugin installation lifecycle."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import PluginLifecycleManager
from assayer_host.plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport
from assayer_host.plugin_lifecycle_ops import plan_plugin_change

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
PLUGIN_ID = "test-minimal"


def _catalog_plan(**overrides):
    plan = {
        "operation": "install",
        "pluginId": "ass-spec",
        "currentVersion": None,
        "targetVersion": "0.9.0",
        "currentState": "absent",
        "nextState": "installed",
        "status": "ready",
        "preconditions": [],
        "source": "https://example.com/ass-spec-0.9.0.whl",
        "checksum": "abc123",
        "gates": [],
        "requiresConfirmation": True,
    }
    plan.update(overrides)
    return plan


def _catalog_text(*, sha256="a" * 64, wheel="https://example.com/ass-spec-0.9.0.whl"):
    return json.dumps({
        "schemaVersion": "1.0.0",
        "plugins": {
            "ass-spec": {
                "pluginId": "ass-spec",
                "name": "ass-spec",
                "description": "test catalog plugin",
                "versions": {
                    "0.9.0": {
                        "pluginId": "ass-spec",
                        "version": "0.9.0",
                        "platformApiVersion": "1.0.0",
                        "wheelUrl": wheel,
                        "sha256": sha256,
                    },
                },
            },
        },
    })


class PluginLifecycleMcpTest(unittest.TestCase):
    def test_tool_names(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        self.assertEqual(
            [item["name"] for item in transport.list_tools()],
            ["list_plugins", "get_plugin_info", "plan_plugin_change", "execute_plugin_change"],
        )

    def test_plan_execute_install_info_uninstall_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)})
            self.assertFalse(planned["isError"])
            self.assertEqual(planned["structuredContent"]["result"]["plan"]["status"], "ready")
            token = planned["structuredContent"]["result"]["token"]

            installed = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertFalse(installed["isError"])
            self.assertEqual(installed["structuredContent"]["result"]["status"], "completed")
            self.assertEqual(installed["structuredContent"]["result"]["resultingState"], "installed")

            info = transport.call_tool("get_plugin_info", {"pluginId": PLUGIN_ID})
            self.assertFalse(info["isError"])
            self.assertEqual(info["structuredContent"]["result"]["pluginId"], PLUGIN_ID)

            unplanned = transport.call_tool("plan_plugin_change", {"operation": "uninstall", "pluginId": PLUGIN_ID})
            self.assertEqual(unplanned["structuredContent"]["result"]["plan"]["status"], "ready")
            removed = transport.call_tool(
                "execute_plugin_change",
                {"token": unplanned["structuredContent"]["result"]["token"], "confirmed": True},
            )
            self.assertFalse(removed["isError"])
            self.assertEqual(removed["structuredContent"]["result"]["resultingState"], "absent")

    def test_legacy_mutation_tools_require_plan(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        for tool, arguments in (
            ("install_plugin", {"plugin": "ass-spec"}),
            ("upgrade_plugin", {"plugin": "ass-spec"}),
            ("downgrade_plugin", {"pluginId": "ass-spec", "version": "0.9.0"}),
            ("rollback_plugin", {"pluginId": "ass-spec"}),
            ("uninstall_plugin", {"pluginId": "ass-spec"}),
        ):
            result = transport.call_tool(tool, arguments)
            self.assertTrue(result["isError"], tool)
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLAN_REQUIRED", tool,
            )

    def test_plan_blocked_during_active_run(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), active_run_guard=lambda: True,
            )
            result = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec"})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["result"]["error"]["code"], "RUN_ACTIVE")

    def test_plan_blocked_preconditions(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            for operation, args in (
                ("upgrade", {"plugin": "ass-spec"}),
                ("rollback", {"pluginId": "ass-spec"}),
                ("downgrade", {"pluginId": "ass-spec", "version": "0.9.0"}),
                ("uninstall", {"pluginId": "ass-spec"}),
            ):
                result = transport.call_tool("plan_plugin_change", {"operation": operation, **args})
                self.assertFalse(result["isError"], operation)
                plan = result["structuredContent"]["result"]["plan"]
                self.assertEqual(plan["status"], "blocked", operation)
                self.assertEqual(plan["blocker"]["code"], "UNKNOWN_PLUGIN", operation)
                self.assertNotIn("token", result["structuredContent"]["result"], operation)

    def test_plan_install_blocked_when_already_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)})
            transport.call_tool(
                "execute_plugin_change",
                {"token": planned["structuredContent"]["result"]["token"], "confirmed": True},
            )

            replan = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)})
            self.assertFalse(replan["isError"])
            plan = replan["structuredContent"]["result"]["plan"]
            self.assertEqual(plan["status"], "blocked")
            self.assertEqual(plan["blocker"]["code"], "PLUGIN_CONFLICT")
            self.assertNotIn("token", replan["structuredContent"]["result"])

    def test_catalog_change_invalidates_plan_token(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(_catalog_text(), encoding="utf-8")
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), catalog_index=str(catalog),
            )
            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"},
            )
            self.assertFalse(planned["isError"])
            self.assertEqual(planned["structuredContent"]["result"]["plan"]["status"], "ready")
            token = planned["structuredContent"]["result"]["token"]

            catalog.write_text(_catalog_text(sha256="b" * 64), encoding="utf-8")

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["result"]["error"]["code"], "PLAN_STALE")

    def test_read_only_tools_unaffected_by_active_run(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), active_run_guard=lambda: True,
            )
            listing = transport.call_tool("list_plugins", {})
            self.assertFalse(listing["isError"])

    def test_unknown_plugin_info_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            result = transport.call_tool("get_plugin_info", {"pluginId": "missing"})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "UNKNOWN_PLUGIN",
            )

    def test_lifecycle_transport_rejects_invalid_requests_structurally(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        for name, arguments in (("get_plugin_info", {}), ("plan_plugin_change", {}),
                                ("execute_plugin_change", {"confirmed": True}),
                                ("plan_plugin_change", {"operation": "install", "version": "1"})):
            result = transport.call_tool(name, arguments)
            self.assertTrue(result["isError"], name)
            error = result["structuredContent"]["result"]["error"]
            self.assertEqual(error["code"], "INVALID_REQUEST", name)
            self.assertFalse(error["retryable"], name)
            self.assertEqual(error["nextAction"], "correct_request", name)

    def test_mutation_result_exposes_state_and_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)})
            result = transport.call_tool("execute_plugin_change", {
                "token": planned["structuredContent"]["result"]["token"], "confirmed": True,
            })
            payload = result["structuredContent"]["result"]
            self.assertEqual(payload["resultingState"], "installed")
            self.assertEqual(payload["activeVersion"], payload["version"])
            self.assertIsNone(payload["previousVersion"])
            self.assertFalse(payload["retryable"])
            self.assertEqual(payload["nextAction"], "continue")

    def test_unknown_tool_fails_closed(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        with self.assertRaises(Exception):
            transport.call_tool("nope", {})

    def test_plan_then_execute_install_is_one_shot(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            with patch(
                "assayer_host.plugin_lifecycle_mcp.plan_plugin_change",
                return_value=_catalog_plan(),
            ), patch(
                "assayer_host.plugin_lifecycle_ops.add_from_catalog",
                return_value={"operation": "install", "status": "completed",
                              "pluginId": "ass-spec", "version": "0.9.0"},
            ) as add:
                planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"})
                self.assertFalse(planned["isError"])
                token = planned["structuredContent"]["result"]["token"]

                executed = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
                self.assertFalse(executed["isError"])
                self.assertEqual(executed["structuredContent"]["result"]["status"], "completed")
                self.assertEqual(executed["structuredContent"]["result"]["resultingState"], "installed")

                replayed = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
                self.assertTrue(replayed["isError"])
                self.assertEqual(
                    replayed["structuredContent"]["result"]["error"]["code"], "PLAN_TOKEN_USED",
                )
            add.assert_called_once_with(
                "ass-spec", version="0.9.0",
                index=transport._catalog_index, store_root=transport._store_root,
            )

    def test_execute_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(str(Path(directory) / "store"))
            with patch(
                "assayer_host.plugin_lifecycle_mcp.plan_plugin_change",
                return_value=_catalog_plan(),
            ):
                planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec"})
                token = planned["structuredContent"]["result"]["token"]

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": False})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "CONFIRMATION_REQUIRED",
            )

    def test_unknown_plan_token_fails_closed(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        result = transport.call_tool("execute_plugin_change", {"token": "nope", "confirmed": True})
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["result"]["error"]["code"], "PLAN_TOKEN_INVALID",
        )

    def test_expired_plan_token_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), plan_ttl=0.0,
            )
            with patch(
                "assayer_host.plugin_lifecycle_mcp.plan_plugin_change",
                return_value=_catalog_plan(),
            ):
                planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec"})
                token = planned["structuredContent"]["result"]["token"]

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLAN_TOKEN_EXPIRED",
            )

    def test_plan_invalidated_by_store_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = Path(directory) / "store"
            transport = PluginLifecycleMcpToolTransport(str(store_root))
            with patch(
                "assayer_host.plugin_lifecycle_mcp.plan_plugin_change",
                return_value=_catalog_plan(),
            ):
                planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec"})
                token = planned["structuredContent"]["result"]["token"]

            PluginLifecycleManager(PluginInstallationStore(store_root)).install(PACKAGE)

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLAN_STALE",
            )

    def test_plan_local_package_missing_descriptor_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            transport = PluginLifecycleMcpToolTransport(store_root)
            empty = Path(directory) / "empty"
            empty.mkdir()
            result = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(empty)})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_PACKAGE_NOT_FOUND",
            )

    def test_plan_local_package_descriptor_not_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            transport = PluginLifecycleMcpToolTransport(store_root)
            bad = Path(directory) / "bad-json"
            bad.mkdir()
            (bad / "assayer-plugin-release.json").write_text("{not json", encoding="utf-8")
            result = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(bad)})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            )

    def test_plan_local_package_descriptor_missing_plugin_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            transport = PluginLifecycleMcpToolTransport(store_root)
            no_id = Path(directory) / "no-id"
            no_id.mkdir()
            (no_id / "assayer-plugin-release.json").write_text(
                json.dumps({"pluginVersion": "1.0.0"}), encoding="utf-8",
            )
            result = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(no_id)})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            )

    def test_plan_local_package_descriptor_missing_plugin_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            transport = PluginLifecycleMcpToolTransport(store_root)
            no_version = Path(directory) / "no-version"
            no_version.mkdir()
            (no_version / "assayer-plugin-release.json").write_text(
                json.dumps({"pluginId": "test-minimal"}), encoding="utf-8",
            )
            result = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": str(no_version)})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            )

    def test_plan_local_package_nonexistent_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            missing = Path(directory) / "does-not-exist"
            with self.assertRaises(Exception) as raised:
                plan_plugin_change(
                    "install", package=str(missing), store_root=store_root,
                )
            self.assertEqual(getattr(raised.exception, "code", None), "PLUGIN_PACKAGE_NOT_FOUND")

    def test_plan_invalid_operation_fails_closed(self):
        transport = PluginLifecycleMcpToolTransport(tempfile.mkdtemp())
        result = transport.call_tool("plan_plugin_change", {"operation": "unknown", "plugin": "ass-spec"})
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["result"]["error"]["code"], "INVALID_OPERATION",
        )

    def test_step_for_invalid_operation_fails_closed(self):
        with self.assertRaises(Exception) as raised:
            PluginLifecycleMcpToolTransport._step_for(
                "unknown", {"pluginId": "ass-spec", "source": None, "targetVersion": "0.9.0"},
            )
        self.assertEqual(getattr(raised.exception, "code", None), "INVALID_OPERATION")

    def test_execute_catalog_unavailable_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(_catalog_text(), encoding="utf-8")
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), catalog_index=str(catalog),
            )
            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"},
            )
            self.assertFalse(planned["isError"])
            token = planned["structuredContent"]["result"]["token"]

            catalog.unlink()

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_CATALOG_UNAVAILABLE",
            )


if __name__ == "__main__":
    unittest.main()
