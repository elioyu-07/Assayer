# Assayer Audit Plugin Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.0.2 |
| Date | 2026-09-07 |
| Status | Frozen for M2; executable Agent-boundary enforcement is active for declared bundles and pending legacy-plugin migration |
| Owner | Assayer maintainers |

## 1. Contract surface

An audit plugin is an independently inspectable package that contributes one
or more domain Checks. It is selected by `pluginId` and Check identity before a
Run starts. Registration, manifest loading, lifecycle, safety, persistence,
commit, and reporting remain platform-owned.

The required domain operations are:

```text
discover(scope, capability_context) -> WorkItemSet
inspect(work_items, check, capability_context) -> InvestigationPacketSet
restore(case, capability_context) -> RecoveryResult (optional when recovery is required)
summarize(canonical_result) -> domain_extension (optional)
```

`decide` is an Agent semantic operation. The plugin may provide prompts,
rubrics, or a decision provider, but the Host validates the proposal and owns
the commit transaction.

## 2. Manifest requirements

Every manifest declares:

- stable `pluginId`, semantic `version`, and supported `platformApiVersion`;
- one or more domain and subject-kind identifiers;
- each Check's ID, version, applicable subject kinds, dimensions, allowed
  result states, required Evidence kinds, required capabilities, capability
  absence outcome, and invalidation signals;
- execution constraints for batching, ordering, parallelism, cache reuse,
  failed-batch splitting, and checkpoints;

Every registration also publishes its supported execution modes, factories,
capability set, and a JSON-compatible `scopeSchema` used to validate business
input. Registration metadata is platform packaging data and does not expand
the manifest's safety authority.

An interactive release claiming conformance to the
[Plugin Development Standard v1](plugin-development-standard-v1.md) also
publishes one versioned, machine-readable Agent contract bundle per interactive
Check. The bundle maps every review-required Evidence `collectionId` to its
checkpoint payload schema and declares the finalization schema. The Host
freezes its identity and digest for the Run. Prose or runtime validation cannot
add an undeclared required field.

The manifest is a declaration, not a permission grant. A plugin cannot request
credentials, writes, or a capability that the platform and user scope have not
authorized.

## 3. WorkItem and investigation rules

`WorkItem` identity is stable within a Run and includes a domain kind, source
identity, and state digest. Discovery must reject duplicate IDs and must not
promote an unverified candidate directly into a formal WorkItem.

Each inspected WorkItem returns exactly one `InvestigationPacket` for the
selected Check. The packet contains every declared dimension, concise
observations, Evidence references, source identity, and recovery status.
Evidence is immutable and Host-verified; a plugin runtime may collect it but
cannot rewrite it after the packet is returned.

## 4. Decision and recovery rules

The Agent returns exactly one `DecisionProposal` per inspected WorkItem. The
proposal must cover every Check dimension exactly once and use only states
declared by that Check:

- `scanned_no_issue`: every required dimension is `satisfied` and coverage is
  complete;
- `issue_found`: at least one dimension is `violated`, with the Evidence and
  issue details required by the Check;
- `needs_review`: at least one dimension is `unresolved`, `blocked`, or
  `conflicted`, with a concrete blocker and next action;
- `not_applicable`: the Check is inapplicable and the proposal explains why;
- `noise`: the observation is intentionally excluded with a reason.

The Host rejects malformed, under-covered, stale, cross-identity, or
post-recovery proposals. Commit receipts are durable when production
persistence is available and are replay-safe by Run/WorkItem/Check identity
and proposal digest.

The latest recovery outcome is an active commit barrier, not merely a diary
entry. `uncertain` or `failed` recovery blocks a new Decision; a later
`restored` or `not_required` outcome is required before commit. Recovery state
cannot change after that WorkItem Decision is committed. Failed terminal Runs
may retain earlier invalidated Decisions in the canonical ledger for
diagnosis, but their formal result and publication surface must suppress those
Decisions and receipts.

## 5. Execution and optimization

