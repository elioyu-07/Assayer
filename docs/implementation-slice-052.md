# Vertical Slice 052: Append-Only Review Checkpoint Correction

## Status

Implemented on 2026-09-03 for the generic interactive plugin lifecycle. Real
Codex CLI interruption acceptance remains owner-run evidence.

## Problem

A durable checkpoint cannot be overwritten without destroying audit history,
but an accepted semantic record may still require correction before the final
WorkItem Decision. Treating every historical record as current would duplicate
coverage and could assemble the obsolete payload into the formal result.

## Platform behavior

- `reviewCheckpoint.supersedesCheckpointId` identifies an append-only
  correction. The Host continues to own checkpoint and operation identity.
- The target must exist and be the current effective leaf for the same Run,
  WorkItem, frozen Check version, Evidence collection, and exact item IDs.
- Unknown targets, cross-scope corrections, changed item coverage, stale
  branches, and corrections after Decision commit fail before ledger mutation.
- The canonical ledger retains the original and every accepted correction.
  Coverage, semantic paging, plugin validation context, decision assembly,
  progress, and finalization use only effective unsuperseded leaves.
- The replaced checkpoint is excluded from the plugin's prior semantic
  decisions. This permits a correction to retain the same domain Finding
  identity without falsely triggering duplicate-identity validation.
- Identical correction retries resolve to the same content-derived operation
  ID and do not append a second record.
- Active-Run hydration validates and reconstructs the full correction chain;
  the resumed boundary does not reoffer items covered by its effective leaf.

## Verification

- A correction leaves two immutable ledger records but one effective record.
- Formal decision assembly consumes the corrected payload and excludes the
  replaced payload.
- Unknown, wrong-scope, wrong-item, and already-superseded targets fail without
  adding records.
- Exact correction replay retains one correction operation and revision.
- A replacement Host resumes from the effective corrected boundary.
- The built-in Spec plugin can reuse the replaced Finding identity in the
  corrected semantic payload.

## Remaining J06b work

- Fault injection around active pointer, resume descriptor, ledger, checkpoint
  acknowledgement, and terminal result publication boundaries.
- Package/build verification followed by owner-run Codex CLI acceptance at
  multiple interruption timings.
