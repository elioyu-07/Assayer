# Platform Boundary Map

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-02 |
| Status | Reference map aligned with M2 frozen contracts |
| Owner | Assayer Maintainers |

## Implementation Status

The first extraction slice is implemented in `src/assayer_platform`. The configuration-quality plugin and the frontend compatibility plugin both execute through the same `PlatformKernel` conformance gates. The frontend bridge delegates discovery, bounded investigation, evidence collection, and recovery to the existing product facade, so the legacy MCP contract and browser ledger remain unchanged. Plugin manifests are selected through `PluginRegistry`; installed distributions may contribute `assayer.plugins` entry points, and the generic CLI can execute a registered non-browser Check.

This remains a compatibility milestone, but public product routing now creates an
`InteractivePlatformRun` by default. Its discovery, investigation, commit, and
terminal checkpoints are persisted beside the Host ledger in the same SQLite
database. The Host ledger remains the browser-domain record for object/evidence
closure and Assessment display; the platform ledger is the sole authoritative
decision and generic cross-domain lifecycle record. Installed distributions may
contribute `assayer.plugins` entry points, and both the generic CLI and the
domain-neutral `list_plugins`/`run_plugin` MCP entrypoints execute registered
batch Checks.

The generic commit seam now exists: `DecisionCommitter` returns a validated
`CommitReceipt`, and the frontend compatibility package owns translation from
the browser-domain atomic write to that Platform receipt. Receipt replay is
idempotent across kernel restarts, and every Platform Run records
`decision_authority=platform`. The batch commit adapter remains available for
direct kernel callers; the interactive product facade records a distinct
Platform commit ID and an exact Host Assessment projection reference.

`SQLitePlatformLedgerStore` is now the default bridge for the product runtime:
it stores the immutable platform ledger in a `platform_ledgers` table inside
the existing Host SQLite database. This gives both layers one durable file and
one transaction owner without changing the public MCP names or making browser
entities part of the generic contract. Full replacement of browser-shaped
entities remains a separate migration.

Real-browser Runtime instances now create their Host `SQLiteStore` at
`<scan-output>/host-ledger.sqlite3` instead of using an in-memory database.
This makes the existing browser audit ledger restart-readable by default and
exposes the matching `platform_ledger_store` to the product transport.
The product Run also exports `platform-ledger.json`, `platform-events.jsonl`,
and `platform-run.log` into the Scan output directory as it checkpoints.

The product-facing `prepare_decision` composite now applies the frontend
plugin's Check and Finding gates before it invokes `record_findings` or any
Host persistence. Invalid semantic proposals are rejected without staging a
Host Finding. Object identity, evidence closure, recovery, and final commit
remain Host-owned.

This map records which current Assayer modules are reusable platform-kernel material and which are frontend-specific adapters. It prevents the current FUA-10/browser implementation from silently becoming the future platform model.

## 1. Target Layers

| Target layer | Responsibility |
|---|---|
| Platform kernel | Run lifecycle, work scheduling, identity, Evidence, decisions, recovery contract, budgets, persistence, observability, and publication gates |
| Plugin SDK/contract | Manifest, checks, dimensions, capability requirements, execution constraints, invalidation, and conformance hooks |
| Runtime adapter | Browser, API, file, repository, database, log, or other approved data access |
| Agent/Skill adapter | Domain instructions, semantic selection, semantic decisions, and user-facing workflow |
| Report adapter | Domain presentation derived from the immutable ledger |

## 2. Current Module Classification

