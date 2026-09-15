---
name: assayer-plugin-lifecycle
description: "Manage installed Assayer domain plugins in natural language: install, upgrade, downgrade, rollback, uninstall, list, and inspect. Use when the user asks to install, add, update, pin, remove, or look up an Assayer plugin. Do not use for developing or verifying plugin source (see assayer-plugin-development), running a plugin (see assayer-plugin), or auditing a URL (see assayer-audit)."
---

# Assayer Plugin Lifecycle

Use this Skill when the user asks to manage Assayer domain plugins themselves —
install, upgrade, downgrade, roll back, uninstall, list, or inspect — not to run
one and not to install Assayer itself. The Assayer MCP exposes a deterministic
product lifecycle tool; this Skill only maps a natural-language request to it.
Never hardcode a plugin's rules, versions, or checks here.

When this Skill is loaded inside Codex, use the Assayer MCP tools already
available in the current task. Do not invoke the `codex` CLI, start another
Codex task, or use a nested Agent. If `mcp__assayer__*` tools are not present
initially, resolve the deferred local `assayer` MCP server through the client's
tool-discovery mechanism before reporting the integration as unavailable.

## Which Skill handles which request

| User intent | Skill | Tools |
|---|---|---|
| Verify a local plugin source or build its verified wheel | `assayer-plugin-development` | `verify_plugin_source` |
| Manage an Assayer domain plugin (install/upgrade/remove/look up) | this Skill | `apply_plugin_change` / `list_plugins` / `get_plugin_info` |
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

For "version", "latest version", or "what version is installed" questions,
call `get_plugin_info` first and only. Do not scan the repository, manifests,
lock files, Git history, package indexes, or call `list_plugins` afterward.
Report `activeVersion` as the installed version. Report
`latestAvailableVersion` as the catalog latest only when
`latestVersionKnown=true`. When it is false, say that the upstream latest is
unknown because the catalog is unavailable or has no entry; do not retry the
catalog within the same user request. Read-only catalog lookup has a single
three-second budget. Use `versionRelation` directly: `current` means installed
and catalog versions match, `update_available` means a newer catalog release
exists, and `installed_ahead_of_catalog` means the local installation is newer
than the public catalog. Do not reinterpret these relations from repository
files.

These never mutate and need no confirmation.

## Mutations are Host-owned transactions

`install`, `upgrade`, `downgrade`, `rollback`, and `uninstall` are mutating.
Use the product-level `apply_plugin_change` tool. The Host performs planning,
source/checksum validation, isolated installation, conformance, and atomic
commit internally; the Agent must not orchestrate those phases or inspect the
repository to rediscover them.

An explicit first-time install by trusted plugin name may complete in one Host
transaction. Upgrades, downgrades, rollbacks, uninstalls, local package
sources, and repairs return one compact confirmation plan. After the user
confirms, call `apply_plugin_change` again with its token and
`confirmed=true`.

| User says (examples) | Product call |
|---|---|
| "install ass-spec" | `apply_plugin_change(operation="install", plugin="ass-spec")` |
| "install ass-spec 0.9.0" | `apply_plugin_change(operation="install", plugin="ass-spec", version="0.9.0")` |
| "upgrade ass-spec" | `apply_plugin_change(operation="upgrade", pluginId="ass-spec")` |
| "pin ass-spec to 0.9.0" | `apply_plugin_change(operation="downgrade", pluginId="ass-spec", version="0.9.0")` |
| "rollback ass-spec" | `apply_plugin_change(operation="rollback", pluginId="ass-spec")` |
| "uninstall ass-spec" | `apply_plugin_change(operation="uninstall", pluginId="ass-spec")` |

Resolve the plugin identifier from the user's words. When the identifier is
ambiguous, use `list_plugins()` or `get_plugin_info` to disambiguate rather than
guessing.

The confirmation token is one-time use, expires, and is invalidated if the
store changes underneath it. If `apply_plugin_change` returns
`PLAN_TOKEN_EXPIRED`, `PLAN_TOKEN_USED`, or `PLAN_STALE`, request a fresh plan
rather than retrying the mutation.

## Failure handling

If a plan or execute returns a `dirty`/`quarantined` state, a `PLUGIN_CONFLICT`,
or a `failed` status, surface the returned `error.code` and message to the user
and ask how to proceed; never silently retry a mutating operation. A `RUN_ACTIVE`
block means a plugin Run is in progress — finish or abort it before changing the
installed plugins.

## Install then run

When the user asks to run a plugin that is not installed, do not fail or start a
Run against a missing plugin. Instead:

1. Tell the user the plugin is missing and that you will install it first.
2. `apply_plugin_change(operation="install", plugin=...)`.
3. After a successful install, hand off to the `assayer-plugin` workflow to run
   it. If the install fails or quarantines, stop — never continue to a Run.

## Report

After a successful mutation, trust the returned `resultingState`, version, and
next action; do not call `list_plugins` or `get_plugin_info` just to repeat the
same confirmation. Query them only when the result is uncertain, dirty, or the
user explicitly asks for status. Never expose
credentials, secrets, raw wheel bytes, hidden reasoning, or internal protocol
details. Keep the answer to the lifecycle facts: what changed, what state it is
now in, and what the user can do next.
