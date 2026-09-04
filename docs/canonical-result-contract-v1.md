# Assayer Canonical Audit Result Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-03 |
| Status | Frozen for M2; schema/install enforcement tracked by M3 |
| Owner | Assayer maintainers |

The canonical result is the portable, domain-neutral result of one platform
Run. It is derived from the immutable ledger and is the only input permitted
to report adapters. `audit-summary.md`, JSON views, logs, and plugin summaries
are presentations of this result, not competing truths.

## Required meaning

- `status` is `completed`, `partial`, or `failed`;
- `conclusionValidity` is `valid` or `invalidated`;
- `coverage` explains discovered, processed, skipped, unprocessed, and
  incomplete WorkItems or scope units;
- `outcomes` contains one result per committed WorkItem/Check;
- `findings`, `needsReview`, `unverified`, and `failures` preserve actionable
  detail without inventing facts;
- `performance` separates Agent waiting, transport, Host, provider, and other
  measured time; unavailable telemetry is explicit;
- `trace` points to the Run ledger and event stream;
- `domainExtension` is plugin-owned, JSON-compatible, and cannot override
  common fields or terminal validity.

An invalidated result may be displayed for diagnosis but must not be presented
as a valid audit conclusion. A failed result cannot publish formal outcomes.
The result contract does not impose a maximum wall-clock duration on a Run;
long-running work remains valid when progress and intermediate checkpoints are
observable.

The generic `platform-performance-bill.json` diagnostic view carries richer
visibility and aggregation labels. A result adapter maps `captured` bill
timings to `measured`; `partial` and `not_exposed` map to `unavailable` with
their reason. Estimate-only scheduler wait reduction belongs in performance
details and must never be mapped to a measured canonical timing.

The platform now derives `canonical-result.json` directly from the terminal
platform ledger. Plugins do not construct common result fields. The derived
artifact maps committed Decisions and receipts to outcomes, Findings to their
InvestigationPacket Evidence references, unresolved Findings to review items,
unfinished applicable WorkItems to unverified items, and recorded failures to
conservative ownership and retry declarations. A failed Run suppresses formal
outcomes and Findings while retaining invalidated history only in the ledger.

The trace includes the exact SHA-256 of the persisted deterministic ledger
bytes. Loading a historical terminal result validates its schema, Run identity,
status, and ledger digest before returning it. References are stable relative
artifact names rather than local absolute paths.

Public Decision, Finding, failure, and extension text is sanitized.
Secret-like assignments, environment values, and absolute local paths cannot
be copied from a plugin or exception into the portable result.

The legacy frontend journey follows the same public contract through a
compatibility adapter over its validated terminal `audit-ledger.json`.
Frontend Assessments become outcomes, DimensionFindings retain their Evidence
references, and unfinished object or entrypoint scope becomes unverified.
Failed Scans suppress all formal history. Page, object, entrypoint, issue, and
browser diagnostic counts remain in a validated namespaced extension instead
of adding frontend vocabulary to the common schema. The existing frontend
reports remain unchanged and the canonical trace binds the exact published
AuditLedger bytes.

Plugin summaries remain separate derived views. A namespaced
`domainExtension` is optional and cannot replace or override status, validity,
coverage, outcomes, Findings, failures, performance, or trace. Automatic
plugin-summary promotion into that field remains disabled until the plugin
declares a separately validated extension schema and privacy contract.
