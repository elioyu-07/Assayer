# Assayer Platform Project Plan

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-03 |
| Status | Execution baseline |
| Owner | Assayer maintainers |
| Primary client | Codex CLI |

## 1. Purpose

Assayer is being developed as a stable, domain-neutral audit platform with an
independently installable plugin ecosystem. The first product journey is the
anonymous URL frontend audit, but the platform must also support non-browser
domains such as specifications, configuration, repositories, APIs, databases,
and logs.

This plan turns the earlier C03-C08 implementation work and the J00-J08 user
journey into one delivery sequence. It is the planning authority for priority
and sequencing; the user-journey Definition of Done remains the release gate
for the current Alpha product.

## 2. Product outcome

The first platform release is complete only when both statements are true:

1. A new CLI user can install Assayer once, provide only a natural-language
   request and business input such as a URL or file, run an audit, understand
   progress and results, recover from failure, and reuse the installation.
2. A new audit domain can be delivered as an independently packaged plugin
   without modifying Assayer platform source, while passing the same safety,
   evidence, decision, recovery, observability, and performance gates.

The platform's canonical output is a versioned structured result. Markdown,
HTML, screenshots, and other renderings are optional artifacts derived from
that result and never an independent source of truth.

## 3. Guiding constraints

- The platform owns lifecycle, permissions, evidence integrity, decision gates,
  persistence, recovery, observability, and common result semantics.
- A capability provider owns controlled access to a browser, file, repository,
  API, database, log, or other approved source. It does not decide compliance.
- A plugin owns domain rules, applicability, WorkItem discovery, evidence
  organization, semantic review requirements, and domain summary data.
- User intent or an explicitly documented default preset defines the audit
  boundary. The platform may adapt discovery and execution only inside that
  boundary; it must not silently add rule domains that the user did not select.
- The Agent is the primary interaction and semantic decision interface. It is
  not the trust boundary: generated plugins remain inspectable, versioned, and
  machine-validated packages.
- Unknown, missing, stale, ambiguous, or contaminated evidence fails closed and
  must never be presented as a pass.
- Optimizations are valid only when evidence closure, recovery, coverage, and
  result accuracy are unchanged.
- CLI is the primary acceptance client. Desktop uses the same deliverable and
  Host path as a compatibility client.

## 4. Delivery stages

### Stage 0 — Scope and baseline (M0)

**Goal:** stop scope drift and establish one measurable baseline.

**Deliverables**

- Reconcile implementation status against the J00-J08 journey, not against
  individual component test counts.
- Freeze the current Alpha scope: anonymous URL audits with interruption-safe
  checkpoint recovery; no login/SSO, source attribution proof, or automatic
  screenshot sanitization.
- Keep a decision log for accepted design choices and explicitly deferred work.
- Record a clean-configuration CLI baseline for startup, runtime, result, and
  failure-recovery behavior.

**Exit gate**

- Every open item has an owner, a journey stage, and a pass condition.
- New FUA rules and demonstration plugins are paused until the platform gates
  below are complete.

### Stage 1 — Close the current vertical user journey (M1)

**Goal:** finish the product foundation before turning current behavior into a
platform-wide promise.

**Deliverables**

- Close J04 runtime experience with continuous progress, bounded Agent waiting,
  clear phase changes, and diary-like logs.
- Close J05 result experience for `completed`, `partial`, `failed`, and concrete
  `needs_review` explanations.
- Close J06a second-use isolation with a new independent Run, no
  reconfiguration, and no state leakage after a terminal Run.
- Close J06b interruption-safe failure diagnosis and recovery as a separately
  gated platform capability. A user may stop a Run at any point and later say
  "continue"; the Host must resume from the last durable boundary without
  duplicate decisions, lost Evidence, stale writes, or reliance on Agent
  guesses. J06a second-use isolation must not substitute for J06b recovery.
- Close J07 in a clean CLI configuration outside the repository, including a
  success and a failure-recovery scenario and three consecutive independent
  runs without stale-state reuse.
