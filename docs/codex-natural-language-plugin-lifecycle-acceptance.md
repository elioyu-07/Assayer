# Codex Natural-Language Plugin Lifecycle Acceptance

> **Historical acceptance record.** This evidence predates the hard cut to the
> single compiled-plugin contract. References to legacy registration, domain
> result adapters, or ordinary-plugin wheels are historical facts only and do
> not describe a supported implementation path.

This document records the acceptance evidence for the plugin lifecycle journey
described in [codex-natural-language-plugin-lifecycle-plan.md](codex-natural-language-plugin-lifecycle-plan.md).
It deliberately separates repository automation from a clean Codex profile run.
Passing MCP tests is not, by itself, evidence that Codex Marketplace discovery,
Skill loading, and natural-language routing work in a clean user environment.

Pass, failure, and blocked decisions in this record follow
[Operator Release Gate v1](operator-release-gate-v1.md). This document is an
evidence log; it does not define a competing operator gate. In particular, one
successful baseline Run does not close J04 or J05.

## Status

| Gate | Status | Evidence |
|---|---|---|
| Lifecycle MCP contract | Passed | `tests/test_plugin_lifecycle_mcp.py` |
| Natural-language local-source verification MCP contract | Passed | `verify_plugin_source`; transport and real stdio coverage in `tests/test_plugin_lifecycle_mcp.py` and `tests/test_mcp_stdio_integration.py` |
| Fresh bundle `verify_plugin_source` call on 2026-09-15 | Passed | Assayer `0.1.2+codex.20260915014354`; fresh private runtime exposed 17 tools and verified `plugins/frontend-audit` through compile, generated contracts, isolated wheel, and installed lifecycle |
| Same-connection install and registry refresh | Passed | `tests/test_plugin_registry_refresh.py` |
| Same-connection complete Run and result paging | Passed | `tests/test_mcp_stdio_integration.py` |
| Exact DomainResult contract and semantic rejection safety | Passed | `tests/test_plugin_compatibility.py`, `tests/test_plugin_conformance.py`, `tests/test_plugin_release_gate.py` |
| Same-connection upgrade and rollback | Passed | `tests/test_mcp_stdio_integration.py` |
| Natural-language route classification | Passed | `tests/test_plugin_lifecycle_router.py` |
| Full browser/MCP regression gate | Deferred | Browser full gate is intentionally outside the current task scope |
| Exact Assayer bundle build and offline launcher check | Passed | Deterministic package and launcher checks; see `docs/exact-contract-release-performance-evidence.md` |
| External `ass-spec` SDK consumer against exact `assayer 0.1.2` | Passed | 56 tests; exact current SDK contract and manifest accepted |
| External `ass-spec` against an older Assayer contract | Rejected as incompatible | Exact protocol/SDK and DomainResult identity checks fail closed |
| Clean Codex CLI marketplace discovery and install | Passed | Disposable `CODEX_HOME`; `codex plugin marketplace add`, `plugin list --available`, `plugin add`, and `plugin list` all reported `assayer@assayer-clean` at `0.1.2+codex.20260909095928` |
| Clean installed-bundle MCP startup | Passed | Installed cache copy launched with isolated `HOME`/`XDG_CACHE_HOME`; private runtime created offline, `bundle-ready` and `runtime-identity.json` written, launcher exited 0 on EOF |
| Clean Codex natural-language lifecycle acceptance | Pending operator evidence | Requires an interactive Codex session for natural-language routing and confirmation UX; CLI/plugin and launcher gates above are complete |
| Operator-session attempt on 2026-09-09 | Blocked by environment | Isolated `codex exec` loaded the disposable profile but the model transport timed out through all retries; no domain-plugin mutation was performed |
| Operator-session attempt on 2026-09-11 | Blocked by network | Clean `assayer-clean` marketplace install succeeded; read-only `codex exec` could not complete the model turn after DNS failure and an approved network retry timed out |
| OPR-J04-B01 attempt on 2026-09-14 | Blocked by model transport | Exact Assayer Marketplace discovery and installation passed in a newly generated profile. Thread `01a09f16-112f-7a61-afa8-4c281f4cd06d` exhausted five WebSocket retries and a bounded HTTPS fallback wait before any Assayer tool call or domain-plugin mutation. Machine-readable result: `/Users/sev7nyo/code/Assayer-operator-evidence/operator-acceptance/20260914T084239Z-7dc69e6d080662d9/gate-results.json` |
| OPR-J04-B01 retry on 2026-09-14 | Blocked by model transport | Exact cachebusted candidate `0.1.2+codex.20260914085115` installed through a new local Marketplace. Stdin was closed with EOF; the model transport still exhausted five WebSocket retries and bounded HTTPS fallback waiting before any Assayer tool call. Machine-readable result: `/Users/sev7nyo/code/Assayer-operator-evidence/operator-acceptance/20260914T090452Z-1d26aebf84cef5b3/gate-results.json` |
| OPR-J04-B01 retry on 2026-09-15 | Blocked by model transport | Exact candidate `0.1.2+codex.20260915014354` was staged, installed, and prepared in a new clean profile. The model transport exhausted five WebSocket retries before any Assayer business tool call. Machine-readable result: `/Users/sev7nyo/code/Assayer-operator-evidence/operator-acceptance/20260915T015933Z-576becdf1334bbba/gate-results.json` |
| OPR-J04-B01 capacity retry on 2026-09-15 | Blocked by model capacity | Exact candidate `0.1.2+codex.20260915014354` was discovered, installed, and prepared in another clean profile. Codex loaded the lifecycle and plugin-run Skills, then the selected model reported capacity exhaustion before any Assayer business tool call or domain-plugin mutation. Thread `01a0a2dd-b855-7290-ad9e-eb6f8fa0363f`; machine-readable result: `/Users/sev7nyo/code/Assayer-operator-evidence/operator-acceptance/20260915T021825Z-2cead14cfbdd599e/gate-results.json` |
| Local Python 3.13 fast gate on 2026-09-14 | Passed (local evidence) | `scripts/run_tests.py fast --quiet`; 723 tests passed in 8.586 seconds. This is not the GitHub Actions matrix result. |
| Clean temporary-venv Python 3.13 fast gate on 2026-09-14 | Passed (local evidence) | CI-style editable installation plus explicit `setuptools`/`wheel`/`uv`; `scripts/run_tests.py fast --quiet`; 723 tests passed. |
| Local Python 3.13 full gate on 2026-09-14 | Passed (local evidence) | `scripts/run_tests.py full --quiet`; 789 tests passed in 44.624 seconds. This is not operator J04/J05 evidence. |
| Local Python 3.13 split-install matrix on 2026-09-14 | Passed (local evidence) | `scripts/install_matrix.py`; all platform, SDK, Agent, plugin/provider, root-meta, and duplicate-conflict cases passed. This is not the CI 3.11/3.13 result. |
| Isolated release/provider gate with explicit build toolchain on 2026-09-14 | Passed (local evidence) | Temporary venv with `setuptools`, `wheel`, and `uv`; `tests.test_plugin_release_gate` plus `tests.test_provider_release_gate`: 35 tests passed. |

