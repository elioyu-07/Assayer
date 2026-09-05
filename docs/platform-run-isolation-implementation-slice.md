# Platform Run Isolation — Implementation Slice A/B/D

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-04 |
| Status | Implemented; minimum lifecycle slice |
| Scope | Interactive plugin Runs on one machine |

## Delivered

This slice closes the first correctness defect in the multi-window design:

- Host construction and tool discovery are read-only. They never restore or
  claim the Run behind the legacy parent pointer.
- Ownership is Run-local (`<runId>/platform-owner.json`) and protected by an
  operating-system advisory lock (`<runId>/platform-owner.lock`). Independent
  Runs therefore do not compete through one global pointer.
- `resume_plugin_run` is an explicit recovery operation. It requires the
  opaque `runId` returned by `start_plugin_run`; it does not accept plugin,
  Check, scope, or protocol fields from the user.
- A second live Host cannot resume a Run already held by another Host. It gets
  `RUN_ALREADY_ACTIVE` before any ledger mutation.
- `close()` releases the writer lock while retaining the Run ledger,
  checkpoints, Evidence, and resume descriptor for a later explicit resume.
- Terminal completion releases ownership and removes only transient resume
  metadata; terminal result replay remains read-only.
- New Runs no longer write the parent `.active-plugin-run.json` pointer.
  Historical copies may still be inspected by migration diagnostics, but are
  never consulted for ownership or startup recovery.

## Compatibility boundary

The old implicit-recovery tests were changed to close the first Host and call
`resume_plugin_run` explicitly. Existing terminal replay through
`advance_plugin_run` remains available when the same Host connection is still
alive. The public product transport exposes the new resume operation alongside
the existing lifecycle tools.

## Verification

- 2-process race: exactly one simultaneous resume succeeds; the other receives
  `RUN_ALREADY_ACTIVE`.
- Host B construction and `list_tools()` leave Host A's owner metadata and
  writable Run unchanged.
- Host B can start an independent Run while Host A continues its own Run.
- Checkpoint, terminal-publication, Spec restart, and transport tests pass
  with explicit resume semantics.
- Repository fast suite passes, including resilience and English-text gates.

## Deliberately deferred

This slice does not add a SQLite Run registry, lease expiry/heartbeat,
recoverable-Run listing, global resource admission, or process-management UI.
Those remain later only if journey acceptance demonstrates the need.