- Close J08 with version identification, upgrade, rollback, uninstall, cleanup,
  and reproducible feedback diagnostics.
- Close the current platform-level Agent-waiting gap exposed by the Spec run:
  show bounded progress and waiting state, enforce a per-batch budget, and
  prevent an unbounded candidate payload from looking like a hung Host. This
  is an M1 usability safeguard, not a plugin-specific optimization.
- Preserve the existing structured performance bill and separate Agent waiting
  from Host, provider, browser, and transport time.
- Make the derived run diary readable enough for a person to reconstruct the
  sequence, objective, outcome, coverage, and next action without parsing JSON.

**Exit gate**

- The existing frontend vertical product satisfies the current J04-J08
  Definition of Done from a new user's point of view.
- A component test, deterministic harness, or one successful run cannot replace
  the clean CLI evidence.
- Any behavior promoted into the platform contract below has already survived
  a real user journey.
- A long semantic review is observable as Agent work with a bounded next step;
  the platform does not silently wait forever for one monolithic decision.

### Stage 2 — Freeze the platform constitution (M2) — documentation complete

**Goal:** define the stable laws that all plugins and providers share.

**Deliverables**

- Plugin Constitution: purpose, boundaries, safety, evidence, unknown, and
  release laws.
- Audit Plugin Contract: manifest, checks, WorkItems, InvestigationPackets,
  Findings, Decisions, recovery, idempotency, and summaries.
- Capability Provider Contract: capability names, authorization, scope,
  timeout, budget, failure, and evidence semantics.
- Canonical Audit Result Schema: terminal status, coverage, outcomes, findings,
  needs-review items, unverified items, failures, performance, trace, and
  domain extension.
- Versioning and compatibility policy for platform, plugin, check, capability,
  and identity algorithms.

**Exit gate**

- The contracts contain no frontend-only concepts in the generic surface.
- Platform, plugin, provider, Agent, and renderer ownership is unambiguous.
- A breaking change and a backward-compatible change have different version
  rules and migration expectations.

The M2 documentation gate is complete in
[Platform Constitution v1](platform-constitution-v1.md), [Audit Plugin Contract
v1](plugin-contract-v1.md), [Capability Provider Contract v1](capability-provider-contract-v1.md),
[Canonical Audit Result Contract v1](canonical-result-contract-v1.md), and the
[v1 traceability matrix](platform-contract-traceability-v1.md). Machine
conformance and install enforcement remain Stage 3 (M3); the matrix explicitly
marks partial implementation so this milestone does not overclaim.

### Stage 3 — Make the contracts enforceable and efficient (M3)

**Goal:** turn the constitution into install, release, and platform-performance
gates that every plugin shares.

**Deliverables**

- Shared plugin and provider conformance suites.
- Checks for manifest/version compatibility, identity and duplicate handling,
  evidence closure, decision-state gates, capability absence, stale-source
  invalidation, batching/splitting, ordering, retries, restart, recovery,
  terminal states, observability, performance accounting, and result schema.
- Release tooling that refuses to package or install a non-conforming plugin.
- Deterministic fixtures for fast contract tests and explicit real-adapter gates
  for production integrations. Missing real dependencies fail clearly; they do
  not silently skip.
- Generic paged and incremental lifecycle operations with durable checkpoints;
  a plugin can process bounded WorkItem batches without submitting one
  monolithic Agent payload.
- Host-side mechanical deduplication/grouping with an auditable mapping back to
  every original WorkItem; grouping cannot make a semantic decision or discard
  Evidence.
- Layered InvestigationPackets and cursor-based delta transport so Agent turns
  receive summaries first and expand raw Evidence only when needed.
- Platform-level staged Agent output and context governance. A Run reports a
  compact phase start, bounded incremental progress, phase completion, and a
  compact final result instead of emitting one monolithic response or
  narrating every candidate.
- Delta-first progress responses carry only changes since a stable revision or
  cursor. Checkpoint acknowledgements default to counts and durable references;
  repeated Evidence and accepted-item lists are expanded only on request.
