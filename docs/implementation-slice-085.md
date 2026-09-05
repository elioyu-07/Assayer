# Implementation Slice 085 — External Spec-quality Distribution

Status: implemented

The Spec-quality audit domain is now an independently buildable distribution
at `plugins/spec-quality/` (Python package `assayer_spec_quality`), decoupled
from the platform source. It carries the full implementation (`runtime.py`,
`review.py`, `evaluation.py`) with absolute `assayer_platform` imports, its
policy resources (manifest, scope schema, policy, checklist, recognition,
authority and review Markdown, finding/evidence schemas, and the evaluation
corpus), and a one-entry `assayer.plugins` registration.

Proven gates:

- `inspect_plugin_package` (static, no plugin-code import) — passed.
- `inspect_plugin_installation` (isolated install + deterministic fixture) —
  passed; the `spec-quality.smoke` fixture runs a complete 12-chapter Spec
  through the platform kernel and produces a valid `issue_found` result.
- Durable `PluginLifecycleManager` install → `discover_plugin_registry` →
  upgrade (1.0.0 → 1.1.0) → rollback (→ 1.0.0) → uninstall, all fail-closed,
  with the real package.

No platform source change is required to install or update the plugin: the
package depends on `assayer>=0.1.0,<0.2.0`, exposes
`assayer_spec_quality:registration`, and the platform loads it only through
`load_registration` after static validation.

The relocation helper is `scripts/build_spec_quality_plugin.py` (one-time
migration aid; committed files under `plugins/spec-quality/` are the source of
truth). Remaining M4 work: remove the built-in `builtin_plugins/spec_quality`
(and migrate its test references), then record the real interactive CLI
journey.
