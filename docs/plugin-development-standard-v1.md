# Assayer Plugin Development Standard v1

| Metadata | Value |
|---|---|
| Document version | 1.0.1 |
| Date | 2026-09-07 |
| Status | Accepted design; implementation in progress (slices 1-4 complete) |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1, Audit Plugin Contract v1, and Platform--Plugin Boundary Contract v1 |
| Scope | Plugin registration, Agent-facing contracts, Host validation, failure handling, retry policy, and release conformance |

## 1. Purpose

This standard closes the contract gap between an Assayer plugin, the Host, and
the Agent. A plugin is not conformant merely because its Python runtime accepts
some payloads. It is conformant only when the exact input accepted at runtime is
declared in a machine-readable contract, frozen for the Run, exposed at the
Agent boundary, enforced by the Host before plugin code runs, and exercised by
release fixtures.

The keywords **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, and
**MAY** are normative as described by RFC 2119 and RFC 8174.

This document is design-first. It defines the required target behavior and
release gates. The contract data model, registration gate, opt-in Host
structural enforcement, structured error policy, and bounded correction gate
are implemented. Legacy registrations and the remaining plugin migration and
release gates are non-conformant until the remaining implementation slices in
section 14 are complete.

## 2. Problem Statement and Current Evidence

The legacy generic transport validates only that checkpoint `payload` and
decision `finalization` values are JSON objects. A legacy plugin-specific
checkpoint schema is appended to tool descriptions, but is not executable.
Registrations that declare an `AgentContractBundle` instead use the strict Host
boundary defined by this standard.

This creates three incompatible sources of truth:

1. the generic transport schema the Agent is allowed to submit;
2. the plugin schema described to the Agent;
3. the runtime checks performed by plugin validation and assembly hooks.

Before its strict-contract migration, the `ass-spec` plugin demonstrated the
resulting failure mode. Its published checkpoint schema required one 18-row
checklist payload, while the Host paged that collection into smaller tasks. Its
final assembler also required checklist fields absent from the published
schema, so visible-contract-compliant input could fail later with
`SPEC_REVIEW_SCHEMA_OUTDATED`. Retrying Agent generation could not repair that
contradictory contract. `ass-spec` now publishes page-compatible schemas and no
longer contains that late schema-outdated branch.

The required response is fail-fast contract enforcement, not an unbounded Agent
repair loop.

## 3. Goals and Non-goals

This standard has the following goals:

- one executable source of truth for every Agent submission boundary;
- deterministic, pre-plugin validation by the Host;
- a Run-frozen contract identity that prevents stale or cross-version input;
- clear error ownership and retry disposition;
- release gates proving schema/runtime equivalence from an installed artifact;
- evidence-backed semantic completion without guessed defaults.

This standard does not:

- move domain semantics into the platform;
- make the Host decide whether a domain finding is correct;
- permit the Agent to write the ledger or bypass Host lifecycle gates;
- require all plugins to use semantic checkpoints;
- treat JSON Schema validation alone as proof of a correct Decision;
- change production runtime behavior in this documentation slice.

## 4. One Executable Contract

### 4.1 Contract authority

Every interactive Check that accepts Agent input MUST publish one
machine-readable `AgentContractBundle`. Prose, prompts, examples, tool
descriptions, Python type hints, and runtime error messages are explanatory and
MUST NOT define fields that are absent from the bundle.

The same bundle MUST be used to:

1. validate plugin registration and release contents;
2. construct the Agent-facing semantic task;
3. validate inbound Agent submissions before plugin code runs;
4. generate positive and negative conformance fixtures;
5. verify plugin validator and assembler behavior.

A plugin MUST NOT maintain a second handwritten payload definition that can
drift from the registered bundle. Generated code or typed models are allowed
only when their equivalence to the bundle is checked during release.

### 4.2 Required bundle

The target registration model MUST expose one bundle per interactive Check with
at least these fields:

