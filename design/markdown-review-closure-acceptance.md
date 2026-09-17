# Markdown compiled review closure acceptance

Date: 2026-09-17
Design: `design/changes/markdown-review-closure-acceptance.json`

## Scope and observed behavior

This acceptance covers one authorized Markdown document through the installed
compiled-plugin transport and the real Markdown Provider. The deterministic
Agent decisions in the test validate orchestration and integrity, not the
quality of an actual model's domain judgments.

- Provider discovery and three one-element collection pages produce exactly
  three review atoms and three batches, with no duplicate or missing elements.
- Evidence from a different page/atom or Run is rejected. Corrupted Run,
  WorkItem, source digest, Check ID and Check version bindings are rejected
  without changing either memory or persisted coverage; valid evidence remains
  acceptable. Missing and duplicate batch decisions cannot mutate coverage.
- Source discovery, collection and replanning cannot replace an existing review
  plan. A restarted controller cannot overwrite an existing Run identity.
- Finalization requires every atom to have an accepted terminal decision.
  Satisfied, violated, unknown, blocked and not-applicable decisions remain
  distinct in the persisted ledger and terminal result. `completed` denotes
  workflow closure, not a pass assertion.
- Shared ledger contexts retain plugin contract identity and Host-issued
  evidence provenance (Run, WorkItem, Check, source digest and Provider). The
  individual source element stays in its atom. These are bounded evidence
  bindings and element projections, not a separate archive of full Provider
  response payloads.
- Result reads derive from validated persisted CoverageLedger data, including
  its SHA-256 trace, rather than an in-memory result cache. A fresh transport
  with no active controller or Provider returns the same terminal result.
- Nested immutable source metadata is converted to JSON-compatible data before
  MCP response serialization.

## Verification

- `python3 -m unittest tests.test_compiled_interactive tests.test_compiled_transport tests.test_markdown_navigation -q`: 28 tests passed.
- `python3 scripts/run_tests.py fast -q`: 450 tests passed.
- `python3 scripts/check_architecture_boundaries.py`: passed.
- `python3 scripts/check_document_boundaries.py`: passed.
- `python3 scripts/check_plugin_source_boundaries.py`: passed.
- `python3 scripts/check_design_confirmation.py design/changes/markdown-review-closure-acceptance.json`: passed.
- Current-change paths checked against the new approved record: passed.
- `git diff --check`: passed.

The new freeze, restart-read and Run-reuse tests exposed failures before their
fixes. The transport journey also exposed nested metadata serialization before
that fix. Evidence binding tests characterize the existing rejection checks.

Independent reviewer dispatch was attempted twice but failed because the
selected model was at capacity; no independent review approval is claimed.

## Remaining repository gate and limitations

`python3 scripts/check_design_confirmation.py --git-range HEAD` rejects 30
pre-existing changed paths without approved design coverage, listed below.
They were present before this change and have not been retroactively approved.
This prevents claiming repository-wide completion or committing the entire
migration as accepted. No commit, installation or release was performed.

The result is the current compiled five-state coverage result. This work does
not restore the superseded v1 canonical-result protocol, add an audit report
adapter, prove live Agent judgment quality, add multi-file scope support, or
prove an in-progress Run can resume after restart. The full optional integration
release profile was not run.

- `docs/agent-domain-result-boundary-v2.md`
- `docs/agent-first-plugin-lifecycle-design.md`
- `docs/architecture.md`
- `docs/codex-natural-language-plugin-lifecycle-acceptance.md`
- `docs/codex-natural-language-plugin-lifecycle-plan.md`
- `docs/design-governance.md`
- `docs/markdown-navigation-capability.md`
- `docs/operator-release-gate-v1.md`
- `docs/platform-contract.md`
- `docs/platform-foundation-and-user-journey-plan.md`
- `docs/plugin-lifecycle-design.md`
- `docs/plugin-protocol-v1.md`
- `docs/plugin-sdk-schema-ownership.md`
- `docs/plugin-version-axes-v1.md`
- `docs/product-contract.md`
- `docs/spec-review-getting-started.md`
- `plugins/assayer/.codex-plugin/plugin.json`
- `plugins/frontend-audit/checks.yaml`
- `rules/FUA-10-v1.1.md`
- `rules/FUA-10.md`
- `rules/_template.md`
- `rules/registry.json`
- `src/assayer_host/__main__.py`
- `src/assayer_host/browser_runtime.py`
- `src/assayer_host/core.py`
- `src/assayer_host/harness.py`
- `src/assayer_host/observability.py`
- `src/assayer_host/platform_store.py`
- `src/assayer_host/resources.py`
- `src/assayer_host/runtime_router.py`