The plugin declares whether discovery, inspection, or decision batching,
parallelism, and cache reuse are safe. The Host may combine public calls but
must retain per-WorkItem Cases, Evidence, Findings, Decisions, receipts, and
timing. A failed batch is split only when the declared ordering and isolation
rules permit it; otherwise affected items become blocked or `needs_review`.
`failureSplitting=allowed` is an explicit assertion that a failed inspection
batch can be retried as smaller independent batches without unsafe duplicate
effects. It is effective only with `inspectBatching=allowed` and
`ordering=independent`; omission means `forbidden`. After a successful split,
the platform may retain the largest successful sub-batch as the safe size for
later WorkItems in the same Run.

A plugin with a large array inside one Evidence payload may declare an
`evidenceCollections` entry in its InvestigationPacket metadata. Each entry
identifies the Evidence ID, an RFC 6901 JSON pointer to the array, a stable
unique item-ID field, and optional mechanical grouping fields. The platform
validates the declaration, retains the full immutable Evidence in the ledger,
and owns bounded paging, cursors, and the mapping from groups back to original
item IDs. A declared group is never a semantic finding merge.

An Evidence collection may set `reviewRequired=false` when it is immutable
reference context rather than a semantic review queue. Reference collections
use the same validation, grouping, and paging contract, but do not contribute
to review coverage, do not block a WorkItem Decision, and reject review
checkpoints. Omitting `reviewRequired` preserves the default value `true`.
At a decision or finalization boundary, the Host includes a compact
`referenceCollectionIndex` so an Agent can select a group and page without
reloading the full Evidence payload.

Long semantic reviews may be persisted as `ReviewCheckpoint` records. Each
checkpoint binds plugin-owned domain semantics to one WorkItem, Check version,
declared Evidence collection, and a non-empty set of stable item IDs. Domain
meaning remains opaque to the platform; payload structure does not. Under the
[Plugin Development Standard v1](plugin-development-standard-v1.md), the Host
must validate the checkpoint against the Run-frozen schema for that exact
`collectionId` before plugin code runs.

The platform rejects stale contract identity, malformed payload structure,
unknown IDs, and overlapping coverage, makes identical accepted replays
idempotent, and only then calls the plugin's required
`validate_review_checkpoint` hook for declared domain invariants. The hook
receives the proposed checkpoint, its selected immutable collection items,
earlier accepted checkpoints for that WorkItem and collection, the
InvestigationPacket, Check, and PlatformContext. It must reject malformed
domain Findings, unknown or incomplete domain references, and Evidence that
does not trace to the selected items. It must not require structure absent from
the executable schema. A plugin without this hook cannot persist semantic
checkpoints. A rejected checkpoint leaves no checkpoint, operation, decision,
receipt, or revision change in the ledger.

A final decision may reference checkpoints only when they cover every
non-empty collection whose `reviewRequired` value is `true`, with every item
covered exactly once. Reference collections are excluded. The Host validates
the declared finalization schema before the plugin assembles domain details;
the normal decision and commit gates then apply without exception. A
schema-valid payload rejected for undeclared structure is a plugin contract
implementation mismatch and is never an Agent retry condition.

Strict boundary failures use the structured
[`plugin-agent-error.schema.json`](../schemas/plugin-agent-error.schema.json)
contract. An Agent-owned structural failure allows one explicit correction at
that durable semantic boundary, never an automatic retry. A second rejection
records the unresolved WorkItem and closes the Run `partial`; plugin- or
platform-owned contract failures have no Agent correction budget. This
terminalization does not create a synthetic `needs_review` Decision.

An accepted checkpoint is immutable. Before its WorkItem Decision is
committed, a reviewer may append a correction with
`supersedesCheckpointId`. The target must be the current effective checkpoint
for the same Run, WorkItem, Check version, Evidence collection, and exact item
IDs. The ledger retains both records, while coverage, subsequent validation,
decision assembly, and finalization use only unsuperseded leaves. Exact
correction replay is idempotent; unknown targets, stale branches, scope
changes, and post-Decision corrections fail without mutation.

