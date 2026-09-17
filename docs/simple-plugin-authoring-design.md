# Simple Plugin Authoring Architecture

| Metadata | Value |
|---|---|
| Document version | 0.1.0 |
| Date | 2026-09-13 |
| Status | Superseded by Platform Constitution v2; retained as historical migration reference |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1.1 §2/§3.14-18 |
| Scope | Policy Pack, Simple SDK, SDK compiler, Host review planning, migration, and release workflow |

> **Superseded.** The current target is the zero-Python unified Plugin
> Contract in Platform Constitution v2. This design is historical migration
> context and cannot authorize a Python or Advanced SPI exception.

## 1. Problem

The current SDK fixes dependency direction but exposes the platform's low-level
runtime protocol to plugin authors. Even a small plugin must understand source
reading, identity and digest construction, WorkItems, InvestigationPackets,
Evidence, Agent result Schemas, validation and mapping hooks, registrations,
compatibility, packaging, and release lifecycle tests.

This is an Advanced SPI, not an ordinary authoring SDK. Generating the same
mechanical files with an Agent does not remove the contract burden; it only
moves repeated protocol learning into generation and repair.

## 2. Decision

Assayer will provide three separate authoring levels:

1. **Policy Pack** for declarative rules, semantic guidance, and examples. It
   requires no Python.
2. **Simple SDK** for deterministic domain scanning over frozen typed inputs.
3. **Advanced SPI** for custom providers, authorized external effects,
   non-standard workflows, or result semantics that cannot use the common
   review model.

The current low-level SDK contracts become the Advanced SPI. They remain
available through a compatibility adapter while existing plugins migrate, but
they are removed from the default tutorial, scaffold, and ordinary conformance
requirements.

The Simple SDK compiler translates author declarations into strict internal
platform contracts. Strictness is retained; its mechanical expression moves to
the compiler and Host.

## 3. Ordinary author source

The canonical ordinary-plugin source is:

```text
my-plugin/
├── plugin.yaml
├── checks.yaml
├── semantic-review.md
├── cases/
└── plugin.py                 # optional
```

Only these domain inputs are author-maintained:

- plugin ID, name, description, input kind, and one business version;
- stable Check, rule, and dimension IDs;
- applicability, rule meaning, default severity, and remediation guidance;
- deterministic candidate, fact, and relationship recognition;
- unknown, escalation, and not-applicable semantics;
- semantic-review guidance and domain invariants; and
- business examples with expected domain outcomes.

Generated manifests, Schemas, registrations, compatibility declarations,
entry points, descriptors, acceptance drivers, and checksums are build output.
They are not editable sources and do not enter source-control review as
independent declarations.

## 4. Simple SDK surface

The ordinary Python surface contains no more than these concepts:

- `policy_plugin`
- `Document`
- `Candidate`
- `Fact`
- `Relation`
- `Support`
- `Unknown`
- `invariant`

The normal deterministic extension has one operation:

```python
from assayer_plugin_sdk.simple import Candidate, Document, policy_plugin


@policy_plugin(
    id="example.policy",
    version="3.1.0",
    input="markdown",
    checks="checks.yaml",
    instructions="semantic-review.md",
)
class ExamplePolicy:
    def scan(self, document: Document):
        if document.contains("permission") and not document.contains_any(
            "denied", "unauthorized", "data scope",
        ):
            yield Candidate(
                rule="PERM-001",
                subject="permission behavior",
                message="Permission behavior lacks denial and data-scope rules.",
                support=document.absence(
                    ["denied", "unauthorized", "data scope"],
                    scope=document.full_scope,
                ),
                severity="P2",
                recommendation="Define roles, data scope, and denial responses.",
            )
```

A domain that compares documents may additionally implement a typed
`relations(documents)` operation. No ordinary plugin implements discovery,
inspection, Agent projection, validation, mapping, commit, summary, finalization,
retry, replay, or transport operations.

Simple code is deterministic and side-effect free. It receives no path,
credential, network client, platform context, clock, random source, provider
envelope, or mutable platform object. Evidence support is obtained only from
the frozen typed input API.

## 5. Source boundary

A source provider owns acquisition, decoding, freezing, source identity,
chunking, navigation, anchors, and cache invalidation. The Host binds the
provider snapshot to the Run. A Simple plugin receives a `DocumentSnapshot`
view and cannot reopen the original source.

`absence` is a proof-producing provider operation, not a string-search helper.
It binds the normalized query, closed search scope, frozen source digest,
provider and algorithm version, and inspected ranges. An open or truncated
scope produces `Unknown`, never negative Evidence.

## 6. Common incremental review model

The Host owns one typed review model:

- `DimensionVerdict`
- `CandidateDisposition`
- `Finding`
- `RelationshipVerdict`
- `Applicability`
- `Confidence`
- `EvidenceSupport`
- `Escalation`
- `Unknown`

Plugins define the dimensions and their meaning, but do not define a complete
Agent result protocol. A typed domain extension is optional and namespaced.
The common review model is the only input to platform Decision assembly and the
existing canonical result remains the only final projection.

A violated or conflicted `DimensionVerdict` may carry typed reviewer-origin
`Finding` values when semantic review discovers a material problem that was not
represented by a deterministic Candidate. Each Finding contains domain content
and task-local Support only; it has no Agent-authored identity. A Finding lists
all affected dimensions and is stated once on its primary dimension so the Host
can retain coverage without repeating one root cause in the formal report. Its
Support may combine the current batch's handles for all affected dimensions;
the primary dimension does not constrain the evidence set.