| Current module or resource | Current role | Target layer | Migration decision |
|---|---|---|---|
| `src/assayer_host/core.py` | Run lifecycle, ledger transactions, browser-shaped entity handlers, decision gates | Kernel plus browser adapter seams | Keep lifecycle and gates in the kernel; extract browser assumptions behind runtime interfaces |
| `src/assayer_host/store.py` | SQLite persistence and runtime-event storage | Platform kernel | Reuse; rename generic fields only when a compatibility migration exists |
| `src/assayer_host/observability.py` | Event integrity, performance bill, telemetry limitations | Platform kernel | Reuse and add plugin/public-call dimensions |
| `src/assayer_host/reporting.py` | Ledger-derived issue and diagnostic projections | Kernel plus report adapters | Keep generic diagnostics in kernel; move domain-specific renderers to plugins |
| `src/assayer_host/transport.py` | JSON/MCP envelopes, generic plugin entrypoint, and product facade composites | Kernel transport adapter | Keep protocol ownership; browser-specific names remain compatibility aliases |
| `src/assayer_host/runtime_router.py` | Scan-owned runtime routing and lease supervision | Platform kernel | Reuse; runtime factory becomes adapter selection |
| `src/assayer_host/errors.py` | Stable Host error codes and retry guidance | Platform kernel | Reuse; add domain-neutral capability and plugin compatibility errors |
| `src/assayer_host/evidence.py` | Evidence validation and immutable references | Platform kernel | Reuse; payload collectors become adapter-owned |
| `src/assayer_host/recovery.py` | Recovery result and recovery policy abstraction | Platform kernel | Reuse; browser replay remains adapter-specific |
| `src/assayer_host/action_safety.py` | Safety classification for typed actions and writes | Kernel policy plus browser adapter | Keep generic deny-by-default policy; move browser action vocabulary to browser runtime |
| `src/assayer_host/object_identity.py` | Browser object fingerprinting and rebinding | Runtime adapter | Generalize to WorkItem identity; retain browser fingerprint implementation |
| `src/assayer_host/page.py` | PageState, entrypoint, and page/object observations | Browser runtime adapter | Do not expose these types as platform concepts |
| `src/assayer_host/browser_*.py` | Chromium session, DOM, visual, actions, source, and replay | Browser runtime adapter | Keep isolated behind the runtime adapter boundary |
| `src/assayer_agent/loop.py` | Agent orchestration and semantic investigation loop | Agent/Skill adapter | Generalize from page/object terms to WorkItem/InvestigationPacket terms |
| `src/assayer_host/harness.py` | Deterministic Host lifecycle harness | Platform conformance adapter | Reuse for contract tests; never present it as external-runtime proof |
| `rules/registry.json` | Enabled FUA rules and frozen rule metadata | First plugin package | Move toward a plugin-owned check registry; preserve Run freeze and digest semantics |
| `rules/FUA-*.md` | Frontend rule semantics and coverage | First plugin package | Keep domain-local; no new kernel branches for individual FUA rules |
| `.agents/skills/assayer-audit/SKILL.md` | Codex frontend audit workflow | Agent/Skill adapter | Keep as the first domain Skill; generic batch plugins use `run_plugin` |
| `plugins/assayer/skills/assayer-audit/SKILL.md` | Installed frontend audit Skill | Agent/Skill adapter | Package with the first plugin, not the platform kernel |
| `schemas/*` | Persistent entity and protocol contracts | Kernel or plugin-owned schemas | Split generic Run/Evidence/Decision schemas from browser observation schemas |

## 3. Compatibility Rules During Migration

1. Existing browser MCP tools remain supported while the generic contract is introduced.
2. No current FUA-10 result may change solely because a module moved layers.
3. The kernel may expose compatibility aliases, but new code must use generic concepts internally.
4. Browser-specific schemas remain valid for the browser adapter and are not copied into generic schemas.
5. A plugin may opt out of batching, caching, parallelism, or compression; the kernel must honor the declaration.
6. Every extraction step adds a conformance test before deleting or renaming a browser-specific path.

## 4. First Non-browser Validation

The recommended second plugin is a read-only specification or configuration quality checker. It should use file/repository input and structured Evidence, while exercising the same Run, WorkItem, Check, Finding, Decision, checkpoint, diagnostics, and performance contracts. Its implementation must not require changes to browser runtime modules.

This milestone is complete: the frontend compatibility plugin and the
non-browser configuration and Spec-quality plugins pass the same kernel
conformance suite. The production-facing transport checkpoints frontend
lifecycle in the generic ledger, exposes `run_plugin` for registered batch
Checks, and now also exposes the domain-neutral interactive lifecycle without
changing existing frontend tool names. The frontend runtime itself is not yet
migrated onto that generic controller.
