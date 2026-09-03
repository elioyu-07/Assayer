# Assayer Platform Contract

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-02 |
| Status | Superseded authority; see [Platform Constitution v1](platform-constitution-v1.md) and the v1 contracts |
| Owner | Assayer Maintainers |

## Implementation Note

This document is retained as the detailed design reference. The normative M2
authority is split into the [Platform Constitution v1](platform-constitution-v1.md),
[Audit Plugin Contract v1](plugin-contract-v1.md), [Capability Provider Contract
v1](capability-provider-contract-v1.md), and [Canonical Audit Result Contract
v1](canonical-result-contract-v1.md). Those documents resolve ownership and
versioning; this reference must not introduce a conflicting rule.

The in-process reference kernel, manifest loader, configuration-quality plugin, and frontend compatibility plugin are implemented under `src/assayer_platform`. The frontend plugin adapts the existing product facade for discovery and investigation, and its Check/Finding gates are applied on the product-facing decision path. The product MCP transport now starts an interactive platform Run by default and persists its lifecycle checkpoints in the Host-owned SQLite database. The browser-shaped ledger and public MCP tools remain compatibility surfaces while the generic ledger supplies the cross-domain lifecycle record.

## 1. Purpose

This document defines the smallest domain-neutral contract for the Assayer platform. It is intentionally broader than web auditing. A plugin may inspect a browser page, an API, a repository, a configuration file, a database, a log stream, or another approved data source.

The platform contract standardizes lifecycle, evidence integrity, decisions, scheduling, recovery, observability, and user-facing progress. It does not standardize how a domain discovers facts or what a domain considers correct.

The current frontend audit is one plugin/runtime combination used to validate this contract. It is not the platform model.

## 2. Goals and Non-goals

### Goals

1. Add a new domain without changing the platform lifecycle or ledger invariants.
2. Allow each plugin to declare the evidence, ordering, batching, caching, and recovery constraints required for accurate results.
3. Move deterministic mechanical work from Agent turns into the Host while preserving Agent semantic authority.
4. Give every domain the same traceable run, evidence, decision, failure, and performance views.
5. Keep the Codex CLI journey simple: the user supplies intent and domain inputs; the platform owns internal protocol details.

### Non-goals for this contract version

- A universal ontology for every business or technical domain;
- A mandatory programming language or plugin SDK implementation;
- A distributed execution fabric;
- A plugin marketplace or remote plugin trust model;
- Automatic business conclusions made by the Host;
- Parallel execution of operations whose ordering or isolation is not proven safe.

## 3. Layering

```text
User intent and domain inputs
            |
       Agent / Skill
  semantic selection and decision
            |
   Platform Host Kernel
 lifecycle, scheduling, evidence, integrity, recovery, observability
            |
     Plugin + Runtime Adapter
 domain discovery and facts      browser / API / file / database / log access
```

The dependency direction is one-way:

```text
Agent -> Platform Kernel -> Plugin Contract -> Runtime Adapter
```

Plugins and adapters may not bypass kernel safety, evidence, persistence, or publication gates.

## 4. Platform Concepts

| Concept | Platform meaning | Domain-specific content allowed? |
|---|---|---:|
| `PlatformRun` | One bounded execution from initialization to terminal status | Input scope and plugin set |
| `WorkItem` | One logical subject that may be checked | Subject identity and domain metadata |
| `Check` | A versioned requirement applicable to a WorkItem kind | Rule semantics and dimensions |
| `InvestigationCase` | A bounded evidence-gathering unit | Ordered actions and preconditions |
| `Evidence` | An immutable, Host-verified fact tied to a run and WorkItem | Payload shape and collector kind |
| `Finding` | An Agent or evaluator status for one Check dimension | Dimension name and rationale |
| `Decision` | The final state for one WorkItem and Check | Allowed states and gates |
| `Operation` | One idempotent Host execution record | Runtime operation details |
| `Artifact` | A report, image, diff, trace, or other derived output | Rendering and presentation |
| `CapabilityProfile` | Available data sources and execution limits | Adapter capabilities |

Each Run has one `decision_authority`. The reference kernel and all registered
plugin committers use `platform`. A browser compatibility adapter may still
write the domain-specific Host Assessment, but that Assessment is a projection
identified from Platform receipt metadata (`hostAssessmentId`); it is not a
second decision authority. A single Run cannot mix authorities, which keeps a
receipt and its resulting projection attributable to one durable owner.

`PageState`, `DOM`, `Tab`, and `Screenshot` are valid frontend plugin details, not platform-level concepts. A different plugin may use `RepositorySnapshot`, `ApiResponse`, `FileVersion`, or `LogWindow` as its runtime-specific evidence payload.

## 5. Plugin Contract

