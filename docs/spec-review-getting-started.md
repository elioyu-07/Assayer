# Getting Started: Reviewing a Specification with Assayer

This guide walks a new user from zero to a completed specification review. It is
the shortest path for the `ass-spec` journey: install the platform, install the
`ass-spec` plugin, and ask for a review in a fresh Codex task.

> **Status:** `ass-spec` is Usable Alpha. The flow below works end-to-end today,
> but the plugin is currently built from source and installed through a personal
> Codex marketplace. A stable public installation and lifecycle flow is not yet
> delivered. The main acceptance evidence in this repository is the anonymous URL
> audit; the spec journey is the second cross-domain reference.

## 1. Prerequisites

| Requirement | Why |
|---|---|
| macOS arm64 + CPython 3.13 | The current Alpha bundle is platform- and Python-minor-pinned. A mismatch fails explicitly with `ASSAYER_BUNDLE_INCOMPATIBLE`. |
| Python | External prerequisite; Assayer does not install it. |
| Chromium | Only needed for web URL audits. Spec review is a read-only file journey and never starts a browser. |

## 2. Install the Assayer platform

Install the Assayer Codex Plugin from a personal Codex marketplace. Installing
this one deliverable brings the Skill, MCP server, rules, schemas, and an offline
wheelhouse with it; there is nothing to configure by hand.

Details: [installation](j01-install-delivery.md) and
[activation and discovery](j02-activation-and-discovery.md).

## 3. Install the `ass-spec` plugin

Spec review is **not** built into the platform. `ass-spec` is an independent
distribution that lives in its own repository and is installed separately, then
discovered through the `assayer.plugins` entry point.

- Today this means installing it from source through a personal marketplace, the
  same way as the platform. A public, one-command install flow is still open work.
- Once installed, `installed_plugin_registry()` discovers it automatically; you
  never point the platform at a path or write an entry point by hand.

## 4. Open a fresh Codex CLI task

Plugins are discovered when a task starts. An already-open task does not pick up
a newly installed plugin, so **open a new task** after installing `ass-spec`.

## 5. Ask in natural language

In that fresh task, say:

```text
Audit this project's spec.md
```

or, to target the plugin explicitly:

```text
review spec.md with ass-spec
```

You do not supply `scanId`, `runId`, `outputDir`, or any protocol fields. The
platform owns those. You give the intent and the target document; the Skill
resolves the installed plugin and drives the domain-neutral lifecycle
(`start_plugin_run` → `advance_plugin_run` → semantic review → finalize).

What happens under the hood:

1. The deterministic `ass-spec` runtime reads the Markdown and emits bounded
   candidate excerpts against the canonical 18-point quality policy.
2. The Agent performs the semantic judgment that software cannot — deciding
   whether a candidate is actually incomplete, ambiguous, contradictory, or
   missing a definition.
3. The platform still owns evidence closure, decision gates, receipts, and
   publication. The runtime never promotes a scanner hit into a final finding on
   its own.

## 6. Read the result

Every run ends in one platform status — `completed`, `partial`, or `failed` —
and publishes:

- `canonical-result.json` — the formal, immutable result, bound to the exact
  ledger digest. This is the single source of truth.
- a plain-language summary and a run diary — presentation artifacts that
  explain the result; they never override `canonical-result.json`.

Individual checks resolve to one of five decisions: `issue_found`,
`scanned_no_issue`, `not_applicable`, `needs_review`, or `noise`.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Plugin does not appear in the catalog | You kept an old task open. Install `ass-spec`, then open a **new** task. |
| "not installed" hint when you run | `ass-spec` was not installed as a separate distribution. Install it (step 3). |
| `ASSAYER_BUNDLE_INCOMPATIBLE` | The platform/OS mismatch. Use macOS arm64 with CPython 3.13. |
| `dirty` / quarantine on install | A conformance gate failed. The plugin is visible but not runnable; inspect the gate report before retrying. |

## Related

- [Plugin lifecycle (interactive)](agent-first-plugin-lifecycle-design.md)
- [Plugin development](plugin-development.md)
- [Canonical result contract](canonical-result-contract-v1.md)
