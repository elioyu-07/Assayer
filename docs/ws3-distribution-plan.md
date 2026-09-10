# WS3 Distribution Split — Plan and Dependency DAG

| Metadata | Value |
|---|---|
| Plan | A 方案（发行解耦 + 根包平台-only） |
| Phase | 3 — root `assayer` is platform-only |
| Baseline checkpoint | `docs/ws3-distribution-baseline.md` |
| Rollback tag | `pre-platform-only-flip` |

This document freezes the four independent Assayer distributions and the
dependency direction that the A-plan install matrix (phase 2) and root-package
flip (phase 3) depend on.

## Distributions

| Distribution | Modules | Version | Runtime deps | Entry points | Shipped data |
|---|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | 0.1.2 | `jsonschema` | — | `schemas/*.json` (SDK-owned) |
| `assayer-plugin-frontend-audit` | `assayer_frontend_audit` | 0.1.0 | `assayer-plugin-sdk` | `assayer.plugins: assayer.frontend-audit` | `manifest.json`, `semantic-review.md` |
| `assayer-provider-markdown` | `assayer_document_navigation` | 0.1.0 | `assayer-plugin-sdk` | `assayer.providers: markdown` | — |
| `assayer-platform` | `assayer_platform`, `assayer_host`, `assayer_agent` | 0.1.2 | `assayer-plugin-sdk`, `jsonschema` | console scripts (`assayer`, `assayer-*`) | `share/assayer/schemas/*`, `share/assayer/rules/*` |

## Dependency DAG

```text
assayer-plugin-frontend-audit ─┐
assayer-provider-markdown ─────┼─► assayer-plugin-sdk   (no outgoing deps)
assayer-platform ──────────────┘
```

Rules: `plugin/provider → sdk`; `platform → sdk`; `sdk → 无`. No distribution
depends on the root `assayer` meta package.

## Decisions and transitional notes

- **`assayer_agent` is folded into `assayer-platform` for now.** It is
  stdlib-only and imported by nothing in the tree. Splitting it into its own
  `assayer-agent` distribution remains a deferred, optional decision; folding
  it in avoids silently dropping the module when the root package becomes
  platform-only.
- **The root `assayer` meta package is platform-only (phase 3).** It ships
  `assayer_platform`, `assayer_host`, and `assayer_agent`, depends on
  `assayer-plugin-sdk`, and declares no plugin/provider packages or entry
  points. The Codex bundle wheelhouse builds the SDK, frontend plugin, and
  markdown provider as separate wheels and the launcher installs them alongside
  `assayer[browser,mcp]`.
- **Interim schema duplication.** The platform wheel still ships every repo
  schema, including the six SDK-owned ones. Single-sourcing lands at WS4/WS10;
  a drift test guards the copies today.
- **Build hygiene.** Build all four with
  `python scripts/build_distributions.py`; it cleans `src/*.egg-info` and
  `packages/*/build` after each build so staged metadata cannot pollute
  `importlib.metadata.entry_points()`. `packages/*/build/` is git-ignored.
- **Versions** track the current distribution line (`0.1.2` for sdk/platform,
  `0.1.0` for the plugin/provider) until the SDK version axis is cut at WS6.

## Phase 1 acceptance

- `python scripts/build_distributions.py` produces exactly four wheels.
- Each wheel contains only its own modules (no cross-distribution leakage).
- Entry-point ownership is exclusive: only the frontend plugin declares
  `assayer.plugins`, only the markdown provider declares `assayer.providers`,
  and neither the SDK nor the platform declares those groups.
- `tests/test_plugin_sdk_distribution.py` asserts the DAG, the scoped
  `packages` lists, and the entry-point ownership.

## Phase 3 acceptance

- The root wheel contains only `assayer_platform`, `assayer_host`,
  `assayer_agent` and depends on `assayer-plugin-sdk`.
- `scripts/install_matrix.py` is green: `root-meta` installs `0/0`, and the
  duplicate-entry-point case still fails closed with `PLUGIN_CONFLICT` via a
  synthetic shadow distribution.
- `scripts/build_plugin_bundle.py` produces a bundle whose wheelhouse holds the
  SDK, frontend plugin, and markdown provider wheels; its clean-venv smoke check
  asserts one plugin and one provider entry point, a loadable registry, and the
  MCP tool surface.
- `plugins/assayer/scripts/launch_assayer_mcp` installs the plugin and provider
  next to `assayer[browser,mcp]` from the bundle wheelhouse.

**Rollback.** `git checkout pre-platform-only-flip` restores the root meta
package. The published `v0.1.0` tag remains installable and the bundle builder
still accepts an explicit `--python`; reverting the flip and rebuilding the
bundle restores the previous wheelhouse shape.
