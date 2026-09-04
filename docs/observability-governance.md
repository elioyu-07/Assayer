# Assayer Observability Governance and Diagnostic Contract (C08.1)

| Metadata | Value |
|---|---|
| Document version | 1.1.0-draft |
| Date | 2026-09-04 |
| Status | C08.1-C08.4 completed; ongoing governance baseline for J04-J08 acceptance |
| Owner | Agent Runtime / Host Core / Product Owner |

## 1. Objective

Observability is not auxiliary logging. It is the infrastructure that lets Assayer obtain reliable clues across browsers, page frameworks, permission environments, and business systems, and then improve continuously. Every scan should answer:

1. What happened in the environment: browser, page, route, capability, network, and runtime state;
2. What the Host observed: objects, controls, lists, Evidence, actions, recovery, and gate results;
3. What the Agent did: investigation objective, public action rationale, evidence used, and next step;
4. Why the conclusion was reached: Finding, Assessment, blockers, conflicts, and coverage chain;
5. Which layer should be fixed: target page, browser adapter, Host, Agent, rule, transport, or runtime environment.

Observability diagnostics must not stop at `failed` or `needs_review`. They must distinguish fault ownership and provide reproducible clues.

## 2. Four-Layer Fact Model

| Layer | Sole responsibility | Fact carrier | May change a formal conclusion? |
|---|---|---|---|
| Audit ledger | Prove final conclusions, evidence, and reference closure | `audit-ledger.json` | Yes, and only through Host transactions |
| Runtime events | Record process, sequence, duration, errors, and environment | `runtime-events.jsonl` | No |
| Public decision trace | Explain Agent objectives, action rationale, and Host feedback | `RuntimeEvent(category=decision/tool)` | No |
| Diagnostic view | Aggregate timelines, bottlenecks, attribution, and integrity | `observability-manifest.json`, `run-diagnostics.*` | No |

The ledger remains the only source of truth for audit conclusions. Runtime events may reveal why no conclusion exists or where reliability was lost, but they must never generate Findings, Assessments, or Issues retroactively.

## 3. Unified Event Model

Persisted events follow [`runtime-event.schema.json`](../schemas/runtime-event.schema.json). Every event must contain:

- `eventId`, `scanId`, `runId`, and a monotonically increasing `sequence`;
- wall-clock time in `occurredAt` and process-monotonic time in `monotonicOffsetMs`; duration events must include `durationMs`;
- `source`, `category`, `name`, `phase`, `severity`, and `outcome`;
- a short, human-readable, sanitized `summary`;
- reference closure: when available, `agentTurnId`, `requestId`, `operationId`, and PageState, Object, Case, Finding, Assessment, and Evidence references;
- a `privacy` declaration and constrained `attributes`; raw pages, credentials, prompts, and hidden model reasoning must never be placed in events.

A timed action uses a `phase=start/finish` event pair, with `durationMs` recorded on the finish event. If time cannot be measured reliably, the event must explicitly use `outcome=unknown` or describe the diagnostic limitation; zero must not be used to pretend the action completed instantly.

## 4. Required Core Events

The following events form the C08 core diagnostic closure:

| Category | Minimum events |
|---|---|
| lifecycle | `scan.started`, `scan.terminal` |
| tool | `operation.started`, `operation.finished` for every Operation |
| decision | Every public Agent tool decision and its concise rationale |
| safety | Host gate acceptance, rejection, blocking, and error codes |
| browser | Session start, page navigation, page observation, and Context invalidation |
| recovery | Case recovery start, every attempt, and final result |
| lease | Renewal, expiry, and Supervisor convergence |
| integrity | Event-stream validation, reference closure, and diagnostic-integrity results |

When any core event is missing, `coreCompleteness.status` in `observability-manifest.json` must be `incomplete`, and diagnostic views may only report `diagnosticCompleteness=limited/invalid`.

## 5. Extended Telemetry

Model token counts, model end-to-end latency, retry counts, stream interruptions, transport reconnections, and browser process metrics are extended telemetry. The system must not assume every caller exposes them. Each signal must be marked `captured`, `partial`, or `not_exposed`; `not_exposed` must never be interpreted as zero.

Missing extended telemetry does not invalidate audit conclusions, but it reduces diagnostic completeness and the limitation must be displayed in the report.

## 6. Public Decision Trace and Privacy

The system may record the current investigation objective, a short public rationale for tool selection, entity references used, Host response status, gate result, next action, turn sequence, request ID, and duration.