```json
{
  "contractId": "dev.assayer.ass-spec.review",
  "contractVersion": "2.0.0",
  "checkId": "SPEC-001",
  "checkVersion": "2.0.0",
  "schemaDialect": "https://json-schema.org/draft/2020-12/schema",
  "checkpointPayloadSchemas": {
    "checklist-dimensions": {},
    "candidate-findings": {}
  },
  "finalizationSchema": {},
  "semanticInstructions": {
    "path": "semantic-review.md",
    "sha256": "<lowercase-hex>"
  }
}
```

`contractId` MUST be stable and globally namespaced. `contractVersion` MUST be
semantic-versioned. The schema dialect MUST be JSON Schema Draft 2020-12 until
a later platform contract explicitly adds another dialect.

Each non-empty Evidence collection with `reviewRequired=true` MUST have exactly
one schema keyed by its `collectionId`. A schema MUST validate one checkpoint
page, not an entire collection, unless the Host guarantees that the collection
is delivered atomically in one task. A shared schema MAY be referenced by
multiple collection IDs, but the mapping MUST remain explicit.

A plugin that uses checkpointed decision assembly MUST declare a
`finalizationSchema`. A plugin that does not accept finalization MUST declare
that fact explicitly and the transport MUST reject a `finalization` field.

Schemas MUST be self-contained within the release or use only package-local,
release-validated references. Remote schema fetches during registration or a
Run are forbidden. Object schemas SHOULD set `additionalProperties: false`
unless forward-compatible extension data has an explicit namespace and owner.

### 4.3 Contract digest

The platform MUST canonicalize the complete bundle, including resolved local
schemas and the semantic-instructions digest, and compute a SHA-256 digest. The
digest algorithm and canonicalization format MUST be versioned platform
behavior. A Run freezes all of the following before discovery:

```text
pluginId + pluginVersion + checkId + checkVersion
+ contractId + contractVersion + contractDigest
```

Resume MUST verify the complete frozen identity. An installed package change,
schema change, instruction change, or Check version change MUST fail closed;
the Host MUST NOT silently rebind an active Run to the new contract.

## 5. Agent Boundary

### 5.1 Host response

Every `semanticTask` MUST contain only the schema applicable to that exact
task, together with its frozen identity. It MUST NOT concatenate schemas for
unrelated plugins or collections into prose. The target shape is:

```json
{
  "kind": "review_evidence_items",
  "workItemId": "spec:requirements",
  "collectionId": "checklist-dimensions",
  "itemIds": ["CHK-01", "CHK-02"],
  "agentContract": {
    "contractId": "dev.assayer.ass-spec.review",
    "contractVersion": "2.0.0",
    "contractDigest": "sha256:<digest>",
    "inputKind": "reviewCheckpoint",
    "schema": {}
  }
}
```

A `finalize_decision` task MUST similarly return `inputKind=finalization` and
the exact finalization schema. Generic decision fields remain governed by the
platform Decision schema and MUST NOT be redefined by a plugin.

### 5.2 Agent submission

Every Agent semantic submission MUST echo the frozen `contractDigest` at the
boundary containing the checkpoint or decision. The Agent MUST submit only the
requested `inputKind`, WorkItem, collection, and item IDs. It MUST NOT infer a
schema from an earlier Run, another collection, examples, or a runtime error.

The Host MUST treat a missing or unequal digest as stale contract input. It
MUST return the current semantic boundary without invoking plugin code or
persisting a mutation.

## 6. Mandatory Validation Order

The Host MUST validate every semantic mutation in this order:

1. **Generic envelope** — tool name, allowed fields, primitive types, size
   bounds, and mutual exclusivity.
2. **Frozen contract identity** — Run, plugin, Check, contract version, and
   digest match the active semantic boundary.
3. **Boundary JSON Schema** — checkpoint payload or finalization conforms to
   the exact schema selected for the current collection and input kind.
4. **Platform invariants** — WorkItem identity, Evidence item membership,
   coverage, correction lineage, replay identity, recovery barrier, and Run
   revision.
5. **Plugin semantics** — the plugin validator may check domain relationships
   that JSON Schema cannot express.
6. **Persistence** — only a fully accepted mutation enters the ledger.

Failure at any step MUST stop evaluation immediately. Steps 1 through 4 MUST
NOT invoke plugin validation, assembly, or commit code for that submission. No
rejected submission may create a checkpoint, operation, Decision, receipt, or
revision change.

