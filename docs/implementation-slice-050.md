# Vertical Slice 050: Idempotent Semantic Operation Acknowledgements

## Status

Implemented on 2026-09-03 for the active interactive Host lifecycle. Durable
controller hydration after process restart remains part of the next J06b
slice.

## Problem

An Agent interruption can hide a successful tool response. Retrying the same
semantic checkpoint or Decision must not create a second ledger mutation or
repeat an external commit, and the Agent must not be asked to invent protocol
identifiers.

## Platform behavior

- The Host derives semantic operation identity from the Run and canonical
  submitted content; `operationId` is not part of the public MCP input schema.
- A review checkpoint's content-derived stable ID is also its semantic
  operation acknowledgement.
- A Decision receives a content-derived stable operation ID before commit.
- Checkpoint state and its operation acknowledgement are staged together and
  written through one atomic ledger replacement.
- Decision state, the commit receipt, and its operation acknowledgement are
  likewise written together.
- `runRevision` is the durable operation count. It increases only when a new
  operation is accepted and remains unchanged for an idempotent replay.
- Retrying an identical checkpoint returns the same operation ID, marks the
  response as replayed, and returns the authoritative current semantic
  boundary without another checkpoint record.
- Retrying an identical Decision returns the same operation ID and receipt
  without invoking the plugin committer again.
- A different Decision for an already committed WorkItem fails closed with
  `COMMIT_CONFLICT`.

## Verification

- A simulated lost checkpoint acknowledgement followed by the identical
  `advance_plugin_run` request keeps one checkpoint and one operation.
- Calling `advance_plugin_run` with no semantic input after that loss returns
  the same authoritative next boundary and revision.
- A repeated semantic Decision invokes a recording external committer once,
  retains one Decision and one commit operation, and reports replay without a
  revision increase.
- A changed retry is rejected before a second committer invocation.

## Remaining J06b work

This slice closes duplicate mutation inside one active Host lifecycle. The next
slice must hydrate an active Run from its durable ledger after MCP/Host restart,
return a structured stale-revision synchronization response, and make terminal
result pickup explicit. Append-only superseding correction and full
fault-injection acceptance follow that work.
