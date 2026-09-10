# Assayer Distribution Split

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | SDK and frontend-audit split implemented; provider, agent, and the root meta remain |
| Owner | Assayer maintainers |
| Authority | Platform--Plugin Boundary Contract v1 §5, plugin-version-axes-v1 |

## 1. Target distributions

| Distribution | Packages | Depends on | Entry points | Status |
|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | `jsonschema` | — | **split** |
| `assayer-plugin-frontend-audit` | `assayer_frontend_audit` | `assayer-plugin-sdk` | `assayer.plugins` → `assayer.frontend-audit` | **split** |
| `assayer-platform` | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | pending |
| `assayer-provider-markdown` | `assayer_document_navigation` | `assayer-plugin-sdk` | `assayer.providers` → `markdown` | pending |
| `assayer-agent` | `assayer_agent` | `assayer-plugin-sdk` | — | pending |
| `assayer` (dev meta) | all of the above re-exported | each of the above | all | current root build, unchanged |

## 2. Layout

Each split distribution has a build config under `packages/<distribution>/`
that builds from the shared `src/` tree:

```text
packages/
  assayer-plugin-sdk/pyproject.toml
  assayer-plugin-frontend-audit/pyproject.toml
```

Build a wheel:

```bash
python -m pip wheel --no-deps --no-build-isolation -w dist packages/assayer-plugin-sdk
```

> Building a split wheel writes a `<package>.egg-info` directory under `src/`.
> These are gitignored, but they pollute `importlib.metadata.entry_points()` in
> the current environment and can duplicate plugin entry points during tests.
> Run split-wheel builds in a dedicated/clean CI job, or remove the stray
> `src/*.egg-info` directories (except `src/assayer.egg-info`) before tests.

The root `pyproject.toml` continues to build one development distribution so a
single `pip install -e .` keeps the whole tree importable and all entry points
registered. The split is a packaging change only; source imports are already
SDK-only where required.

## 3. Rules

1. A distribution MUST declare its dependencies; it MUST NOT vendor another
   distribution's package.
2. A plugin or provider distribution MUST depend only on `assayer-plugin-sdk`
   and the standard library, never on `assayer-platform` or `assayer-host`.
3. The SDK wheel MUST carry its schemas (`assayer_plugin_sdk/schemas/*.json`).
4. The platform sentinel for its resource directory MUST name a platform-owned
   schema (see `plugin-sdk-schema-ownership.md` §6).

## 4. Remaining work

- **Provider split** requires moving `ProviderRegistration` and
  `load_provider_descriptor` into the SDK (they currently live in
  `assayer_platform.provider_registry`) with `capability-provider.schema.json`
  and `common.schema.json` in the SDK schema set, then switching
  `assayer_document_navigation` to SDK-only imports.
- **Agent split** and the **platform** distribution follow the same pattern.
- The root `pyproject.toml` is reduced to the development meta once every
  component has its own build config.