Schema evaluation MUST report deterministic field locations as RFC 6901 JSON
Pointers. The Host MUST cap error count and payload size so validation itself
cannot become an unbounded workload.

## 7. Plugin Runtime Obligations

`validate_review_checkpoint` MUST accept every checkpoint payload that:

- satisfies the registered schema for its collection;
- references the selected immutable items;
- satisfies documented domain preconditions visible in the semantic task.

It MAY reject schema-valid input only for a declared semantic invariant that
cannot be represented in JSON Schema, such as a relationship to prior accepted
checkpoints or a source-bound evidence rule. Such a rejection MUST use a
registered semantic error code, safe message, JSON Pointer when applicable,
owner, and retry disposition.

`assemble_review_checkpoints` MUST accept every combination produced by valid
checkpoint fixtures plus a schema-valid finalization fixture when platform
coverage gates pass. It MUST NOT require a field that is optional or absent in
the executable contract.

An undeclared required field, `KeyError`, `TypeError`, assertion failure,
unclassified exception, or structural rejection of schema-valid data is a
plugin implementation defect. The Host MUST map it to
`PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH`, block the affected WorkItem, retain
diagnostic detail in protected logs, and MUST NOT ask the Agent to repair the
payload.

## 8. Error Contract and Retry Policy

Every boundary failure MUST use a structured platform error containing:

```json
{
  "code": "AGENT_CONTRACT_INPUT_INVALID",
  "message": "Checkpoint payload does not match the active contract.",
  "retryable": false,
  "requiredNextStep": "correct_agent_input",
  "owner": "agent_input",
  "retryDisposition": "agent_correction",
  "contractDigest": "sha256:<digest>",
  "errors": [
    {"pointer": "/checklist_review/0/status", "keyword": "enum", "message": "..."}
  ],
  "requestId": "<correlation-id>",
  "correctionBudget": {
    "maximumCorrections": 1,
    "correctionsUsed": 0,
    "correctionsRemaining": 1,
    "exhausted": false
  }
}
```

The executable response shape is
[`plugin-agent-error.schema.json`](../schemas/plugin-agent-error.schema.json).
`retryable` is always `false`: `agent_correction` authorizes one explicit new
submission after inspecting the field errors; it never authorizes automatic
transport or model retry.

Messages returned to an Agent or user MUST NOT expose stack traces, package
paths, secrets, source text outside authorized Evidence, or internal exception
class names. Protected logs MUST retain the full exception, correlation ID,
Run identity, plugin identity, contract digest, and validation stage.

The initial error taxonomy is:

| Code | Owner | Retry disposition | Required action |
|---|---|---|---|
| `AGENT_CONTRACT_ENVELOPE_INVALID` | `agent_input` | `agent_correction` | Correct the named generic fields |
| `AGENT_CONTRACT_STALE` | `contract_state` | `refresh_boundary` | Fetch the current `semanticTask`; do not replay old content |
| `AGENT_CONTRACT_INPUT_INVALID` | `agent_input` | `agent_correction` | Correct the reported JSON Pointers against the same frozen schema |
| `PLATFORM_CONTRACT_STATE_INVALID` | `platform` | `none` | Block and diagnose the Host/platform invariant |
| `PLUGIN_SEMANTIC_INPUT_INVALID` | `agent_input` | `agent_correction` | Correct the declared domain invariant |
| `PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH` | `plugin` | `none` | Block and fix/release the plugin contract or runtime |
| `PLUGIN_RUNTIME_FAILURE` | `plugin` | `none` | Block and diagnose an unexpected plugin failure |

Deterministic contract errors MUST NOT enter an automatic retry loop. The same
canonical submission MUST never be regenerated or resubmitted automatically.
By default, the orchestrator MAY permit at most one explicit Agent correction
for one semantic boundary, and only when `retryDisposition=agent_correction`
provides actionable field or semantic errors against the unchanged contract.
A second rejected correction records
`AGENT_CORRECTION_BUDGET_EXHAUSTED`, leaves the WorkItem without a fabricated
Decision, exposes it in the terminal review queue, and ends the Run `partial`.
The budget is keyed by the Run-frozen contract and current WorkItem semantic
boundary, and is reconstructed from the durable rejection events on resume.

