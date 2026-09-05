# Platform Foundation and User-Journey Delivery Plan

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-09-04 |
| Status | Planning proposal; implementation sequencing authority pending review |
| Owner | Assayer Maintainers |
| Scope | Minimum platform work required to deliver a reliable Alpha user journey |

## 1. Purpose

Assayer should first become a dependable product for one complete user journey.
The platform must be broad enough to support the journey across clients,
plugins, failures, and releases, but no broader than the evidence requires.

This plan is the practical boundary between two common mistakes:

- polishing one plugin while shared lifecycle failures still strand Runs;
- building a complete imagined ecosystem before any end-to-end journey is
  consistently usable.

The current Alpha has substantial contracts and implementation slices. Those
assets are not the same as an accepted product journey. The release gate remains
the J01-J08 user journey and its Definition of Done, not the number of passing
component tests or the existence of an MCP tool.

## 2. Product outcome

The next meaningful release is complete when a new CLI user can:

```text
install once
  -> open a fresh task
  -> provide only intent and a business target
  -> see understandable progress
  -> receive a valid result or a useful failure
  -> interrupt and continue safely
  -> run a second independent audit
  -> identify the version and attach diagnostics
```

The first platform proof uses two complementary journeys:

1. **Frontend journey:** the existing primary Alpha path, which proves real
   Chromium, safe interaction, visual/DOM Evidence, and FUA behavior;
2. **Spec journey:** the first cross-domain platform pilot, which proves a
   non-browser plugin, long semantic review, staged output, checkpointing, and
   eventual independent packaging.

Spec is a pilot, not a replacement for the frontend Alpha acceptance gate.

## 3. What is already in place

The following foundations exist and should be reused rather than redesigned:

| Foundation | Current state | Evidence boundary |
|---|---|---|
| Platform ownership and invariants | v1 documents define Platform, Plugin, Provider, and Agent boundaries | Contracts and governance documents |
| Generic plugin lifecycle | Batch and interactive lifecycle, checkpointing, summaries, and result paging exist | M3 implementation slices |
| Evidence and Decision gates | Findings, Evidence references, recovery, receipts, and failed-result suppression are enforced | Conformance and interactive tests |
| Canonical result | All current plugins publish validated `canonical-result.json` | Vertical Slices 073-074 |
| Observability | Ledger, event stream, diary, diagnostics, and performance bill exist | C08 governance and result artifacts |
| Interruption recovery | Durable checkpoint recovery and stale-writer fencing exist for the current single-owner model | J06b accepted deterministic and owner-run evidence |
| Plugin release gates | Registration, package, isolated-install, provider, and fixture checks exist | M3 conformance tooling |
| Codex delivery | CLI is primary; the bundled Skill and deferred MCP launcher are version-aligned | J01-J02 implementation evidence |

These foundations are valuable, but some are still single-Host or built-in
assumptions and therefore need journey-level acceptance.

## 4. Minimum platform foundation

Only the following shared capabilities are release-blocking before the Spec
golden journey is polished.

### 4.1 Installation and discovery (J01-J02)

**Required outcome:** one installed deliverable exposes the Skill, MCP launcher,
rules, schemas, and runtime resources without repository paths or manual
configuration.

**Must verify:**

- version and bundle identity are visible and internally consistent;
- Python and Chromium are declared external prerequisites and fail clearly when
  absent or incompatible;
- a fresh CLI task discovers the Skill and MCP without stale-session ambiguity;
- MCP tool discovery never starts a browser or claims an audit Run;
- CLI is the primary acceptance client and Desktop follows the same path.

**Not required yet:** public marketplace UX, automatic dependency installation,
or a cross-platform release matrix beyond the current supported Alpha target.

### 4.2 Run start and scope binding (J03)

**Required outcome:** the user provides intent and business input only; the
platform creates and freezes the Run identity, selected plugin/Check, scope,
capability profile, and output location.

**Must verify:**

- invalid or missing scope fails before domain work;
- internal IDs, revisions, paths, and protocol fields are Host-owned;
- a new Run never restores semantic state from another Run;
- frontend URL binding and Spec file binding are explicit and traceable;
- `audit` cannot silently fall back to deterministic `smoke`.

### 4.3 Runtime experience (J04)

**Required outcome:** a user can tell whether the Host is working, the Agent is
thinking, a semantic decision is required, recovery is running, or the Run has
ended.