- Host-driven workflow orchestration through mechanical boundaries. After the
  Agent supplies a semantic decision for a batch, the platform automatically
  persists checkpoints, advances deterministic paging, validates coverage,
  assembles the plugin Decision, and performs eligible closeout steps. The
  Agent is asked to act again only when a new semantic decision is required.
- Interruption-safe checkpoint recovery as a shared lifecycle contract:
  idempotent operation IDs, atomic checkpoint-and-response persistence,
  resume-state synchronization, revision fencing for stale requests,
  append-only superseding corrections, and pre-commit evidence/coverage
  validation. A correction extends the audit trace rather than overwriting an
  accepted checkpoint.
- A domain-neutral audit-selection contract supporting exact Checks, named
  rule profiles, and documented default presets. The selected rules are frozen
  at Run start, and the ledger and final result identify both covered and
  intentionally excluded scope.
- Safe adaptive batch sizing and failure splitting, honoring plugin-declared
  ordering, isolation, parallelism, and cache constraints.
- Platform-level Evidence cache keys and invalidation checks, plus measured
  parallel execution only for independent WorkItems.
- Performance conformance checks proving that call reduction does not reduce
  Evidence closure, coverage, recovery, result validity, or diagnostic detail.

**Exit gate**

- The same suite applies to built-in and external plugins.
- A plugin cannot publish a formal result when a mandatory conformance check
  fails.
- Conformance output identifies the failed invariant and the next action.
- A large WorkItem set can be processed incrementally with bounded Agent
  payloads, and every grouped item remains traceable in the ledger.
- Agent-visible progress identifies the current phase, completed and total
  groups/items, result counters, blockers, and next action at phase boundaries
  or a bounded cadence. It does not repeat Evidence or produce
  candidate-by-candidate narration.
- A Run with pending mechanical work remains explicitly non-terminal and
  exposes `requiredNextStep`, `canFinish: false`, and the remaining count. The
  Host must drive eligible mechanical closeout instead of relying on the Agent
  to issue one tool call per bookkeeping step.
- The lifecycle exposes an explicit `awaiting_agent_decision` state for
  genuine semantic gaps. No Run may stop in an ambiguous half-finished state,
  and no formal summary is emitted until coverage and closeout gates pass.
- The canonical result and ledger retain complete Evidence and decisions even
  when chat output is staged or compact. Staging and context governance cannot
  truncate unseen Evidence, reduce audit coverage, or introduce an elapsed-time
  cutoff.
- Performance bills separate Agent waiting, transport, Host, provider, cache,
  and split time; unavailable telemetry is explicit.

Current M3 progress: WorkItem inspection paging, summary-first Evidence,
on-demand full Evidence expansion, declarative nested Evidence collection
paging/grouping, adaptive batch sizing, and safe failure splitting are
implemented in Vertical Slices 042-045. Incremental semantic-review
checkpointing and final decision assembly are implemented in Vertical Slice
046. Host-driven workflow state, explicit semantic boundaries, deterministic
advance, and eligible automatic closeout are implemented in Vertical Slice
047. The normal product MCP catalog now exposes only the Host-driven lifecycle
surface; primitive lifecycle calls remain diagnostic-only, and explicit
noncompleted closeout is carried by `advance_plugin_run`. Staged user-facing
output and delta result transport are implemented in Vertical Slice 048:
terminal arrays and oversized text are sectioned by the platform, details use
result-bound cursor pages, and the full JSON remains a durable artifact.
Pre-persistence semantic checkpoint validation is implemented in Vertical
Slice 049: the platform now requires a plugin validation hook before any
checkpoint mutation, and the Spec plugin rejects malformed Findings,
candidate-coverage errors, and untraceable Evidence at the current semantic
boundary instead of deferring failure to finalization.
Stable Host-generated semantic operation acknowledgement is implemented in
Vertical Slice 050: checkpoints and Decisions bind content-derived operation
identity to the same atomic ledger mutation, identical retries do not add
records or repeat committers, and responses expose the current durable
revision without asking the Agent for protocol fields.
Durable active-Run resume is implemented in Vertical Slice 051: a replacement
interactive Host verifies and hydrates the persisted Run, exposes its exact
durable boundary, and fences the older Host owner before mutation. Terminal
closeout removes temporary resume metadata.
Append-only semantic checkpoint correction is implemented in Vertical Slice
052: exact-scope corrections retain immutable history, supersede only the
current effective leaf, replay idempotently, survive Host restart, and make
coverage, validation, decision assembly, and finalization consume only the
effective chain.
Deterministic persistence and terminal-publication fault injection is
implemented in Vertical Slice 053: running-ledger rollback, pending-result
promotion, latest-terminal replay, orphan-pointer repair, and corrupt-state
fail-closed behavior are now covered.
The checkpoint and incremental semantic-review recovery path is accepted for
this milestone. Bundle `0.1.0+codex.20260903082017` passed deterministic
verification and isolated startup; the owner then completed a real Codex CLI
trial and reported the interruption/continuation experience as acceptable on
2026-09-03. Exact timing telemetry was not supplied and is not inferred.
Cache/parallel execution and install/release conformance remain open for
subsequent slices.

