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
| Same-connection upgrade and rollback | Passed | `tests/test_mcp_stdio_integration.py` |
| Natural-language route classification | Passed | `tests/test_plugin_lifecycle_router.py` |
| Clean Codex Marketplace acceptance | Pending operator evidence | This document, section 3 |

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
| Date/time and timezone | pending |
| Codex version | pending |
| Assayer plugin version | pending |
| Domain plugin and version | pending |
| OS and architecture | pending |
| Python version | pending |
| Codex profile/config root | pending disposable profile |
| MCP startup result | pending |

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

This acceptance is complete only when all of the following are recorded:

- Assayer is installed by Codex Marketplace without manual MCP configuration;
- the Assayer MCP starts and reports dependency failures clearly;
- `ass-spec` is installed through natural language and explicit confirmation;
- the same Codex task uses `ass-spec` and receives a terminal structured result;
- upgrade and rollback are completed through natural language;
- uninstall is completed through natural language;
- a Codex restart shows the expected durable state;
- no step requires the user to provide internal protocol fields or restart MCP manually.