Interactive plugins may expose the Host-driven `advance_plugin_run` operation.
It performs deterministic discovery and inspection, pauses at an explicit
semantic boundary, persists supplied checkpoints, assembles supplied decisions,
and performs eligible closeout. Normal product transports must make this the
only route for discovery, inspection, checkpoints, decisions, and closeout;
primitive lifecycle operations belong to diagnostic transports. They may also
expose `expand_evidence_collection` as a bounded read-only operation for
reference collections named by the semantic task; it cannot checkpoint,
decide, recover, or advance a Run. Responses identify `state`, `phase`,
`requiredNextStep`, `canFinish`, and remaining work counts.
`awaiting_agent_decision` means semantic input is required;
`ready_to_finish` means all coverage gates pass and the Host may produce the
formal summary. A Run with pending mechanical work is never represented as
terminal.

The product transport owns semantic operation identity. Agents and users do
not supply `operationId` or revision fields. The Host derives stable operation
identity from canonical semantic content, persists the accepted mutation and
its acknowledgement in the same ledger replacement, and returns the current
`runRevision`. Replaying identical checkpoint or Decision content cannot add a
second ledger mutation or invoke the plugin committer again. Changed semantic
content for an already committed WorkItem fails closed rather than inheriting
the earlier receipt.

The interactive Host persists a validated active-Run resume descriptor beside
the canonical ledger. A replacement Host must verify frozen plugin/Check
identity and scope digest before hydrating state. Ownership transfer creates a
new internal epoch and fences the older Host before any mutation. A no-input
`advance_plugin_run` call is the normal synchronization path: it returns the
current durable revision, last accepted operation, workflow boundary, and
required next step without asking the Agent to provide internal identifiers.
Resume metadata is removed after terminal closeout.

Terminal publication uses recoverable two-phase files. The Host first writes a
complete pending result, then commits the terminal ledger, promotes the result
atomically, and publishes a latest-terminal pointer before removing active-Run
metadata. A replacement Host can repair a missing active pointer from one
unambiguous Run descriptor and can promote a valid pending result after a
terminal-ledger interruption. Result identity and digest mismatches fail
closed; a terminal acknowledgement is never returned before its replay route
is durable.

Terminal plugin summaries are delivered through the platform's staged-result
contract. Non-empty arrays and oversized text become stable section
references; `get_plugin_result` pages those sections with result-bound opaque
cursors. Each page is a delta and must not repeat already delivered items. The
full plugin summary is durably published as `result-summary.json`, and staging
must never remove Evidence, decisions, receipts, or failures from the ledger.
Plugins provide semantic summary content but do not own transport pagination.

The platform also derives a mandatory terminal `resultOverview` from the
ledger. It exposes conclusion validity, discovery and WorkItem coverage,
outcome counts, review and failure counts, and a next action independently of
optional plugin summary content. Decision pages preserve the committed reason
and every dimension reason. `needs_review` creates an actionable review item
from its unresolved, blocked, or conflicting Findings. A failed Run publishes
no formal decision or plugin-summary content; invalidated Decisions remain in
the immutable ledger for diagnosis and are counted only as invalidated.

## 6. Summary and release requirements

An optional plugin summary is a domain explanation derived from the canonical
result. It cannot add a new terminal state, override a decision, hide
unverified scope, or mutate the ledger. A release must include the manifest,
runtime source, scope schema, semantic-review instructions, deterministic
fixtures, conformance metadata, and every executable Agent schema required by
its interactive Checks. The first M3 gate validates registrations, manifest
compatibility, capability declarations, scope schemas, execution profiles,
Agent contract completeness, and constructed runtime interfaces before
publication. Package resource completeness and independent installer
enforcement remain mandatory gates for built-in and external packages alike.

The complete release gate must run against the exact built artifact and prove
schema/runtime equivalence, pre-plugin rejection of malformed payloads,
page-compatible checkpoint validation, finalization, replay, resume, and
terminal publication. Deterministic contract failures fail fast and cannot be
hidden by Agent retry. The complete requirements and staged implementation are
defined by the
[Plugin Development Standard v1](plugin-development-standard-v1.md).

Every isolated release fixture also passes the shared terminal result
conformance gate. The gate validates ledger/result identity, terminal-event
closure, Evidence and Decision references, latest recovery state,
authoritative receipts, completed coverage, failed-result suppression, and
artifact-to-receipt traceability. A fixture whose expected outcome happens to
match still fails release when any of these platform invariants is broken.
