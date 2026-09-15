# Assayer Plugin Development Standard v1

| Metadata | Value |
|---|---|
| Document version | 1.3.0 |
| Date | 2026-09-14 |
| Status | Simple authoring and formal-report standards adopted; migration in progress |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1.2, Audit Plugin Contract v1.2, and Platform--Plugin Boundary Contract v1.4 |
| Scope | Ordinary author source, compiler output, Host validation, incremental review, errors, and release conformance |

## 1. Purpose

This standard makes ordinary plugin development domain-only while retaining
strict platform contracts. It specifies what an author maintains, what the SDK
compiler generates, what the Host validates and persists, and what the exact
wheel must prove before installation or publication.

The keywords **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, and
**MAY** are normative as described by RFC 2119 and RFC 8174.

This is the target development standard. The current registration,
DomainResultContract, validator/mapper, and release-driver implementation is an
Advanced SPI migration path and does not satisfy the ordinary authoring exit
gate.

## 2. Ordinary project

The normal project contains:

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
plugin.py                 # optional
```

The names may be configured by the compiler, but their ownership may not.
Author-maintained content is limited to domain identity, one business version,
input kind, rule semantics, deterministic recognition, invariants, semantic
guidance, and business cases.

An ordinary repository MUST NOT require an author-maintained manifest, JSON
Schema, compatibility matrix, registration, Python entry point, execution
profile, semantic digest, release descriptor, acceptance driver, mapper,
committer, summary hook, or packaging RECORD.

Generated files MUST be placed in a clearly identified build directory or
wheel staging tree. They MUST NOT be edited or reviewed as independent sources
of domain truth.

## 3. Declaration requirements

`plugin.yaml` declares only:

- plugin ID, name, description, and one semantic business version;
- one platform-supported input kind;
- the Check declaration source;
- the semantic instruction source; and
- optional domain-extension and report-template declarations.

`checks.yaml` declares stable Check, rule, and dimension IDs; meaning;
applicability; required domain observations; default severity; remediation
guidance; and unknown or not-applicable behavior.

Declarations cannot request raw platform capabilities. The compiler maps the
input kind to the minimum provider capability and the Host intersects it with
platform and user authorization. A declaration cannot widen authority.

## 4. Deterministic code

Python is optional. When present, it imports only
`assayer_plugin_sdk.simple` and implements `scan(document)` plus optional
`relations(documents)`.

The compiler and conformance runner MUST reject direct imports or access to:

- `assayer_platform`, `assayer_host`, or `assayer_plugin_sdk.advanced`;
- filesystem paths, environment credentials, network or subprocess clients;
- clocks or random sources used in domain output;
- provider request/response envelopes;
- platform identities, digests, stores, ledgers, or lifecycle objects; and
- model, MCP, CLI, or product transport APIs.

The scanner returns only typed Candidate, Fact, Relation, Support, or Unknown
values. It cannot commit a Finding, declare coverage, or publish a result.

## 5. Frozen source contract

The Host selects and authorizes a provider. The provider acquires and freezes
the complete source state before ordinary plugin logic reads it. The snapshot
contains typed navigation and query operations but no reopenable source path.

Every Support resolves to the frozen snapshot. The Host verifies scope,
revision, anchor bounds, and provider algorithm identity before it persists
Evidence. Cross-Run, stale, fabricated, or out-of-scope Support fails without
mutation.

An absence Support MUST prove a closed search scope. If the provider cannot
prove completeness because of truncation, permissions, parse failure, or stale
state, the operation returns Unknown.

## 6. Constraint compilation

Typed fields and `invariant` declarations are the only structural and
relational contract authorities. For every ordinary plugin, the compiler MUST
derive the same semantics into:

1. input and common-review JSON Schemas;
2. Agent-facing result shape and rule guidance;
3. runtime validation with stable paths and rule IDs;
4. positive and negative conformance cases; and
5. immutable contract digest material.

No handwritten validator may reject a generated-contract-valid value for an
undeclared structural or relationship condition. Genuinely algorithmic domain
recognition remains ordinary plugin logic and returns typed output or Unknown.

Compiler output MUST be deterministic. The same normalized author source,
compiler version, and dependency lock produce byte-identical generated
contracts.

## 7. Agent boundary

The Agent sees only the current bounded ReviewBatch, typed domain meaning,
task-local Support handles, and the common review result shape. It does not see
or author Run, WorkItem, Evidence, Candidate, Finding, receipt, checkpoint,
contract digest, task digest, cursor, revision, coverage, recovery, replay, or
finalization identity.

A violated or conflicted dimension may include reviewer-origin Findings for
material problems that have no deterministic Candidate. Such a Finding contains
only title, concise message, severity, recommendation, current-task Support, and
affected dimension names. It is declared once on a primary dimension; neither
the Agent nor the plugin supplies a Finding identity or repeats it for every
affected check. Its Support may combine Evidence handles offered for any of its
affected dimensions in the same current batch. It MUST NOT cite a handle from
another batch or one not published in the current task.

The Agent returns only common review values for the current batch. The Host
performs, in order:

1. resolve the active immutable task context;
2. validate the generated common-review contract;
3. resolve Support against the frozen Evidence registry;
4. enforce batch membership and exactly-once disposition;
5. evaluate generated invariants;
6. atomically persist the review and acknowledgement; and
7. update CoverageLedger and plan the next step.

A rejected submission creates no review record, operation, Decision, receipt,
or revision change.

## 8. Bounded incremental review

Every semantic input and output has a Host-defined hard byte, item, and depth
limit independent of total source size. The Host splits Candidates, dimensions,
relationships, and Evidence retrieval into ReviewBatches. A plugin cannot opt
out of bounded execution or require one complete-WorkItem submission.

CoverageLedger records planned, accepted, superseded, blocked, and outstanding
domain atoms. Completion requires every applicable required dimension and every
review-required Candidate or Relation to have one current terminal disposition.
The Agent never accumulates prior batch payloads to prove completeness.

Identical accepted replay returns the original acknowledgement. A changed
submission for a committed batch follows append-only correction rules before a
formal Decision, and fails closed after Decision commit.

## 9. Decision and result

The Host maps complete common review state to the platform Decision model,
creates identities, builds Evidence lineage and graphs, applies the default
committer, and derives the canonical result and paged views.

A read-only ordinary plugin has no commit, summarize, or finalize interface. A
report template can change presentation only. A typed domain extension is
validated and namespaced and cannot change common outcomes, coverage,
conclusion validity, Evidence, failures, performance, or trace.

External effects require Advanced SPI admission and explicit user-authorized
capabilities, idempotency, recovery, and effect receipts.

## 10. Formal audit report

Every terminal Run publishes one Host-generated formal report conforming to
the [Formal Audit Report Format](audit-report-format-v1.md). The ordinary
plugin supplies only domain issue content: title, concise explanation, default
severity, remediation guidance, and Support. The Host supplies report
structure, source location, Evidence excerpt, ordering, de-duplication,
coverage, audit metadata, pagination, and artifact publication.

The fixed problem table is:

```text
严重程度 | 问题位置 | 问题说明 | 判定依据 | 整改要求
```

A confirmed Candidate is the ordinary problem-level record. Dimension
verdicts establish coverage and MUST NOT repeat a represented Candidate as a
second issue. A rejected independent Relationship may produce one problem
record. Merged or equivalent records produce one row. Confidence is not a
normal report column: insufficient confidence is represented as a pending item
rather than as a weak conclusive issue.

The terminal result exposes the exact report through `auditReport`, inline or
as losslessly paged text, and publishes `<runId>.audit-report.md`. Agent and
product adapters MUST present that report without independently summarizing,
renaming, reordering, expanding, or omitting its sections or rows.

## 11. Error policy

Every failure identifies its owner as Agent input, plugin domain logic, compiler
contract, provider, source target, Host/platform, transport, or environment.

- Agent-owned shape errors receive at most one explicit correction for the
  current durable boundary.
- Stale task state is rebound by the Host; the Agent is not asked to edit an ID
  or digest.
- Plugin, compiler, or platform contract defects receive no Agent correction.
- Provider and environment failures follow bounded platform recovery policy.
- Insufficient domain Evidence becomes Unknown or escalation, not a retry loop
  that invents a conclusion.

An exhausted correction closes affected scope as partial or failed according
to conclusion validity and does not manufacture a synthetic Finding.

## 12. Business cases

Authors supply business inputs and domain expectations, for example:

```yaml
input: weak-spec.md
expect:
  candidate_rules: [PERM-001]
  final: rework
