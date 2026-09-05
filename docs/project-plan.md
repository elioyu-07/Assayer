# Assayer Platform Project Plan

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-04 |
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
- Measured parallel execution only for independent WorkItems, bounded by
  negotiated runtime concurrency and merged deterministically. Persistent or
  cross-process platform caching is explicitly deferred; the existing in-process
  compatibility cache is not expanded in this milestone.
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
047. The normal product MCP catalog exposes the Host-driven lifecycle plus
bounded read-only Evidence collection paging; primitive lifecycle mutations
remain diagnostic-only, and explicit noncompleted closeout is carried by
`advance_plugin_run`. Staged user-facing
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
The first plugin release gate is implemented in Vertical Slice 054: plugin
registration and package validation now reject missing factories, undeclared
capabilities, invalid scope schemas, unsafe execution profiles, manifest
incompatibility, and incomplete constructed interfaces. The bundle builder
runs the same gate before wheel creation and emits actionable `PCV1-*`
conformance failures. Package-resource fixtures and independent installer
enforcement remain open.
Independent package resource validation is implemented in Vertical Slice 055:
the static gate verifies a release descriptor, safe contained paths, manifest
and project identity, business scope schema, runtime registration source,
semantic-review instructions, deterministic fixture contracts, and Python
entry-point metadata without importing plugin code. Isolated installation and
fixture execution remain open.
Isolated release execution is implemented in Vertical Slice 056: a statically
valid local package is installed into a temporary target without dependency
download, loaded in a new Python worker through exactly one declared entry
point, reconciled against its static manifest and scope schema, and exercised
through deterministic Platform Kernel fixtures. This gate does not persistently
install, upgrade, or trust unreviewed code.
Generic runtime progress and diary output are implemented in Vertical Slice
057: every interactive plugin response now exposes a compact phase, lifecycle
state, waiting owner, completed and remaining counters, durable next step, and
plain-language next action. `platform-run.log` is a derived diary that explains
the Run identity, chronological work, decisions, failures, current position,
and whether Agent waiting is expected. The canonical ledger remains unchanged
as the source of truth. Real clean-CLI acceptance remains open.
Platform-owned result experience is implemented in Vertical Slice 058: every
generic terminal response now explains validity, discovery and WorkItem
coverage, common outcome counts, review/failure counts, and the next action.
Decision pages preserve committed reasons and dimension reasons;
`needs_review` exposes concrete unresolved dimensions; and failed Runs suppress
invalidated Decisions and optional plugin summaries from the formal result
while retaining immutable ledger history.
The checkpoint and incremental semantic-review recovery path is accepted for
this milestone. Bundle `0.1.0+codex.20260903082017` passed deterministic
verification and isolated startup; the owner then completed a real Codex CLI
trial and reported the interruption/continuation experience as acceptable on
2026-09-03. Exact timing telemetry was not supplied and is not inferred.
Persistent platform caching is deferred. Parallel execution and provider
install/release conformance are implemented in subsequent slices.
Capability-provider registration conformance is implemented in Vertical Slice
066: provider descriptors, API compatibility, scope schemas, authorization,
failure and retry semantics, algorithm identity, factories, constructed
interfaces, duplicate IDs, and ambiguous capability selection now fail closed
through the shared `assayer-provider-check` gate. Runtime capability
intersection and provider Evidence binding are implemented in the following
slices; external package validation and isolated installation follow in Slice
069.
Four-way capability and budget negotiation is implemented in Vertical Slice
067: the effective profile is the exact intersection of Check requirements,
provider declarations, platform policy, and user scope; effective limits can
only narrow provider ceilings; and blocked negotiation cannot create a runtime
context. Provider-bound runner integration and provider Evidence/failure
binding are implemented in Vertical Slice 068: Host-created requests carry
Run, WorkItem, Check, provider, capability, source-state, idempotency, and
effective-budget identity; responses must contain bounded source facts or a
declared safe failure; and the Kernel rejects incomplete or substituted
Provider Evidence before semantic decision. Existing product adapters remain
explicit compatibility paths, while external provider packaging and isolated
installation are implemented in Vertical Slice 069. Provider source packages
now bind descriptor, entry point, runtime source, package metadata, and
deterministic capability fixtures without static code import; a new-process
temporary installation gate reconciles runtime identity and executes both fact
and classified-failure fixtures through the negotiated Provider boundary.
Persistent installation and product-adapter migration remain open.
The parallel inspection contract is implemented in Vertical Slice 070:
plugins must explicitly allow parallel work and guarantee independent ordering,
the scheduler cannot exceed negotiated `maxConcurrency`, missing policy falls
back to serial, failures remain isolated, and accepted packets are merged in
original WorkItem order before semantic decision. The ledger and performance
metrics record whether parallel execution occurred and why. Existing plugins
remain serial until their independence is proven; no manifest is widened by
this slice.
The unified platform performance bill is implemented in Vertical Slice 071.
Every generic Run now separates available wall-clock elapsed time, summed Host
operation work, isolated Provider request work, parallel inspection elapsed
time, and summed parallel task work. Missing Run, Agent, model, transport, or
Provider telemetry is explicit and has no fabricated zero duration. Scheduler
wait reduction is labeled as an estimate and is never reported as measured
end-to-end speedup. JSON and summary-first Markdown views are derived from the
canonical platform ledger and cannot affect Evidence, Decisions, or status.
Shared terminal result and recovery conformance is implemented in Vertical
Slice 072. Batch publication, interactive terminal persistence, and isolated
external-plugin fixtures now enforce the same ledger/result identity,
terminal-event, Evidence/Decision, latest-recovery, receipt, completed
coverage, failed-result suppression, and artifact traceability invariants.
The latest interactive recovery outcome is an active Decision barrier rather
than passive diagnostic text.
Portable canonical result publication is implemented in Vertical Slice 073.
Generic batch and interactive terminal Runs now derive and validate one
`canonical-result.json` from the canonical ledger, with common coverage,
outcomes, Findings, review and unverified items, failures, performance, and
relative trace references. The result binds the exact persisted ledger digest,
and historical replay validates identity, status, schema, and digest before
returning it. Plugins cannot override common result fields through their
separate summary views.
Frontend canonical result compatibility is implemented in Vertical Slice 074.
The validated frontend AuditLedger now derives the same portable result
contract without changing `audit-summary.md`, `issues.json`,
`audit-ledger.json`, or browser diagnostics. The adapter preserves exact
Assessment, Finding, Evidence, invalidation, coverage, performance, and ledger
digest semantics; frontend-only counts live in a validated namespaced
extension.

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
- deeper batching, parallelism, and incremental traversal;
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
| M1 Vertical user journey | J04-J05 implementation and J06b recovery complete | J04-J05 owner acceptance, J07 clean CLI, and J08 lifecycle evidence |
| M2 Platform constitution | Documentation complete | Machine enforcement belongs to M3 |
| M3 Enforceable and efficient platform | Active; Slices 042-074 implemented and J06b accepted; multi-window ownership defect designed | Implement multi-window Run isolation, then collect measured owner workloads and remaining clean-CLI evidence |
| M4 External Spec plugin | Active; durable platform plugin lifecycle landed (install/upgrade/rollback/uninstall + fail-closed discovery) | Move Spec-quality into an independently buildable distribution and prove the CLI journey |
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

