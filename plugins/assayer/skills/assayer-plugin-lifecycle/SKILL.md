---
name: assayer-plugin-lifecycle
description: "Manage installed Assayer domain plugins in natural language: install, upgrade, downgrade, rollback, uninstall, list, and inspect. Use when the user asks to install, add, update, pin, remove, or look up an Assayer plugin. Do not use for running a plugin (see assayer-plugin) or auditing a URL (see assayer-audit)."
---

# Assayer Plugin Lifecycle

Use this Skill when the user asks to manage Assayer domain plugins themselves —
install, upgrade, downgrade, roll back, uninstall, list, or inspect — not to run
one and not to install Assayer itself. The Assayer MCP exposes the deterministic
lifecycle tools; this Skill only teaches how to map a natural-language request to
them. Never hardcode a plugin's rules, versions, or checks here.

When this Skill is loaded inside Codex, use the Assayer MCP tools already
available in the current task. Do not invoke the `codex` CLI, start another
Codex task, or use a nested Agent. If `mcp__assayer__*` tools are not present
initially, resolve the deferred local `assayer` MCP server through the client's
tool-discovery mechanism before reporting the integration as unavailable.

## Which Skill handles which request

| User intent | Skill | Tools |
|---|---|---|
| Manage an Assayer domain plugin (install/upgrade/remove/look up) | this Skill | `plan_plugin_change` / `execute_plugin_change` / `list_plugins` / `get_plugin_info` |
| Run an installed plugin against an input | `assayer-plugin` | `start_plugin_run`, `advance_plugin_run`, … |
| Audit a web URL | `assayer-audit` | `start_audit`, … |
| Install/upgrade Assayer itself (the Codex plugin) | Codex Marketplace / product lifecycle | not an MCP tool |

If "install" or "update" is ambiguous between Assayer itself and a domain
plugin, ask which one the user means. If the user asks to run a plugin that is
not installed, tell them it is missing and offer to install it first (see
"Install then run" below).

## Read-only tools

| User says (examples) | Tool |
|---|---|
| "what plugins do i have", "list plugins" | `list_plugins()` |
| "tell me about ass-spec", "info ass-spec" | `get_plugin_info(pluginId="ass-spec")` |

These never mutate and need no confirmation.

## Mutations are two-phase: plan, confirm, execute

`install`, `upgrade`, `downgrade`, `rollback`, and `uninstall` are mutating.
Always use the two-phase flow — never call the single-shot mutation tools
directly when the plan gate is available:

1. Call `plan_plugin_change(operation=..., ...)` and read the returned `plan`
   (operation, pluginId, current and target version, current and next state,
   source, checksum, gates) plus the one-time `token`.
2. Show the plan to the user in one or two sentences: the plugin, the version
   change, and the source. Ask for an explicit yes.
3. Only after the user confirms, call
   `execute_plugin_change(token=..., confirmed=true)`.

| User says (examples) | Plan call |
|---|---|
| "install ass-spec" | `plan_plugin_change(operation="install", plugin="ass-spec")` |
| "install ass-spec 0.9.0" | `plan_plugin_change(operation="install", plugin="ass-spec", version="0.9.0")` |
| "upgrade ass-spec" | `plan_plugin_change(operation="upgrade", plugin="ass-spec")` |
| "pin ass-spec to 0.9.0" | `plan_plugin_change(operation="downgrade", pluginId="ass-spec", version="0.9.0")` |
| "rollback ass-spec" | `plan_plugin_change(operation="rollback", pluginId="ass-spec")` |
| "uninstall ass-spec" | `plan_plugin_change(operation="uninstall", pluginId="ass-spec")` |

Resolve the plugin identifier from the user's words. When the identifier is
ambiguous, use `list_plugins()` or `get_plugin_info` to disambiguate rather than
guessing.

The plan token is one-time use, expires, and is invalidated if the store changes
underneath it. If `execute_plugin_change` returns `PLAN_TOKEN_EXPIRED`,
`PLAN_TOKEN_USED`, or `PLAN_STALE`, call `plan_plugin_change` again for a fresh
plan rather than retrying the execute.

## Failure handling

If a plan or execute returns a `dirty`/`quarantined` state, a `PLUGIN_CONFLICT`,
or a `failed` status, surface the returned `error.code` and message to the user
and ask how to proceed; never silently retry a mutating operation. A `RUN_ACTIVE`
block means a plugin Run is in progress — finish or abort it before changing the
installed plugins.

## Install then run

When the user asks to run a plugin that is not installed, do not fail or start a
Run against a missing plugin. Instead:

1. `get_plugin_info(pluginId=...)` (or `list_plugins()`) to confirm it is absent.
2. Tell the user the plugin is missing and that you will install it first.
3. `plan_plugin_change(operation="install", plugin=...)`, show the plan, wait.
4. `execute_plugin_change(token=..., confirmed=true)`.
5. After a successful install, hand off to the `assayer-plugin` workflow to run
   it. If the install fails or quarantines, stop — never continue to a Run.

## Report

After any mutation, confirm the outcome with `list_plugins()` or
`get_plugin_info` and tell the user the resulting state. Never expose
credentials, secrets, raw wheel bytes, hidden reasoning, or internal protocol
details. Keep the answer to the lifecycle facts: what changed, what state it is
now in, and what the user can do next.
