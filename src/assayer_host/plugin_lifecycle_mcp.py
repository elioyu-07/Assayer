"""MCP facade for the plugin installation lifecycle.

The Agent-facing product surface for plugin management.  Each tool maps one
deterministic lifecycle operation to the same ``execute_intent_step`` path the
CLI uses, so a natural-language request ("install ass-spec") and a scripted
tool call land on identical, fail-closed code.  Dangerous operations carry no
confirmation prompt at the transport layer; the Codex skill is responsible for
showing the plan and obtaining user confirmation before calling a mutating tool.
"""

from __future__ import annotations

import json
from pathlib import Path

from .plugin_intent import IntentStep
from .plugin_lifecycle_ops import (
    DEFAULT_CATALOG_URL,
    execute_intent_step,
    latest_available_from,
)


class PluginLifecycleMcpToolTransport:
    """Deterministic MCP tools for install/upgrade/uninstall/list/info/downgrade/rollback."""

    def __init__(self, store_root: str, *, catalog_index: str = DEFAULT_CATALOG_URL):
        self._store_root = str(store_root)
        self._catalog_index = catalog_index

    def _run(self, step: IntentStep) -> dict:
        return execute_intent_step(
            step, self._store_root, "./assayer-output",
            latest_available_from(self._catalog_index), self._catalog_index,
        )

    def _respond(self, payload: dict) -> dict:
        failed = payload.get("status") not in {"completed", "quarantined"}
        return {
            "structuredContent": {"status": "failed" if failed else "ok", "result": payload},
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}],
            "isError": failed,
        }

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "list_plugins",
                "description": "List installed plugins, their versions and lifecycle state, and mark plugins that have a newer version available in the public catalog as upgradable.",
                "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
            },
            {
                "name": "get_plugin_info",
                "description": "Show one installed plugin's version, state, source, gate results, and checksum. The state is one of installed, upgradable, or dirty.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["pluginId"],
                    "properties": {"pluginId": {"type": "string", "minLength": 1}},
                },
            },
            {
                "name": "install_plugin",
                "description": "Download, verify, and install a plugin by name from the public catalog (for example 'ass-spec'). Returns a conflict if already installed.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["plugin"],
                    "properties": {
                        "plugin": {"type": "string", "minLength": 1},
                        "version": {"type": "string", "minLength": 1},
                    },
                },
            },
            {
                "name": "upgrade_plugin",
                "description": "Upgrade an installed plugin to the newest version in the public catalog.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["plugin"],
                    "properties": {"plugin": {"type": "string", "minLength": 1}},
                },
            },
            {
                "name": "uninstall_plugin",
                "description": "Remove an installed plugin from the store.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["pluginId"],
                    "properties": {"pluginId": {"type": "string", "minLength": 1}},
                },
            },
            {
                "name": "downgrade_plugin",
                "description": "Pin an installed plugin to a specific earlier version.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["pluginId", "version"],
                    "properties": {
                        "pluginId": {"type": "string", "minLength": 1},
                        "version": {"type": "string", "minLength": 1},
                    },
                },
            },
            {
                "name": "rollback_plugin",
                "description": "Roll back an installed plugin to its previous version.",
                "inputSchema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["pluginId"],
                    "properties": {"pluginId": {"type": "string", "minLength": 1}},
                },
            },
        ]

    def call_tool(self, name: str, arguments: object) -> dict:
        schemas = {item["name"]: item["inputSchema"] for item in self.list_tools()}
        if name not in schemas:
            from .errors import HostError
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict):
            from .errors import HostError
            raise HostError("INVALID_REQUEST", f"{name} arguments must be an object")

        if name == "list_plugins":
            return self._respond(self._run(IntentStep("list")))
        if name == "get_plugin_info":
            return self._respond(self._run(IntentStep("info", plugin_id=arguments["pluginId"])))
        if name == "install_plugin":
            plugin = arguments["plugin"]
            if Path(plugin).expanduser().is_dir():
                step = IntentStep("install", package=str(Path(plugin).expanduser().resolve()))
            else:
                step = IntentStep("install", plugin_id=plugin)
            return self._respond(self._run(step))
        if name == "upgrade_plugin":
            plugin = arguments["plugin"]
            if Path(plugin).expanduser().is_dir():
                step = IntentStep("upgrade", package=str(Path(plugin).expanduser().resolve()))
            else:
                step = IntentStep("upgrade", plugin_id=plugin)
            return self._respond(self._run(step))
        if name == "uninstall_plugin":
            return self._respond(self._run(IntentStep("uninstall", plugin_id=arguments["pluginId"])))
        if name == "downgrade_plugin":
            return self._respond(self._run(IntentStep(
                "downgrade", plugin_id=arguments["pluginId"], version=arguments["version"],
            )))
        if name == "rollback_plugin":
            return self._respond(self._run(IntentStep("rollback", plugin_id=arguments["pluginId"])))

        from .errors import HostError
        raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")


__all__ = ["PluginLifecycleMcpToolTransport"]