1. Converge the four logical platform layers and plugin boundary according to
   [Four-Layer Platform Boundary Convergence](architecture-layer-convergence-design.md).
   Design is complete; implementation must proceed through L1-L6 with Spec and
   Config as the first two migration proofs. Do not split processes or add
   ecosystem machinery during this stage.
2. Complete the remaining acceptance around the platform-level
   [multi-window Run isolation and Host lifecycle design](multi-window-run-isolation-and-host-lifecycle-design.md)
   only to the minimum required for safe user journeys. The first implementation
   slice is complete: startup/tool discovery no longer claims a Run, ownership
   is Run-local, independent Runs coexist, simultaneous cross-process resume
   has one winner, `resume_plugin_run` is explicit, and MCP shutdown releases
   ownership. The implementation and focused evidence are recorded in
   [platform-run-isolation-implementation-slice.md](platform-run-isolation-implementation-slice.md).
   Clean owner-run CLI interruption and multi-window acceptance remains before
   this item can be closed. A cross-process registry is permitted only if this
   minimum proves insufficient; global scheduling and broad migration remain
   deferred.
3. Complete the Spec golden journey through the generic plugin path: fresh CLI
   discovery, long-running staged progress, bounded semantic checkpoints,
   interruption and resume, readable terminal result, failure and retry, and a
   second independent Run. Build a fixed semantic evaluation corpus covering
   strong, weak, contradictory, ambiguous, duplicate, false-positive, large,
   interrupted, and cross-document Specs. Cross-document cases must use one
   anchor Spec to identify semantic ambiguity, conflict, contradiction, and
   drift in explicitly related or profile-selected documents, with both source
   sides, document digests, relationship evidence, impact, and resolution
   ownership. Improve the plugin-owned 18-check authority, checklist, rubric,
   template, NFR catalog, recognition signals, cross-document comparison rules,
   and review instructions against that corpus. Treat this as an anchored
   analysis profile mapped to affected core checks; do not scan the whole
   repository or add a `CHK-19` until the corpus proves a separate dimension is
   necessary. Fix only shared platform gaps or demonstrated Spec domain defects.
   The S1 corpus slice is complete: the platform provides a domain-neutral
   schema and reference validator, while the Spec plugin packages its semantic
   baseline plus fixed strong, weak, ambiguity, contradiction, duplicate, and
   false-positive Markdown cases, plus large, interrupted, and anchored
   cross-document cases. Every deterministic candidate is compared by stable ID
   and every candidate has an explicit semantic disposition. Cross-document
   relationships carry both source identities, relationship evidence, impact,
   and resolution ownership; the scanner leaves their semantic comparison to
   review. The S2 policy slice is also complete: the reviewed CHK-01 through
   CHK-18 standard is content-first and layout-neutral by default, the bundled
   twelve-chapter structure is an explicit strict profile, source evidence is
   pageable and immutable, and checklist decisions are processed in four
   required durable checkpoint batches before finalization. The next Spec
   slice has started with single-document calibration: exact candidate ID and
   rule pairs are regression-checked, and Agent guidance now distinguishes
   ambiguity, contradiction, duplicate root causes, and non-normative noise.
   The model-level structural comparator, corpus-suite aggregator, terminal
   ledger adapter, and portable result schemas are complete. They compare
   candidate dispositions, merged root-cause clusters, severity, and readiness
   without binding generated IDs or prose, while keeping missing, unexpected,
   and failed Cases distinct. The first anchored cross-document contract slice
   is complete: the plugin publishes an exclusive `anchor + relatedDocuments +
   relationships` scope schema, creates only one anchor WorkItem, computes all
   document digests internally, invalidates on either-side drift, and exposes a
   schema-validated pageable evidence collection carrying relationship basis,
   ownership, both document identities, exact excerpts, source chunks, line
   ranges, and digests. Duplicate or unanchored scope is rejected, and the
   fixed retention corpus Case exercises this packet without pretending that
   deterministic code has made the semantic decision. Connecting these
   bilateral references to Agent-origin cross-document findings is also
   complete at the admission boundary. Review schema `1.2.0` requires a valid
   semantic type, declared relationship, exactly both documents, affected CHK
   and elements, matching resolution owner, next action, and immutable evidence
   from both sides; formal readiness, commit receipts, and summary projection
   preserve those fields. The Host-driven relationship slice is also complete:
   every declared relationship is a required review checkpoint between
   candidates and the checklist, resume returns the remaining relationship,
   finalization cannot bypass it, and the product MCP surface provides bounded
   read-only paging over the bilateral Evidence groups. The Assayer Spec Skill
   describes the same workflow. The corpus evaluator now models this contract
   directly: cross-document Cases compare exact relationship coverage and
   outcomes plus semantic type, status, severity, both document identities,
   and resolution ownership, instead of pretending that relationship findings
   are scanner candidates. Executing actual model corpus Runs remains next;
   real CLI acceptance remains user-run. The immediate validation slice now
   narrows to a reusable Markdown document-navigation provider: it returns a
   complete, line-addressable structure map for the Spec plugin while leaving
   semantic judgment in the plugin/Agent. JSON, YAML, PDF, and other formats
   remain out of scope until the Markdown-to-Spec A/B result proves the
   boundary useful. The provider and Spec navigation-mode flow are now covered
   by focused contract tests: every parsed unit is pageable, checkpointed once,
   and included in a complete 18-dimension review before finalization. The
   legacy candidate strategy remains the default until real corpus evidence
   demonstrates that navigation-first review improves quality without hiding
   findings. The platform now exposes built-in and installed capability
   providers through one isolated Provider Catalog; the Assayer distribution's
   own entry point is deduplicated while third-party identity conflicts remain
   fail-closed. The subsequent Spec result-integrity Slices A-E are now
   implemented deterministically: review schema `1.3.0` requires source-backed
   document context, applicability, precise checklist explanations, and
   actionable Findings; the plugin exposes stable source facts; Host admission
   rejects directly false absence claims and orphan `REWORK`; canonical output
   preserves the admitted Findings; and the fixed corpus now distinguishes a
   shared delegated contract from a feature Spec with genuine CASE and
   risk-scenario omissions. Automated corpus checks cover `FR-G01` through
   `FR-G08`, `AS-006`, delegation, non-applicability, classification, and
   checklist outcomes. A user-run real-model CLI evaluation remains the exit
   gate; deterministic completion alone is not model-quality acceptance.