```

Authors do not script lifecycle tools. The platform test generator applies the
same cases to construction, source freezing, batching, correction, resume,
replay, pagination, terminal publication, upgrade, rollback, and uninstall.

A case may assert domain observations and terminal meaning. It cannot bless a
platform protocol field or bypass a common conformance failure.

## 13. Verification and release

`assayer plugin verify` is the single ordinary developer gate. It MUST:

1. validate declarations and Simple imports;
2. compile all internal contracts;
3. prove generated projections have one source of truth;
4. build an isolated wheel;
5. inspect package contents before importing plugin code;
6. install the exact wheel in an isolated environment;
7. run generated business and lifecycle cases;
8. verify canonical result, ledger, Evidence, coverage, resume, replay,
   pagination, and performance invariants; and
9. emit the exact wheel digest and reproducible release descriptor.

Installation and publication accept only the verified wheel identity. Local
source installation first builds and verifies a wheel; it never recursively
copies the repository.

## 14. Advanced SPI standard

Advanced SPI packages use `assayer_plugin_sdk.advanced` and publish the
additional low-level contracts required by their admitted capability. They MUST
pass all common Host gates plus contract-specific permission, identity,
idempotency, recovery, compatibility, and release tests.

The Advanced SPI is not a fallback when the compiler or Host lacks a helper.
Such a gap is platform work unless the admission conditions in the boundary
contract are met.

## 15. Migration and acceptance

The existing SDK v2 DomainResult path is retained as an Advanced migration
adapter. It is not taught to new ordinary authors. Migration must preserve
domain conclusions and immutable historical Runs while moving mechanical code
into the compiler and Host.

The standard is implemented only when:

- Policy Pack works with zero Python;
- the minimal Simple plugin contains no platform mechanics and at most 50 lines
  of domain Python;
- no ordinary plugin maintains Schema, digest, registration, compatibility, or
  release-driver code;
- 10 MB and 100,000-line inputs use bounded incrementally durable tasks;
- one command verifies the exact installed artifact; and
- `ass-spec` removes at least 60 percent of platform-mechanical code while
  preserving accepted domain outcomes.

See [Simple Plugin Authoring Architecture](simple-plugin-authoring-design.md)
for the implementation slices and non-goals.
