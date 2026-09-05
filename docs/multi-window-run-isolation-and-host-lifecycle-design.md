# Multi-Window Run Isolation and Host Lifecycle

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-09-04 |
| Status | Minimum lifecycle slice implemented; registry design remains pending |
| Owner | Assayer Maintainers |
| Scope | Same-user, multi-process Codex CLI and Desktop sessions on one machine |

## 1. Executive decision

Assayer must support multiple Codex windows running independent audits at the
same time. Each audit Run must have an independent state namespace and exactly
one active writer. A Host process may own a Run only through an explicit lease;
starting a Host must never silently take ownership of an existing Run.

The original implementation did not satisfy this design. It used a shared
output root and one `.active-plugin-run.json` ownership pointer. Every new
interactive controller restored the Run behind that pointer and wrote a new
`ownerEpoch`, which could fence the original Host or expose the wrong Run.
The minimum lifecycle slice described below has removed that implicit takeover
path; the broader registry and resource-admission design is not implemented.

The target design separates four lifecycles:

```text
Codex window/session
        |
    MCP connection
        |
     Host process
        |
   one or more Runs
```

The Run ledger remains the source of audit truth. A new coordination registry
owns only process/session identity, Run ownership, leases, and resource
admission. It must never decide or rewrite an audit conclusion.

The minimum implementation intentionally precedes that registry: Run-local
operating-system locks now prevent implicit takeover, independent Runs can
coexist, shutdown releases ownership, and recovery is explicit. See
[platform-run-isolation-implementation-slice.md](platform-run-isolation-implementation-slice.md).
SQLite leases, global admission, and recoverable-Run discovery remain deferred
until user-journey evidence shows they are required.

## 2. Current behavior and failure model

### 2.1 Current layout

The bundled launcher defaults every MCP process to the same parent directory:

```text
${TMPDIR}/assayer-output
```

Each Run gets a random subdirectory. Interactive recovery now uses Run-local
metadata and an operating-system lock:

```text
assayer-output/.latest-plugin-run.json
assayer-output/<run-id>/platform-resume.json
assayer-output/<run-id>/platform-owner.json
assayer-output/<run-id>/platform-owner.lock
```

Historical `.active-plugin-run.json` files may remain for migration
diagnostics, but new Runs do not write them. `InteractivePluginController`
neither reads nor follows them during startup. Explicit start or resume
acquires the Run-local writer lock, and mutating operations require the locally
held lock plus the matching owner epoch.

The transport keeps `_active_run_id` in process memory, backed by that Run-local
lock. A second transport begins with no active Run until it starts a new Run or
explicitly resumes a retained Run ID. The local `RuntimeRouter` still has its
own Scan capacity, so several independent Host processes can bypass one
another's resource limit; global admission remains future work.

### 2.2 Observable consequences

| Situation | Current consequence |
|---|---|
| Host A owns Run A; Host B starts | B may restore Run A and replace A's `ownerEpoch` |
| Host A submits after B takes over | A receives `STALE_RUN_OWNER` and cannot write |
| Host B is intended for a new target | B may see Run A as its active Run and return `RUN_CONFLICT` |
| A frontend Host starts while a Spec Run exists | Frontend process construction can still restore and re-own the Spec Run |
| Several windows run frontend audits | Random Scan directories usually isolate artifacts, but browser and CPU limits are per process, not global |
| Codex task exits unexpectedly | The MCP process may remain alive; the Run remains recoverable, but no clear owner handoff exists |

The existing fencing and idempotency rules are valuable: they normally prevent
silent double commits. They do not provide correct multi-window scheduling or
user-friendly recovery.

## 3. Goals

This design must provide:

1. Independent concurrent Runs for different targets, plugins, and Checks;
2. one active writer per Run, with stale writers rejected before mutation;
3. no automatic ownership takeover during Host startup or tool discovery;
4. explicit, safe recovery after `Esc`, connection loss, process crash, or Host restart;
5. no cross-window exposure of another Run's semantic task or business scope;
6. global resource admission across Host processes on the same machine;
7. prompt cleanup of disconnected Host processes without deleting Run artifacts;
8. compatibility with existing Run ledgers, checkpoints, receipts, and results;
9. human-readable ownership, recovery, capacity, and process diagnostics;
10. preservation of all current evidence, coverage, result, and recovery invariants.

## 4. Non-goals

This design does not introduce:

- persistent or cross-process evidence caching;
- distributed or multi-machine scheduling;
- a marketplace or plugin trust/signing system;
- login, SSO, or credential sharing between Runs;
- changes to plugin rule semantics or Agent decision authority;
- automatic screenshot redaction;
- automatic merging of two Runs that inspect the same target;
- a fixed total-duration cutoff for large audits.

Two Runs may inspect the same URL or file, but they remain separate audits. A
later Run must not reuse a prior Run's conclusion unless a future, separately
approved evidence-cache contract allows it.

## 5. Normative invariants

These invariants extend the Platform Constitution and require traceability and
schema coverage before implementation.

| ID | Invariant |
|---|---|
| MW-001 | Every Run has a unique Run identity and an isolated ledger/artifact namespace. |
| MW-002 | A Run has at most one active mutation lease at a time. |
| MW-003 | Host startup and MCP tool discovery never claim or renew an existing Run. |
| MW-004 | A Run can be claimed only by an explicit start or resume operation that passes registry, lease, identity, and revision checks. |
| MW-005 | Every mutating request is fenced by Run identity, Host session identity, lease token, and expected Run revision. |
| MW-006 | A stale, expired, or released writer may read diagnostic state but cannot mutate ledger, checkpoint, Evidence, Decision, or terminal result. |
| MW-007 | A new Run never inherits semantic state, Evidence, checkpoints, or conclusions from another Run. |
| MW-008 | A recovery operation must identify one unambiguous Run; ambiguity is returned to the user instead of guessed. |
| MW-009 | Lease expiry preserves the Run ledger and artifacts; expiry changes writability, not audit facts. |
| MW-010 | Registry failure fails closed for mutations but must not delete or invalidate a durable Run ledger. |
| MW-011 | Global resource admission is independent from plugin semantic decisions and cannot change a Decision. |
| MW-012 | A terminal Run releases its lease and cannot be resumed as an active Run. Terminal result replay remains read-only. |
| MW-013 | A disconnected Host cannot keep a lease alive without an active MCP/session liveness signal. |
| MW-014 | Registry and lease records contain no credentials, cookies, Authorization values, raw page bodies, or hidden Agent reasoning. |
| MW-015 | Existing single-window protocol aliases remain behaviorally compatible when no concurrent Run is present. |

## 6. Target architecture

```text
                    +-----------------------+
 Codex task A ----> | MCP / Host process A   | ----+
                    +-----------------------+     |
                                                    |  lease + capacity
                    +-----------------------+     v
 Codex task B ----> | MCP / Host process B   | -> Run Registry (SQLite)
                    +-----------------------+     ^
                                                    |
                    +-----------------------+     |
 Codex task C ----> | MCP / Host process C   | ----+
                    +-----------------------+

 Run A: output-root/run-A/ledger + Evidence + checkpoint
 Run B: output-root/run-B/ledger + Evidence + checkpoint
 Run C: output-root/run-C/ledger + Evidence + checkpoint
```

### 6.1 Responsibilities

| Component | Responsibility |
|---|---|
| Codex session binding | Identifies the logical client session; it is not audit authority |
| Host process | Executes tools, owns a leased Run while connected, and reports liveness |
| Run Registry | Coordinates session identity, Run state, lease ownership, fencing, and resource admission |
| Run ledger | Stores authoritative audit facts, Evidence, Decisions, recovery, events, and terminal state |
| Plugin | Owns domain discovery, Evidence organization, and semantic-review requirements |
| Agent | Supplies intent and semantic decisions; never supplies lease or fencing authority |

The registry is a coordination index, not a second ledger. It may point to a
Run's durable directory and ledger digest, but it cannot alter the conclusion.

## 7. Coordination state

The first implementation should use one per-user SQLite database at the
validated Assayer output root, with file permissions restricted to the user:

```text
assayer-output/.assayer-run-registry.sqlite3
```

The database is coordination metadata only. SQLite transactions provide atomic
claim, heartbeat, release, and capacity checks across processes on one machine.

### 7.1 `host_sessions`

| Field | Meaning |
|---|---|
| `session_id` | Random logical MCP/Codex connection identity |
| `host_id` | Random Host process identity |
| `client_kind` | `cli`, `desktop`, or `diagnostic` |
| `process_id` | Diagnostic PID, when available; never authority by itself |
| `started_at` | Session start time |
| `last_seen_at` | Last valid liveness update |
| `status` | `active`, `disconnected`, `expired` |
| `runtime_version` | Safe release identity for diagnostics |

### 7.2 `runs`

