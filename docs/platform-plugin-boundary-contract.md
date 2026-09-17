# Platform--Plugin Boundary Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.4.0 |
| Date | 2026-09-13 |
| Status | Superseded by Platform Constitution v2; retained as historical migration reference |
| Authority | Derived from Platform Constitution v1.1 and Audit Plugin Contract v1.1 |
| Scope | Ownership, dependency direction, Simple SDK, Advanced SPI, and migration gates |

> **Superseded.** Advanced SPI and parallel ordinary-plugin paths described in
> this historical record are no longer permitted. Platform Constitution v2 is
> the active boundary.

## 1. Decision

Assayer has one domain-neutral platform and independently packaged executable
domain policies. The platform and capability providers close every source,
identity, Evidence, lifecycle, incremental review, persistence, result, and
release concern. An ordinary plugin contributes only domain declarations,
deterministic observations, semantic guidance, invariants, and examples.

The public authoring boundary is `assayer_plugin_sdk.simple`. Existing
low-level contracts move behind `assayer_plugin_sdk.advanced` and a versioned
migration adapter. Package independence alone does not make a low-level SPI a
Simple SDK.

## 2. Terms

| Term | Meaning |
|---|---|
| Policy Pack | A zero-Python executable domain policy |
| Simple plugin | Domain declarations plus optional deterministic scanning over frozen typed inputs |
| Advanced plugin | A package using low-level SPI for an exceptional provider, effect, workflow, or result need |
| SDK compiler | Deterministic compiler from author source to strict internal platform contracts |
| Source provider | Authorized source acquisition and immutable snapshot boundary |
| ReviewBatch | One bounded Host-planned semantic unit |
| CoverageLedger | Host-owned durable coverage across accepted ReviewBatches |
| Migration adapter | Versioned bridge from a supported Advanced SPI contract to current platform internals |

## 3. Ownership matrix

| Concern | Platform/Host | Source provider | Ordinary plugin | Agent/Skill |
|---|---:|---:|---:|---:|
| Run, WorkItem, Candidate, Evidence, Finding, receipt identity | Owns | Supplies source identity | Does not see | Does not author |
| Source access and freezing | Authorizes and binds | Owns | Reads typed snapshot | Requests through Host |
| Rules, applicability, and domain meaning | Must not own | Must not own | Owns | Applies |
| Candidate/fact/relationship recognition | Validates typed output | Supplies source facts | Owns domain logic | May interpret current batch |
| Evidence lineage and graph | Owns | Supplies anchors | Supplies typed Support only | References task-local Support |
| Review Schema, batching, and coverage | Owns | Does not own | Declares dimensions/invariants | Submits current batch only |
| Decision mapping, commit, and canonical result | Owns | Must not decide | Supplies optional typed extension | Proposes domain verdicts |
| Recovery, replay, and correction | Owns | Implements source recovery | Does not implement | Follows next step |
| Build, compatibility, and release mechanics | Owns/compiler | Owns provider release | Supplies business source/cases | Does not own |

## 4. Dependency graph

New ordinary code follows:

```text
Policy Pack ─────────────────────> SDK compiler declarations
Simple plugin ───────────────────> assayer_plugin_sdk.simple
Agent/Skill ─────────────────────> Host product tools and resources
Host/platform ───────────────────> generated internal contracts
Host/platform ───────────────────> capability provider interfaces
Capability provider ─────────────> authorized source implementation
Report adapter ──────────────────> canonical result and ledger read models
```

Advanced code follows:

```text
Advanced plugin/provider ────────> assayer_plugin_sdk.advanced
Migration adapter ───────────────> advanced contracts + platform internals
```

Forbidden dependencies include:

```text
Simple plugin ─X─> assayer_platform, assayer_host, advanced SPI, MCP, CLI
Simple plugin ─X─> provider request/response envelopes or concrete providers
Simple plugin ─X─> filesystem, network, subprocess, clock, random, persistence
Platform kernel ─X─> concrete domain semantics
Agent ──────────X─> provider implementation or ledger mutation
Report adapter ─X─> Run or Decision mutation
```

## 5. Simple public surface

The ordinary symbol surface is limited to:

- `policy_plugin`
- `Document`
- `Candidate`
- `Fact`
- `Relation`
- `Support`
- `Unknown`
- `invariant`

The symbol whitelist and import conformance gate MUST reject all other Simple
imports. Adding a symbol requires an ownership decision, version change,
documentation, and conformance fixture. Convenience access to an internal type
is not sufficient justification.

The Simple surface contains no `PluginRegistration`, manifest loader,
compatibility object, execution profile, platform context, provider envelope,
WorkItem, InvestigationPacket, EvidenceRecord, DomainResultContract,
DecisionProposal, CommitReceipt, mapper, committer, or lifecycle hook.

