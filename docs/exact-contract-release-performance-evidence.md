# Exact-Contract, Release-Lifecycle, and Performance Evidence

Date: 2026-09-11

> **Historical evidence.** This record describes an earlier runtime and release
> path. It cannot authorize ordinary-plugin implementation. The active path is
> the zero-Python declaration contract and exact `compiled-plugin.json` artifact
> governed by Platform Constitution v2.

This record separates deterministic repository evidence from operator-level
acceptance. A passing deterministic gate does not claim that a real Codex
natural-language session or browser journey was executed.

## Exact-contract hard cut

The current contract is exact: protocol and SDK ranges must be equal to the
Host versions, compatibility must be declared in the manifest, and the
registered `DomainResultContract` must match the manifest declaration. Older
protocols, ranges, missing declarations, legacy review payload schemas, and
direct semantic submission paths fail closed before Run creation.

Evidence command:

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_plugin_compatibility \
  tests.test_plugin_conformance \
  tests.test_plugin_release_gate
```

Result on 2026-09-11: 49 tests passed. The suite covers old protocol rejection,
exact SDK identity, manifest/registration identity, DomainResult schema gates,
installed-artifact acceptance, replay, and malformed-input rejection.

## Clean CLI and release lifecycle

The repository CLI lifecycle is exercised with a temporary store, outside the
user's installed plugin directory:

```bash
ASSAYER_STORE="$(mktemp -d)/assayer/plugins" \
  .venv/bin/python -m unittest \
  tests.test_cli_plugin_lifecycle \
  tests.test_isolated_lifecycle_acceptance
```

Result: 70 tests passed. The deterministic lifecycle covers install, list,
info, upgrade, downgrade, rollback, uninstall, conflict rejection, quarantine,
registry refresh, and terminal runtime cleanup.

The isolated lifecycle acceptance also passed:

```text
upgrade  -> completed
rollback -> completed
uninstall -> completed
runtime cleanup -> removed
```

Its limitation is explicit: it uses a fake Codex executable and therefore does
not prove a real Codex authorization boundary or a natural-language operator
session. Those remain J08 operator evidence.

A real clean Codex profile was then created at `/tmp/assayer-clean-cli-home`.
Marketplace discovery and installation succeeded:

```text
marketplace: assayer-clean
plugin: assayer@assayer-clean
version: 0.1.2+codex.20260909231840
state: installed=true, enabled=true
```

The read-only Codex session reached the model turn but could not complete: the
sandbox first failed DNS resolution for `api.openai.com`, and the approved
network retry timed out. No browser was opened and no plugin mutation was
performed by the model session. This is recorded as blocked operator evidence,
not as a product success.

## Platform performance baseline

The repeatable platform-only workload is:

```bash
PYTHONPATH=src .venv/bin/python scripts/platform_performance_acceptance.py \
  --iterations 10
```

The 2026-09-11 run passed all 10 completed Runs and measured:

| Metric | Value |
|---|---:|
| p50 wall-clock | 2.272 ms |
| p95 wall-clock | 3.525 ms |
| mean wall-clock | 2.509 ms |
| Run operations | 4 per Run |

This is a reproducible platform baseline for discovery, inspection, decision,
commit, ledger, and performance-bill construction. It is not a browser or
Agent end-to-end latency SLO. Interactive Runs now capture Host-side semantic
task compilation, successful direct-transport request timing, and accepted-turn
Agent wait; model telemetry remains explicitly `not_exposed` until a client
exposes the corresponding timing.

## Remaining acceptance gates

- J04/J05 still require real clean-CLI user-journey evidence: consecutive Runs,
  visible progress, diagnosable failure/recovery, and understandable terminal
  results for `completed`, `partial`, and `failed`.
- J08 still requires a real trusted Codex/plugin-management boundary proving
  natural-language confirmation, upgrade, rollback, uninstall, cleanup, and
  restart persistence.
- Browser full gate remains intentionally deferred by the current task scope.
