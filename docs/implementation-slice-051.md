# Vertical Slice 051: Durable Active-Run Resume

## Status

Implemented on 2026-09-03 for the generic interactive plugin transport. Real
Codex CLI `Esc -> continue` acceptance remains owner-run evidence.

## Problem

In-process checkpoint replay does not help when interruption also restarts the
MCP or Host process. The replacement Host must reconstruct the one active Run
from durable state, advertise the exact next semantic boundary, and prevent an
older Host instance from continuing to write.

## Platform behavior

- Starting an interactive Run atomically publishes a small active-Run pointer
  and a Run-local resume descriptor containing the validated business scope,
  frozen plugin/Check identity, and capability names.
- A new interactive controller detects that pointer, verifies scope digest and
  exact plugin version, loads the canonical platform ledger, reconstructs
  WorkItems, InvestigationPackets, checkpoints, Decisions, receipts,
  operations, events, failures, and Evidence collections, then derives the
  authoritative current workflow boundary.
- Recovery fails closed when the pointer, scope digest, ledger, or installed
  plugin identity does not match. It never silently starts a replacement Run.
- The replacement controller writes a new internal owner epoch. Every mutating
  operation verifies that epoch; an older live Host is rejected with
  `STALE_RUN_OWNER` before it can mutate the ledger.
- Calling `advance_plugin_run` without semantic input synchronizes the caller
  to the current durable boundary, including `runRevision`, last operation,
  and exact required next step.
- A terminal acknowledgement can be replayed in the same active transport by
  calling `advance_plugin_run` without semantic input after a lost response.
- Terminal closeout removes active resume metadata. It does not retain the raw
  business scope in a no-longer-needed resume descriptor.

The public tool input remains business-only. Users and Agents do not provide a
Run ID, owner epoch, operation ID, or expected revision.

## Verification

- A fixture Run resumes in a new transport from the same checkpoint boundary.
- The old transport is fenced before mutation after ownership transfer.
- Replaying the accepted checkpoint after restart retains one checkpoint and
  the same durable revision.
- The real built-in Spec plugin resumes after a checkpoint and does not reoffer
  the already reviewed candidate.
- A lost terminal acknowledgement is replayed without reopening the Run.
- Active pointer and resume descriptor are absent after terminal closeout.

## Remaining J06b work

- Append-only checkpoint superseding correction was completed in Vertical
  Slice 052.
- Corruption and interruption fault injection around descriptor, ledger, and
  terminal publication boundaries was completed in Vertical Slice 053.
- Real Codex CLI acceptance at multiple `Esc` timings.