| Field | Meaning |
|---|---|
| `run_id` | Immutable Assayer Run identity |
| `plugin_id`, `plugin_version` | Frozen plugin identity |
| `check_id`, `check_version` | Frozen Check identity |
| `scope_digest` | Digest of validated business scope; not raw scope text |
| `run_path` | Contained relative Run directory |
| `ledger_digest` | Latest known ledger digest for reconciliation |
| `state` | `starting`, `running`, `awaiting_agent_decision`, `recoverable`, `completed`, `partial`, `failed` |
| `created_at`, `updated_at` | Registry timestamps |
| `terminal_at` | Terminal timestamp, if any |
| `resource_profile` | Negotiated resource class and reservation summary |

### 7.3 `run_leases`

| Field | Meaning |
|---|---|
| `run_id` | One-to-one active lease target |
| `host_id`, `session_id` | Current writer identity |
| `fencing_token` | Monotonically increasing token for this Run |
| `claimed_at` | Claim timestamp |
| `heartbeat_at` | Last accepted heartbeat |
| `expires_at` | Lease deadline |
| `released_at` | Explicit release timestamp, if released |
| `release_reason` | `terminal`, `disconnect`, `expired`, `replaced`, or `shutdown` |

### 7.4 `resource_reservations`

Reservations are optional for non-browser Runs but required for real browser
capacity. They record a Run, resource class, requested units, granted units,
and release time. They do not record raw URLs, credentials, or page content.

## 8. Run lifecycle

### 8.1 Host and MCP startup

1. Host creates a `host_session` and verifies the registry schema/version.
2. Host registers capabilities and available resource classes.
3. Host does not scan for an active Run and does not write any Run Owner.
4. Host may list recoverable Runs only through a read-only operation.
5. A malformed or ambiguous legacy pointer is reported as a diagnostic, not
   silently repaired by claiming a Run.

### 8.2 Starting a new Run

`start_plugin_run` always creates a new Run unless the same idempotency key
already maps to the same start request.

```text
validate plugin/check/scope
  -> reserve global capacity if needed
  -> create Run directory atomically
  -> insert registry Run row
  -> claim lease with fencing token 1
  -> write Run-local resume descriptor
  -> start deterministic/plugin work
```

The operation must not inspect or reuse another Run's semantic state. If
capacity is unavailable, it returns a retryable capacity result without
creating a partial Run.

### 8.3 Normal operation

Every mutating operation carries an internal context assembled by the Host:

```text
runId + hostId + sessionId + fencingToken + expectedRunRevision + operationId
```

The Agent does not invent these values. The registry checks lease ownership;
the Run ledger checks revision and idempotency; the Host performs the domain
operation only after both checks pass.

Heartbeats are sent while the MCP connection is live, including during long
Agent reasoning. Heartbeats renew liveness but do not change the Run revision
or audit content.

### 8.4 Explicit resume

“Continue” must map to a Host-generated opaque Run handle retained by the
conversation or to an unambiguous recent Run selected by the platform. The
Agent must not guess a filesystem path, Run ID, or lease token.

```text
resolve Run handle
  -> read registry and Run ledger
  -> if current lease is live: route to that Host or report waiting
  -> if lease is expired/released: atomically increment fencing token
  -> verify plugin/check/scope/ledger identity
  -> bind new Host session
  -> return authoritative phase, revision, and exact next action
```

If multiple Runs match a natural-language “continue” request, the platform
returns a short disambiguation list containing safe labels such as plugin,
target basename, creation time, and status. It must not guess.

### 8.5 Connection loss and Host shutdown

On normal MCP EOF or shutdown:

1. stop accepting new mutations;
2. flush runtime events and the latest ledger digest;
3. release browser/provider resources;
4. release or mark the Run lease according to whether a durable recovery
   boundary exists;
5. mark the Host session disconnected;
6. exit the MCP process.

On a crash, no cleanup is trusted. The lease expires naturally. The Run ledger
and checkpoint remain intact, and the next explicit resume performs ownership
transfer. A stale process cannot regain the lease merely by sending a late
heartbeat.

### 8.6 Terminal Run

Terminalization is a transaction across the Run ledger and registry:

```text
validate coverage/evidence/decision gates
  -> persist terminal ledger and canonical result
  -> record terminal registry state
  -> release lease and resource reservations
  -> remove only Run-local active metadata
```

Terminal result replay is read-only. A terminal Run cannot become active again;
a user retry creates a new independent Run.

### 8.7 Legal state transitions

