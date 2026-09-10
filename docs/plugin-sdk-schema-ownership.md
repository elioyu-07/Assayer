# Plugin SDK Schema Ownership

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | Interim model frozen; single-source scheduled with WS3/WS10 |
| Owner | Assayer maintainers |

## 1. Current model (interim, explicit)

The plugin SDK ships copies of the schemas its own validators need in
`src/assayer_plugin_sdk/schemas/`:

- `common.schema.json`
- `plugin-manifest.schema.json`
- `evidence-claim.schema.json`
- `actionable-result.schema.json`
- `evaluation-corpus.schema.json`

`assayer_plugin_sdk.resources.schema_root()` resolves that directory, so an
installed SDK is self-sufficient and never reads `share/assayer/schemas`. The
repository `schemas/` directory remains the single glob root for platform and
domain schemas (providers, canonical result, lifecycle, browser domain, and so
on).

This is a deliberate **copy + guard** transition, not the final ownership
model. Two guards make it legitimate:

1. **Self-containment guard** (`tests/test_plugin_sdk_distribution.py`):
   `schema_root()` must point inside the SDK package and every required schema
   must be present.
2. **Drift guard**: each SDK schema copy must be byte-equal to its repository
   counterpart.

## 2. Why not single-source yet

`common.schema.json` is `$ref`-ed by most platform and domain schemas, and the
platform loads schemas from a single directory via `registry._schema_root()`
(also honoring `ASSAYER_RESOURCE_ROOT`). Moving the canonical files into the SDK
changes every directory-glob schema loader. That is the schema ownership split,
which belongs to WS10 (domain/generic schema split) and WS3 (wheel split).

## 3. Closing condition

This debt is closed when:

- the SDK schema directory is the canonical source for plugin-facing schemas;
- the platform resolves plugin-facing schemas through
  `assayer_plugin_sdk.resources.schema_root()` (or a generated copy at build);
- the byte-equality drift guard is removed because there is no second copy.

## 4. Known trap

The drift guard is intentionally stricter than semantic equality. If a
platform-only reformat of a shared schema turns the guard red, that is exactly
the Constitution §3.13 signal (a platform-side change forcing a plugin-side
change). It is not a reason to relax the guard; it is the reason the copy must
be closed at WS3/WS10.

## 5. Gate status

The guards currently run in the fast verification suite. Wiring them into the
release gate (the same place the §3.13 isolation regression runs) is tracked by
WS8 and is not yet done.
