---
name: assayer-plugin-lifecycle
description: "Manage installed Assayer plugins in natural language: install, upgrade, downgrade, rollback, uninstall, list, and inspect. Use when the user asks to install, add, update, pin, remove, or look up an Assayer plugin; do not use for running a plugin or auditing content."
---

# Assayer Plugin Lifecycle

Use this Skill when the user asks to manage Assayer plugins themselves — install,
upgrade, downgrade, roll back, uninstall, list, or inspect — not to run one. The
Assayer MCP exposes the deterministic lifecycle tools; this Skill only teaches
how to map a natural-language request to them. Never hardcode a plugin's rules,
versions, or checks here.

When this Skill is loaded inside Codex, use the Assayer MCP tools already
available in the current task. Do not invoke the `codex` CLI, start another
Codex task, or use a nested Agent. If `mcp__assayer__*` tools are not present
initially, resolve the deferred local `assayer` MCP server through the client's
tool-discovery mechanism before reporting the integration as unavailable.

## Map intent to tools

| User says (examples) | Tool |
|---|---|
| "install ass-spec", "add ass-spec" | `install_plugin(plugin="ass-spec")` |
| "what plugins do i have", "list plugins" | `list_plugins()` |
| "tell me about ass-spec", "info ass-spec" | `get_plugin_info(pluginId="ass-spec")` |
| "upgrade ass-spec", "update ass-spec" | `upgrade_plugin(plugin="ass-spec")` |
| "remove ass-spec", "uninstall ass-spec" | `uninstall_plugin(pluginId="ass-spec")` |
| "pin ass-spec to 0.9.0" | `downgrade_plugin(pluginId="ass-spec", version="0.9.0")` |
| "undo the last update", "rollback ass-spec" | `rollback_plugin(pluginId="ass-spec")` |

Resolve the plugin identifier from the user's words. When the identifier is
ambiguous, use `list_plugins()` or `get_plugin_info` to disambiguate rather than
guessing.

## Confirm before mutating

`install_plugin`, `upgrade_plugin`, `downgrade_plugin`, `rollback_plugin`, and
`uninstall_plugin` mutate the durable store. Before calling one of them, tell the
user in one sentence what you are about to do (the plugin and, when relevant,
the version) and wait for an explicit yes. Read-only tools (`list_plugins`,
`get_plugin_info`) require no confirmation.

If a tool returns a `dirty`/`quarantined` state, a `PLUGIN_CONFLICT`, or a
`failed` status, surface the returned `error.code` and message to the user and
ask how to proceed; never silently retry a mutating operation.

## Report

After any mutation, confirm the outcome with `list_plugins()` or
`get_plugin_info` and tell the user the resulting state. Never expose
credentials, secrets, raw wheel bytes, hidden reasoning, or internal protocol
details. Keep the answer to the lifecycle facts: what changed, what state it is
now in, and what the user can do next.
