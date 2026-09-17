# WS3 Distribution Split — Plan and Dependency DAG

| Metadata | Value |
|---|---|
| Plan | A 方案（发行解耦 + 根包平台-only） |
| Phase | 3 — root `assayer` is platform-only |
| Status | Current: three shipped Python distributions; zero concrete Providers |

The platform ships only generic Provider contracts, registry, negotiation, and
execution boundaries. Concrete source Providers are independent deployments;
the default distributions and product bundle contain none.

## Distributions

| Distribution | Modules | Runtime deps | Entry points |
|---|---|---|---|
| `assayer-plugin-sdk` | `assayer_plugin_sdk` | `jsonschema` | — |
| `assayer-agent` | `assayer_agent` | `assayer-plugin-sdk` | — |
| `assayer-platform` | `assayer_platform`, `assayer_host` | `assayer-plugin-sdk`, `jsonschema` | platform CLIs |

Ordinary plugins are compiled JSON artifacts and do not have Python
distributions. No shipped distribution declares `assayer.providers`.

## Dependency DAG

```text
assayer-agent ───────┐
assayer-platform ────┴─► assayer-plugin-sdk   (no outgoing deps)
```

## Acceptance

- `python scripts/build_distributions.py` produces exactly three Python wheels.
- `plugins/*/plugin.yaml` compiles to data-only `compiled-plugin.json` artifacts.
- Every install-matrix case observes zero ordinary plugin and zero Provider
  entry points.
- The root wheel contains only `assayer_platform` and `assayer_host`.
- The product bundle and runtime preparer contain no concrete Provider wheel.
- A run requiring an unavailable capability returns `PROVIDER_NOT_FOUND`; the
  platform never falls back to a parser, browser client, or compatibility shim.

Historical split plans that listed concrete Provider packages are retained only
for traceability and are not current implementation guidance.