### Interruption recovery gate (J06b / M3-R)

This is the next platform reliability slice because it protects every domain
plugin and every long-running Run, rather than one plugin's business logic.

**Required behavior**

- Every mutating Agent operation carries an `operationId`; replaying the same
  operation returns the original outcome without duplicating ledger records.
- Checkpoint state, Run revision, and the response acknowledgement are
  persisted atomically, so an interruption cannot leave the Agent guessing
  whether a write happened.
- A resume call first synchronizes from the Host's durable state and returns
  the authoritative phase, revision, completed boundary, and exact next
  action.
- Stale revisions are rejected safely and answered with the current state;
  they cannot overwrite a newer checkpoint.
- Invalid checkpoint submissions fail before persistence. A correction is an
  append-only superseding record with an auditable link to the invalid record.
- Closeout is forbidden while required recovery work, coverage, or semantic
  decisions remain unresolved.

**Acceptance evidence**

- Deterministic tests cover interruption before write, after write/before
  acknowledgement, after acknowledgement, duplicate replay, stale revision,
  Host restart, invalid checkpoint correction, and resume after partial
  failure.
- A clean CLI journey demonstrates `Esc -> continue` on both a semantic
  checkpoint and a mechanical checkpoint, with no duplicate decisions and a
  traceable final result.
- The diary identifies the interruption point, durable boundary, recovery
  action, and final outcome in human-readable language.

**Gate status:** accepted on 2026-09-03. Deterministic fault tests cover the
durable invariants, and the owner reported a successful real Codex CLI trial
after installing bundle `0.1.0+codex.20260903082017`. The acceptance record
does not claim timing measurements that were not captured.

### Stage 4 — Externalize the Spec plugin (M4)

**Goal:** prove that the ecosystem model works outside the platform source.

**Deliverables**

- Move the Spec-quality implementation and its policy resources into an
  independently buildable distribution.
- Register it through the `assayer.plugins` entry-point mechanism.
- Support independent install, discovery, upgrade, rollback, and uninstall.
- Preserve the existing structured review summary and remove HTML as a
  canonical or required output.
- Run a complete CLI journey using only the Spec business input.

**Exit gate**

```text
independent package
  -> install and discover
  -> run with Agent semantic review
  -> produce a valid platform result
  -> upgrade and rollback
  -> uninstall cleanly
```

No Assayer platform source change is allowed as part of installing or updating
the external Spec plugin.

### Stage 5 — Build the Agent-first plugin workbench (M5)

**Goal:** let a developer create a conforming plugin through conversation
without making conversation the only trust mechanism.

**Agent workflow**

```text
describe intent
  -> clarify subject, rule, evidence, and unknown cases
  -> match existing capabilities
  -> generate standard plugin package and fixtures
  -> run conformance checks
  -> repair failures
  -> request human confirmation
  -> install/register/version
```

**Required outputs**

