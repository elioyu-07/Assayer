# Assayer Audit Plugin Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.2.0 |
| Date | 2026-09-14 |
| Status | Simple author and formal-report contracts frozen; migration in progress |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1.2 |

## 1. Contract surface

An ordinary audit plugin is an independently inspectable executable domain
policy. It contributes Checks, deterministic domain observations, semantic
guidance, and business examples. It does not implement the Assayer lifecycle.

The ordinary contract has two forms:

- a **Policy Pack** containing declarations and examples only; or
- a **Simple SDK plugin** adding deterministic `scan` logic over frozen typed
  source objects.

The existing WorkItem, InvestigationPacket, Evidence, registration, Agent
contract, mapper, committer, and summary interfaces are the **Advanced SPI**.
They are not ordinary plugin requirements and must not appear in the default
tutorial or scaffold.

## 2. Author-maintained declarations

An ordinary plugin declares only:

- stable plugin ID, display metadata, one business version, and input kind;
- stable Check, rule, and dimension IDs;
- rule meaning, applicability, default severity, and remediation guidance;
- unknown, escalation, and not-applicable semantics;
- semantic-review instructions and domain invariants; and
- business cases with expected domain outcomes.

The SDK compiler generates manifests, registrations, entry points, input and
review Schemas, semantic digests, compatibility metadata, safe execution
profiles, release descriptors, and platform lifecycle acceptance cases.
Generated projections are immutable distribution artifacts, not parallel
author inputs.

An author declares one plugin business version. Check, protocol, SDK,
domain-contract, package, and compatibility identities remain distinct in the
generated artifact and ledger, but the compiler derives them deterministically.

## 3. Policy Pack

A Policy Pack MUST be capable of expressing a useful audit without Python. It
contains plugin metadata, Check declarations, semantic-review instructions, and
business examples. Declarative recognizers MAY create Candidates, Facts,
Relations, Supports, or Unknowns.

A Policy Pack cannot request arbitrary code execution, source access, a
transport tool, an external effect, or a custom result protocol. If its domain
requires deterministic logic beyond the declaration language, it moves to the
Simple SDK rather than embedding an escape command.

## 4. Simple SDK

The Simple SDK exposes at most `policy_plugin`, `Document`, `Candidate`, `Fact`,
`Relation`, `Support`, `Unknown`, and `invariant`.

A normal Simple plugin implements:

```text
scan(document) -> Candidate | Fact | Relation | Unknown stream
```

A cross-document plugin MAY additionally implement:

```text
relations(documents) -> Candidate | Relation | Unknown stream
```

The plugin receives only frozen typed source abstractions. It MUST NOT receive
or construct a Run ID, WorkItem, EvidenceRecord, Finding or receipt identity,
digest, provider envelope, platform context, transport client, mutable source,
or persistence handle.

Simple code MUST be deterministic and side-effect free. It cannot reopen a
path, access a network, read credentials, use clock or randomness as domain
input, spawn a process, write an external system, or import the Host or
Advanced SPI. Source support is obtained only through the typed snapshot API.

A Candidate is an unverified domain observation. It never becomes a formal
Finding until semantic or deterministic review has passed Host validation and
commit gates.

## 5. Source and support

The capability provider acquires, decodes, freezes, indexes, and navigates the
source. A provider may expose `discover_sources(scope, context)` to return SDK
`ProviderSourceSnapshot` values; the Host binds those snapshots to the Run and
creates WorkItems. The plugin interprets only the resulting frozen snapshot.

Support returned by a plugin is a typed reference created by the source API.
The Host resolves it to canonical Evidence and owns its identity and lineage.
Plugins cannot manufacture, copy, or rewrite canonical source metadata.

An absence Support is valid only over a provider-confirmed closed scope and
records the normalized query, inspected range, frozen source state, and
provider algorithm version. Truncated, unavailable, open, or stale scope yields
Unknown and cannot support a pass or confirmed issue.

## 6. Common review contract

All ordinary plugins use the platform common review model:

- dimension verdict;
- candidate disposition;
- finding;
- relationship verdict;
- applicability;
- confidence;
- Evidence support; and
- escalation or unknown.

Plugins declare dimension meaning and domain invariants. They do not publish a
complete DomainResult Schema, validate raw Agent JSON, map results into platform
Decisions, build an Evidence Graph, issue receipts, or assemble terminal
summaries.

A plugin MAY contribute a compiler-generated, typed, namespaced domain
extension. It cannot replace common review fields, alter conclusion validity,
hide coverage, or create a second final result authority.

## 7. Incremental review and coverage

The Host partitions semantic work into bounded ReviewBatches. Each batch names
only current Candidates, dimensions, relationships, and available Supports. The
Agent returns only the corresponding common review values.

