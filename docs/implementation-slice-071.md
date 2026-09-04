# Vertical Slice 071: Unified Platform Performance Bill

## Goal

Make serial and parallel platform performance understandable without coupling
the contract to browsers, frontend auditing, or any one plugin domain.

## Canonical inputs

The bill is a deterministic diagnostic view of the generic platform ledger:

- Run wall-clock measurement comes from the Kernel monotonic clock;
- Host work is the sum of recorded Operation durations;
- Provider work is measured inside the negotiated Provider boundary;
- execution mode, reason, tasks, and workers come from the parallel plan;
- parallel elapsed time and summed task work come from the scheduler; and
- Agent wait, transport, and model time remain unavailable unless their owning
  client exposes authoritative telemetry.

Batch Kernel timing ends at formal terminalization; later derived-artifact
publication is outside that measurement. Interactive clients that do not yet
provide a stable interruption-aware wall-clock measurement report it as
`not_exposed` instead of deriving a misleading number from incomplete timing.

The ledger remains authoritative. The bill cannot create Evidence, change a
Decision, alter coverage, or affect terminal status.

## Honest timing semantics

Elapsed time and summed work are different quantities. Parallel Host,
Provider, or inspection work may overlap and therefore may sum to more than
Run wall clock. The schema records the aggregation for every captured timing.

Missing timing is `not_exposed` or `partial` with a reason and no numeric
duration. Zero is reserved for a captured measurement that actually rounded
to zero milliseconds.

The scheduler computes `summed task work - parallel elapsed window` as an
estimated wait reduction. Both JSON and Markdown label it as an estimate. It
is not a measurement of end-to-end user time saved because Agent, transport,
model, startup, discovery, decision, commit, and publication time may exist
outside that window.

## Published views

Each persisted generic Run writes:

- `platform-performance-bill.json` for machines; and
- `platform-performance-bill.md` for a summary-first human view.

The existing browser Scan performance bill remains compatible and separate;
generic plugins do not inherit browser-only fields.

## Acceptance evidence

Deterministic tests prove serial fallback explanations, parallel timing and
estimate labeling, Provider telemetry visibility, missing-telemetry honesty,
domain-neutral vocabulary, schema validity, and artifact publication. Existing
parallel-versus-serial tests continue to prove equivalent Evidence and
Decisions.

## Remaining boundary

No current plugin manifest is changed to enable parallel work. Real speedup
claims remain owner-run acceptance evidence, not an inference from scheduler
estimates. Persistent or cross-process platform caching remains deferred.
