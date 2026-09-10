# Assayer Distribution Split

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | SDK, plugin, and provider split implemented; root `assayer` is platform-only; agent folded into platform |
| Owner | Assayer maintainers |
| Authority | Platform--Plugin Boundary Contract v1 §5, plugin-version-axes-v1 |

## 1. Target distributions

| Distribution | Packages | Depends on | Entry points | Status |
|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | `jsonschema` | — | **split** |
| `assayer-plugin-frontend-audit` | `assayer_frontend_audit` | `assayer-plugin-sdk` | `assayer.plugins` → `assayer.frontend-audit` | **split** |
| `assayer-platform` | `assayer_platform`, `assayer_host`, `assayer_agent` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | **split** |
| `assayer-provider-markdown` | `assayer_document_navigation` | `assayer-plugin-sdk` | `assayer.providers` → `markdown` | **split** |
| `assayer` (product base) | `assayer_platform`, `assayer_host`, `assayer_agent` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | platform-only |

## 2. Layout

Each split distribution has a build config under `packages/<distribution>/`
that builds from the shared `src/` tree:

```text
packages/
  assayer-plugin-sdk/pyproject.toml
  assayer-plugin-frontend-audit/pyproject.toml
  assayer-provider-markdown/pyproject.toml
  assayer-platform/pyproject.toml
```

Build a wheel:

```bash
python -m pip wheel --no-deps --no-build-isolation -w dist packages/assayer-plugin-sdk
```

Build every distribution and clean the staging metadata in one step:

```bash
python scripts/build_distributions.py
```

> Building a split wheel writes a `<package>.egg-info` directory under `src/`.
> These are gitignored, but they pollute `importlib.metadata.entry_points()` in
> the current environment and can duplicate plugin entry points during tests.
> `scripts/build_distributions.py` removes them after every build; do the same
> by hand if you build a split wheel directly.

The root `pyproject.toml` is **platform-only**: it ships `assayer_platform`,
`assayer_host`, and `assayer_agent`, depends on `assayer-plugin-sdk`, and no
longer declares the plugin/provider packages or their entry points. A clean
development environment installs the SDK first, then the plugin and provider
distributions alongside the platform root:

```bash
pip install -e packages/assayer-plugin-sdk
pip install -e packages/assayer-plugin-frontend-audit \
            -e packages/assayer-provider-markdown -e '.[test]'
```

## 3. Rules

1. A distribution MUST declare its dependencies; it MUST NOT vendor another
   distribution's package.
2. A plugin or provider distribution MUST depend only on `assayer-plugin-sdk`
   and the standard library, never on `assayer-platform` or `assayer-host`.
3. The SDK wheel MUST carry its schemas (`assayer_plugin_sdk/schemas/*.json`).
4. The platform sentinel for its resource directory MUST name a platform-owned
   schema (see `plugin-sdk-schema-ownership.md` §6).

## 4. Remaining work

- A standalone `assayer-agent` distribution remains an optional, deferred
  decision; `assayer_agent` currently ships inside `assayer-platform` and the
  platform-only root.
- Single-sourcing the schema copies still shipped by the platform wheel lands at
  WS4/WS10; a drift test guards the copies today.