- Readable source files and manifest, not opaque model state.
- Rule and evidence definitions, scope schema, tests, Skill instructions, and
  release metadata.
- A summary of assumptions, permissions, unresolved questions, and generated
  changes before installation.

**Exit gate**

- A developer can inspect or edit every generated artifact.
- The Agent cannot bypass conformance, permission, or publication gates.
- Re-running the same request is deterministic or explains its differences.

### Stage 6 — Externalize frontend and providers (M6)

**Goal:** prove the platform handles materially different domains and reusable
capabilities.

**Deliverables**

- External frontend audit plugin with no new FUA-specific branches in the
  kernel.
- One concise `web-audit` Skill owns the stable browser-audit journey; FUA
  rules remain versioned Checks in the frontend plugin rather than becoming
  one Skill per rule or being embedded wholesale in the Skill.
- Frontend rule selection supports one exact FUA, a named topic profile, or a
  documented audit preset. Natural-language intent is resolved into one of
  these explicit selections before the Run begins.
- Browser capability provider isolated from frontend rule semantics.
- At least one additional provider boundary (file/repository/API/database)
  where a real plugin needs it.
- Two independently packaged plugins using the same provider or contract
  surface.

**Exit gate**

- Spec and frontend plugins install and run independently.
- A targeted frontend request executes only the selected rule boundary, while
  an unspecified request uses the documented default preset. Adaptive page and
  object discovery stays inside that boundary, and the result cannot imply
  coverage of excluded FUA domains.
- Provider failures are classified separately from rule decisions.
- Platform source remains unchanged when a domain plugin is added or upgraded.

### Stage 7 — Ecosystem expansion (M7)

Only after M4-M6 are stable:

- additional domain plugins and capability providers;
- organization policy, signing, trust, and marketplace workflows;
- deeper batching, caching, parallelism, and incremental traversal;
- screenshot sanitization and stronger source attribution;
- distributed execution and larger-scale scheduling.

Each new abstraction must be justified by at least two real plugin use cases.

## 5. Cross-cutting quality requirements

### Observability

Every Run must provide a diary-like trace of lifecycle, tool calls, Host
operations, browser/provider events, decisions, recovery, leases, failures,
and terminal status. Diagnostics must attribute likely ownership to the
environment, provider, Host, Agent strategy, rule, transport, or target
application. Missing telemetry is recorded as unavailable, never as zero.

### Performance

Measure public Agent calls and waiting separately from Host, provider,
transport, and cache time. Batch discovery/investigation/decisions only when a
plugin declares it safe. Preserve per-item evidence and ledger records inside
combined calls. Optimize the platform seams first; avoid case-specific timing
patches until real bills identify a repeated bottleneck.

### Agent output and context

Agent output follows four user-visible stages: phase started, compact periodic
progress, phase completed, and compact final result. Progress contains only the
current phase, completed and total groups/items, result counters, material
blockers, and the next action. Tool responses are summary-first and delta-first;
full Evidence remains available through explicit expansion and in the durable
ledger. Splitting text into more chat messages is not itself a context
optimization, so the platform must prevent repeated payloads and Evidence
residency rather than merely increasing narration. No output policy may hide an
unverified boundary or allow an incomplete Run to imply full coverage.

### Workflow driving

The Host is responsible for advancing deterministic lifecycle work and for
making terminal-state eligibility explicit. Agent turns provide semantic
judgments; they do not need to orchestrate every page, checkpoint, coverage,
or closeout operation. A Run either continues through Host-owned mechanical
steps, pauses as `awaiting_agent_decision`, or ends as `completed`, `partial`,
or `failed` with a concrete reason. An Agent message that stops without one of
these states is not treated as a completed audit.

### Security and privacy

Credentials, cookies, authorization values, hidden reasoning, raw page bodies,
and unsanitized sensitive screenshots must not enter ordinary Agent context,
logs, or reports. Screenshot sanitization remains a deferred security phase;
controlled trials must state when sanitization was not performed.

### Compatibility

Historical frontend MCP names remain compatibility aliases. New plugins use
generic lifecycle names and platform schemas. Historical Runs remain readable
and are never reinterpreted by a newer plugin or rule version.

