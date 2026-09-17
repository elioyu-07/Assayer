# Agent Domain-Result Boundary v2

| Metadata | Value |
|---|---|
| Document version | 0.1.0 |
| Date | 2026-09-09 |
| Status | Historical result-boundary record; superseded and not executable |
| Owner | Assayer maintainers |
| Scope | Agent-facing interactive plugin submission and Plugin SDK ownership |

> This document records an earlier SDK v2 DomainResult implementation. The
> ordinary authoring target no longer asks a plugin to declare a complete
> DomainResult Schema or mapper. Platform Constitution v1.1, the Audit Plugin
> Contract v1.1, and the
> [Simple Plugin Authoring Architecture](simple-plugin-authoring-design.md)
> supersede those author-facing requirements. The Host will retain the useful
> domain-only Agent boundary while replacing complete-WorkItem submission with
> the common incremental review model.

## 1. Decision

The Agent-facing interactive contract is domain-only. The Agent receives the
current business task, immutable Evidence, and the plugin Check's executable
domain-result schema. It returns only a `DomainResult` conforming to that
schema.

The Host owns the platform envelope and binds the result to the current task
context. The Agent MUST NOT construct or echo Run, WorkItem, collection,
checkpoint, digest, revision, coverage, replay, recovery, or finalization
fields.

This is a boundary change, not a removal of platform guarantees. The Host
continues to create checkpoints, validate coverage, resolve Evidence, append
the ledger, assemble Decisions, and publish terminal results internally.

## 2. Public responsibility split

| Concern | Agent | Plugin SDK | Host/platform | Admin diagnostics |
|---|---:|---:|---:|---:|
| Domain interpretation | owns | defines rules | validates declared rules | reads |
| Domain result fields | supplies | declares Schema | validates | reads |
| Scope and source inspection | reads supplied Evidence | implements discovery/inspection | authorizes and persists | reads |
| Evidence selection | selects stable references | defines meaning | resolves and verifies | reads lineage |
| Run/WorkItem identity | does not see internal shape | does not create | owns | reads |
| Paging and coverage | follows high-level next step | declares domain cardinality | owns | reads |
| Checkpoint and ledger | never writes | never writes | owns | reads |
| Retry, replay, resume | follows outcome | declares safe execution profile | owns | reads |
| Terminal result | explains findings | contributes domain extension | assembles and publishes | reads |

The Agent may receive an opaque run handle from an SDK session, but it cannot
manufacture or modify the handle's internal identity. Direct model output is
never authoritative for platform identity.

## 3. Agent-facing shape

The semantic task contains only the information needed for the current domain
decision:

```json
{
  "kind": "domain_review",
  "instructions": "Check whether each requirement has a traceable acceptance criterion.",
  "items": [
    {
      "candidate_id": "acceptance-missing",
      "content": "..."
    }
  ],
  "evidence": [
    {
      "evidence_ref": "source:e48eeaf6a549:10:10",
      "content": "..."
    }
  ],
  "resultSchema": {}
}
```

The Agent submission contains only the plugin-defined result:

```json
{
  "result": {
    "decisions": [
      {
        "candidate_id": "acceptance-missing",
        "status": "CONFIRMED",
        "severity": "P1",
        "reason": "no stable acceptance identifier",
        "supportedBy": ["R1"]
      }
    ]
  }
}
```

`result` is the only model-authored value. `runId`, `workItemId`,
`collectionId`, `itemIds`, `taskDigest`, `contractDigest`, checkpoint IDs,
`reviewCheckpointIds`, and platform `finalization` are forbidden in the
Agent-facing submission schema.

## 4. Trusted Host binding

For every semantic task, the Host retains an immutable internal `TaskContext`:

```text
Run identity + WorkItem + stage + collection + ordered item IDs
+ Evidence snapshot + contract identity + task revision + idempotency key
```

On submission the Host performs, in order:

1. resolve the active `TaskContext`;
2. parse and validate the DomainResult Schema;
3. resolve every Evidence reference from the Host-owned immutable ledger;
4. enforce task membership, Evidence scope, and coverage;
5. invoke the plugin's declared domain validator;
6. create the internal checkpoint and canonical Decision data;
7. atomically append the accepted mutation and acknowledgement.

No rejected result may create a checkpoint, operation, Decision, receipt, or
revision change. Identical accepted results are idempotent; changed results
for an already committed task fail closed.

## 5. Evidence references

The Agent returns only stable Host-issued Evidence references. It MUST NOT
copy paths, line ranges, source digests, or other canonical metadata into the
submission merely to satisfy transport requirements.

The Host resolves and records the canonical Evidence record. Unknown,
cross-WorkItem, cross-Run, out-of-scope, or superseded references are rejected
before persistence. A plugin may add domain meaning to a reference but cannot
replace the Host's Evidence identity.

## 6. Plugin SDK obligations

An interactive Check publishes one executable domain contract containing:

- business scope schema;
- DomainResult schema;
- domain semantic rules and stable rule IDs;
- optional domain validator;
- optional deterministic result mapper or reducer.

The plugin MUST NOT publish a platform checkpoint envelope, platform
finalization envelope, transport cursor, ledger writer, or retry loop. A
multi-stage domain review is represented as multiple domain tasks, each with a
domain schema; it is not exposed as a platform checkpoint protocol.

The SDK, Host validator, plugin runtime validator, and release fixtures MUST
derive from the same executable contract. A schema-valid result rejected for
an undeclared field is a plugin implementation defect and has zero Agent
correction budget.

## 7. Error and retry policy

The Agent-facing API exposes a high-level error and next action, not platform
state-machine details:

| Failure | Host action |
|---|---|
| Plugin registration or contract invalid | Reject before Run; no retry |
| Plugin violates its declared SDK contract | Fail the affected Run/WorkItem; no Agent retry |
| DomainResult shape invalid | No mutation; at most one bounded gateway correction |
| Evidence reference invalid | No mutation; return the exact reference error |
| Stale/concurrent task | Rebind or re-offer the current task; do not ask Agent to edit a digest |
| Transient provider/model failure | Bounded platform retry according to policy |
| Domain evidence insufficient | Return plugin-defined unresolved/needs-review result |

Correction budgets, replay identity, and terminalization remain Host-owned and
are not part of the DomainResult contract.

## 8. Hard-cut migration rule

The old Agent-facing checkpoint protocol is not a fallback path. A plugin or
client that publishes or submits the old platform envelope is rejected with an
unsupported contract/protocol error before semantic execution.

The existing checkpoint, preflight, and finalization implementations may remain
as private Host functions during migration, but they MUST NOT remain in the
normal Agent tool catalog or in the public Plugin SDK.

## 9. Exit criteria

This target boundary is implemented only when all of the following hold:

- Agent submissions contain only DomainResult fields;
- Host binds task identity without model-authored digests or IDs;
- Evidence references are resolved from the immutable Host ledger;
- invalid submissions produce no ledger mutation;
- plugin contract defects fail without Agent retries;
- replay, resume, paging, coverage, and terminal publication remain green;
- the migrated reference plugin passes the installed-artifact release gate;
- the Agent Skill no longer instructs checkpoint, preflight, or finalization construction;
- old interactive envelope submissions are rejected rather than translated.

This historical core boundary is implemented for the Advanced migration path.
Ordinary plugins do not add domain-stage contracts. The Host plans bounded
ReviewBatches, persists common review values, and owns CoverageLedger without
reintroducing platform checkpoint, digest, cursor, or finalization fields at
the Agent boundary.
