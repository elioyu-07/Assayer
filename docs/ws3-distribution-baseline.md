# WS3 Distribution Split — Baseline Checkpoint

> **Superseded.** This document predates the Platform Constitution v2 and is
> retained as a historical migration reference. The Platform Constitution v2
> and the v1 contracts govern current platform work; this content is not
> current implementation guidance.


| Metadata | Value |
|---|---|
| Date | 2026-09-10 |
| Plan | A — distribution decoupling with a platform-only root package |
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
| WS2 | SDK extraction (contract, helpers, validators, registration, manifest) with platform re-export shims | `07dba51`, `5e42085`, `a025ca4`, `cf17081`, `f0d955c` |
| WS2c | In-repo frontend plugin switched to SDK-only imports | `27b0e67` |
| WS4 | SDK schema resolver + self-contained SDK schemas | `dc00415`, `baf520b` |
| WS3 (partial) | Buildable split distributions: sdk / frontend-audit / provider-markdown | `517866d`, `8d64219` |
| WS3 (browser provider scaffold) | SDK-only browser snapshot provider and split package | working tree |
| WS4/WS3 debt | Interim schema-copy debt recorded; platform schema sentinel hardened | `da054ec`, `efec93c` |
| WS8 (partial) | Fail-fast capability failure semantics pinned (both `PROVIDER_NOT_FOUND` and `CAPABILITY_NEGOTIATION_BLOCKED`, no ledger / no `needs_review`) + reserved fields marked | `8e4d087` + follow-up |

## Known open items carried into WS3

- Root `pyproject.toml` still bundles the split packages and declares their
  entry points; co-installing with split wheels yields `PLUGIN_CONFLICT`.
  (Fix is staged in A-plan phase 3, gated on phase 2.)
- `ass-spec` production runtime is now SDK/provider-only. The Host/platform
  stack remains only in its `test` extra for release acceptance; runtime
  dependencies are exact `assayer-plugin-sdk==0.1.2` and
  `assayer-provider-markdown==0.1.0` (A-plan phase 4 closed).
- The hard-cut contract hygiene is now enforced: stale checkpoint/schema
  artifacts are removed, provider result schemas are published and Host-
  validated, and acceptance tests must stay aligned with exact versions.
- `assayer-registry/plugins.json` catalog is stale (A-plan phase 6, blocked on
  publishing decoupled versions).
- SDK owns the stable provider descriptor contract; provider result schemas are
  provider-owned metadata carried through that descriptor rather than copied
  into platform code.
- WS8 is only partially closed: the two fail-fast capability codes are pinned
  and in the fast gate, but the surface gate ("artifacts depend on the SDK
  only"), the §3.13 regression fixture, and the release-gate aggregation remain
  (A-plan phase 5).