## 6. Generated internal surface

The SDK compiler creates the exact low-level declarations required by the Host.
The Host validates and freezes them before a Run. Generated declarations may
include manifests, scope and review Schemas, registrations, entry points,
semantic digests, execution defaults, compatibility identities, release
descriptors, and acceptance cases.

These objects are platform/compiler exchange data. Ordinary plugin code cannot
import, alter, or replace them. A generated declaration that disagrees with its
author source or another generated projection is a compiler defect and fails
before execution.

## 7. Source-provider boundary

The provider owns acquisition, decoding, source-state detection, snapshotting,
chunking, navigation, anchors, absence search, and source cache invalidation.
The Host owns authorization, provider selection, Run binding, Evidence identity,
and lineage.

When a provider exposes `discover_sources`, it returns SDK
`ProviderSourceSnapshot` values; the Host turns them into WorkItems and caches
the discovery result for that Run. A plugin may not implement a parallel source
discovery path for a Check that declares provider-owned discovery.

An ordinary plugin receives only a frozen typed view. It cannot receive a path
and reopen it. Provider capabilities and authorization scopes are inferred from
the declared input kind and user scope. A custom provider is an Advanced SPI
package and remains separate from domain policy.

## 8. Review and result boundary

Typed Candidates and Facts are projected by the Host into bounded
ReviewBatches. The Agent returns common domain verdicts for the current batch.
The Host validates batch membership and Support, persists the review, updates
CoverageLedger, maps Decisions, commits receipts, and projects the canonical
result.

The plugin does not know the Agent context budget, cursor, checkpoint, task or
contract digest, review history, result pagination, or completeness state.
Field-name filtering is not an acceptable trust boundary; only typed projection
may determine Agent-visible data.

The common review model is the only ordinary result language. A namespaced
typed extension may add domain data but cannot replace or reinterpret common
coverage, Findings, conclusion validity, or trace fields.

## 9. Advanced SPI admission

Advanced SPI admission requires a reviewed design showing at least one of:

- a new controlled source provider;
- an authorized write to an external system;
- a domain lifecycle that cannot be expressed as scan plus incremental review;
  or
- result semantics that cannot be represented as the common review model plus
  a typed extension.

The design must define permissions, failures, recovery, idempotency,
compatibility, conformance, and migration. Performance, batching, caching,
formatting, report generation, packaging, or a missing helper are rejected as
admission reasons and must be solved in the platform or Simple SDK.

## 10. Versioning and migration

The author declares one plugin business version. The compiler records all
distinct internal identities and exact compatibility. A compatible platform
implementation change cannot require author-source edits.

Migration order is:

1. add common review IR and bounded durable ReviewBatch support;
2. add frozen typed source snapshots and Support resolution;
3. add the compiler and Simple public surface;
4. migrate `minimal` with result equivalence;
5. migrate the next Advanced SPI plugin with result equivalence and
   mechanical-code reduction;
6. move low-level exports to the advanced namespace; and
7. deprecate the top-level Advanced SPI only after adapter and installed-wheel
   acceptance are green.

No migration may weaken Evidence closure, recovery, replay, coverage, canonical
result, or artifact verification.

## 11. Governance gates

| Gate | Required evidence |
|---|---|
| Author surface | Only domain source and Simple imports are maintained |
| Ownership | Platform mechanics do not appear in plugin code |
| Source | Supports resolve to one frozen provider snapshot |
| Constraint | Typed model/invariant generates all equivalent projections |
| Incrementality | Task bounds are independent of total source size |
| Result | Common review maps to canonical result without plugin hooks |
| Release | The exact generated wheel passes the derived lifecycle suite |
| Migration | Old and new paths produce equivalent domain conclusions |

## 12. Current implementation debt

The current implementation does not yet conform to this target boundary:

- low-level SDK types remain exposed at top level;
- plugins construct WorkItems, packets, Evidence, graphs, and DomainResult
  contracts;
- source plugins may reopen paths after provider collection;
- plugin validator, mapper, committer, summary, and acceptance hooks remain;
- semantic input paging does not provide incremental DomainResult submission;
- Agent projection still contains name-based filtering paths; and
- local development installation can materialize a source repository instead
  of the exact wheel.

These are migration facts, not permitted patterns for new plugins. Historical
implementation-slice documents remain evidence of earlier contracts and do not
override this boundary.

## 13. Acceptance statement

The boundary is complete when a zero-Python Policy Pack and a one-file Simple
plugin both compile, install, run incrementally over large inputs, resume,
replay, and publish a valid canonical result without authoring or importing any
platform lifecycle structure.
