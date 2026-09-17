# Assayer Platform Constitution v1

| Metadata | Value |
|---|---|
| Document version | 1.3.0 |
| Date | 2026-09-14 |
| Status | Superseded by Platform Constitution v2; retained as historical migration reference |
| Owner | Assayer maintainers |
| Applies to | Platform kernel, plugins, capability providers, Agent adapters, and report adapters |

> **Superseded.** The v2 Constitution is the active authority. This v1 record
> is retained only to explain historical decisions and does not authorize an
> implementation exception.

## 1. Purpose and authority

This Constitution is the highest technical authority for the domain-neutral
Assayer platform. It freezes the laws that every plugin and capability provider
must obey. The product contract still owns user promise and Alpha scope; the
domain rule contract owns the meaning of an individual check. When a lower
document conflicts with this Constitution, the lower document must be updated
before implementation continues.

The Constitution is intentionally small. It defines trust boundaries, result
integrity, lifecycle, compatibility, and release laws. It does not define how
a domain discovers facts or what a domain considers compliant.

### Normative vocabulary boundary

Normative platform documents MUST use domain-neutral concepts only. A concrete
plugin name, rule family, Check ID, input filename, or domain workflow MUST NOT
become a platform concept, exception, or acceptance gate. Such names belong in
the owning plugin package, a plugin-specific document, or a historical record
that is explicitly marked non-normative. A generic example may be used only
when it is clearly illustrative and cannot be read as a built-in capability.

## 2. Ownership boundaries

| Layer | Owns | Must not own |
|---|---|---|
| Platform kernel | Run identity and lifecycle, authorization, source binding, execution planning, WorkItem and result identity, Evidence lineage and graphs, incremental review state, coverage, validation, common decisions, default commit and persistence, recovery, observability, budgets, result projection, and publication | Domain correctness or a rule-specific branch |
| Audit policy/plugin | Audit subject and input kind, stable rule and dimension identity, applicability and domain meaning, deterministic candidate/fact/relationship recognition, default severity and remediation guidance, semantic-review instructions, domain invariants, business examples, and one business version | Source acquisition, platform identity or digests, Agent/result Schemas, execution planning, lifecycle hooks, Evidence graphs, coverage state, formal Decision mapping, default commit, result pagination, or transport and release mechanics |
| Capability provider | Controlled access to an approved source (browser, file, repository, API, database, or log), immutable source snapshots and anchors, capability metadata, limits, source facts, and explicit absence proofs over a closed scope | Compliance decisions, rule changes, arbitrary writes, or widening user authorization |
| Agent/Skill adapter | Natural-language interaction, semantic investigation of the current bounded review batch, domain verdicts, and explanations | Inventing facts, accumulating platform coverage, changing protocol fields, bypassing safety, or publishing an unvalidated result |
| Report adapter | Deterministic views derived from the canonical result/ledger | Mutating conclusions, hiding invalidation, or becoming a second source of truth |

The user supplies intent and business input. The platform supplies protocol
versions, Run IDs, output locations, rule snapshots, and internal defaults.

### Platform change isolation

Platform implementation and transport changes MUST remain transparent to
registered plugins. An optimization of serialization, paging, MCP framing,
recovery, persistence, observability, or internal orchestration MUST NOT
require a plugin source, manifest, schema, or domain-result change. The Host
MUST absorb such changes behind the existing public contract.

A plugin migration is justified only when a versioned public platform or
domain contract changes, or when the plugin voluntarily adopts a new optional
capability. In that case the platform MUST publish the compatibility boundary,
retain an adapter for the supported prior contract when practical, and reject
or migrate explicitly rather than silently coupling every plugin to an
internal implementation change. Platform concerns MUST NOT be relocated into
plugin code merely because the platform currently lacks a generic adapter.

### Plugin authoring boundary

An ordinary audit plugin is an executable domain policy, not a miniature
Assayer platform. Its author-maintained source MUST be limited to:

1. plugin identity, one business version, and a supported platform input kind;
2. stable Check, rule, and dimension IDs with their domain meaning and
   applicability;
3. deterministic candidate, fact, and relationship recognition where a
   declaration alone is insufficient;
4. default severity, remediation guidance, and the meaning of unknown or not
   applicable outcomes;
5. semantic-review instructions and domain invariants declared once; and
6. business examples with expected domain outcomes.

A pure policy pack MUST be expressible without Python. When deterministic code
is required, the Simple SDK MUST give it frozen, typed source abstractions and
allow it to return only typed domain facts, candidates, relationships, support,
or unknowns. The ordinary author surface MUST NOT expose `WorkItem`,
`InvestigationPacket`, `EvidenceRecord`, Evidence or Finding identities,
`DomainResultContract`, `PluginRegistration`, `CommitReceipt`, provider
request/response envelopes, or lifecycle hooks.

