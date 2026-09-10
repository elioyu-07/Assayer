# WS3 Distribution Split — Baseline Checkpoint

| Metadata | Value |
|---|---|
| Date | 2026-09-10 |
| Plan | A 方案（发行解耦 + 根包平台-only） |
| Starting commit | `8e4d087` |
| Baseline (M0) commit | `83c9ebd` |
| Prior starting point | `5971977` |
| Working tree | clean |
| Verification | `scripts/run_tests.py fast` → 760 OK (skipped=36) |

This checkpoint pins the starting line for the WS3 distribution split. All
work after `8e4d087` proceeds under the A-plan staging rules: prove the four
independent distributions in a clean venv install matrix first, then flip the
root `assayer` distribution to platform-only.

## Frozen contract documents (M0 `83c9ebd`)

- `docs/platform-constitution-v1.md` — document version `1.0.1` (§2 / §3.13 revisions frozen)
- `docs/platform-plugin-boundary-contract.md`
- `docs/agent-domain-result-boundary-v2.md`
- `docs/plugin-development-standard-v1.md`

M0 committed 71 files, 5488 insertions / 1299 deletions.

## Completed workstreams up to the starting line

| WS | Deliverable | Commit(s) |
|---|---|---|
| M0 | Baseline freeze (SDK v2 DomainResult boundary) | `83c9ebd` |
| WS1 + WS5 | Protocol envelope schema + version axes | `19bf65e` |
| WS2 | SDK extraction (contract, helpers, validators, registration, manifest, provider) with platform re-export shims | `07dba51`, `5e42085`, `a025ca4`, `cf17081`, `f0d955c`, `8d64219` |
| WS2c | In-repo frontend plugin switched to SDK-only imports | `27b0e67` |
| WS4 | SDK schema resolver + self-contained SDK schemas | `dc00415`, `baf520b` |
| WS3 (partial) | Buildable split distributions: sdk / frontend-audit / provider-markdown | `517866d`, `8d64219` |
| WS4/WS3 debt | Interim schema-copy debt recorded; platform schema sentinel hardened | `da054ec`, `efec93c` |
| WS8 (partial) | Fail-fast capability failure semantics pinned + reserved fields marked | `8e4d087` |

## Known open items carried into WS3

- Root `pyproject.toml` still bundles the split packages and declares their
  entry points; co-installing with split wheels yields `PLUGIN_CONFLICT`.
  (Fix is staged in A-plan phase 3, gated on phase 2.)
- `ass-spec` still depends on `assayer>=0.1.2,<0.2.0` and imports platform /
  provider internals (A-plan phase 4).
- `assayer-registry/plugins.json` catalog is stale (A-plan phase 6, blocked on
  publishing decoupled versions).
- SDK ships interim schema copies guarded by a drift test; single-source closes
  at WS3/WS10.