| Current state | Trigger | Next state | Guard |
|---|---|---|---|
| absent | accepted start | `starting` | Scope, plugin, Check, capacity, and idempotency are valid |
| `starting` | initialization succeeds | `running` | Run directory, registry row, lease, and initial ledger agree |
| `starting` | initialization fails | `failed` or absent | No formal conclusion; partial creation is reconciled or removed safely |
| `running` | semantic input required | `awaiting_agent_decision` | Deterministic work reached a durable semantic boundary |
| `awaiting_agent_decision` | valid checkpoint/Decision accepted | `running` | Lease, fencing token, revision, Evidence, and idempotency all pass |
| `running` or `awaiting_agent_decision` | connection/Owner lost | `recoverable` | Durable boundary exists and Run is not terminal |
| `recoverable` | explicit resume succeeds | `running` or `awaiting_agent_decision` | Old lease is inactive; identity and ledger reconcile; fencing token increments |
| non-terminal | terminal gates pass | `completed` or `partial` | Coverage and publication requirements determine the exact terminal state |
| non-terminal | unrecoverable failure | `failed` | Diagnostics are durable and formal conclusions are suppressed |
| terminal | read/replay | unchanged | No mutation or lease claim is allowed |

Illegal transitions fail before mutation and return the authoritative state and
next action. A registry timestamp, process existence, or newly started Host is
never sufficient to cause a state transition.

## 9. Protocol surface

The normal product MCP should retain a small surface:

```text
start_plugin_run
  -> advance_plugin_run*
  -> expand_evidence_collection* (bounded reference reads only)
  -> get_plugin_progress / get_plugin_result
  -> recover_work_item (when domain recovery is required)
  -> terminal result
```

The following platform behaviors are required; exact names may be finalized in
the protocol review:

| Behavior | Purpose |
|---|---|
| `list_recoverable_runs` | Read-only, safe summaries for an explicit Continue request |
| `resume_plugin_run` | Atomically claim an expired Run with a new fencing token |
| session heartbeat | Maintain Host/session liveness without changing audit state |
| `release_plugin_run` | Explicitly stop ownership while retaining recoverable state |
| `get_host_status` | Read-only process, lease, capacity, and version diagnosis |

These are not user-facing fields. The Skill and Host assemble protocol
identifiers internally. Users should continue to provide only intent and
business input.

The legacy frontend names remain compatibility aliases. They must bind to the
Run associated with their current session and may not discover or mutate a
different active Run.

## 10. Resource admission and parallelism

The existing plugin `executionProfile.parallelism` governs independent WorkItem
inspection inside one Run. It does not govern multiple Runs.

The registry adds a separate machine-level admission policy:

- maximum active browser contexts;
- maximum active Host resource units;
- optional CPU/memory class limits;
- per-plugin or capability-provider reservation limits when required;
- deterministic queue/retry response when capacity is full.

Admission is checked before Run creation or before a Run requests a new resource.
Releasing one Run's reservation must never release another Run's lease. Resource
limits protect the machine but cannot downgrade a valid audit to a pass.

## 11. Migration and compatibility

Migration must be recoverable and must not delete existing artifacts.

### 11.1 Legacy pointer import

On first startup after upgrade:

1. create the registry database atomically;
2. inspect `.active-plugin-run.json` and each Run-local descriptor read-only;
3. validate Run identity, plugin version, scope digest, and ledger status;
4. import one valid legacy active Run as `recoverable` with no active lease;
5. mark ambiguous, malformed, or multiple active descriptors as diagnostics;
6. retain legacy files until the corresponding Run reaches terminal state or an
   explicit cleanup command confirms they are no longer needed.

The importer must never assign a new Owner solely because a Host started.

### 11.2 Existing outputs

Current Run directories, `platform-ledger.json`, checkpoints, professional
frontend artifacts, and `canonical-result.json` remain readable. Registry rows
refer to contained relative paths and source-ledger digests. Historical Runs
are never reinterpreted by a newer plugin or Check version.

### 11.3 Compatibility failure

If the registry cannot reconcile a legacy Run, the platform returns:

```text
recoverable state exists, but ownership is unverified
required action: explicit resume or a fresh independent Run
```

It must not report a formal conclusion and must not silently start a replacement
Run under the old identity.

## 12. Failure and race matrix

