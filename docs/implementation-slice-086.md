# Implementation Slice 086 — Remove Built-in Spec and Prove the External CLI Journey

Status: implemented

M4 is now closed out. The Spec-quality plugin is no longer a platform built-in;
it exists only as the independent `plugins/spec-quality` distribution and is
loaded through the `assayer.plugins` entry point / lifecycle loader.

## Removal

- `src/assayer_platform/builtin_plugins/spec_quality/` is deleted.
- `builtin_plugin_registry()` now registers only `assayer.config-quality` and
  `assayer.frontend-audit`; `installed_plugin_registry()` discovers Spec-quality
  exclusively through the installed distribution's entry point.
- The platform `pyproject.toml` no longer declares package-data for the built-in
  Spec plugin.
- `scripts/build_spec_quality_plugin.py` (the one-time relocation helper) is
  removed now that the committed `plugins/spec-quality/` files are the single
  source of truth.

## Test migration

Spec unit and integration coverage now imports the external package
(`assayer_spec_quality`) instead of the deleted built-in:

- `tests/test_spec_quality_plugin.py` — Spec classes, review helpers, runtime
  helpers, and a `spec_registry()` helper that combines the platform built-ins
  with the external Spec registration (the installed view).
- `tests/test_spec_quality_evaluation.py` — evaluation module and helpers.
- `tests/test_layer_migration_validation.py` — `SpecQualityPlugin`.
- `tests/test_plugin_packaging.py` — asserts the external package's own
  `package-data` and resource files, and that the built-in Spec no longer exists.
- `tests/test_plugin_release_gate.py` — built-in release gate now expects two
  built-ins; the release CLI registry factory reports two plugins.
- `tests/test_cli.py` — plugin catalog lists the two built-ins (Spec is no
  longer built-in).

`scripts/run_tests.py` adds `plugins/spec-quality/src` to the discovery path so
the external package is importable during tests.

## Side-effect-free isolated install gate

`inspect_plugin_installation` now removes the transient `build/` and
`*.egg-info` artifacts that a local `--no-build-isolation` install leaves in the
package source, so the release gate no longer pollutes the tree or leaks an
`assayer-spec-quality` entry point into later tests.

## CLI journey evidence

`scripts/spec_plugin_external_acceptance.py` records the full M4 exit gate as one
reproducible journey using the real package and Spec business input only:

```text
install (1.0.0) -> discover -> run (Agent semantic review, structured summary,
no HTML) -> valid platform result -> upgrade (1.1.0) -> rollback (1.0.0) ->
uninstall
```

The result is validated against `schemas/spec-external-journey.schema.json` and
executed by `tests/test_spec_quality_external_acceptance.py`.

## Verification

- Full fast suite: 640 tests pass.
- `scripts/check_architecture_boundaries.py` passes.