### Scope governance

Users decide what kind of audit they want; the platform decides how to inspect
that declared scope efficiently and completely. Exact Checks, topic profiles,
and audit presets are first-class, versioned selections. Every Run freezes the
resolved selection and reports included and excluded coverage. Adaptive
discovery may reject inapplicable objects or optimize traversal, but it cannot
silently broaden the selected rule boundary.

## 6. Current priority and explicit deferrals

### Execution snapshot

| Stage | Current status | Remaining release gate |
|---|---|---|
| M0 Scope and baseline | Scope is documented | Refresh the clean CLI baseline when owner acceptance runs |
| M1 Vertical user journey | Partially implemented; J06b accepted | J04 observability, J07 clean CLI, and J08 lifecycle evidence |
| M2 Platform constitution | Documentation complete | Machine enforcement belongs to M3 |
| M3 Enforceable and efficient platform | Active; Slices 042-053 packaged and J06b accepted | Conformance/release enforcement, then measured cache/parallel work |
| M4 External Spec plugin | Queued | Starts only after the M3 reliability gate |
| M5 Agent-first workbench | Queued | Starts after one external plugin lifecycle is proven |
| M6 External frontend/providers | Queued | Requires stable external plugin and provider contracts |
| M7 Ecosystem expansion | Deferred | Requires evidence from at least two real plugin use cases |

### Active reliability slice sequence

1. Reject invalid Evidence, Finding, and coverage references before checkpoint
   persistence. Completed in Vertical Slice 049.
2. Add Host-generated idempotent operation identity and atomically persist the
   semantic mutation, durable revision basis, and acknowledgement. Completed
   for the active Host lifecycle in Vertical Slice 050.
3. Make resume synchronization return the authoritative durable boundary and
   exact next action; fence stale Host ownership without turning it into a
   dead end. Completed in Vertical Slice 051.
4. Add append-only checkpoint correction through an explicit superseding
   record; never rewrite accepted history. Completed in Vertical Slice 052.
5. Add deterministic interruption and restart fault-injection tests, then
   package the plugin. Persistence and terminal-publication fault injection is
   completed in Vertical Slice 053; bundle `0.1.0+codex.20260903082017` is
   built, isolated-launcher verified, and installed from the personal
   marketplace.
6. Leave real `Esc -> continue` CLI acceptance to the owner and record the
   evidence before claiming J06b complete. Accepted on 2026-09-03 from the
   owner's installed Codex CLI trial; no uncaptured timing values are claimed.

### Next work, in order

1. Start the M3 plugin conformance and release gate: turn the shared plugin
   constitution into package-time validation that rejects incompatible or
   incomplete plugins before installation.
2. Close the remaining J04-J08 implementation gaps, including the M1 bounded
   Agent-waiting safeguard, readable diary output, and fresh clean-CLI
   evidence. Retain three independent owner-run CLI journeys as an explicit
   acceptance item until they are actually run.
3. Complete the remaining M3 platform conformance, install, and performance
   gates: shared contract enforcement, release rejection, cache/parallel
   measurements, and result/recovery invariants across generic plugins.
4. Externalize the Spec plugin and complete its independent lifecycle.
5. Build the Agent-first plugin workbench.

### Deferred

- J04 real CLI evidence: three independent Runs, including a diagnosable
  partial or failed Run and a successful retry without stale-state reuse. The
  implementation may advance to J05, but J04 cannot be marked accepted until
  this evidence exists;
- New FUA cases and broad crawler/deep-recursion expansion;
- Plugin marketplace UI and distributed execution;
- Automatic screenshot redaction;
- Source/version attribution proof;
- Per-plugin micro-optimizations;
- Advanced distributed execution without measured evidence.

## 7. Change-control rule

Every proposed task must name the stage it advances, the shared contract it
changes (if any), its acceptance evidence, and the reason it cannot wait. If it
does not advance a current stage gate or remove a release blocker, it belongs
in the backlog rather than the active sprint.
