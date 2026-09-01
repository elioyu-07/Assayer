# Real Browser and MCP Integration Plan

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-30 |
| Status | B01-B12 Host integration completed (B07c deferred); LLM investigation C01-C07 completed |
| Owner | Host Core / Security Owner |

## 1. Objective and Completion Definition

Connect the Harness-validated Host Core to real test/pre-release pages and use the same protocol through Codex MCP. Completion requires:

- One managed browser Context per Scan, shared by every browser adapter through one serial execution queue;
- Page, object, action, request, recovery, and screenshot facts come from that Context with no fixture fallback;
- Credentials enter Host memory only through a local secure channel, are cleared after login, and remain invisible to MCP, CLI, SQLite, and reports;
- Request interception is installed before any audit action and fails closed when pre-send blocking cannot be proven;
- MCP and CLI are transport adapters over the same `HostCore.handle`;
- Real-browser integration covers success, blocking, timeout, crash, recovery failure, screenshot binding/fail-closed behavior, and transport consistency; B07c separately covers automatic screenshot sanitization;
- No HTML is produced and `audit-ledger.json` remains the sole runtime source of truth.

## 2. Integration Gaps and Requirements

| Area | Earlier state | Requirement |
|---|---|---|
| Browser-session ownership | Independent adapter interfaces | Centrally create, locate, serialize, and destroy Context by Scan |
| Production defaults | Page and identity previously defaulted to deterministic fixtures | Browser-fact adapters default unavailable; explicit assembly required |
| Browser configuration | `browserProfile` existed without behavior | Resolve to constrained versioned config; reject arbitrary arguments/scripts |
| Credentials | Vault stored one string | Typed Host secrets; MCP sees one-time handle only |
| Login | No real navigation/form/SSO convergence | Separate login from audit actions; unsupported flows fail explicitly |
| Page and object | No DOM/ARIA collection or locator registry | Raw selectors stay in memory; Core sees summaries and handles only |
| Request safety | Classification without pre-send browser hook | Install route before first navigation; unknown requests block |
| Actions and recovery | State machine without real inverse/replay | Action log references internal recovery handles; all checks observable |
| Visual evidence | Object screenshots existed; pixel sanitization absent | Host handles location, crop, format, digest, immutability; B07c adds masking proof |
| Transport | Deterministic CLI only | JSON CLI and MCP share validation, Core routing, and errors |
| Budgets and exit | Required conceptually | Bound every external wait; close Context on terminal state, exception, and process exit |

## 3. Fixed Design Decisions

1. **Single session aggregate**: `BrowserSession` owns page, locators, requests, actions, recovery, and screenshot transient state; thin adapters map existing contracts to it.
2. **Per-Scan serialization**: browser commands for one Scan run serially; isolated Scans may run concurrently.
3. **Production/fixture separation**: only Harness/tests inject deterministic adapters. Missing required production capability refuses startup with no partial fallback.
4. **Short-lived locator registry**: selectors, ElementHandles, and original values exist only in Session memory. Ledger stores opaque `hostLocatorId` and identity digest; rerender requires rebind.
5. **Interception before navigation**: install routing before the first page request. Login has a separate authorized phase, followed by stricter audit policy.
6. **Configuration allowlist**: browser type, headless, viewport, locale, and budgets are typed. Reject arbitrary executable, extension, proxy credentials, launch arguments, and injected scripts.
7. **Transport has no business logic**: MCP/CLI do not generate IDs, decide rules, read browser, or write reports; they manage secure local input, instance routing, and Core calls only.
8. **Closure invalidates**: browser crash, Session loss, or unprovable close/clear fails the Scan; never open a new browser and reuse old objects or conclusions.

## 4. Ordered Tasks

| # | Deliverable | Acceptance | Status |
|---|---|---|---|
| B01 | Unavailable production adapters and explicit fixtures | Partial injection cannot produce fixture page/object | completed |
| B02 | Session registry, bundle, constrained config, lifecycle/concurrency | One Context per Scan, serial commands, reliable release | completed |
| B03 | Typed in-memory credentials, local prompt, login policy, clearing proof | Plaintext absent from args/env/log/SQLite; cleared on failure/timeout | completed |
| B04 | Playwright read-only slice | Local site bootstrap/inspect, stable ambiguity rejection, no fallback | completed |
| B05 | Pre-send interception, attribution, safe actions, RequestObservation | Writes/cross-origin/unknown blocked; races become unknown/failed | completed, slice 015 |
| B06 | Inverse, refresh replay, nine checks, rebind | Targeted/fallback success, uncertainty, and contamination recomputable | completed, slice 016 |
| B07a | Real structured Evidence | Minimum DOM/ARIA facts, strong bindings, typed field state, sanitization | completed, slice 017 |
| B07b | Object-level screenshots | Crop, rebind, bounds/format/digest, immutable files, derivation | completed, slice 018; `sanitizationStatus=not_performed`, so formal issue blocked |
| B07c | Automatic screenshot sanitization | Sensitive-region detection, irreversible masking, proof | deferred; never backfill or fabricate state |
| B08 | JSON Lines CLI and optional SDK stdio MCP | Equivalent response across transports; no credential values | completed |
| B09 | Local test site, browser E2E, fault injection, runbook | Security gates pass; no HTML | completed, slice 020 |
| B10 | Anonymous mode, BrowserHostRuntime, audit/serve assembly | Public URL launches Chromium through one Host protocol | completed, slice 021 |
| B11 | Entrypoint exploration, tabs, read-only XHR, bounded traversal | Traverse discovered tabs; preserve structure/error/Evidence; block writes | completed, slice 022 |
| B12 | Site-independent Host fact-chain acceptance | Real URL collects facts without site labels/selectors; no LLM claim | completed, slice 023 |

B07a/B07b prove safe capture and binding of raw object images, not sanitization. Until B07c, real paths cannot publish `issue_found`. B08-B10 transport, entrypoint, and fault handling cannot bypass this gate.

B01-B12 prove the Host/browser/protocol execution plane. [LLM Agent Investigation Plan](llm-agent-integration-plan.md) owns C01-C07 semantic control. `assayer smoke` collects Host facts without Assessment; formal `assayer audit` is Codex Agent plus dynamic MCP.

## 5. B07 Sanitization Decision

Decision date: 2026-08-30. B07a structured collection and B07b object crop/binding/digest/immutability are complete. B07c sensitive-region detection, irreversible masking, and proof are deferred. Screenshot schema and independent-image issue gate remain. Controlled test data may produce Raw Visual with `sanitizationStatus=not_performed`; it cannot be labeled `sanitized` or support formal publication. Whole-page screenshots, recordings, and manual screenshots are not substitutes. Resuming B07c adds masking and proof tests without weakening B07b alignment and fail-closed gates.

## 6. B01 Change Note

B01 changed default HostCore page and object-identity adapters to unavailable. Harness and tests explicitly inject fixtures. This preserves testing while eliminating the unsafe combination where login configuration silently produced fake page facts.

## 7. Non-Goals

- No production environment, unbounded crawl, arbitrary script, or user selector;
- No FUA decision inside browser adapters;
- No post-hoc request log impersonating pre-send interception;
- No recorded cookie, storage state, or browser profile replacing the credential channel;
- No HTML report or second source of truth.
