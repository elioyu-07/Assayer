# Codex Natural-Language Plugin Lifecycle Acceptance

This document records the acceptance evidence for the plugin lifecycle journey
described in [codex-natural-language-plugin-lifecycle-plan.md](codex-natural-language-plugin-lifecycle-plan.md).
It deliberately separates repository automation from a clean Codex profile run.
Passing MCP tests is not, by itself, evidence that Codex Marketplace discovery,
Skill loading, and natural-language routing work in a clean user environment.

## Status

| Gate | Status | Evidence |
|---|---|---|
| Lifecycle MCP contract | Passed | `tests/test_plugin_lifecycle_mcp.py` |
| Same-connection install and registry refresh | Passed | `tests/test_plugin_registry_refresh.py` |
| Same-connection complete Run and result paging | Passed | `tests/test_mcp_stdio_integration.py` |
| Checkpoint draft preflight and semantic rejection safety | Passed | `tests/test_interactive_protocol.py`, `tests/test_agent_contract_boundary.py` |
| Same-connection upgrade and rollback | Passed | `tests/test_mcp_stdio_integration.py` |
| Natural-language route classification | Passed | `tests/test_plugin_lifecycle_router.py` |
| Full browser/MCP regression gate | Passed | `scripts/run_tests.py full` — 769 tests |
| Exact Assayer bundle build and offline launcher check | Passed | `scripts/build_plugin_bundle.py` — `assayer-0.1.2`; SDK release-gate wheel SHA-256 `6bdcf099244690e9d29e6e579c5185e8a07eae3121a9b6a8375472bfc21f6d99`; fresh local CLI bundle embeds wheel SHA-256 `5f246587a9dc69c708499f511e6e528922287f7a14ba3d9c3d322ccf52740f6f` |
| External `ass-spec` SDK consumer against exact `assayer 0.1.2` | Passed | 64 tests; exact-wheel release gate passed all four stages |
| External `ass-spec` against previously released `assayer 0.1.1` | Rejected as incompatible | Old bundled schema rejects `checkpointSemanticRules`; `ass-spec` now requires `assayer>=0.1.2` |
| Clean Codex CLI marketplace discovery and install | Passed | Disposable `CODEX_HOME`; `codex plugin marketplace add`, `plugin list --available`, `plugin add`, and `plugin list` all reported `assayer@assayer-clean` at `0.1.2+codex.20260909095928` |
| Clean installed-bundle MCP startup | Passed | Installed cache copy launched with isolated `HOME`/`XDG_CACHE_HOME`; private runtime created offline, `bundle-ready` and `runtime-identity.json` written, launcher exited 0 on EOF |
| Clean Codex natural-language lifecycle acceptance | Pending operator evidence | Requires an interactive Codex session for natural-language routing and confirmation UX; CLI/plugin and launcher gates above are complete |
| Operator-session attempt on 2026-09-09 | Blocked by environment | Isolated `codex exec` loaded the disposable profile but the model transport timed out through all retries; no domain-plugin mutation was performed |

## Automated Evidence

The deterministic integration journey is:

```text
install v1
-> list
-> start_plugin_run
-> advance_plugin_run
-> submit decision
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
Install ass-spec.
Confirm the displayed installation plan.
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
- the plan shown before every mutation;
- the user's confirmation or rejection;
- the resulting plugin state and active version;
- the Run ID and terminal status for plugin usage;
- the result section/report location;
- any failure, recovery, or retry.

## Exit Criteria

The automated and CLI portions are complete. Full operator acceptance still requires all of the following:

- Assayer is installed by Codex Marketplace without manual MCP configuration;
- the Assayer MCP starts and reports dependency failures clearly;
- `ass-spec` is installed through natural language and explicit confirmation;
- the same Codex task uses `ass-spec` and receives a terminal structured result;
- upgrade and rollback are completed through natural language;
- uninstall is completed through natural language;
- a Codex restart shows the expected durable state;
- no step requires the user to provide internal protocol fields or restart MCP manually.
