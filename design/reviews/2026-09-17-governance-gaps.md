# Governance gap review — 2026-09-17

Status: disposition required; no implementation, approval backfill, commit or release performed.

## Findings

The working tree contains 34 paths without an approved `changedPaths` match:
30 tracked paths and 4 untracked frontend case files. The repository command
`--git-range HEAD` reports only tracked changes. All paths, including untracked
files, were checked independently with the existing `validate_changed_paths`.
This review does not alter the gate or claim that earlier preflight occurred.

The exact reviewed file contents are identified in
[the SHA-256 manifest](2026-09-17-governance-gaps.json); the full changes are in
[the review patch](2026-09-17-governance-gaps.patch).

## Recommended disposition requiring confirmation

| Group | Paths | Concrete disposition | Existing related design direction |
|---|---:|---|---|
| Document migration | 16 | Retain historical/superseded labels and local-only lifecycle descriptions; separately correct remaining current-authority references to Constitution v2 before final acceptance. | `platform-constitution-v2-gate`, `deep-clean-legacy-plugin-documentation-and-gates`, `migrate-plugin-catalog-to-compiled-artifacts` |
| Retired Host and rule resources | 12 | Retain removal of the old Host entry point, browser runtime, HostCore, harness, router and global rule files; retain schema-only resource discovery and related terminology cleanup. Do not restore retired compatibility entry points. | `migrate-host-transport-to-compiled-runtime`, `delete-tests-for-retired-plugin-runtimes`, `remove-plugin-specific-host-and-bundle-coupling` |
| Frontend Check identity and cases | 5 | Retain `FUA-01` as the intended Check ID and the insufficient-evidence / missing-reset business cases. Treat the ID change as a public semantic migration; require a new business version and freshly verified compiled artifact before installation or release. Current source still says version `1.0.0`; no same-version artifact replacement is approved by this review. | `data-only-plugin-contract` covers compiler/tests, but does not explicitly approve the source Check rename or these new case paths. |
| Codex bundle version | 1 | Treat `0.1.2+codex.20260915125952` as an existing local build marker only. Do not claim it contains current Markdown fixes or publish/reinstall that older bundle. Any delivery needs a fresh verified bundle identity. | No exact release record was found for this version-only edit. |

The related records support direction, not exact-path authorization or proof
that a machine-checked record preceded these edits. Approved records remain
immutable. A user disposition must explicitly address the existing worktree
snapshot and the missing historical preflight; any newly required code or
release work starts under a new checked Design Confirmation. Merely adding
paths or backdating approvals would not resolve the governance breach.

## Validation and limits

Focused plugin verification, frontend migration, packaging and CLI tests passed
(see command below). These tests do not establish semantic business approval,
real-browser acceptance, or successful publication. Verification produced only
test-local artifacts; nothing was installed or published by this review.

```text
python3 -m unittest tests.test_plugin_verify tests.test_frontend_migration tests.test_plugin_packaging tests.test_cli -q
```

## Exact path inventory

| Path | Category | State |
|---|---|---|
| `docs/agent-domain-result-boundary-v2.md` | documents | modified |
| `docs/agent-first-plugin-lifecycle-design.md` | documents | modified |
| `docs/architecture.md` | documents | modified |
| `docs/codex-natural-language-plugin-lifecycle-acceptance.md` | documents | modified |
| `docs/codex-natural-language-plugin-lifecycle-plan.md` | documents | modified |
| `docs/design-governance.md` | documents | modified |
| `docs/markdown-navigation-capability.md` | documents | modified |
| `docs/operator-release-gate-v1.md` | documents | modified |
| `docs/platform-contract.md` | documents | modified |
| `docs/platform-foundation-and-user-journey-plan.md` | documents | modified |
| `docs/plugin-lifecycle-design.md` | documents | modified |
| `docs/plugin-protocol-v1.md` | documents | modified |
| `docs/plugin-sdk-schema-ownership.md` | documents | modified |
| `docs/plugin-version-axes-v1.md` | documents | modified |
| `docs/product-contract.md` | documents | modified |
| `docs/spec-review-getting-started.md` | documents | modified |
| `plugins/assayer/.codex-plugin/plugin.json` | bundle_version | modified |
| `plugins/frontend-audit/cases/orders-insufficient.json` | frontend_identity_and_cases | untracked |
| `plugins/frontend-audit/cases/orders-insufficient.yaml` | frontend_identity_and_cases | untracked |
| `plugins/frontend-audit/cases/orders-no-reset.json` | frontend_identity_and_cases | untracked |
| `plugins/frontend-audit/cases/orders-no-reset.yaml` | frontend_identity_and_cases | untracked |
| `plugins/frontend-audit/checks.yaml` | frontend_identity_and_cases | modified |
| `rules/FUA-10-v1.1.md` | retired_host_and_rules | deleted |
| `rules/FUA-10.md` | retired_host_and_rules | deleted |
| `rules/_template.md` | retired_host_and_rules | deleted |
| `rules/registry.json` | retired_host_and_rules | deleted |
| `src/assayer_host/__main__.py` | retired_host_and_rules | deleted |
| `src/assayer_host/browser_runtime.py` | retired_host_and_rules | deleted |
| `src/assayer_host/core.py` | retired_host_and_rules | deleted |
| `src/assayer_host/harness.py` | retired_host_and_rules | deleted |
| `src/assayer_host/observability.py` | retired_host_and_rules | modified |
| `src/assayer_host/platform_store.py` | retired_host_and_rules | modified |
| `src/assayer_host/resources.py` | retired_host_and_rules | modified |
| `src/assayer_host/runtime_router.py` | retired_host_and_rules | deleted |
