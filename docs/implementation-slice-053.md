# Vertical Slice 053: Interruption and Terminal Publication Fault Injection

## Status

Implemented and accepted on 2026-09-03 for the generic interactive plugin
lifecycle. After installing bundle `0.1.0+codex.20260903082017`, the owner ran
the real Codex CLI flow and reported that the result was acceptable. Exact
interruption timing telemetry was not supplied and is not inferred here.

## Problem

Resume logic that passes only ideal-path tests can still lose the final
acknowledgement. The previous closeout sequence committed a terminal ledger
before writing the complete result. A failure in that gap left a terminal Run
without a replayable formal result. A missing active pointer could also strand
an otherwise valid Run descriptor and ledger.

## Platform behavior

- Complete terminal content is first written atomically to
  `result-summary.pending.json` while the canonical ledger remains running.
- The Host then commits the terminal ledger. The in-memory Run rolls back to
  its prior running state if that ledger save fails.
- After terminal ledger persistence, the pending result is atomically promoted
  to `result-summary.json`. A failure here retains the active pointer and
  descriptor; a retry or replacement Host validates and promotes the pending
  result.
- The Host publishes `.latest-plugin-run.json` before removing active resume
  metadata. Failure to publish that pointer is returned as an error, not a
  successful terminal acknowledgement. The retained active pointer repairs the
  terminal replay route on the next synchronization.
- A new transport reconstructs the staged terminal result and result paging
  from the canonical result artifact, so a lost terminal response survives a
  Host restart.
- If the active pointer is missing but exactly one Run-local resume descriptor
  exists, the Host validates and reclaims that Run. Multiple orphan descriptors
  fail closed rather than selecting one implicitly.
- Corrupt descriptors, result identity mismatches, and result digest mismatches
  fail without mutating canonical ledger history.

## Deterministic fault evidence

- Result publication failure leaves the ledger running and retryable.
- Terminal ledger save failure restores in-memory status and retains a valid
  pending result for retry.
- Interruption between terminal ledger commit and result promotion recovers
  from the pending result.
- Interruption before latest-terminal pointer publication is repaired from the
  retained active metadata.
- Missing active pointer is repaired from one unambiguous descriptor.
- Corrupt resume and terminal-result data fail closed without ledger mutation.
- Lost terminal acknowledgement is replayed by a new transport, including
  bounded result-detail paging.

## Release and acceptance record

- Coherent bundle `0.1.0+codex.20260903082017` containing Slices 049-053 was
  built, passed isolated launcher verification, and was reinstalled from the
  local personal marketplace on 2026-09-03.
- Owner-run Codex CLI acceptance was reported as successful and acceptable on
  2026-09-03.
- No detailed timing trace or individual interruption-boundary transcript was
  supplied, so this record makes no finer-grained performance claim.
- J06b is closed for this milestone; future regressions remain guarded by the
  deterministic fault suite and must be rechecked during release acceptance.
