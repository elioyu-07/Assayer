# Vertical Slice 073: Portable Canonical Result

## Goal

Give every generic audit plugin one portable terminal result that CLI clients,
CI gates, report systems, and future ecosystem tools can consume without
knowing plugin-specific result structures.

## Ledger-only derivation

`canonical-result.json` is derived only from the terminal platform ledger.
Plugins cannot write or override its common fields. The adapter maps:

- frozen Run, plugin, and Check identity;
- applicable, processed, skipped, and unprocessed WorkItem coverage;
- committed Decisions and authoritative receipts to outcomes;
- Findings to the Evidence references carried by matching investigated
  dimensions;
- unresolved, blocked, and conflicted Findings to actionable review items;
- unfinished applicable WorkItems to unverified items;
- failures to conservative ownership and retry declarations;
- the unified performance bill to measured or explicitly unavailable timing;
  and
- stable relative ledger, event, and diagnostic references.

All public free-text fields are sanitized before publication. Credential-like
assignments, environment-style values, and absolute local paths are redacted;
the canonical ledger retains the controlled diagnostic history.

A failed Run invalidates and suppresses formal outcomes, Findings, and review
items. Its ledger may retain invalidated history for diagnosis.

## Exact trace binding

The canonical result records the SHA-256 of the exact deterministic bytes
persisted as the platform ledger. Result replay validates:

- canonical-result schema conformance;
- Run identity;
- terminal status; and
- the persisted ledger digest.

A missing, edited, or detached result fails closed instead of being shown as a
valid historical audit.

## Publication

Terminal `JsonPlatformLedgerStore` persistence writes the canonical artifact
for both batch and interactive Runs. Batch summaries and interactive terminal
responses expose only its relative filename. They do not expose an absolute
local path.

Interactive plugin summaries remain in the existing staged result artifact.
The optional canonical `domainExtension` surface is supported by the adapter,
but arbitrary plugin summaries are not promoted automatically. That requires
a separately declared extension schema and privacy gate so plugin data cannot
leak paths or sensitive runtime values.

## Acceptance evidence

Deterministic tests prove schema validity, completed and failed semantics,
Finding-to-Evidence derivation, optional extension isolation, exact persisted
ledger digest binding, detached-result rejection, batch artifact publication,
and interruption-safe interactive terminal replay.

## Follow-on boundary

Vertical Slice 074 adds the legacy frontend adapter while preserving its
historical Scan reports. Real CLI/browser acceptance and measured performance
claims remain owner-run activities.
