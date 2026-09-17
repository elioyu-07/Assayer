---
name: assayer-plugin-development
description: "Verify a local Assayer ordinary plugin declaration and produce its exact compiled-plugin.json artifact. Use when the user asks to validate, verify, check release readiness, or compile a plugin. Do not use to install or run a plugin, or to audit a URL."
---

# Assayer Plugin Development

Use the Assayer MCP `verify_plugin_source` tool for the complete developer gate.
The Host owns declaration validation, compilation, contract-digest checking,
business-case acceptance, and artifact publication. Do not reconstruct those
phases with shell commands or inspect generated internals to decide whether the
plugin passed.

An ordinary plugin source directory contains only the declaration surface:

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
```

Ordinary plugins contain zero Python and are not Python distributions. The
verification result is exactly one `compiled-plugin.json` artifact. It is the
only artifact that may be installed or published for an ordinary plugin.

Pass the absolute local plugin source directory as `source`. For "this plugin"
or "the current plugin", use the current workspace directory only when it is
the intended plugin project. If more than one plausible project is in scope,
ask the user which directory to verify instead of guessing.

One explicit verification request authorizes creation of the generated artifact
under `<source>/.assayer/verified`; it does not authorize installation,
publication, source edits, or fixes. Never call `apply_plugin_change`, release
services, or lifecycle Run tools as part of verification.

On success, report the plugin ID and business version, exact compiled artifact
path, contract digest, and passed stages. On failure, report the returned error
code, message, and failed stage. Do not retry, relax a gate, edit the plugin,
or invent a compatibility path.

If the Assayer MCP tools are deferred, resolve the local `assayer` MCP server
through tool discovery. If `verify_plugin_source` is unavailable, say that the
installed Assayer Codex plugin is stale; do not substitute an Agent-authored
verification process.