**Must verify:**

- phase transitions and bounded progress are visible;
- long Agent reasoning does not look like a Host hang or lease failure;
- large candidate sets use summary-first, paging, and incremental checkpoints;
- diary entries explain objective, action, outcome, blocker, and next action;
- no total-duration cutoff invalidates a legitimately large audit;
- Agent, Host, Provider, transport, browser, and scheduling time remain
  distinguishable when telemetry is available.

### 4.4 Result experience (J05)

**Required outcome:** a non-developer can understand the terminal status, actual
coverage, issues, unresolved work, and next action.

**Must verify:**

- `completed`, `partial`, and `failed` have distinct plain-language meanings;
- `needs_review` names the missing discriminating fact and attempted checks;
- a failed Run never presents prior observations as formal conclusions;
- the canonical ledger, `canonical-result.json`, diary, and diagnostics agree;
- plugin-specific reports remain derived views, not competing truth sources.

### 4.5 Recovery and second use (J06)

**Required outcome:** `Esc -> continue` resumes one interrupted Run safely, and a
second audit starts independently without reinstalling or editing configuration.

**Must verify:**

- interruption before and after checkpoint acknowledgement is recoverable;
- replay is idempotent and does not duplicate Evidence, checkpoints, or Decisions;
- stale or replaced Hosts cannot mutate a newer boundary;
- a second Run cannot see the first Run's semantic state;
- multiple windows do not steal ownership merely by starting;
- MCP shutdown releases resources while preserving recoverable artifacts.

The smallest implementation may initially support one active writer per Run and
multiple independent Run directories. A global scheduler, permanent daemon, or
full process manager is not required unless acceptance reveals a resource
problem.

### 4.6 Clean acceptance and release feedback (J07-J08)

**Required outcome:** the journey works from a clean directory and the user can
identify the installed version, retry a failure, and collect safe diagnostics.

**Must verify:**

- three independent owner-run CLI journeys do not reuse stale state;
- at least one success, one controlled issue, and one diagnosable failure or
  partial result are exercised;
- upgrade, rollback, uninstall, and cleanup use the supported Codex boundary;
- feedback artifacts contain safe Run IDs and relative diagnostic references;
- no internal absolute paths, credentials, cookies, or hidden reasoning leak.

The real J08 lifecycle remains open until its Codex authorization and disposable
installation acceptance are run; fake-process lifecycle tests are supporting
evidence only.

## 5. Workstream ordering

The platform and product work should proceed in this order:

### Workstream A — Journey baseline and observability closure

Reconcile J01-J08 against the current implementation, refresh the clean CLI
baseline, and ensure every run can be explained from its diary, ledger, result,
and diagnostics.

**Exit gate:** a fresh CLI run has an attributable start, progress, result or
failure, and safe artifacts.

### Workstream B — Minimum concurrency and lifecycle correction

Address only the shared lifecycle defect that blocks reliable reuse and multiple
tasks:

- no Host startup takeover;
- Run-local ownership and fencing;
- explicit resume after an expired lease;
- MCP EOF/shutdown cleanup;
- cross-process race tests.

Use the [multi-window design](multi-window-run-isolation-and-host-lifecycle-design.md)
as the reference, but implement only this minimum slice first. Do not begin with
a daemon, general scheduler, persistent cache, or management UI.

**Exit gate:** two independent Runs and one interrupted Run are safe in
process-isolated CLI acceptance.

### Workstream C — Spec golden journey

Use the existing Spec plugin to prove the complete non-browser path:

```text
discover -> inspect -> staged semantic review -> checkpoint
  -> interrupt -> resume -> final decision -> canonical result
  -> readable summary -> retry -> independent second Run
```

Focus on user-visible waiting, context bounds, review explanations, and
diagnostics. The Workstream must also improve the Spec plugin's normative policy
content and prove that its findings are useful, consistent, and actionable. This
is plugin-domain work and must not introduce Spec-specific branches into the
platform.

#### Spec plugin validation focus

The golden journey must exercise more than successful execution. Its evaluation
set must cover:

- a strong Spec that should avoid unsupported findings;
- a weak Spec with missing structure, untestable requirements, and absent NFR
  decisions;
- contradictory statements across requirements, AC, CASE, field rules, and
  dependencies;
