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
