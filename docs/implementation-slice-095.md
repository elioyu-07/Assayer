# Implementation Slice 095 — P0 Platform-Owned Evidence Binding

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: in progress; current agentView/supportedBy/provider boundary is executable, and the hard-cut removal of legacy protocol paths is in progress
Priority: P0
Owner: Assayer platform maintainers, with ass-spec plugin support
Date: 2026-09-09

## Objective

Close the cross-layer Evidence reference mismatch exposed by
`PLUGIN_SEMANTIC_INPUT_INVALID` and make the platform absorb Evidence identity
and lineage complexity. Agents must perform domain judgment only; they must not
construct or copy `evidence_id`, `source_chunk_id`, digests, paths, line ranges,
Run IDs, or other Host-owned fields.

This slice also closes the excessive implementation and release coupling
between Host, SDK, plugins, and Agent interaction contracts. The current
interaction protocol is one exact contract. Older protocol envelopes,
registrations, SDK ranges, and active Runs are rejected rather than adapted.

## Design principles

1. **Platform owns complexity.** Evidence identity, lineage, lifecycle,
   checkpointing, retries, replay, resume, ledger writes, and protocol
   compatibility stay inside Host/SDK.
2. **Agent owns semantic judgment only.** The Agent receives bounded business
   material and returns plugin-defined domain conclusions, not platform
   bookkeeping.
3. **One Evidence authority.** Plugins do not define private Evidence
   namespaces or independently parse platform IDs.
4. **Exact protocol, independent implementation.** Host, SDK, plugin, and
   Agent use one exact current contract. Distribution releases remain separate,
   but no older execution contract is adapted.
5. **No silent guessing or adaptation.** Arbitrary paths, digests, line ranges, or free-form
   Evidence objects are never silently converted into references.

## Minimal Agent-facing contract

The Host compiles the immutable InvestigationPacket into task-local opaque
handles such as `R1`, `R2`, and `C1`. A semantic task contains only the bounded
content needed for judgment, candidate handles, and the plugin's domain schema.
The Agent returns decisions and, for new findings, `supportedBy` task handles.
It does not submit `evidenceRefs`, `evidence_id`, `source_chunk_id`, digests,
paths, Run/WorkItem IDs, checkpoints, revisions, or finalization fields.

For existing candidates the Host automatically inherits frozen Evidence. For
new findings the Host resolves the handles to complete internal lineage.

## Complexity boundary

After this slice, plugin authors only implement manifest and business scope,
Check definitions, DomainResult schema, domain validation/mapping, and domain
fixtures. The platform owns task compilation, Evidence binding, lifecycle,
checkpoint and Decision assembly, retry/replay/resume, MCP transport,
ledger/reporting, compatibility negotiation, and release gates.

This is a medium-to-high complexity platform change. The difficult work is
lineage persistence, hard-cut rejection, release verification, and the
cross-layer test matrix—not migration or adapter support.

## Final target statement

Build a plugin platform in which the platform owns runtime and Evidence
complexity, plugins evolve independently through a stable SDK, Agents submit
only minimal domain results, and unsupported contract combinations fail before
execution without compatibility adapters.

The target is considered explicit only when these four outcomes are testable:

1. Agents never receive or submit platform identity or state-machine fields.
2. Plugins never depend on Host internals or maintain private Evidence logic.
3. Host, SDK, plugin, and Provider implementations can evolve independently
   behind one exact current contract.
4. Unsupported combinations fail before Run creation, not during semantic
   execution.

## Hard protocol rules

### Task-local handles are the only Agent references

`R1`, `R2`, `C1`, and similar handles are the only references an Agent may
return. They are opaque, issued by the Host, valid only within the current
semantic task, and never reusable across Runs, tasks, or protocol versions.
The Host owns the mapping from each handle to source chunk, parent Evidence,
digest, WorkItem, and Run lineage.

The Agent must not receive or submit `evidence_id`, `source_chunk_id`, digest,
path, line range, or free-form Evidence objects. Existing candidate findings
inherit their frozen Evidence automatically; new findings use `supportedBy`
task handles.

### Version responsibilities are separated

| Version layer | Owns | Must not own |
|---|---|---|
| Interaction protocol | wire shape, lifecycle semantics, handles, errors, capabilities | domain rules or plugin implementation details |
| Agent domain contract | one Check's domain schema and semantic fields | Run, ledger, checkpoint, or Evidence identity |
| SDK API | stable typed helpers and current-contract types | Host implementation modules |
| Host implementation | orchestration, persistence, binding, exact-match rejection, recovery | plugin-specific business conclusions |
| Plugin implementation | domain behavior and domain release cadence | transport, lifecycle, or private Evidence namespaces |