| Failure or race | Required result |
|---|---|
| Two Hosts claim the same live Run | One transaction succeeds; the other receives a recoverable ownership conflict |
| Old Host writes after lease replacement | Reject before mutation with `STALE_RUN_OWNER` or equivalent fencing error |
| Heartbeat arrives after expiry | Reject; it cannot revive the old lease |
| Host crashes after ledger write, before response | New resume replays the durable operation acknowledgement; no duplicate record |
| Host crashes before ledger write | New resume reports the prior durable boundary; operation can be retried safely |
| Registry is locked or unavailable | Fail closed for mutation; preserve ledger and provide retry guidance |
| Two independent Runs request capacity | Admit both if limits permit; otherwise queue/reject one without changing either result |
| One Run reaches `failed` | Release only its lease/resources; other Runs continue |
| User says “continue” with two matching Runs | Ask for safe disambiguation; never guess |
| MCP process remains after connection loss | Session/lease expires and the process supervisor records a stale-process diagnostic |
| Terminal publication partially fails | Keep Run non-terminal until ledger/result/registry reconciliation succeeds |

## 13. Observability requirements

The Run diary and structured event stream must expose ownership without leaking
secrets. Add these event families:

- `host.session.started`, `host.session.heartbeat`, `host.session.disconnected`;
- `run.lease.claimed`, `run.lease.renewed`, `run.lease.rejected`,
  `run.lease.expired`, `run.lease.released`;
- `run.resume.requested`, `run.resume.ambiguous`, `run.resume.succeeded`,
  `run.resume.rejected`;
- `run.capacity.requested`, `run.capacity.granted`, `run.capacity.deferred`,
  `run.capacity.released`;
- `host.process.shutdown`, `host.process.stale`, `host.process.cleanup`.

Every ownership event includes:

```text
runId, hostId, sessionId, fencingToken (when known), registry revision,
lease deadline, reason, and safe diagnostic references
```

The human diary must explain, in order:

1. which Host/session started or resumed the Run;
2. whether another Owner existed;
3. why a claim was accepted, rejected, or deferred;
4. the durable boundary at interruption;
5. who resumed the Run and what the next action was;
6. when the lease and resources were released.

Performance bills must keep multi-Run scheduling time separate from Agent wait,
Host work, provider work, browser time, and in-Run parallel inspection.

## 14. Security and privacy

- The registry is user-private and created with restrictive permissions.
- Only digests and safe labels are stored in coordination records; raw scope
  may remain in the Run-local protected descriptor where the existing contract
  requires it.
- PID is diagnostic metadata, never proof of ownership; processes can be reused.
- Fencing tokens are unguessable or monotonic under an atomic registry
  transaction and are never supplied by the Agent.
- A registry or Host diagnostic must not expose credentials, cookies, raw page
  bodies, Authorization headers, or hidden model reasoning.
- Registry cleanup removes only expired coordination rows after their Run
  artifacts and terminal/recovery state are preserved.

## 15. Alternatives considered

| Alternative | Decision | Reason |
|---|---|---|
| Give every Codex window a different output root | Transitional diagnostic only | It avoids collisions but makes users/configuration own an internal concern, fragments recovery, and cannot enforce machine-wide capacity. |
| Keep one global active-Run pointer and add more fields | Rejected | A singleton pointer cannot represent several independent active Runs. |
| Let the newest Host always take ownership | Rejected | Startup order is not user intent or proof that the prior Owner is dead. |
| Use PID existence as ownership | Rejected | PID reuse, hung processes, and live-but-disconnected tasks make it neither durable nor authoritative. |
| Use only per-Run OS file locks | Accepted for the minimum slice, not the final contract | Locks now serialize one Run's live writers and survive process crashes by automatic release. They do not provide durable lease history, session identity, or global capacity; a registry remains optional future work. |
| Kill all old MCP processes before every audit | Rejected | It breaks legitimate parallel tasks and treats process cleanup as user workflow. |
| Reuse `RuntimeRouter.max_runtimes` | Insufficient | The limit applies inside one Host process and is bypassed by multiple Codex windows. |
| Run one permanent Assayer daemon | Deferred | It could centralize ownership, but introduces service installation, upgrade, availability, and security work beyond the current local stdio product. The SQLite registry provides the required single-machine coordination first. |

The selected design preserves the current Codex-managed stdio MCP model while
adding the minimum durable coordination needed across those processes.

## 16. Implementation sequence

This change should be delivered as platform slices, each with its own contract
and evidence. No plugin-specific workaround is sufficient.

### Slice A — Specify and instrument the boundary (implemented)

- Freeze MW invariants and protocol names.
- Add process/session/Run ownership fields to schemas and observability.
- Add race fixtures that reproduce the current shared-pointer takeover.
- Record the minimum implementation evidence in
  [platform-run-isolation-implementation-slice.md](platform-run-isolation-implementation-slice.md).