The SDK compiler and Host MUST derive or own all mechanical artifacts and
behavior, including manifests, compatibility declarations, scope and Agent
Schemas, semantic digests, registrations, entry points, release descriptors,
execution profiles, task batching, Evidence lineage and graphs, result
validation and mapping, default commit, summaries, pagination, recovery,
telemetry, packaging, and release acceptance. Generated artifacts may exist in
a distribution, but they MUST NOT become independently maintained sources of
truth in an ordinary plugin repository.

The platform MUST provide one generic, incrementally committable audit-review
model covering dimension verdicts, candidate dispositions, findings,
applicability, confidence, Evidence support, relationship verdicts, escalation,
and unknown. A plugin may add a typed domain extension, but it MUST NOT replace
the common model or define an equivalent complete result protocol. The
canonical result and immutable ledger remain the only final result authorities.

Plugin authoring has three deliberately separate levels:

- **Policy Pack** is the default and contains declarative rules, semantic
  instructions, and business examples, with no Python requirement.
- **Simple SDK** adds deterministic domain scanning over frozen typed inputs.
- **Advanced SPI** is reserved for custom capability providers, authorized
  external effects, non-standard audit workflows, or result semantics that
  cannot be represented by the common review model.

Performance tuning, batching, caching, reporting, installation, transport, or
pagination are platform responsibilities and MUST NOT be accepted as reasons
to move an ordinary plugin onto the Advanced SPI. Advanced SPI contracts MUST
remain in an explicitly named advanced namespace and outside the ordinary
plugin tutorial and scaffold.

The normative implementation design for this boundary is
[Simple Plugin Authoring Architecture](simple-plugin-authoring-design.md).

## 3. Non-negotiable laws

1. **Fail closed.** Unknown, missing, stale, ambiguous, contaminated, or
   unverifiable evidence cannot produce a pass or a valid issue.
2. **Host is the trust boundary.** Only the Host may authorize capabilities,
   execute provider operations, bind identities, persist Evidence, and commit a
   formal Decision. The Agent may propose but never attest to a fact.
3. **One source of truth.** The immutable platform ledger is authoritative;
   summaries, JSON views, Markdown, and other artifacts are read-only
   derivatives.
4. **Evidence closure.** Every formal outcome references the current Run,
   WorkItem, Check version, Investigation Case, and valid Evidence. Evidence
   cannot cross identities, checks, or incompatible source states.
5. **Coverage is factual.** A plan, batch completion, candidate, or runtime
   success is not coverage. A pass requires every required dimension to have a
   current resolved Finding.
6. **Recovery precedes publication.** A Case with uncertain or failed recovery
   cannot support a formal Decision. Environmental contamination invalidates
   affected conclusions.
7. **Safety is deny-by-default.** A capability or action not explicitly
   authorized by the platform, provider, plugin, and user scope is rejected.
8. **Idempotency is mandatory.** A retry with the same identity and request
   digest reuses the known result. A changed request or unknown result fails
   closed and requires explicit resolution.
9. **Domain neutrality.** Generic contracts use WorkItem, Check, Evidence,
   Finding, Decision, and Capability terms. Browser concepts such as PageState,
   DOM, tab, and screenshot remain adapter or plugin details.
10. **Optimization preserves proof.** Batching, caching, parallelism, and
    compression are allowed only when the plugin declares them safe and the
    ledger retains per-item evidence and traceability.
11. **No silent degradation.** Missing Agent, provider, or transport runtime
    capabilities are reported as unavailable or failed; deterministic smoke
    cannot be presented as a production audit.
12. **Historical immutability.** A completed Run is interpreted with the
    frozen plugin, Check, capability, protocol, and algorithm versions recorded
    at its start.
13. **Plugin change isolation.** A platform-only implementation or transport
    optimization does not require plugin changes. Any required plugin change
    must be justified by a versioned public-contract change or an explicitly
    adopted optional capability, with compatibility and migration behavior
    documented before implementation.
14. **Authoring surface is domain-only.** Ordinary plugin authors maintain
    domain policy and examples, never platform envelopes, identities, digests,
    Schemas, lifecycle drivers, compatibility matrices, or packaging metadata.
15. **Constraints are declared once.** A typed field or invariant has one
    executable declaration from which the SDK derives Schema, Agent guidance,
    validation errors, and conformance tests. Handwritten equivalent validators
    or semantic-rule copies are non-conformant.
16. **Sources cross one frozen boundary.** A source provider reads and freezes
    the source once. Ordinary plugins receive immutable typed snapshots and
    anchors and MUST NOT reopen paths or reconstruct source identity. An absence
    claim is valid only when it records a closed scope, normalized query, frozen
    source state, and provider algorithm version.
17. **Semantic work is bounded and incremental.** Agent input and output are
    partitioned into Host-planned review batches with hard size limits
    independent of total source size. The Host persists each accepted batch and
    computes exactly-once coverage across the ledger; no Agent submission is
    required to restate the complete WorkItem or remember prior batches.
    The frozen coverage plan is persisted once and later transitions are
    append-only and digest-chained; steady-state persistence and transition
    validation MUST be proportional to the current delta, not the total Run.