### The current contract is exact

Every packaged plugin declares one exact protocol version, one exact SDK
contract version, and its capabilities. The Host rejects ranges, older
protocols, older SDK declarations, legacy envelopes, and adapter identities
before Run creation. A contract break is released as a new explicit contract;
there is no execution migration window.

## Target boundary

The Host compiles an immutable InvestigationPacket into a minimal semantic task
with task-local opaque handles (`R1`, `R2`, `C1`, ...). The Agent returns only
plugin-defined semantic decisions and references to those handles. The Host
resolves handles, binds candidate and source lineage, validates task membership,
assembles the platform Decision, and persists the canonical ledger record.

```text
Agent handle (R1/C1)
  -> Host TaskContext
  -> source chunk
  -> parent Evidence
  -> digest / WorkItem / Run lineage
  -> canonical Decision and ledger
```

## Reordered development tasks

### Phase 0 — Freeze the contract

1. Approve the Evidence-reference ADR and the minimum Agent-facing
   DomainResult contract.
2. Define the independent version layers, exact-contract capability
   vocabulary, and breaking-change policy.
3. Record the migration rule for old Runs: protocol changes require
   `RUN_RESTART_REQUIRED`; frozen Evidence is never rewritten.

### Phase 1 — Build the stable platform boundary

4. Implement the public SDK primitives: task builder, opaque handle types,
   Evidence resolver, and decision binder.
5. Implement the Host-owned `EvidenceHandleRegistry` with durable mappings from
   task handles to source chunk, parent Evidence, digest, WorkItem, and Run.
6. Compile InvestigationPackets into minimal semantic tasks containing only
   bounded content, candidate handles, task-local review handles, and the
   plugin DomainResult schema.
7. Change `advance_plugin_run` to resolve handles, validate task membership,
   assemble platform fields, and persist lineage internally.
8. Add capability negotiation before Run creation and explicit errors for
   unknown, stale, cross-Run, unsupported, or incompatible inputs.

### Phase 2 — Migrate the plugin boundary

9. Remove Agent-input Evidence parsing from `ass-spec`; make it consume
   Host-resolved evidence and retain only domain validation/mapping.
10. Update `ass-spec` schemas, semantic-review guidance, and release fixtures to
    the minimum Agent contract using `supportedBy` task handles.
11. Reject every non-current protocol, SDK, or capability declaration before
    any Run is created. No adapter or compatibility window is part of the
    execution path.

### Phase 3 — Prove the contract end to end

12. Add the real MCP round-trip gate:
    `start -> semanticTask -> minimal DomainResult -> bind -> terminal ledger`.
13. Add negative tests for foreign handles, old digests, raw platform IDs,
    paths, line ranges, duplicate handles, changed submissions, and old
    protocol envelopes.
14. Add exact-contract tests proving the current plugin runs and every older,
    ranged, or adapter-bearing declaration fails before Run creation.
15. Add resume/replay tests proving task handles remain valid only within their
    issuing semantic task and that protocol changes require restart.

### Phase 4 — Gate, migrate, and release

16. Upgrade release/install conformance to require the end-to-end semantic
    round trip, not only import, registration, or scope checks.
17. Add observability for protocol version, contract version, capability set,
    handle namespace, error owner, and internal Evidence lineage without
    exposing document bodies.
18. Publish the exact-contract rejection policy and current plugin authoring
    guide; do not publish a legacy migration or adapter window.
19. Publish Host/SDK and `ass-spec` compatible releases, migrate the local
    installation, and mark old Runs for explicit restart.
20. Verify a fresh Run through terminal completion and record the regression
    result for the former `Unknown source Evidence reference` failure.

## Version-decoupling requirements

The interaction protocol is an independent compatibility product. Host package
versions, SDK versions, plugin versions, and Agent domain-contract versions must
not be treated as one lockstep number.

- The stable protocol owns wire shape, lifecycle semantics, task-local handles,
  error classes, and capability negotiation.
- The SDK owns typed helpers; plugins depend on the one exact SDK contract,
  not on Host implementation modules.
- Plugin package versions describe domain behavior and may evolve independently
  when their declared domain contract remains compatible.
- Host may add optional fields and capabilities only through a new explicit
  contract; the current contract never silently absorbs older declarations.
- A breaking semantic or lifecycle change requires a new protocol major and an
  explicit compatibility gate; it must not fail later inside plugin code.
- Every installed plugin must declare one exact protocol/SDK pair and its
  capabilities; the Host rejects any other combination before Run creation.

