# Assayer Distribution Split

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | SDK, platform, and compiled-plugin artifact split implemented; no concrete Provider is shipped |
| Owner | Assayer maintainers |
| Authority | Platform--Plugin Boundary Contract v1 §5, plugin-version-axes-v1 |

> **Plugin-specific release inventory.** This document names the concrete
> distributions currently shipped. It does not add any plugin or provider
> name to the platform vocabulary or constitution.

## 1. Target distributions

| Distribution | Packages | Depends on | Entry points | Status |
|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | `jsonschema` | — | **split** |
| `assayer-platform` | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | **split** |
| `assayer` (product base) | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | platform-only |

## 2. Layout

Each Python distribution has a build config under `packages/<distribution>/`.
Ordinary plugins are compiled from declaration sources into a standalone
`compiled-plugin.json` artifact and do not have a Python distribution.

The other distributions build from the shared `src/` tree:

```text
packages/
  assayer-plugin-sdk/pyproject.toml
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

The aggregate command builds only platform and SDK wheels.
Plugin declarations are verified separately and emit only `compiled-plugin.json`.

> Building a split wheel writes a `<package>.egg-info` directory under `src/`.
> These are gitignored, but they pollute `importlib.metadata.entry_points()` in
> the current environment and can duplicate plugin entry points during tests.
> `scripts/build_distributions.py` removes them after every build; do the same
> by hand if you build a split wheel directly.

The root `pyproject.toml` is **platform-only**: it ships `assayer_platform` and
`assayer_host`, depends on `assayer-plugin-sdk`, and no
longer declares the plugin/provider packages or their entry points. A clean
development environment installs the SDK first, then the platform root
alongside it:

```bash
pip install -e packages/assayer-plugin-sdk
pip install -e '.[test]'
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

- The `assayer-agent` distribution was retired: its model-independent Agent
  loop drove the removed vertical audit protocol and had no v2 consumer, so the
  Agent role is owned by the platform and the external Host client instead.
- SDK Schema single-sourcing is complete: public contract schemas exist only in
  `assayer_plugin_sdk/schemas`, while the platform wheel ships only
  platform-owned resources and resolves shared references through the SDK.
- Concrete capability Providers are intentionally absent from this release.
  The platform retains only the generic Provider contract, registry, and
  negotiation boundary; a request requiring an unavailable capability fails
  closed.
- The product bundle carries the Frontend compiled contract as data under its
  runtime plugin directory. It is loaded by the platform lifecycle and does not
  register a Python entry point.
