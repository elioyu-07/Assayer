# J02: Activation and Discovery Acceptance

> **Historical acceptance record.** This file records one acceptance run on
> 2026-09-01 against the vertical runtime that this repository no longer ships.
> The `assayer:assayer-audit` Skill and the web-URL audit capability were
> retired, and the compiled Host now exposes `start_compiled_run`,
> `bind_provider`, `discover_sources`, `collect_evidence`,
> `plan_review_batches`, `submit_review_batch`, `finalize_compiled_run`, and
> `get_compiled_result` instead of `start_audit`. The observations below are
> retained for traceability of that execution and are not current guidance or
> an active gate.

## Objective

After installing the Assayer Plugin, a new Codex Desktop task or CLI session receives the same Skill and MCP without copying configuration, entering absolute paths, or registering Assayer MCP manually.

## Recorded status at the 2026-09-01 acceptance

On 2026-09-01, a real fresh Codex CLI session automatically discovered `assayer:assayer-audit`, resolved the local Plugin MCP, and directly called `mcp__assayer__start_audit` for a formal lease-mock audit. CLI primary acceptance passed; Desktop remains a later compatibility check, not the current primary gate.

- `assayer@personal` is `installed, enabled`; its version may include `+codex.<cachebuster>`;
- Plugin cache contains manifest, Skill, lightweight MCP launcher, explicit runtime preparer, offline wheelhouse, and bundle manifest;
- Legacy user `mcp_servers.assayer` and repository `.codex/config.toml` absolute-path wiring was removed;
- `codex mcp list` no longer shows Assayer as a manually configured external MCP; Plugin manages its source;
- Open tasks do not refresh Plugin catalogs dynamically; acceptance requires a new task or app-server restart;
- Codex CLI may defer local Plugin MCP startup; after Skill selection it must resolve the deferred `assayer` catalog before formal audit;
- Runtime preparation selects a matching Python minor from the bundle manifest and cannot use a PATH Python below 3.11 or with incompatible ABI; normal MCP launch performs no environment installation.

## Fresh-Task Acceptance

Send this in a new Desktop or CLI task:

```text
Check Assayer CLI integration. You may discover the tool catalog and connect to Assayer MCP. List only `mcp__assayer__*` tool names. Do not call any Assayer business tool and do not start a browser.
```

Pass conditions:

1. CLI resolves deferred `assayer` MCP and lists at least 17 `mcp__assayer__*` tools;
2. Tool descriptions such as `start_audit` and `complete_audit` include complete `request` envelope and specialized `input` structure;
3. The task could call tools, but this step starts no browser;
4. Natural language triggers the Skill and the Skill explicitly forbids nested Codex;
5. User supplies no `scanId`, `runId`, `expectedRunRevision`, output directory, or absolute path.

## CLI and Desktop Consistency

Both clients discover from the same Plugin manifest, Skill dependency, and `.mcp.json`; the launcher starts the same Python Host from the Plugin private runtime. Deferred local MCP startup is allowed, but Skill selection must resolve the `assayer` catalog. Resolution failure reports concrete startup diagnostics and never falls back to legacy repository wiring or smoke.
