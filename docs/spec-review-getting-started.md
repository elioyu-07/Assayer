# Getting Started: Reviewing a Specification with Assayer

This guide walks a new user from zero to a completed specification review. It is
the shortest path for the `ass-spec` journey: install the platform, install the
`ass-spec` plugin, and ask for a review in a fresh Codex task.

> **Status:** `ass-spec` is Usable Alpha. The flow below works end-to-end today.
> The plugin is built from source and installed through a personal Codex
> marketplace, then installed and managed in natural language inside Codex. The
> main acceptance evidence in this repository is the anonymous URL audit; the
> spec journey is the second cross-domain reference.

## 1. Prerequisites

| Requirement | Why |
|---|---|
| Python package runtime | CPython 3.11–3.13 are supported by the packages and CI (3.11/3.13 full gate; 3.12 fast gate). |
| Codex Alpha bundle | The currently published bundle is pinned to macOS arm64 + CPython 3.13. A mismatch fails explicitly with `ASSAYER_BUNDLE_INCOMPATIBLE`; this is a bundle limitation, not a package support limitation. |
| Python | External prerequisite; Assayer does not install it. |
| Chromium | Only needed for web URL audits. Spec review is a read-only file journey and never starts a browser. |

## 2. Install the Assayer platform

Install the Assayer Codex Plugin from a personal Codex marketplace. Installing
this one deliverable brings the Skill, MCP server, rules, schemas, and an offline
wheelhouse with it; there is nothing to configure by hand.

Prepare the private runtime once and verify readiness:

```bash
assayer doctor --target ./spec.md --fix
```

This explicit preparation may create a venv and perform an offline install.
Normal MCP starts do not repeat that work.

Details: [installation](j01-install-delivery.md) and
[activation and discovery](j02-activation-and-discovery.md).

## 3. Install the `ass-spec` plugin

Spec review is **not** built into the platform. `ass-spec` is an independent
distribution that lives in its own repository and is installed separately, then
discovered through the `assayer.plugins` entry point.

Once the Assayer Plugin is enabled, install `ass-spec` in a fresh Codex task by
asking in natural language:

```text
install ass-spec
```

For a first-time install by trusted catalog name, the Host resolves and applies
the deterministic transaction directly (download → checksum verification →
conformance gate → materialize). Local packages, repairs, upgrades, downgrades,
rollbacks, and removals still show one confirmation plan.
You never point the platform at a path or write an entry point by hand.

The same natural-language path covers the rest of the lifecycle:

```text
what plugins do i have
upgrade ass-spec
remove ass-spec
```

For the deterministic substrate and its error codes, see the
[agent-first plugin lifecycle](agent-first-plugin-lifecycle-design.md).

## 4. Continue in the same task

The Host reads the durable plugin store for the Run, so a successful `ass-spec`
install can be followed immediately by a review. A new task is only needed when
the Assayer Codex Plugin itself was installed or upgraded and Codex must reload
its Skill and MCP definitions.

## 5. Ask in natural language

Then say:

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
| `ASSAYER_BUNDLE_INCOMPATIBLE` | The bundled runtime does not match its manifest. Use the current macOS arm64 + CPython 3.13 Alpha bundle, or run the Python packages directly on a supported 3.11–3.13 runtime. |
| `dirty` / quarantine on install | A conformance gate failed. The plugin is visible but not runnable; inspect the gate report before retrying. |

## Related

- [Plugin lifecycle (interactive)](agent-first-plugin-lifecycle-design.md)
- [Plugin development](plugin-development.md)
- [Canonical result contract](canonical-result-contract-v1.md)