It must not record hidden model reasoning, complete prompts or messages, chain-of-thought, plaintext credentials, cookies, Authorization values, raw user input, raw HTML or DOM, request or response bodies, or base64 images. Model confidence alone must never serve as issue evidence.

## 7. Fault-Attribution Dimensions

Diagnostic reports must attribute faults to at least one of these layers and cite supporting events: `environment`, `browser_adapter`, `host_contract`, `agent_strategy`, `rule_contract`, `transport_runtime`, or `target_application`. Attribution is diagnostic guidance, not a rule decision. When evidence is insufficient, use `unattributed`.

## 8. Integrity Gates

[`observability-manifest.schema.json`](../schemas/observability-manifest.schema.json) fixes the following checks: event sequences are contiguous; Operation start/end, real time, and duration are closed; Tool/Decision events can be associated with requests, Operations, and Host responses; every Agent decision has Host feedback; the Scan has a terminal event; every Assessment traces back to its Finding, Evidence, Case, and timeline; lease state agrees with the Scan terminal state; and privacy gates pass.

If any core closure is missing, the report must explicitly state that diagnostics are incomplete and must not claim that no runtime problem was found.

## 9. Phased Implementation

| Slice | Deliverable | Status |
|---|---|---|
| C08.1 | This specification, RuntimeEvent/Manifest schemas, attribution, and privacy boundaries | completed |
| C08.2 | Host Operation/browser/recovery/safety event persistence, with real time and duration corrected | completed |
| C08.3 | Agent Decision Trace, model/transport/lease events, and cross-entity references | completed |
| C08.4 | Assessment timeline, diagnostic reports, integrity gates, and regression samples | completed |

## 10. Generic Plugin Run Views

Interactive plugin Runs expose one canonical history and three derived reading
views:

- `platform-ledger.json` is the canonical structured history;
- `platform-events.jsonl` is the machine-oriented chronological stream;
- `platform-run.log` is the derived human diary;
- `progress` in interactive responses is the compact current-position view.

The progress view identifies the phase, lifecycle state, waiting owner,
completed and remaining counts, durable required next step, plain-language next
action, and terminal status. `awaiting_agent_decision` means durable state is
saved and semantic Agent judgment is genuinely required; it must not look like
a Host timeout or unexplained hang. The diary gives Run/plugin/Check identity,
chronological lifecycle explanations, decisions, failures, current position,
and the same next action. These views are deterministically derived and cannot
create or alter Evidence, Decisions, failures, coverage, or recovery history.

Batch Runs also record `inspection.parallel.planned`. The event states whether
inspection is serial or parallel, why that mode was selected, and the bounded
task and worker counts. Metrics separate parallel wall time, summed task time,
and estimated wait reduction. The estimate is diagnostic, not an audit fact or
an exact end-to-end performance claim.

Generic platform Runs publish `platform-performance-bill.json` and a
summary-first Markdown view. The bill keeps elapsed wall time separate from
summed Host, Provider, and task work because concurrent work can overlap.
Provider timing is captured at the controlled Provider boundary; Agent wait,
transport, and model time remain `not_exposed` unless their owning client
supplies measurements. Missing telemetry has no numeric duration. Scheduler
wait reduction is labeled `estimated` and cannot be presented as measured
end-to-end speedup or used to change an audit conclusion.

Terminal result conformance is independently derived from the canonical
ledger. It verifies that the terminal event, latest recovery status,
Evidence/Decision references, authoritative receipts, formal result, and
published artifacts agree. A failed conformance report blocks publication and
identifies the violated `RCV1-*` invariant; it never repairs or rewrites the
ledger silently.

Every terminal plugin Run also publishes `canonical-result.json`. Generic Runs
derive it from `platform-ledger.json`; the frontend compatibility journey
derives it from its validated `audit-ledger.json`. It is the portable public
result and contains only stable relative trace references. Its ledger digest
covers the exact persisted source-ledger bytes. Historical result loading
validates the schema, Run identity, status, and digest before returning a
terminal acknowledgement. Plugin-specific summary content remains a separate
derived view unless a declared extension schema and privacy gate authorize it.

## 11. Relationship to Deferred Work

- B07c automatic screenshot sanitization remains an independent security task. C08 must not write unsanitized images to events or relax the `issue_found` gate.
- `inspect_source` remains a future capability. C08 may record that it is unavailable or that a call was rejected, but must not fabricate source evidence.
- C08 diagnostic results do not change the current FUA-10 scope: inspect frontend presentation only, not backend integration.
