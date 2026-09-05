# Spec Plugin Externalization (M4) — Design

Status: in progress

## Purpose

Stage 4 (M4) proves the ecosystem model works outside the platform source: the
Spec-quality audit domain becomes an independently buildable, installable, and
upgradeable plugin distribution. Installing or updating the external Spec
plugin must require **no Assayer platform source change**.

## What already exists

- `assayer_platform.conformance.inspect_plugin_package` — static package gate
  (no plugin-code import) requiring `assayer-plugin-release.json`, a manifest,
  scope schema, runtime source, semantic-review Markdown, `pyproject.toml` with
  an `assayer.plugins` entry point, and deterministic fixtures.
- `assayer_platform.installation_conformance.inspect_plugin_installation` —
  isolated install + fixture execution gate.
- `assayer_platform.plugin_lifecycle.PluginLifecycleManager` +
  `PluginInstallationStore` — durable install / upgrade / rollback / uninstall.
- `assayer_platform.plugin_lifecycle.discover_plugin_registry` — store-backed
  discovery that merges installed plugins into a `PluginRegistry`.
- `assayer_platform.plugin_registry.PluginRegistry.from_entry_points` — the
  `assayer.plugins` entry-point discovery used by `installed_plugin_registry`.

## What is missing

The Spec domain still lives only inside the platform source
(`src/assayer_platform/builtin_plugins/spec_quality/`). It has never been
built, installed, discovered, upgraded, rolled back, or uninstalled as an
independent distribution.

## Approach

1. **Relocate** the Spec implementation and policy resources into a standalone
   distribution at `plugins/spec-quality/` (Python package `assayer_spec_quality`).
   The three runtime modules (`runtime.py`, `review.py`, `evaluation.py`) switch
   their platform imports from relative (`from ...contract`) to absolute
   (`from assayer_platform.contract`); intra-package imports (`from .review`)
   stay relative.
2. **Register** it through `[project.entry-points."assayer.plugins"]` with one
   `PluginRegistration` built from the packaged `manifest.json` and
   `scope.schema.json`.
3. **Package** the release descriptor, semantic-review Markdown, and one
   deterministic fixture that exercises the plugin through the isolated
   install gate.
4. **Prove** the M4 exit gate: install → discover → run (Spec business input)
   → produce a valid platform result → upgrade → rollback → uninstall.
5. **Keep** the structured review summary; HTML is not a canonical or required
   output for the Spec plugin (it never was — the plugin emits candidate
   findings and a structured checklist summary only).

## Package layout

```text
plugins/spec-quality/
  pyproject.toml                    # assayer-spec-quality, assayer.plugins entry point
  assayer-plugin-release.json       # release descriptor
  semantic-review.md                # Agent decision boundary (structured review)
  fixtures/
    spec-smoke.json                 # deterministic fixture
    spec-smoke.md                   # business input for the fixture
  src/assayer_spec_quality/
    __init__.py                     # registration + re-exports
    runtime.py                      # SpecQualityPlugin (absolute platform imports)
    review.py                       # SpecQualityDecisionCommitter + review helpers
    evaluation.py                   # semantic-review corpus evaluation
    manifest.json                   # pluginId assayer.spec-quality, version 1.0.0
    scope.schema.json               # files | anchor+relatedDocuments+relationships
    policy.json / checklist.json / recognition.json
    authority.md / quality-standard.md / spec-template.md / nfr-catalog.md
    self-check-checklist.md / review-rubric.md / expert-judgment-governance.md
    spec-driven-development.md
    *.schema.json                   # finding/review/evidence/claim schemas
    evaluation/                     # semantic corpus + profiles
```

## Import rewrite (deterministic)

| Builtin (relative) | Standalone (absolute) |
|---|---|
| `from ...contract import` | `from assayer_platform.contract import` |
| `from ...registry import` | `from assayer_platform.registry import` |
| `from ...actionable_result import` | `from assayer_platform.actionable_result import` |
| `from ...navigation import` | `from assayer_platform.navigation import` |
| `from ...review_protocol import` | `from assayer_platform.review_protocol import` |
| `from ...evidence_graph import` | `from assayer_platform.evidence_graph import` |
| `from ...identity import` | `from assayer_platform.identity import` |
| `from ...source_chunking import` | `from assayer_platform.source_chunking import` |
| `from ...source_fact_index import` | `from assayer_platform.source_fact_index import` |
| `from ...evaluation import` | `from assayer_platform.evaluation import` |
| `from ... import ` | `from assayer_platform import ` |

The build/migration helper is `scripts/build_spec_quality_plugin.py`; it is a
one-time relocation aid, not a runtime dependency. After the relocation the
committed files under `plugins/spec-quality/` are the source of truth.

## Migration rules

- Behavior is identical: the plugin keeps the same `pluginId`
  (`assayer.spec-quality`), manifest, policy, and review semantics. Candidate
  IDs, evidence, and result projection are unchanged.
- The platform never imports `assayer_spec_quality`; it only loads it through
  the entry point / `load_registration` after static validation.
- The boundary checker scans only `src/`, so the external package introduces no
  new boundary violations.
- The built-in `builtin_plugins/spec_quality` remains until the removal slice
  (with its test migration) lands; the external package is additive first.

## Acceptance

- `inspect_plugin_package(plugins/spec-quality)` passes.
- `inspect_plugin_installation(plugins/spec-quality)` passes (isolated install +
  fixture).
- A durable `PluginInstallationStore` install → discover → upgrade → rollback →
  uninstall the real package without touching platform source.
- A real Spec business-input run produces a valid platform result with the
  structured review summary intact and no HTML output.