18. **Advanced SPI is exceptional.** Low-level platform contracts remain
    available only through an explicit advanced boundary. Convenience,
    performance, or missing Host helpers do not justify exposing that boundary
    to an ordinary plugin.
19. **The formal report is unified and non-duplicative.** The Host derives one
    formal user report from the terminal ledger. One root cause appears once;
    Candidate, Dimension, Relationship, Finding, and Evidence projections MUST
    NOT create parallel descriptions of the same issue. The report uses the
    fixed structure in the
    [Formal Audit Report Format](audit-report-format-v1.md). Plugins supply
    domain content but do not define a parallel report protocol, and Agent or
    product adapters present the Host report without rewriting its conclusion,
    ordering, rows, or coverage statement.
20. **Verification follows risk, not volume.** A test exists only to prove a
    constitutional invariant, a classified failure boundary, a compatibility
    promise, or an observable user journey. Test count, line coverage, and
    repeated examples are not quality claims.

## 4. Lifecycle law

```text
registered -> validated -> enabled -> selected -> running -> terminal
```

The Host freezes the selected manifest, Check, capability profile, scope
identity, source snapshots, generated contract, and algorithm versions at Run
start. It plans bounded semantic review batches, persists each accepted batch,
and derives completion from the durable coverage ledger rather than from one
complete Agent payload. A terminal Run is one of `completed`, `partial`, or
`failed`. A failed Run has no valid formal conclusions; a partial Run retains
only conclusions not invalidated by its unfinished or contaminated scope.

A platform Run has no fixed wall-clock termination deadline. Long-running
audits may continue for hours while they make observable progress. Timeouts
are local controls for one provider or Host operation; they produce a classified
failure and recovery path, not an automatic termination of the whole Run.

## 5. Version and compatibility law

All platform, plugin, Check, capability, protocol, identity, evidence, and
result contracts use semantic versions.

- A **major** change alters lifecycle, safety, evidence, decision states,
  persistence meaning, or another invariant. It requires a new major API and a
  migration or explicit incompatibility error.
- A **minor** change adds backward-compatible optional fields, capabilities, or
  operations. Older consumers must continue to read the previous subset.
- A **patch** change clarifies wording or fixes an implementation defect without
  changing accepted data or behavior.

The Host rejects a plugin that requires an unsupported platform major or newer
minor API before discovery. An ordinary plugin author declares only the plugin
business version. The SDK compiler deterministically stamps the distinct Check,
protocol, SDK, domain-contract, package, and compatibility identities needed by
the Host; those identities remain auditable but are not parallel author inputs.
Advanced SPI packages may manage independent public contract versions when the
advanced boundary requires them. Rule semantics are never silently
reinterpreted for historical Runs.

## 6. Release law

An artifact may be published only when the canonical result validates, all
mandatory references close, and the plugin/provider conformance gates pass.
M2 freezes the contracts; M3 adds the machine-enforced install and release
gates. A plugin generated through Agent interaction is still an ordinary,
inspectable package and receives no trust exemption.

The ordinary author workflow MUST expose one deterministic verification and
release action. That action compiles the author sources, builds an isolated
wheel, validates the exact wheel, installs and exercises that same artifact,
derives lifecycle, resume, replay, correction, pagination, and publication
tests from the supplied business examples, and emits a reproducible release
descriptor. A local source repository MUST NOT be copied into the installation
store; `.git`, virtual environments, tests, build output, and other undeclared
source-tree content cannot become installed plugin material.

## 7. Verification law

Each behavior has one authoritative test at the lowest layer that owns it and,
where externally observable, no more than one representative journey per
public interface. Repeating the same positive or negative behavior through
SDK, Host, CLI, MCP, Skill, installation, and release layers is permitted only
when each test proves a distinct boundary translation or trust decision.

Tests MUST exercise public behavior. Direct access to private state is reserved
for failure injection, crash recovery, or persistence corruption that cannot be
created through a public operation, and the test must make that reason clear.
Repository contracts are inspected structurally as typed values, JSON, TOML,
YAML, or syntax trees; tests MUST NOT protect implementation wording, source
layout, helper names, or documentation prose through substring matching.

Generated Schema, Agent guidance, validation errors, and acceptance cases are
tested from their single executable declaration. A test fixture MUST NOT become
an independently maintained copy of a generated contract or validator.

The fast profile is deterministic and dependency-light. The full profile runs
the real optional MCP, packaging, isolated-installation, and Chromium boundaries
and rejects every skip. Heavyweight processes may be reused, but each Run,
browser context, installation store, output directory, and ledger remains
isolated. A deleted or superseded production surface loses its tests and
Schemas in the same change; tests cannot be the sole consumers keeping an old
architecture alive.