The executable declaration is `PluginCompatibility` (available from the
public SDK and embedded in a packaged plugin manifest): protocol min/max,
SDK min/max, and protocol capabilities. Min/max values must be identical, and
the declaration must equal the Host's current contract. Missing, ranged, old,
or adapter-bearing declarations are rejected before plugin initialization,
ownership claims, or ledger creation. The handshake result is frozen in the
Run resume descriptor and is visible as metadata in the `start_plugin_run`
response.

## Execution work plan

| Work package | Priority | Depends on | Deliverable | Exit gate |
|---|---:|---|---|---|
| WP-0 Contract freeze | P0 | None | ADR, protocol layers, handle rules, compatibility policy | Maintainers approve one executable contract |
| WP-1 SDK boundary | P0 | WP-0 | Opaque handle types, resolver, task builder, binder interfaces | SDK tests pass without Host-internal imports |
| WP-2 Host handle and task context | P0 | WP-1 | Durable handle registry and minimal semantic task compiler | Task handles survive resume and are task-scoped |
| WP-3 Host submission binding | P0 | WP-2 | `advance_plugin_run` handle resolution and internal lineage assembly | Valid minimal result reaches ledger; invalid result mutates nothing |
| WP-4 Protocol contract | P0 | WP-0, WP-3 | Exact contract declaration and pre-Run rejection | Unsupported combinations fail before Run creation |
| WP-5 ass-spec migration | P0 | WP-3 | Plugin schema/docs/validator/mapper use Host-resolved evidence | `ass-spec` no longer parses Agent platform IDs |
| WP-6 Contract verification | P0 | WP-4, WP-5 | MCP round-trip, hard-cut negatives, resume/replay, and semantic-stability tests | Former Evidence error is a regression case |
| WP-7 Release gates | P0 | WP-6 | Installation/release conformance invokes real semantic round trip | Clean package passes all gates |
| WP-8 Clean rollout | P0 | WP-7 | Reject old active Runs, publish current package, run fresh CLI journey | Fresh Run reaches terminal completion |
| WP-9 Ongoing governance | P1 | WP-7 | Exact-contract change checklist and observability | Future public changes require contract evidence |

Current implementation checkpoint: the exact contract declaration is validated
at registration/startup before plugin initialization and ownership/ledger
creation, exposed as non-sensitive metadata, and frozen in resume descriptors.
There is no version matrix or adapter path. The remaining work is to remove
the residual browser-dependent acceptance gap and make the current-contract
release gate and clean CLI journey authoritative. The removed generic contract
execution path is no longer present in the interactive
controller; registrations carrying it fail before Run creation.

WP-3/WP-5 checkpoint: `supportedBy` is now a Host-recognized Evidence-bearing
field. Task-local handles are resolved before plugin schema/semantic handling,
ass-spec accepts `supportedBy` as its only Agent-facing Evidence field, and an
end-to-end test proves the Agent handle is replaced by the internal source
reference in the durable ledger. The removed `evidenceRefs` shapes are rejected
by the DomainResult Schema; there is no Agent compatibility fallback.
Evidence handle ordinals are Run-local and monotonic across WorkItems, so a
later task does not reuse an earlier task's `R1`. Resume reconstructs the same
ordinal deterministically from frozen investigations; a stale prior-task
handle therefore fails before ledger mutation.
Recognizable prior-task handles return `STALE_EVIDENCE_HANDLE` with
`contract_state` ownership and `refresh_semantic_boundary`; this path does not
consume the Agent correction budget. Arbitrary unknown references remain
Agent-input validation failures.
Active Run resume now compares the frozen protocol/SDK/capability handshake
with the current Host. Missing or changed handshake metadata returns
`RUN_RESTART_REQUIRED` and leaves the ledger untouched; historical terminal
results remain readable because they are not resumed into execution.

The current ass-spec registration receives `agentView` as
its only evidence-bearing input. The Host compiles bounded `domainData`,
candidate material, source excerpts, and review context while stripping Run,
WorkItem, Evidence, source-chunk, digest, path, and line identities. The old
`investigation` and top-level `evidenceHandles` views are not part of the
current Agent contract and are rejected at the hard-cut boundary.

The Host validates one exact protocol and SDK declaration before Run creation.
Older versions, ranges, and adapter identities fail closed without creating
Run state. The current contract selects the minimal `agentView` boundary
directly.

### Follow-up — Host semantic-task budget and telemetry

The Host now applies the 64 KiB budget to the complete Agent-facing
`semanticTask`, not only its `agentView`. Durable InvestigationPackets remain
in the ledger; oversized strings and collections are compacted in the
presentation projection and marked explicitly as truncated. The active task's
opaque evidence handles are expanded through the Host-owned
`expand_semantic_evidence` operation, so plugins do not implement pagination.