4. Complete the remaining M3 platform performance evidence required by the Spec
   golden journey:
   collect measured parallel workloads across generic plugins. The legacy
   frontend compatibility result adapter is complete without changing
   historical outputs. Provider timing and the unified performance bill are implemented
   in Slice 071; shared result/recovery conformance is implemented in Slice
   072; portable canonical results are implemented in Slice 073, and frontend
   compatibility publication is implemented in Slice 074. Persistent
   platform caching is deferred and is not a prerequisite.
   The next result-quality slice is Vertical Slice 075: a platform-owned,
   additive actionable-result envelope that separates dimension Findings from
   root-cause Remediations, validates impact/Evidence/action/closure/owner
   fields, and publishes confirmed remediation work alongside unresolved
   blockers. Existing plugin results remain readable with
   `actionability=not_declared` until they adopt the envelope.
   The first Evidence Claim follow-up is also implemented additively: the
   platform validates direct, absence, derived, and external-unverified claim
   shape, same-Investigation Evidence binding, bounded line scopes, and
   Remediation `claimRefs`; the Spec plugin emits bounded absence claims for
   confirmed missing-content findings. Semantic search completeness remains
   plugin-owned.
5. Externalize the Spec plugin and complete its independent lifecycle,
   including persistent install, upgrade, rollback, and uninstall.
