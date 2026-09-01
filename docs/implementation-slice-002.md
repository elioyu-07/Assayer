# Vertical Slice 002: Read-Only Page Discovery

## Deliverables

- `ReadOnlyPageAdapter` exposes only `observe(pageStateId)`;
- `inspect_page` validates the current PageState and observes only the active state;
- Host persists immutable PageState, Entrypoint, and PageCandidate entities;
- Candidates have independent IDs, location digests, and potential rules but are not AuditObjects;
- Reads reuse a persisted snapshot and do not increment `runRevision`;
- Adapter errors, stale PageStates, and schema-invalid output fail closed;
- Missing production browser login adapters fail explicitly without fixture fallback;
- Operation results persist across restart and idempotent retry.

This slice creates no formal AuditObject and performs no click, input, navigation, or network action. Slice 003 adds unique `inspect_object(candidateId)` validation and promotion.