For large inputs the Host creates bounded `ReviewBatch` records. The Agent
submits only verdicts for the current batch. The Host validates Evidence
membership, persists the batch atomically, updates a durable `CoverageLedger`,
and plans the next batch. Exactly-once coverage is a ledger invariant across
batches, not a requirement to repeat every candidate in one submission.
The frozen atom/batch plan is written once; offers, verdicts, corrections,
blocks, and terminal state are appended to a digest-chained journal. Per-batch
persistence and replay MUST apply only the validated delta and MUST NOT rewrite
or globally revalidate the complete ledger after every transition.

Agent input and output limits are independent of total document size. Paging a
source or result does not require the Agent or plugin to author a cursor,
checkpoint, digest, coverage declaration, or finalization payload.

## 7. Single declaration of constraints

Field constraints come from typed models. Relationship constraints come from
`invariant` declarations. The compiler derives from those sources:

- executable JSON Schema;
- Agent-facing rule guidance;
- stable error locations and rule IDs;
- runtime validation; and
- positive and negative conformance cases.

A handwritten validator may implement a genuinely algorithmic domain check,
but it cannot duplicate or strengthen a declared shape or invariant in a hidden
second contract.

## 8. Compiler and generated contracts

The SDK compiler generates:

- manifest and Check projections;
- input/scope and common-review Schemas;
- semantic instruction digest;
- registration and entry point;
- protocol, SDK, Check, domain-contract, and compatibility identities;
- safe default execution profile;
- release descriptor; and
- lifecycle acceptance cases derived from business examples.

The compiler also emits a private typed review-plan adapter for Candidate and
Relationship values. That generated IR is validated against the frozen
Investigation Evidence before the Host creates internal `ReviewAtom` records;
it is not an author-facing lifecycle hook and cannot contain platform Run,
batch, atom, or submission identities.

Ordinary authors maintain only the plugin business version. Generated internal
versions remain independently recorded for audit and compatibility. The
compiler must be deterministic: identical sources and compiler identity produce
byte-identical generated contracts.

For a zero-Python Policy Pack, a Check may declare deterministic
`contains_all`, `contains_any`, and `absent_all` recognition predicates. These
compile to the same frozen `Document` and `Candidate` path as Simple SDK code;
they never read source files directly. Business cases declare expected
candidate rule IDs and one of `ready`, `rework`, `needs_review`, or
`not_applicable`. The compiler generates the installed acceptance driver and
the Host supplies lifecycle, resume, paging, and replay assertions.

## 9. Host-owned lifecycle

The Host owns WorkItem planning, all platform identities and digests, provider
authorization, Evidence records and lineage, candidate graph construction,
Agent projection and retrieval, batching and parallelism, cache behavior,
failure splitting, validation, Decision mapping, default commit and receipts,
summary projection, pagination, retry, correction, recovery, replay,
observability, and terminal publication.

Read-only audits use the default Host committer. An Advanced SPI effect is
required only for an explicitly authorized write to an external system.

## 10. Build, verification, and installation

The ordinary workflow is one command:

```text
assayer plugin verify
```

It validates author declarations, compiles generated contracts, builds an
isolated wheel, validates the exact wheel, installs that wheel into an isolated
environment, and runs generated lifecycle, resume, replay, correction,
pagination, result, and artifact checks. The same verified wheel is the only
input to installation or publication.

Local installation never copies a repository. Source directories, `.git`,
virtual environments, tests, build output, and undeclared files cannot enter the
plugin store.

## 11. Migration

Implementation proceeds in vertical slices:

1. freeze the Simple authoring surface and common review IR;
2. implement Host-owned ReviewBatch and CoverageLedger;
3. implement frozen DocumentSnapshot, anchors, and absence proof;
4. implement the compiler and Policy Pack loader;
5. migrate `minimal` and deliver `assayer plugin verify` plus wheel-only install;
6. migrate a representative Advanced SPI plugin and prove semantic-result
   equivalence; and
7. move current low-level exports into `assayer_plugin_sdk.advanced`, retain a
   versioned adapter, and deprecate their top-level exposure.

Current `PluginRegistration`, WorkItem/Packet/Evidence construction,
DomainResult contracts, validator/mapper hooks, and release acceptance drivers
are migration inputs, not the Simple SDK design.

## 12. Acceptance

The architecture is complete only when:

- a pure policy plugin requires no Python;
- a minimal deterministic plugin has one Python file and no more than 50 lines
  of domain code;
- ordinary plugins maintain no mechanical manifest, Schema, digest,
  compatibility matrix, registration, entry point, or acceptance driver;
- ordinary plugins expose no lifecycle hooks;
- one command builds and verifies the exact installable wheel;
- semantic task size remains bounded for a 10 MB or 100,000-line input;
- Agent verdicts are incrementally durable and never require complete-WorkItem
  resubmission;
- source mutation and absence-proof behavior fail closed;
- compatible platform upgrades require no ordinary plugin source change;
- the representative migrated plugin's platform-mechanical code falls by at
  least 60 percent; and
- generated Schema, Agent rules, runtime validation, and tests cannot drift.

## 13. Non-goals

This design does not move domain meaning into the Host, make deterministic
candidates authoritative findings, permit model-authored Evidence, remove
versioned internal contracts, or eliminate the Advanced SPI. It removes
platform mechanics from the ordinary author model while preserving the
platform's proof and safety guarantees.
