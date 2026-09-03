# Vertical Slice 049: Pre-persistence Review Checkpoint Validation

## Status

Implemented on 2026-09-03. Real CLI interruption acceptance remains owner-run
work and is not claimed by this slice.

## Problem

The platform validated generic WorkItem, Evidence collection, and item
identity before saving a semantic review checkpoint, but domain Finding and
Evidence-trace validation happened only when the terminal Decision was
assembled. A malformed checkpoint could therefore become durable and block
finalization much later, when overwrite would violate the append-only audit
trace.

## Platform behavior

- A plugin that uses semantic `ReviewCheckpoint` persistence must implement
  `validate_review_checkpoint`.
- The Host calls that hook after generic collection binding checks and before
  any checkpoint, operation, event, or ledger mutation.
- The hook receives the immutable selected collection items, prior accepted
  checkpoints, InvestigationPacket, Check, and PlatformContext.
- Missing validation support fails closed with
  `REVIEW_CHECKPOINT_VALIDATION_UNSUPPORTED`.
- A plugin validation exception becomes a stable platform error and leaves the
  current semantic task available for correction and retry.

This is domain-neutral: the platform owns the mandatory validation boundary;
each plugin owns the meaning of its opaque checkpoint payload.

## Spec plugin enforcement

The Spec plugin validates each page before persistence, including:

- exact one-time coverage of the checkpoint's declared candidate IDs;
- candidate existence and page-boundary references;
- unique Finding identity across accepted checkpoints;
- supported review status and status-specific fields;
- one-object confirmed Findings with severity, gap, impact, recommendation,
  closure evidence, and direct Evidence;
- direct Evidence trace to candidate source text;
- valid merge targets from the same or an earlier accepted checkpoint.

The terminal evaluator uses the same validator, avoiding a second weaker
interpretation of the Finding contract.

## Verification

- Invalid candidate references do not enter `review_checkpoints`.
- Untraceable confirmed Finding Evidence does not enter the ledger.
- Rejection through `advance_plugin_run` reoffers the same semantic boundary.
- A plugin that omits checkpoint validation cannot persist opaque review data.
- Existing valid checkpoint assembly and terminal review behavior remain
  covered by the focused interactive and Spec suites.

## Remaining J06b/M3-R work

This slice prevents invalid data from becoming the durable recovery boundary.
It does not yet implement operation-level replay after a lost response,
revision fencing, controller restart hydration, append-only superseding
corrections, or real `Esc -> continue` acceptance.