`refresh_boundary` is state synchronization, not semantic retry. Errors owned
by `plugin` or `platform` are never Agent-retryable. Plugin release checks and
local schema validation are deterministic and MUST run once per artifact or
input digest, not through generic exponential backoff.

## 9. Evidence and Decision Integrity

Agent contract enforcement does not weaken existing evidence rules.

- Semantic tasks MUST reference immutable, Run-frozen Evidence and stable item
  IDs.
- The Agent MUST copy source references from the Host-provided source index; it
  MUST NOT invent, normalize, or substitute IDs from another namespace.
- Accepted checkpoints are immutable except through the existing explicit
  supersession contract.
- A Decision cannot be assembled until every non-empty required collection is
  covered exactly once.
- `scanned_no_issue` is valid only when every required dimension is satisfied,
  all required Evidence and checkpoint coverage is complete, and no unresolved
  semantic or runtime failure remains.
- Missing data, schema ambiguity, validator failure, or contract mismatch MUST
  NOT be converted into `scanned_no_issue` or another successful default.

## 10. Release Conformance

A plugin release MUST fail before publication when any of these gates fails:

1. every schema passes its declared meta-schema and all references resolve
   locally;
2. every interactive Check has a complete `AgentContractBundle`;
3. every non-empty review-required collection has exactly one mapped checkpoint
   schema;
4. checkpoint schemas accept Host page-sized fixtures rather than requiring
   undeclared whole-collection input;
5. checkpoint and finalization schemas reject undeclared required shapes;
6. positive schema fixtures pass the plugin validator and assembler;
7. every declared semantic rejection has a stable error classification;
8. malformed fixtures are rejected by the Host before plugin hook invocation;
9. schema-valid structural fixtures are not rejected by plugin code;
10. an installed wheel completes the fully reviewed interactive lifecycle,
    including paging, finalization, replay, resume, and terminal publication;
11. contract mismatch and plugin defects produce no mutation and no Agent
    retry request;
12. the semantic-instructions file and digest are present in the installed
    artifact.

The complete developer gate is `assayer-plugin-release-check`. It MUST compose
static package inspection, construction, isolated installation, executable
contract checks, lifecycle fixtures, and public-surface checks. Running only
`assayer-plugin-check`, `assayer-plugin-package-check`, or
`assayer-plugin-install-check` is useful for diagnosis but is not sufficient to
claim a release conforms to this standard.

CI MUST build the artifact first and run the complete gate against that exact
artifact, for example `assayer-plugin-release-check --source . dist/*.whl`.
Source-tree success cannot substitute for installed-artifact success. The gate
publishes the wheel filename and SHA-256 and stops at the first failed stage:
`source_static`, `public_surface`, `wheel_artifact`, or
`installed_lifecycle`.

An interactive plugin that publishes an `AgentContractBundle` MUST declare one
`releaseAcceptance` callable and export that callable exactly once in the
`assayer.release_acceptance` entry-point group. The callable is loaded from the
installed wheel. It receives the installed registration, an isolated output
root, and a platform-owned transport factory; it owns only the domain-valid
Agent proposals used by the journey. Its result MUST conform to
`plugin-release-acceptance.schema.json`. The platform independently validates
Check and collection coverage, zero Agent retries, terminal completed ledgers,
ledger identity, and safe ledger paths.

## 11. Compatibility and Versioning

Adding an optional schema field without changing accepted meaning is a minor
contract change. Changing required fields, collection-to-schema mapping,
semantic meaning, or finalization requirements is a major contract change. A
Run never migrates between contract versions in place.

A Host MAY support older contract versions only through an explicit,
conformance-tested compatibility adapter. It MUST NOT silently widen a schema,
drop fields, synthesize plugin data, or retry until legacy content happens to
pass. Unsupported versions fail before discovery.

Legacy plugins without an executable Agent contract may be listed for
diagnosis, but MUST NOT start a new interactive production Run after the
enforcement gate is enabled. Migration compatibility cannot be represented as
full conformance.

