---
name: assayer-plugin-development
description: "Verify a local Assayer plugin source project and produce its exact release wheel. Use when the user asks to validate, verify, check release readiness, or build a verified artifact for a Policy Pack, Simple SDK plugin, or Advanced SPI plugin. Do not use to install or run a plugin, or to audit a URL."
---

# Assayer Plugin Development

Use the Assayer MCP `verify_plugin_source` tool for the complete developer gate.
The tool owns source recognition, Simple compilation, isolated wheel building,
contract validation, installation checks, generated lifecycle acceptance, and
artifact publication. Do not reconstruct those phases with shell commands or
inspect generated platform contracts to decide whether the plugin passed.

Pass the absolute local plugin source directory as `source`. For "this plugin"
or "the current plugin", use the current workspace directory only when it is
the intended plugin project. If more than one plausible project is in scope,
ask the user which directory to verify instead of guessing.

One explicit verification request authorizes creation of the generated wheel
under `<source>/.assayer/verified`; it does not authorize installation,
publication, source edits, or fixes. Never call `apply_plugin_change`, release
services, or lifecycle Run tools as part of verification.

On success, report the plugin ID and business version, exact wheel path,
SHA-256, and passed stages. On failure, report the returned error code, message,
and failed stage. Do not retry, relax a gate, edit the plugin, or switch to the
Advanced SPI unless the user separately asks for a change.

If the Assayer MCP tools are deferred, resolve the local `assayer` MCP server
through tool discovery. If `verify_plugin_source` is genuinely unavailable,
say that the installed Assayer Codex plugin is stale; do not substitute an
Agent-authored verification process.
