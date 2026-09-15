# Assayer Distribution Split

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | SDK, agent, plugin, provider, and platform split implemented; root `assayer` is platform-only |
| Owner | Assayer maintainers |
| Authority | Platform--Plugin Boundary Contract v1 §5, plugin-version-axes-v1 |

## 1. Target distributions

| Distribution | Packages | Depends on | Entry points | Status |
|---|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | `jsonschema` | — | **split** |
| `assayer-agent` | `assayer_agent` | `assayer-plugin-sdk` | — | **split** |
| `assayer-plugin-frontend-audit` | `assayer_frontend_audit` | `assayer-plugin-sdk` | `assayer.plugins` → `assayer.frontend-audit` | **compiler-generated Policy Pack** |
| `assayer-platform` | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | **split** |
| `assayer-provider-markdown` | `assayer_document_navigation` | `assayer-plugin-sdk` | `assayer.providers` → `markdown` | **split** |
| `assayer-provider-browser` | `assayer_browser_provider` | `assayer-plugin-sdk` | `assayer.providers` → `browser` | **split + release-gated** |
| `assayer` (product base) | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs | platform-only |

## 2. Layout

Each split distribution except Frontend has a build config under
`packages/<distribution>/`. The Frontend wheel is compiled exclusively from
the ordinary author source at `plugins/frontend-audit/`; no hand-written
runtime package or alternate build descriptor remains.

The other distributions build from the shared `src/` tree:

```text
packages/
  assayer-plugin-sdk/pyproject.toml
  assayer-agent/pyproject.toml
  assayer-provider-markdown/pyproject.toml
  assayer-provider-browser/pyproject.toml
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

Build the Frontend wheel through the aggregate command above. The compiler is
the only supported path and always emits the package metadata and entry point.

> Building a split wheel writes a `<package>.egg-info` directory under `src/`.
> These are gitignored, but they pollute `importlib.metadata.entry_points()` in
> the current environment and can duplicate plugin entry points during tests.
> `scripts/build_distributions.py` removes them after every build; do the same
> by hand if you build a split wheel directly.

The root `pyproject.toml` is **platform-only**: it ships `assayer_platform` and
`assayer_host`, depends on `assayer-plugin-sdk`, and no
longer declares the plugin/provider packages or their entry points. A clean
development environment installs the SDK and Agent first, then the plugin and
provider distributions alongside the platform root:

```bash
pip install -e packages/assayer-plugin-sdk
pip install -e packages/assayer-agent
# Providers may be editable during platform development.  Install the
# frontend from the compiler-produced wheel emitted by build_distributions.py.
pip install -e packages/assayer-provider-markdown \
            -e packages/assayer-provider-browser -e '.[test]'
python scripts/build_distributions.py --output /tmp/assayer-wheels
pip install --force-reinstall /tmp/assayer-wheels/assayer_plugin_frontend_audit-*.whl
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

- `assayer-agent` is an independent SDK-dependent distribution. It contains
  only the model-independent Agent loop and has no platform, plugin, or
  Provider implementation dependency.
- SDK Schema single-sourcing is complete: public contract schemas exist only in
  `assayer_plugin_sdk/schemas`, while the platform wheel ships only
  platform-owned resources and resolves shared references through the SDK.
- `assayer-provider-browser` exposes the SDK-only snapshot adapter. The Host
  now supports static or per-Run opaque provider-runtime injection; production
  browser lifecycle acceptance still belongs to the BrowserHostRuntime/J04-J08
  operator gate and is not replaced by fixture execution.
- The aggregate split build compiles the Frontend Policy Pack into the release
  wheel. The generated wheel carries the common-review registration, browser
  capability contract, and `frontend_object` subject kind. The former source
  compatibility adapter has been removed.