- genuine ambiguity where the missing decision is named precisely;
- duplicate signals that should merge into one root-cause finding;
- scanner candidates that should be suppressed as noise or false positives;
- incomplete AC-to-CASE and requirement-to-evidence traceability;
- boundary, error, permission, lifecycle, and concurrent-state scenarios;
- large documents requiring bounded batches and staged Agent output;
- interrupted review, replay, retry, and a second independent document.

It must also cover cross-document semantic analysis with one user-selected Spec
as the anchor:

- incompatible definitions of the same business term, field, state, or action;
- contradictory requirements or acceptance conditions across the anchor and its
  related documents;
- unresolved ambiguity in ownership, permissions, validation, lifecycle, or
  error behavior;
- dependency, API, data-model, decision, and test-document drift;
- duplicated requirements whose constraints or consequences diverge;
- a related document that is missing, inaccessible, stale, or version-mismatched.

Every fixture needs expected candidate, suppression, merge, finding, severity,
and readiness outcomes. Human review of the expected outcomes remains the
semantic oracle; candidate count alone is not a quality metric.

#### Spec policy improvement focus

Review and improve the plugin-owned authority, checklist, rubric, template, NFR
catalog, recognition signals, and semantic-review instructions as one versioned
policy set. The work must:

- define each of the 18 checks in plain language with applicability, required
  Evidence, pass, issue, and `needs_review` criteria;
- separate a missing mandatory decision from an optional recommendation;
- require direct source excerpts and exact locations for formal findings;
- make contradiction, ambiguity, testability, atomicity, and traceability rules
  operational rather than subjective;
- define when related candidates merge and when distinct consequences remain
  separate findings;
- define false-positive counterexamples and suppression reasons;
- assign remediation ownership and provide concrete correction guidance without
  rewriting the user's Spec automatically;
- define readiness aggregation from the 18 checks without hiding unresolved or
  unreviewed scope;
- keep NFR evaluation context-sensitive rather than requiring every NFR category
  for every product;
- define cross-document semantic checks around one anchor Spec, including how
  related documents are selected, how terms and entities are matched, and how
  contradiction, conflict, ambiguity, and drift differ;
- require both sides of a cross-document claim to carry source excerpts,
  locations, document digests, and the relationship that justifies comparison;
- treat an unavailable, stale, or weakly related document as `needs_review` or
  unverified scope, never as evidence that no conflict exists;
- keep cross-document analysis bounded to documents explicitly referenced by the
  anchor, supplied in scope, or selected by a versioned and explainable profile;
  repository-wide crawling is not the default;
- report cross-document findings against the anchor Spec while preserving every
  related-document reference and mapping impacts to affected core checks;
- do not add a new `CHK-19` solely to expose this capability until the fixed
  corpus proves that a separate checklist dimension is necessary.
- version policy resources and freeze their digests at Run start.

Policy changes require before/after evaluation against the fixed fixture corpus.
They are accepted only when they reduce incorrect or unhelpful outcomes without
weakening detection of known defects. Optimizing wording for one document is not
enough.

#### Cross-document analysis boundary

The anchor Spec defines the audit coordinate system. The plugin may compare it
with documents that are:

1. explicitly listed by the user or the selected Spec scope;
2. directly referenced by the anchor through a path, identifier, dependency,
   API, model, decision, or test link; or
3. selected by a versioned profile with a bounded root and a recorded selection
   reason.

The Host collects immutable excerpts and document identity digests. The Agent
decides whether two facts are semantically compatible, ambiguous, or
contradictory. A path similarity, matching filename, or model confidence alone
cannot establish a relationship or a formal conflict.

Each cross-document finding must include:

- the anchor Spec and related-document identities and digests;
- at least one exact excerpt and source location from each side;
- the relationship that made the comparison in scope;
- classification as `ambiguity`, `conflict`, `contradiction`, `drift`, or
  `unverified_dependency`;
- affected terms, entities, states, actions, or constraints;
- impact on the anchor's requirements, AC, CASE, NFR, dependency, or decision;
- a concrete resolution owner and next action.

The default result remains anchored to the selected Spec. Related documents do
not become an implicit second audit target, and unrelated repository content is
not silently pulled into Agent context.