The automated Python support policy is recorded in
[CI Support Matrix v1](ci-support-matrix-v1.md): Python 3.11 and 3.13 are
full-gate entries, Python 3.12 is a fast-gate entry, and macOS arm64/CPython
3.13 remains the clean operator reference rather than a CI pass.

The 2026-09-14 attempt used archive SHA-256
`7e24b8f8bc2f276c58aa9848ab1e4024482d4c33a2479efbd83c4acfbbcecdb5`.
It also exposed a release-identity defect: the newly built bytes initially kept
the earlier cachebuster `0.1.2+codex.20260914010407`. That archive is retained
only as blocked diagnostic evidence and is not a publishable candidate. The
cachebuster was replaced through the Codex plugin update helper with
`0.1.2+codex.20260914085115`, and the corrected candidate passed bundle build,
isolated installation, entry-point discovery, Host construction, and offline
launcher startup. A new operator attempt is still required for that exact
candidate.

## Automated Evidence

The deterministic integration journey is:

```text
verify local Policy Pack -> exact wheel (without installation)
->
install v1
-> list
-> start_plugin_run
-> advance_plugin_run
-> submit DomainResult
-> terminal result
-> get_plugin_result
-> upgrade v2
-> rollback v1
-> uninstall
-> start after uninstall is rejected
```

Run it with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_mcp_stdio_integration
```

Run the complete fast regression suite with:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_tests.py fast
```

## Clean Codex Run

Run this section outside the repository's existing Codex profile and record the
actual values. Do not replace a missing value with an assumption.

| Fact | Value |
|---|---|
| Date/time and timezone | 2026-09-09 Asia/Shanghai |
| Codex version | `codex-cli 0.153.0` |
| Assayer plugin version | `0.1.2+codex.20260909095928` |
| Domain plugin and version | Not exercised; operator session could not reach the model service |
| OS and architecture | macOS 26.5.2, Darwin 25.5.0, arm64 |
| Python version | Python 3.13.3 |
| Codex profile/config root | Disposable `/private/tmp/assayer-codex-bundle.C5G91v` (`CODEX_HOME`) |
| MCP startup result | Passed; clean installed bundle created private offline runtime and exited 0 on EOF. Natural-language session was not reached because model transport timed out. |

Use the following natural-language sequence:

```text
Install Assayer from the configured Codex Marketplace.
Run assayer doctor --fix.
Install ass-spec.
Use ass-spec to review spec.md for completeness and ambiguity.
Show me the result and the full report location.
Upgrade ass-spec.
Roll back ass-spec.
Uninstall ass-spec.
Restart Codex and show the plugin state.
```

For each request, record:

- the exact user text;
- whether Codex selected Marketplace, lifecycle, or Run workflow;
- the tools discovered by Codex;
- the plan shown for every confirmation-required mutation (trusted first install is the exception);
- the user's confirmation or rejection;
- the resulting plugin state and active version;
- the Run ID and terminal status for plugin usage;
- the result section/report location;
- any failure, recovery, or retry.

## Exit Criteria

The automated and CLI portions are complete. Full operator acceptance still requires all of the following:

- Assayer is installed by Codex Marketplace without manual MCP configuration;
- the Assayer MCP starts and reports dependency failures clearly;
- `ass-spec` is installed through natural language in one trusted-catalog Host transaction;
- the same Codex task uses `ass-spec` and receives a terminal structured result;
- upgrade and rollback are completed through natural language;
- uninstall is completed through natural language;
- a Codex restart shows the expected durable state;
- no step requires the user to provide internal protocol fields or restart MCP manually.
