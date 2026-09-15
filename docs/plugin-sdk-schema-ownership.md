# Plugin SDK Schema Ownership

| Metadata | Value |
|---|---|
| Document version | 1.1.2 |
| Date | 2026-09-14 |
| Status | Single-source ownership implemented |
| Owner | Assayer maintainers |

## 1. Canonical ownership

The plugin SDK is the only source for the generated internal/Advanced SPI plugin
and capability contracts in `src/assayer_plugin_sdk/schemas/`:

- `common.schema.json`
- `plugin-manifest.schema.json`
- `capability-provider.schema.json`
- `evidence-claim.schema.json`
- `actionable-result.schema.json`
- `evaluation-corpus.schema.json`

These files do not also exist under the platform `schemas/` directory. The SDK
wheel includes them as package data, and
`assayer_plugin_sdk.resources.schema_root()` resolves them without importing
the platform.

Ordinary plugin authors do not edit or copy these Schemas. The Simple SDK
compiler emits the plugin-specific projections derived from typed declarations
and invariants; this directory remains the implementation authority used to
validate those generated artifacts.

The repository `schemas/` directory contains only platform-owned runtime,
protocol, ledger, lifecycle, conformance, and product schemas. The platform
wheel installs that set under `share/assayer/schemas`.

## 2. Runtime resolution

Platform validators build one resolver store from two non-overlapping roots:

1. the SDK root returned by `assayer_plugin_sdk.resources.schema_root()`;
2. the platform-owned resource root selected internally by the registry.

`assayer_platform.registry.schema_store()` indexes both schema names and
canonical `$id` values. A duplicate name or `$id` fails closed with
`SCHEMA_RESOURCE_CONFLICT`; platform resources therefore cannot shadow an SDK
contract. Platform schemas may continue to use relative references such as
`common.schema.json#/$defs/entityId`, which resolve to the SDK-owned schema.

## 3. Distribution guarantees

- `assayer-plugin-sdk` is self-contained and ships all six public schemas.
- `assayer-platform` depends on the exact SDK version and does not ship copies
  of SDK-owned schemas.
- The root `assayer` wheel follows the same platform-only resource boundary.
- Tests assert canonical ownership, combined resolution, and wheel contents;
  there is no byte-drift guard because there is no second copy.

## 4. Resource-root rules

- `ASSAYER_SDK_SCHEMA_ROOT` overrides only the SDK-owned schema root.
- `ASSAYER_RESOURCE_ROOT` overrides only platform-owned resources.
- The platform sentinel remains `platform-ledger.schema.json`, never an
  SDK-owned file.
- A custom Host schema root supplies the platform set; SDK schemas are still
  resolved from the installed SDK.
