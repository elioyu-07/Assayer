# Vertical Slice 058: Platform-Owned Result Experience

## Goal

Guarantee that every plugin Run ends with an understandable, actionable result
even when the plugin supplies no custom summary.

## Problem

The generic terminal transport already paged large output, but its Decision
items contained only WorkItem identity and result state. The durable Decision
reason and dimension reasons were omitted. Optional plugin summaries could be
clear, vague, or absent, so the platform did not uniformly explain coverage,
`needs_review`, failures, conclusion validity, or the next action.

A failed interactive Run could also include earlier committed Decisions in its
public terminal payload. Although the canonical ledger correctly retained that
history, the result view could make invalidated conclusions look usable.

## Platform behavior

Every generic terminal result now includes `resultOverview` with:

- terminal status and conclusion validity;
- whether discovery finished;
- discovered, inspected, decided, failed, and unprocessed WorkItem counts;
- all five common outcome counts;
- needs-review, failure, and invalidated-Decision counts;
- a concise status explanation and plain-language next action.

Decision detail pages now retain Check identity, the committed Decision reason,
and a map of every dimension's status and reason.

Each `needs_review` Decision creates a separate paged review item containing:

- WorkItem and Check identity;
- the Decision reason;
- each unresolved, blocked, or conflicting dimension and its concrete reason;
- the action required to obtain a new evidence-backed Decision.

## Failure boundary

A failed Run reports `conclusionValidity=invalidated`, zero public outcome
counts, and the number of Decisions invalidated by the terminal failure.
Earlier Decisions and optional plugin summary content are omitted from the
formal result view. They remain immutable in `platform-ledger.json` for
diagnosis and historical integrity.

Partial Runs preserve valid recorded Decisions while disclosing incomplete
discovery, unprocessed WorkItems, and failures. A terminal Run is immutable, so
all remediation guidance directs the caller to start a new Run rather than
mutating the closed result.

## Contract and evidence

- `plugin-result-overview.schema.json` validates the platform overview.
- Completed-result tests prove complete coverage, valid outcome counts, and
  retained Decision and dimension reasons.
- Needs-review tests prove concrete gap and next-action delivery.
- Failed-result tests prove invalidation, public Decision suppression, and
  continued ledger retention.
- Partial-result tests prove incomplete discovery is disclosed.
- Terminal replay continues to return the exact original result payload.

## Acceptance boundary

This slice closes the generic J05 implementation gap. Real CLI acceptance for
completed, partial, failed, and needs-review presentation remains owner-run and
is not replaced by deterministic tests. No real CLI or browser audit was
started by this slice.
