# Assayer Platform Project Plan

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
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
- Freeze the current Alpha scope: anonymous URL audits; no login/SSO, source
  attribution proof, automatic screenshot sanitization, or resume checkpoint.
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
- Close J06 recovery and reuse with attributable failures, a new independent
  Run on retry, and no reconfiguration.
- Close J07 in a clean CLI configuration outside the repository, including a
  success and a failure-recovery scenario and three consecutive independent
  runs without stale-state reuse.
- Close J08 with version identification, upgrade, rollback, uninstall, cleanup,
  and reproducible feedback diagnostics.
- Preserve the existing structured performance bill and separate Agent waiting
  from Host, provider, browser, and transport time.

**Exit gate**

- The existing frontend vertical product satisfies the current J04-J08
  Definition of Done from a new user's point of view.
- A component test, deterministic harness, or one successful run cannot replace
  the clean CLI evidence.
- Any behavior promoted into the platform contract below has already survived
  a real user journey.

### Stage 2 — Freeze the platform constitution (M2)

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

### Stage 3 — Make the contracts enforceable (M3)

**Goal:** turn the constitution into an install and release gate.

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

**Exit gate**

- The same suite applies to built-in and external plugins.
- A plugin cannot publish a formal result when a mandatory conformance check
  fails.
- Conformance output identifies the failed invariant and the next action.

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
- Browser capability provider isolated from frontend rule semantics.
- At least one additional provider boundary (file/repository/API/database)
  where a real plugin needs it.
- Two independently packaged plugins using the same provider or contract
  surface.

**Exit gate**

- Spec and frontend plugins install and run independently.
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

### Security and privacy

Credentials, cookies, authorization values, hidden reasoning, raw page bodies,
and unsanitized sensitive screenshots must not enter ordinary Agent context,
logs, or reports. Screenshot sanitization remains a deferred security phase;
controlled trials must state when sanitization was not performed.

### Compatibility

Historical frontend MCP names remain compatibility aliases. New plugins use
generic lifecycle names and platform schemas. Historical Runs remain readable
and are never reinterpreted by a newer plugin or rule version.

## 6. Current priority and explicit deferrals

### Next work, in order

1. Reconcile the current J04-J08 status and close it with fresh CLI evidence.
2. Review and approve the constitution and three v1 contracts.
3. Convert the contracts into conformance and install gates.
4. Externalize the Spec plugin and complete its lifecycle.
5. Build the Agent-first plugin workbench.

### Deferred

- New FUA cases and broad crawler/deep-recursion expansion;
- Plugin marketplace UI and distributed execution;
- Automatic screenshot redaction;
- Source/version attribution proof;
- Per-plugin micro-optimizations;
- Advanced parallelism and caching without measured evidence.

## 7. Change-control rule

Every proposed task must name the stage it advances, the shared contract it
changes (if any), its acceptance evidence, and the reason it cannot wait. If it
does not advance a current stage gate or remove a release blocker, it belongs
in the backlog rather than the active sprint.