The input and evidence foundation for this boundary is implemented. The Spec
plugin accepts either the existing independent `files` scope or an exclusive
cross-document scope containing one `anchor`, explicit `relatedDocuments`, and
directional `relationships`. It computes and freezes all document digests,
creates only the anchor WorkItem, invalidates the packet when either side
changes, and publishes bilateral source material through the pageable
`cross-document-evidence` collection. `scope.schema.json` and
`cross-document-evidence.schema.json` are plugin-owned so the generic Host
remains domain-neutral. Review schema `1.2.0` now admits cross-document
reviewer-origin findings only with bilateral frozen evidence, relationship
closure, affected core checks and elements, matching ownership, and a concrete
next action. Active Host orchestration is now implemented: every declared
relationship becomes a required semantic checkpoint between candidate review
and checklist review, interruption resumes at the remaining relationship, and
finalization cannot bypass the queue. The normal product MCP path exposes the
bounded read-only Evidence pager needed to inspect both sides without exposing
checkpoint primitives. Actual model-corpus execution remains open work.
The portable corpus evaluator is relationship-aware: its cross-document result
schema distinguishes relationship coverage and outcomes from scanner-candidate
coverage and compares the admitted semantic finding structure without scoring
explanation prose.

**Exit gate:** a new user can complete the journey and understand every terminal
state without reading protocol or ledger files. The fixed Spec corpus also meets
its expected semantic outcomes, every formal finding is source-traceable and
actionable, suppressions are explainable, merged findings retain all affected
references, cross-document findings preserve both source sides and their
relationship, unavailable related documents are disclosed, and the policy
version is recorded in the canonical result.

### Workstream D — Frontend Alpha journey closure (deferred)

Re-run the same journey gates for real Chromium, including positive, controlled
negative, partial, failure, second-use, and recovery scenarios. Keep frontend
rule semantics and platform lifecycle changes separate.

This remains a release gate for claiming the overall Frontend Alpha journey, but
it is not part of the current active sequence. It is intentionally deferred
while the platform is proven through the Spec golden path and external package.

**Exit gate:** the J04-J07 frontend acceptance matrix passes without smoke
fallback or silent skips.

### Workstream E — Spec externalization

After Workstreams A-C and the applicable M3 reliability gates pass, move Spec
implementation and policy resources to an independent package. Frontend
Workstream D is not a prerequisite for this platformization pilot. Prove
independent discovery, install, upgrade, rollback, uninstall, and the same
canonical result contract without changing platform source.

**Exit gate:** the M4 independent package lifecycle is complete.

### Workstream F — Frontend Alpha journey closure

Resume the deferred Frontend Alpha acceptance after the Spec package lifecycle
is stable. This work proves the real-browser path; it must not be replaced by
Spec results or deterministic smoke evidence.

**Exit gate:** the J04-J07 frontend acceptance matrix passes and the overall
Alpha user-journey Definition of Done can be evaluated honestly.

### Workstream G — Ecosystem expansion

After Workstreams E-F, externalize frontend and reusable Providers, then add new domains only when two
real plugins justify each new platform abstraction.

## 6. Explicitly deferred until evidence requires it

These are valid future directions, but they are not prerequisites for the next
complete user journey:

- global CPU/memory scheduler or general-purpose queue;
- permanent Assayer daemon or operating-system service;
- persistent or cross-process Evidence cache;
- distributed or multi-machine execution;
- marketplace UI and publisher trust workflows;
- broad crawler/deep-recursion expansion;
- automatic screenshot redaction;
- source/version attribution proof;
- per-plugin performance micro-optimization;
- user-visible process management;
- Agent-generated plugin workbench before one external plugin lifecycle is proven.

The rule for promoting a deferred item is concrete: it must be required by a
failed acceptance scenario, a measured performance bill, a security finding, or
two independent plugin use cases. General architectural elegance is not enough.

## 7. Change-control rule

Every implementation task must state:

1. which J01-J08 stage or Workstream it advances;
2. which shared contract, schema, or invariant it changes;
3. the smallest implementation boundary;
4. the acceptance evidence required;
5. what is explicitly not being built;
6. why the work cannot wait until a later Workstream.

If a task does not close a current journey gate, remove a demonstrated release
blocker, or satisfy a measured cross-plugin need, it belongs in the backlog.

## 8. Immediate next step

Do not implement the full multi-window architecture yet. First review and accept
this plan, then execute Workstream B as the minimum lifecycle correction and
Workstream C as the Spec golden journey. Reassess the remaining design options
only after those acceptance results exist.
