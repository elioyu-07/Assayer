# Vertical Slice 074: Frontend Canonical Result Compatibility

## Goal

Give the established frontend audit journey the same portable
`canonical-result.json` contract as generic batch and interactive plugins,
without rewriting the browser lifecycle or changing any historical frontend
artifact.

## Compatibility boundary

The validated `audit-ledger.json` remains the frontend source of truth. The
adapter derives common result semantics from that ledger after Host reference
and coverage validation has succeeded. It does not read Markdown summaries,
`issues.json`, screenshots, or Agent prose as conclusion sources.

The adapter maps:

- Scan, frontend plugin, and frozen Check identity;
- frontend objects and reachable entrypoints into common coverage units;
- Assessments into outcomes and immutable DimensionFindings into Findings;
- Finding-to-Evidence references without copying raw Evidence payloads;
- `needs_review` Assessments into actionable review items;
- unfinished or invalidated scope into unverified items;
- terminal and diagnostic failures into the common ownership vocabulary;
- the existing browser performance bill into common timing fields; and
- the exact published `audit-ledger.json` digest and relative trace names.

Page, object, entrypoint, issue, and frontend diagnostic counts are retained in
a separately validated `assayer.frontend-audit/canonical-result-extension`.
Browser-only vocabulary does not enter the generic canonical schema.

## Publication and coexistence

Frontend Runs now publish `canonical-result.json` beside the unchanged:

- `audit-ledger.json`;
- `issues.json`;
- `audit-summary.md`;
- runtime and platform ledgers, logs, diagnostics, and performance bills.

The frontend adapter owns this public canonical artifact. The generic platform
ledger remains a trace of the compatibility execution, but its terminal export
cannot replace the AuditLedger-derived result. A later observability refresh
regenerates the same canonical semantics while updating only measured timing.

## Validity and privacy

A failed frontend Scan publishes no outcomes, Findings, or review items.
Invalidated history remains available only in the ledger for diagnosis. Public
reason text redacts credential-like assignments, environment values, absolute
POSIX paths, and absolute Windows paths. Trace references are stable relative
artifact names.

Exactly one frozen Check is required per frontend Run because the common Run
identity is Check-specific. A future multi-Check frontend invocation must split
into independent Runs instead of inventing an aggregate Check identity.

## Acceptance evidence

Deterministic tests cover completed, partial, failed, issue, and
`needs_review` ledgers; Finding/Evidence closure; exact persisted-ledger digest
binding; privacy redaction; Host artifact publication; and preservation of the
existing frontend report bundle. Real CLI and Chromium acceptance remain
owner-run activities and were not executed for this slice.