6. Resume the deferred Frontend Alpha J04-J07 journey closure. Existing
   frontend implementation and regression coverage remain maintained, but this
   acceptance is not in the current active sequence.
7. Build the Agent-first plugin workbench after one external plugin lifecycle is
   proven.

### Deferred

- J04 real CLI evidence: three independent Runs, including a diagnosable
  partial or failed Run and a successful retry without stale-state reuse. The
  implementation may advance to J05, but J04 cannot be marked accepted until
  this evidence exists;
- Full Frontend Alpha journey closure is deferred while the Spec golden journey
  and independent package pilot are completed. Frontend J04-J07 remains a
  release gate and must not be declared complete from Spec or smoke evidence;
- New FUA cases and broad crawler/deep-recursion expansion;
- Plugin marketplace UI and distributed execution;
- Automatic screenshot redaction;
- Source/version attribution proof;
- Per-plugin micro-optimizations;
- Advanced distributed execution without measured evidence.

## 6.1 Platform construction boundary

The global platform construction plan is maintained in
[Platform Foundation and User-Journey Delivery Plan](platform-foundation-and-user-journey-plan.md).
That document is the detailed implementation boundary for the next stage and
must be read together with this roadmap.

Its rule is simple: implement the shared capabilities required by the J01-J08
journey first—installation and discovery, scope binding, runtime progress,
result semantics, recovery and second use, clean acceptance, and safe feedback.
Use Spec as the first cross-domain golden-path pilot while retaining Frontend as
the real-browser acceptance path. Do not build a daemon, general scheduler,
persistent cache, marketplace UI, or other ecosystem machinery until a failed
acceptance scenario, measured workload, security finding, or two independent
plugin use cases requires it.

The plan's current platform sequence is:

```text
journey and observability baseline
  -> minimum ownership/recovery correction
  -> Spec golden journey
  -> independent Spec packaging
  -> Frontend Alpha journey closure
  -> ecosystem expansion
```

This section intentionally summarizes the boundary rather than duplicating the
normative workstreams, exit gates, and deferred list in the dedicated plan.

## 7. Change-control rule

Every proposed task must name the stage it advances, the shared contract it
changes (if any), its acceptance evidence, and the reason it cannot wait. If it
does not advance a current stage gate or remove a release blocker, it belongs
in the backlog rather than the active sprint.