The Host validates membership and Evidence, atomically persists every accepted
batch, and updates a durable CoverageLedger. Candidate exactly-once processing
and required-dimension closure are ledger-wide invariants. No individual Agent
submission must contain or remember the complete WorkItem.

Paging, batch size, ordering, safe parallelism, caching, failure splitting,
retry, correction, resume, replay, and terminal completeness are Host concerns.
Ordinary plugins neither declare nor implement them. The Host may use only
optimizations that preserve the frozen domain declaration, per-item Evidence,
and observable coverage.

## 8. Constraint authority

Each field or invariant has one executable declaration. The compiler derives
the Agent rule, Schema, validation path, and conformance cases from it.

A runtime rejection of data accepted by the generated contract is a compiler,
platform, or Advanced SPI implementation defect. It is not an Agent correction
opportunity. An ordinary plugin cannot add a handwritten validator that creates
a hidden second contract.

Algorithmic domain recognition remains plugin-owned. It must return typed
domain objects or Unknown rather than validating or mutating platform state.

## 9. Decision, persistence, and results

The Host alone maps accepted common review values to Decisions, creates Finding
and receipt identities, commits the ledger, and publishes the canonical result.
Read-only audits always use the default Host committer.

External writes require the Advanced SPI, explicit capability authorization,
effect-specific recovery, and user confirmation. They cannot be introduced by
a Simple plugin declaration.

Reports and paged result views are deterministic projections of the canonical
result and immutable ledger. A plugin MAY supply a presentation template or
domain labels, but no summarize or finalize lifecycle hook.

## 10. Formal report contract

The Host publishes one formal Markdown report for every terminal Run, following
the [Formal Audit Report Format](audit-report-format-v1.md). It contains the
fixed sections Audit Conclusion, Issue Details, Pending Matters when present,
Coverage, and Audit Information. Its issue table uses the exact columns
`严重程度 | 问题位置 | 问题说明 | 判定依据 | 整改要求`.

Plugins provide only typed domain content. They do not calculate source
locations, select or quote canonical Evidence, group or sort rows, describe
coverage, paginate report text, or publish a second report. Candidate,
Dimension, Relationship, Finding, and Evidence projections that refer to one
root cause are represented once at problem level. Dimension verdicts remain
coverage facts unless no problem-level record represents the violation.

The exact report is returned as terminal `auditReport` and durably stored as
`<runId>.audit-report.md`. Agent and product adapters reconstruct paged report
text when necessary and present it without rewriting its recorded contents.

## 11. Errors and recovery

Shape, stale-task, Evidence, provider, plugin, platform, and transport failures
have platform-owned classifications. Rejected input creates no durable review,
Decision, receipt, or revision change. Agent-owned correctable input receives a
bounded correction opportunity; contract defects receive none.

Unknown, missing, stale, ambiguous, contaminated, or insufficient Evidence
cannot be converted into a positive or negative conclusion. Resume and replay
continue from the last accepted Host boundary without plugin-authored IDs or
cursors.

## 12. Build and release

The ordinary author invokes one deterministic verification action:

```text
assayer plugin verify
```

The action compiles declarations, builds an isolated wheel, validates the exact
wheel, installs that wheel into an isolated environment, and derives lifecycle,
resume, replay, correction, pagination, result, and artifact checks from the
business cases.

Installation and publication consume only the verified wheel and its digest.
Local repositories are never copied into the installation store. A generated
plugin receives no exemption from inspection, safety, conformance, or exact
artifact verification.

## 13. Advanced SPI

The Advanced SPI is reserved for:

- a custom capability provider;
- an authorized external write/effect;
- a non-standard audit lifecycle; or
- result semantics that provably cannot map to the common review model.

It is explicitly imported from `assayer_plugin_sdk.advanced`. Its packages must
declare and test their public contracts and compatibility. Missing convenience,
performance, reporting, paging, caching, or packaging helpers are not valid
reasons to use it.

Existing low-level plugins remain supported only through a versioned migration
adapter. New ordinary plugins cannot claim conformance by implementing the old
SPI directly.

## 14. Conformance

An ordinary plugin is conformant only when:

- all author source belongs to the domain-only list in §2;
- imports stay within the Simple SDK surface;
- generated contracts are deterministic and contain no undeclared capability;
- source support resolves to the frozen Run snapshot;
- common review and coverage gates close without plugin lifecycle hooks;
- large-input tasks remain bounded and incrementally durable;
- the formal report is Host-generated, non-duplicative, losslessly pageable,
  and identical to its durable artifact;
- the exact installed wheel passes generated platform journeys; and
- the canonical result validates with complete traceability.

The detailed target architecture and migration order are defined by
[Simple Plugin Authoring Architecture](simple-plugin-authoring-design.md).
