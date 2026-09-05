# Implementation Slice 076 — Generic Evidence-Graph Projection

Status: implemented

## Purpose

Connect the Spec review flow to the platform-owned evidence graph without
moving Spec semantics into the platform or replacing the existing review
protocol.

## Contract

- Every plugin candidate remains addressable by its original candidate ID.
- Grouping is conservative: only exact work-item, check, version, and source
  fingerprint matches may share a root-cause group.
- The adapter never performs fuzzy or semantic matching and never deletes a
  candidate from the plugin payload.
- Final review is rejected when any candidate remains pending.
- Review status is projected to generic dispositions (`confirmed`,
  `suppressed`, `merged`, `needs_review`) for platform coverage accounting.
- A root-cause group may expose all checklist dimensions touched by its member
  decisions; domain meaning remains owned by the plugin and Agent review.

## Compatibility

The existing Spec `candidateFindings`, checkpoint payloads, and result delivery
fields remain unchanged. `candidateGraph` is additive packet metadata and
`candidate_graph` is additive finalized-review metadata.

## Verification

- Adapter tests cover exact grouping, member preservation, multi-dimension
  projection, and pending-candidate coverage.
- Existing Spec plugin tests remain green.
- Repository resilience scan and `git diff --check` pass.

## Explicit deferrals

- No fuzzy duplicate detection.
- No change to Spec semantic rules or finding counts.
- No cross-document grouping in this slice.
