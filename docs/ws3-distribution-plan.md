# WS3 Distribution Split — Plan and Dependency DAG

| Metadata | Value |
|---|---|
| Plan | A 方案（发行解耦 + 根包平台-only） |
| Phase | 3 — root `assayer` is platform-only |
| Baseline checkpoint | `docs/ws3-distribution-baseline.md` |
| Rollback tag | `pre-platform-only-flip` |

This document freezes the six independent Assayer distributions and the
dependency direction that the A-plan install matrix (phase 2) and root-package
flip (phase 3) depend on.

## Distributions

| Distribution | Modules | Version | Runtime deps | Entry points | Shipped data |
|---|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | 0.1.2 | `jsonschema` | — | `schemas/*.json` (SDK-owned) |
| `assayer-plugin-frontend-audit` | `assayer_frontend_audit` | Policy Pack version | `assayer-plugin-sdk` | `assayer.plugins: assayer.frontend-audit` | compiler-generated contracts |
| `assayer-provider-markdown` | `assayer_document_navigation` | 0.1.0 | `assayer-plugin-sdk` | `assayer.providers: markdown` | — |
| `assayer-provider-browser` | `assayer_browser_provider` | 0.1.0 | `assayer-plugin-sdk` | `assayer.providers: browser` | `descriptor.json` |
| `assayer-agent` | `assayer_agent` | 0.1.2 | `assayer-plugin-sdk` | — | — |
| `assayer-platform` | `assayer_platform`, `assayer_host` | 0.1.2 | `assayer-plugin-sdk`, `jsonschema` | console scripts (`assayer`, `assayer-*`) | `share/assayer/schemas/*`, `share/assayer/rules/*` |

## Dependency DAG

```text
assayer-plugin-frontend-audit ─┐
assayer-provider-markdown ─────┼─► assayer-plugin-sdk   (no outgoing deps)
assayer-provider-browser ──────┘
assayer-platform ──────────────┘
```

Rules: `plugin/provider → sdk`; `platform → sdk`; `sdk → 无`. No distribution
depends on the root `assayer` meta package.

## Decisions and transitional notes

- **`assayer_agent` is independently packaged.** It depends only on the SDK
  contract and is installed explicitly alongside the platform when Agent
  orchestration is required.
- **The root `assayer` meta package is platform-only (phase 3).** It ships
  `assayer_platform` and `assayer_host`, depends on `assayer-plugin-sdk`, and
  declares no plugin/provider packages or entry points. The Codex bundle
  wheelhouse builds the SDK, Agent, frontend plugin, and markdown provider as
  separate wheels and the launcher installs them alongside `assayer[browser,mcp]`.
- **Schema ownership is single-source.** The six public contract schemas ship
  only in the SDK wheel. Platform validators compose the SDK and platform
  resource sets and reject duplicate names or `$id` values.
- **Build hygiene.** Build all six with
  `python scripts/build_distributions.py`; it cleans `src/*.egg-info` and
  `packages/*/build` after each build so staged metadata cannot pollute
  `importlib.metadata.entry_points()`. `packages/*/build/` is git-ignored.
- **Versions** track the current distribution line (`0.1.2` for sdk/agent/platform
  and providers); the frontend wheel uses the business version declared by its
  Policy Pack. Internal Check and compatibility versions remain compiler-owned.

## Phase 1 acceptance

- `python scripts/build_distributions.py` produces exactly six wheels.
- The Frontend wheel is compiled from `plugins/frontend-audit/` by the Simple
  SDK compiler. No separate hand-written Frontend runtime exists.
- Each wheel contains only its own modules (no cross-distribution leakage).
- Entry-point ownership is exclusive: only the frontend plugin declares
  `assayer.plugins`; markdown and browser providers declare `assayer.providers`,
  and neither the SDK nor the platform declares those groups.
- `tests/test_plugin_sdk_distribution.py` asserts the DAG, the scoped
  `packages` lists, and the entry-point ownership.

## Phase 3 acceptance

- The root wheel contains only `assayer_platform`, `assayer_host` and depends
  on `assayer-plugin-sdk`.
- `scripts/install_matrix.py` is green: `root-meta` installs `0/0`, and the
  duplicate-entry-point case still fails closed with `PLUGIN_CONFLICT` via a
  synthetic shadow distribution.
- The install-matrix gate inspects the freshly built root wheel and fails if it
  leaks any non-platform top-level module; `build_root` cleans the reusable
  `build/lib` before and after every root build so a stale meta-package build
  cannot ship plugin/provider modules silently.
- `scripts/build_plugin_bundle.py` produces a bundle whose wheelhouse holds the
  SDK, Agent, platform, frontend plugin, and markdown provider wheels; its
  clean-venv smoke check asserts one plugin and one provider entry point, a
  loadable registry, and the MCP tool surface.
- `plugins/assayer/scripts/prepare_assayer_runtime` installs the plugin and
  provider plus `assayer-agent` next to `assayer[browser,mcp]` from the bundle
  wheelhouse only during explicit runtime preparation. The MCP launcher only
  validates the prepared identity and executes it.

**Rollback.** `git checkout pre-platform-only-flip` restores the root meta
package. The published `v0.1.0` tag remains installable and the bundle builder
still accepts an explicit `--python`; reverting the flip and rebuilding the
bundle restores the previous wheelhouse shape.