Interactive ledgers also record semantic-task compilation time, final task
payload bytes, and elapsed Agent wait between task publication and an accepted
DomainResult. These measurements are exposed in the platform performance bill
and canonical result when available. Successful direct Interactive MCP
`advance_plugin_run` requests contribute Host transport timing only when they
reach a semantic or terminal boundary; this keeps diagnostic calls from
causing an extra synchronous ledger write on every request. Terminal result
reads and terminal replay remain read-only. Schema-rejected requests and
rejected semantic input do not persist wait or transport telemetry; the latter
otherwise leaves the ledger unchanged apart from its explicit
boundary-rejection event.

The independently packaged minimal reference plugin now passes its release
acceptance journey using only `agentView` and `supportedBy`, including resume
and terminal replay. Manifest and registration compatibility declarations are
identity-bound; conflicting declarations fail registration instead of letting
the loaded plugin widen its own support range.

Package static conformance now also compares the release descriptor's
`compatibility` block with the packaged manifest. Drift is rejected before
wheel build or isolated installation, closing a second source of version
misalignment in the release pipeline.

### Recommended execution order

```text
WP-0
  -> WP-1
  -> WP-2
  -> WP-3
  -> WP-4 + WP-5
  -> WP-6
  -> WP-7
  -> WP-8
  -> WP-9
  -> WP-10
```

WP-4 and WP-5 may proceed in parallel after WP-3. No plugin migration or
release work should begin before the Host submission boundary is executable;
otherwise plugin code will encode another unstable interpretation.

## Evolution stability guarantees

The platform cannot promise that implementation details never change. It must
promise that public contracts do not change silently. The following are stable
compatibility commitments:

- Agent-facing DomainResult shape and task-local handle semantics;
- protocol capability negotiation and error ownership;
- SDK public surface and plugin compatibility declarations;
- Run lifecycle semantics and Evidence lineage/audit meaning.

Host internals, ledger storage, transport implementation, caching, scheduling,
report generation, and plugin algorithms may evolve behind those boundaries.

Every public-contract change follows this sequence:

```text
RFC/ADR
  -> executable exact-contract tests
  -> additive release or new protocol major
  -> explicit rejection/update of non-current declarations
```

Patch releases may fix defects but must not change protocol meaning. Minor
releases may add optional fields or capabilities. Breaking lifecycle or semantic
changes require a new protocol major and an explicit contract gate. Exact-
contract rejection tests and the end-to-end round-trip gate are
machine-enforced, not documentation-only promises.

## Stability definition of done

This slice is not complete until both forms of stability hold:

1. **Contract stability:** the current plugin contract remains deterministic;
   older or ranged declarations are rejected before execution.
2. **Semantic stability:** the same DomainResult has the same Evidence lineage,
   lifecycle meaning, and terminal Decision semantics across compatible Host
   versions.

Any unsupported combination must be rejected before Run creation with no
partial state and no silent reinterpretation of historical Runs.

## Acceptance criteria

- The Agent never needs to know `evidence_id` or `source_chunk_id`.
- Every handle shown in a semantic task is accepted unchanged by the next
  `advance_plugin_run` call.
- Host, SDK, plugin validator, and mapper use one executable Evidence policy.
- Host persists complete source-chunk-to-parent-Evidence lineage automatically.
- Invalid or stale handles fail before checkpoint, Decision, or ledger mutation.
- Platform contract failures do not consume the Agent correction budget.
- A clean installed `ass-spec` package passes the real MCP round-trip gate.
- The former `Unknown source Evidence reference` failure is a regression test.
- Old Runs are restarted explicitly rather than silently adapted.
- Unsupported protocol combinations fail during capability negotiation with a
  clear actionable error and no partial Run state.
- Task-local handles are the only Agent-visible Evidence references and are
  rejected when reused outside their issuing semantic task.
- Exact-contract rejection is executable: old, ranged, and adapter-bearing
  declarations fail before Run creation.

## Non-goals

- Do not expose platform IDs merely to preserve an old Agent contract.
- Do not add a second plugin-local Evidence namespace.
- Do not silently translate arbitrary paths, digests, or free-form Evidence
  objects into references.
- Do not require plugin authors to chase every Host patch release.
- Do not make protocol, SDK, Host, and plugin versions a single lockstep
  number.
- Do not change public-contract meaning in a patch release.
- Do not silently remove or reinterpret a public field or capability; publish
  a new explicit contract and reject declarations for the old one.