A plugin declares a manifest and implements the domain operations below. The exact transport and programming language remain implementation details.

### 5.1 Manifest requirements

```json
{
  "pluginId": "example.config-quality",
  "version": "1.0.0",
  "platformApiVersion": "1.0.0",
  "domains": ["configuration-quality"],
  "subjectKinds": ["configuration_file"],
  "checks": [
    {
      "checkId": "CFG-001",
      "version": "1.0.0",
      "subjectKinds": ["configuration_file"],
      "dimensions": ["required_keys", "value_types"],
      "decisionStates": ["issue_found", "scanned_no_issue", "needs_review"],
      "requiredEvidenceKinds": ["structured"],
      "requiredCapabilities": ["structured_read"],
      "capabilityMissingOutcome": "needs_review",
      "invalidationSignals": ["source_digest"]
    }
  ],
  "executionProfile": {
    "discoverBatching": "allowed",
    "inspectBatching": "allowed",
    "decisionBatching": "allowed",
    "parallelism": "forbidden",
    "cacheReuse": "allowed",
    "checkpoint": "required"
  }
}
```

The manifest is declarative. It does not grant permission to read arbitrary data or execute unsafe actions.

Every check must declare:

- stable identity and semantic version;
- applicable subject kinds;
- required dimensions;
- allowed decision states and gates;
- required evidence kinds;
- capability prerequisites;
- execution constraints and invalidation conditions.

### 5.2 Domain operations

The platform invokes these conceptual operations:

```text
discover(scope, capabilities) -> WorkItemSet
inspect(workItems, check, capabilities) -> InvestigationPacketSet
restore(case) -> RecoveryResult
```

The Agent performs the semantic operation:

```text
decide(InvestigationPacketSet) -> DecisionProposalSet
```

The Host validates and commits the proposal:

```text
commit(DecisionProposalSet) -> DecisionSet
```

The kernel commit boundary returns a `CommitReceipt`. A memory receipt is only
useful for deterministic contract runs; production adapters must return a
durable receipt that identifies the persisted decision. A proposal is not
counted as committed until the receipt matches its WorkItem, Check, and result.

Commit receipt replay is idempotent. The kernel records a digest of the
validated proposal with each receipt and reuses the receipt when the same
Run/WorkItem/Check is retried. A changed proposal for an already committed
identity fails closed with `COMMIT_CONFLICT`. When a durable ledger store is
configured, receipts are hydrated before execution so this protection also
survives a kernel restart. The public frontend facade remains responsible for
its own atomic persistence; this generic replay guard prevents duplicate calls
at the platform boundary.

The current `discover_scope`, `investigate_object`, and `prepare_decision` calls are browser-facing implementations of this shape. They are not the final domain-neutral names.

### 5.3 Investigation packet

An `InvestigationPacket` is the only object the Agent needs for a semantic decision. It contains concise, structured facts and references to immutable evidence.

```json
{
  "workItem": {
    "id": "work-001",
    "kind": "configuration_file",
    "identity": "sha256:..."
  },
  "check": {
    "checkId": "CFG-001",
    "version": "1.0.0"
  },
  "dimensions": [
    {
      "name": "required_keys",
      "observations": ["The three required keys are present"],
      "evidenceRefs": ["evidence-001"],
      "candidateStatus": "satisfied"
    }
  ],
  "evidenceBundle": {
    "kinds": ["structured"],
    "complete": true
  },
  "recovery": {
    "status": "restored"
  }
}
```

`candidateStatus` is a Host or plugin-supported fact summary, not a committed decision. The Agent may confirm, reject, or mark it unresolved. A plugin may require the Agent to inspect visual or raw evidence even when the candidate summary is deterministic.

## 6. Accuracy and Integrity Invariants

Performance features are valid only when all of these remain true:

1. Every formal decision references one current Run, WorkItem, Check, Case, and valid Evidence set.
2. A missing required dimension cannot become `scanned_no_issue` because a batch completed.
3. Evidence is never merged across WorkItems, Checks, or incompatible runtime states.
4. Cache reuse requires a plugin-declared cache key and a matching source identity, WorkItem identity, Check version, capability profile, and relevant state digest.
5. A plugin may forbid batching, caching, parallelism, or compression independently for each operation.
6. A partial batch is split into smaller work units when safe; otherwise the affected item becomes blocked or `needs_review`.
7. Recovery uncertainty invalidates the affected decision path and cannot be hidden by a fast path.
8. The Host validates references, safety, lifecycle, and gates but never invents a domain conclusion.
9. Unknown plugin capabilities fail closed and use a slower compatible path when one exists.
10. All optimization choices and fallbacks appear in the run diagnostics and performance bill.

## 7. Performance Contract

The platform may optimize at four boundaries:

| Boundary | Safe optimization | Plugin escape hatch |
|---|---|---|
| Discovery | Batch and deduplicate logical WorkItems | `discoverBatching=forbidden` |
| Investigation | Collect several independent packets in one Host call; learn a smaller successful size after an allowed failed-batch split | `inspectBatching=forbidden`, `failureSplitting=forbidden`, or non-independent ordering |
| Decision | Submit independent proposals together | `decisionBatching=forbidden` |
| Evidence | Reuse immutable evidence after identity validation | `cacheReuse=forbidden` or custom invalidators |

The Agent-facing protocol may combine calls, but the ledger still records every internal operation and every Evidence item. A combined call is a transport optimization, not a weaker audit transaction.

Agent-facing inspection is summary-first by default: dimensions, metadata,
and the Evidence index are returned while full Evidence payloads remain in the
Host. Callers can expand an immutable Evidence in full, or page through a
plugin-declared Evidence collection. Collection cursors are bound to the
WorkItem, collection, selected group, and stable ordered item IDs. The platform
may group only by declared fields and must return original item IDs; it cannot
infer that grouped observations share one semantic root cause.

Semantic review of a large collection may be checkpointed incrementally. The
platform owns checkpoint identity, atomic persistence, exact item-ID coverage,
idempotent replay, overlap rejection, and final completeness. Checkpoint
payload meaning remains plugin-owned. At final submission the platform passes
the ordered immutable checkpoints and small finalization data to the selected
plugin's assembler, then applies the same DecisionProposal and commit gates as
a directly submitted decision. A checkpoint is progress evidence, never a
formal audit conclusion by itself.

Checkpoint correction is append-only. A correction names the current
checkpoint it supersedes and must preserve the exact WorkItem, Check,
collection, and item-ID scope. Historical records remain in the canonical
ledger, but only effective unsuperseded leaves contribute to coverage and
decision assembly. This permits recovery from a bad accepted semantic record
without erasing the audit trail or creating duplicate coverage.

The platform must expose both:

- public Agent calls and Agent waiting intervals;
- internal Host operations, browser/runtime time, transport time, and cache/split activity.

No performance target may be defined only as “fewer calls” if it increases unresolved dimensions, invalid recovery, or unexplained coverage.

Failed inspection batches are not retried by default. Recursive isolation is
allowed only when the plugin explicitly declares `failureSplitting=allowed`,
inspection batching is allowed, and ordering is independent. Every failed
attempt, split, isolated WorkItem failure, and learned batch size is retained
in platform diagnostics. A non-splittable batch failure is attributed to every
affected WorkItem without pretending that any one item was proven to be the
cause.

## 8. Capability Negotiation

At Run start the Host freezes a `CapabilityProfile` for the selected runtime and plugin set. A plugin may request capabilities such as `structured_read`, `visual_read`, `network_observation`, `source_lookup`, or `safe_interaction`; these names are examples and remain extensible.

The effective profile is the intersection of:

```text
platform safety policy
  ∩ runtime adapter capabilities
  ∩ plugin requirements
  ∩ user-authorized scope
```

If a required capability is unavailable, the platform reports the concrete gap and follows the plugin's declared outcome, normally `needs_review` or a blocked WorkItem. It must not silently substitute a weaker source.

## 9. Lifecycle and Versioning

```text
registered -> validated -> enabled -> selected -> running -> terminal
```

The platform freezes plugin manifests, check versions, capability profiles, and algorithm versions at Run start. A breaking change to lifecycle, Evidence, Decision, or safety semantics increments the platform major version. A new optional capability or field increments a minor version. Clarifications without behavior changes increment a patch version.

Plugins are versioned independently from the platform. The Host rejects an incompatible plugin before formal work begins. Historical Runs remain readable and are never reinterpreted with a newer plugin.

## 10. Conformance Suite

Every plugin must pass the same platform conformance suite:

- manifest validation and version compatibility;
- WorkItem identity and duplicate handling;
- Evidence and Case reference closure;
- required-dimension and decision-state gates;
- cache invalidation and stale-source handling;
- batch split after one item fails;
- forbidden ordering or parallelism;
- recovery and checkpoint behavior;
- terminal `completed`, `partial`, and `failed` semantics;
- public progress, diagnostics, and performance accounting;
- CLI end-to-end journey using only declared business inputs.

The suite uses a deterministic adapter for contract tests and a real adapter for each plugin's production integration path. Deterministic tests cannot claim that an external runtime works.

## 11. Adoption Rule

The frontend FUA-10 implementation is the first compatibility plugin. It may keep a browser-specific adapter while the platform contract is extracted. No new platform-level field may be named after a frontend-only concept. A second, non-browser plugin must pass the conformance suite before this contract becomes implementation-ready.