**Exit gate:** the current failure is deterministic and every new field has a
traceability entry.

### Slice B — Stop implicit takeover (implemented)

- Make Host startup read-only with respect to existing Runs.
- Move active ownership checks from the parent-level pointer to Run-local state.
- Keep legacy pointer import diagnostic-only until explicit resume.
- Ensure a new `start_plugin_run` cannot bind an existing Run.

**Exit gate:** opening a second Codex window cannot fence the first Host or
change its Run Owner without an explicit resume.

### Slice C — Add the cross-process Run Registry

- Add SQLite schema, atomic transactions, and file-lock/error handling.
- Register Host sessions and Run rows.
- Implement lease claim, heartbeat, release, expiry, and fencing tokens.
- Bind every mutation to registry lease plus ledger revision.

**Exit gate:** deterministic two-process races prove one writer wins, stale
writers cannot mutate, and independent Runs do not conflict.

### Slice D — Explicit resume and process lifecycle (minimum implemented)

- Add explicit resume and MCP EOF/shutdown cleanup.
- Read-only recoverable-Run discovery, stale-process diagnostics, and
  disambiguation remain pending.
- Preserve artifacts across crash and lease expiry.
- Add safe disambiguation for multiple recoverable Runs.

**Exit gate:** clean CLI trials cover normal completion, `Esc -> continue`,
crash/restart, duplicate replay, stale revision, and ambiguous selection.

### Slice E — Global resource admission and migration

- Add cross-process browser/resource reservations.
- Import valid legacy pointers without automatic ownership transfer.
- Add capacity queue/retry behavior and machine-level observability.
- Validate frontend compatibility aliases and generic plugin lifecycle together.

**Exit gate:** multiple independent CLI Runs complete concurrently within a
bounded resource budget, and pre-upgrade artifacts remain recoverable.

## 17. Acceptance matrix

| ID | Scenario | Required evidence |
|---|---|---|
| MW-UAT-01 | Two CLI windows start different Spec files | Both create independent Runs and proceed without `RUN_CONFLICT` |
| MW-UAT-02 | CLI window A runs Spec while window B starts frontend audit | Neither Run changes the other's Owner or semantic task |
| MW-UAT-03 | Two Hosts attempt the same Run resume | Exactly one lease claim succeeds; the loser receives actionable state |
| MW-UAT-04 | Host A is interrupted after a checkpoint | Host B explicitly resumes the same Run without duplicate checkpoint or Decision |
| MW-UAT-05 | Old Host submits after B takes over | Mutation is rejected before persistence; ledger digest is unchanged |
| MW-UAT-06 | Host process receives MCP EOF | Browser/resources close, lease releases or expires, artifacts remain |
| MW-UAT-07 | Three independent Runs exceed browser capacity | Admission is bounded and the deferred Run reports a retryable next action |
| MW-UAT-08 | Upgrade with a legacy active pointer | Import is read-only/recoverable; no automatic Owner theft occurs |
| MW-UAT-09 | Two matching “continue” requests exist | Platform asks for safe disambiguation and never resumes the wrong Run |
| MW-UAT-10 | Terminal Run is replayed from another window | Result is read-only and cannot be reopened or mutated |

The full user-journey gate must still prove `completed`, `partial`, and
`failed` behavior, valid `canonical-result.json`, no duplicate Evidence or
Decision records, and no secrets in artifacts. Multi-window success cannot
replace the existing clean CLI and release-lifecycle gates.

## 18. Open decisions before implementation

The following choices should be accepted in a short design review before Slice
A is coded:

1. **Registry location:** per-user Assayer output root (recommended for the
   current single-machine product) versus a per-user background service.
2. **Run handle:** conversation-bound opaque handle (recommended) versus a
   user-visible short Run label for explicit disambiguation.
3. **Lease duration:** fixed baseline with heartbeat extension (recommended)
   versus capability-specific lease policies.
4. **Resource policy:** browser-only global quota first (recommended) versus
   all provider classes in the first registry release.
5. **Legacy support window:** retain parent-level pointers for one release
   (recommended) and remove them only after migration evidence exists.

Until these decisions are accepted, implementation is not ready. In particular,
do not patch individual `STALE_RUN_OWNER` messages, kill processes as a normal
workflow, or add per-plugin output-directory rules: those approaches hide the
shared lifecycle defect and will fail again with another plugin or client.