## 12. Ownership and Trust Boundaries

| Concern | Owner |
|---|---|
| Generic semantic envelope and lifecycle | Platform |
| Contract registration, freezing, digest, and boundary validation | Host/platform |
| Collection payload and finalization schemas | Plugin |
| Domain semantic invariants and classified errors | Plugin |
| Evidence identity, coverage, replay, recovery, and persistence | Platform |
| Proposal content that conforms to the active task | Agent |
| Retry budget and terminal/blocking transition | Host/orchestrator |
| Release evidence and compatibility declaration | Plugin publisher |

The platform validates plugin-defined structure without owning its domain
meaning. The plugin owns schema correctness but cannot decide when invalid
input should be persisted. The Agent owns a proposed value but never owns the
contract, retry loop, or ledger.

## 13. Current Non-conformance and Migration Debt

At the date of this document, the following known gaps are explicit debt:

- legacy checkpoint and finalization paths still rely on a generic object and
  description text when no `AgentContractBundle` is registered;
- legacy interactive registrations may still omit `AgentContractBundle` during
  the migration window;
- legacy interactive releases without an executable acceptance driver remain
  non-conformant and cannot pass the complete release gate.

These gaps are not permitted examples. New code MUST NOT increase reliance on
them, and no plugin may claim compliance with this standard until the applicable
gates are implemented and passing.

## 14. Small Implementation Slices

Implementation MUST proceed in the following independently verifiable slices:

1. **Freeze the standard — complete.** Merge this document and align existing plugin
   documentation. Exit: no documentation says plugin payload structure is
   opaque to Host validation.
2. **Add the contract data model — complete.** Register and validate
   `AgentContractBundle`, local schemas, version, and digest without changing
   Run behavior. Exit: positive and malformed registrations have deterministic
   conformance results.
3. **Enforce the Host boundary — complete for declared bundles.** Freeze the
   bundle per Run, return the selected schema in `semanticTask`, require the
   digest, and validate before plugin invocation. Exit: hook spies prove
   malformed input cannot reach plugin code or persistence.
4. **Add the failure and retry boundary — complete for declared bundles.** Implement the error taxonomy,
   sanitized response, correlation logging, single-correction budget, and
   blocked/partial transition. Exit: fault-injection tests prove deterministic
   failures do not loop.
5. **Migrate `ass-spec` — complete.** Split its schemas by collection, make each
   schema page-compatible, publish a finalization schema, and align
   runtime-required fields. Exit: the installed plugin completes all pages and
   finalization with no legacy fallback, Agent retry, or schema-outdated error.
6. **Enforce release compliance — complete.** Compose all checks in
   `assayer-plugin-release-check` and require it in CI. Exit: the exact built
   wheel passes the complete lifecycle; each intentionally broken fixture fails
   at its expected pre-plugin stage.

No slice may claim a later gate. In particular, publishing this document alone
does not change runtime behavior, and a passing source-tree registration check
does not prove Agent contract compatibility.

## 15. Acceptance Matrix

| Scenario | Expected boundary | Plugin invoked | Mutation | Agent retry |
|---|---|---:|---:|---:|
| Missing generic field | Envelope validation | No | No | At most one explicit correction |
| Stale contract digest | Frozen identity validation | No | No | No; refresh boundary |
| Wrong payload type or required field | JSON Schema validation | No | No | At most one explicit correction |
| Unknown or overlapping item ID | Platform invariant validation | No | No | No automatic retry |
| Declared semantic relation is invalid | Plugin semantic validation | Yes | No | At most one explicit correction |
| Schema-valid payload triggers missing-field/runtime structural error | Plugin implementation mismatch | Yes | No | No |
| Unexpected plugin exception | Plugin runtime failure | Yes | No | No |
| Valid checkpoint replay | Replay gate | Only if no durable acknowledgement exists | No duplicate | No |
| All valid pages and finalization | Full pipeline | Yes | One accepted mutation per operation | No |

This standard is implementation-ready only after maintainers accept its public
protocol shape, versioning impact, migration policy, and acceptance matrix.
