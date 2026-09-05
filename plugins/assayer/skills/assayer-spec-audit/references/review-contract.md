# Canonical Spec review contract

The Host exposes required review collections in this order:
`candidate-findings`, conditional `cross-document-relationships`, and
`checklist-dimensions`. Checkpoint every returned page by calling
`advance_plugin_run` with one `reviewCheckpoint`.

For `candidate-findings`, the payload contains only the decisions for the
returned candidate IDs:

```json
{
  "workItemId": "<work-item-id>",
  "collectionId": "candidate-findings",
  "itemIds": ["<candidate-id>"],
  "payload": {
    "decisions": [
      {
        "finding_id": "<finding-id>",
        "status": "SUPPRESSED",
        "candidate_ids": ["<candidate-id>"],
        "review_note": "<reason>"
      }
    ]
  }
}
```

For `checklist-dimensions`, the payload contains the CHK decisions for the
returned CHK IDs in the same order:

```json
{
  "workItemId": "<work-item-id>",
  "collectionId": "checklist-dimensions",
  "itemIds": ["CHK-01"],
  "payload": {
    "checklist_review": [
      {
        "check_id": "CHK-01",
        "status": "PASS",
        "note": "<evidence-backed reason>",
        "evidence_refs": [
          {
            "source_chunk_id": "<source-chunk-id>",
            "document_path": "<document-path>",
            "source_digest": "<sha256>",
            "start_line": 1,
            "end_line": 10
          }
        ]
      }
    ]
  }
}
```

For `cross-document-relationships`, read the matching relationship/document
groups from `cross-document-evidence` with `expand_evidence_collection`, then
submit exactly one row per returned relationship ID:

```json
{
  "workItemId": "<work-item-id>",
  "collectionId": "cross-document-relationships",
  "itemIds": ["<relationship-id>"],
  "payload": {
    "cross_document_review": [
      {
        "relationship_id": "<relationship-id>",
        "outcome": "COMPATIBLE",
        "note": "<bilateral evidence-backed reason>",
        "decisions": []
      }
    ]
  }
}
```

`COMPATIBLE` requires no Decisions. `FINDING` requires one or more confirmed
cross-document Decisions. `UNVERIFIED` requires one or more `UNVERIFIED`
`unverified_dependency` Decisions.

Use `dimension-evidence` and `source-sections` as pageable reference context.
They cannot receive checkpoints and never prove a CHK result automatically.

For a cross-document Run, `cross-document-evidence` is an additional pageable
reference collection grouped by `relationship_id` and `document_id`. Read all
pages from both relevant groups; do not checkpoint this reference collection.
Submit any admitted cross-document issue in the matching relationship
checkpoint with:

- `semantic_type`: `ambiguity`, `conflict`, `contradiction`, `drift`, or
  `unverified_dependency`;
- the declared `relationship_id` and exactly its two `document_ids`;
- one affected `CHK-01` through `CHK-18` dimension and non-empty
  `affected_elements`;
- the relationship's `resolution_owner` and a concrete `next_action`;
- at least one exact frozen `evidence_ref` from each side, including
  `document_id`, chunk ID, path, digest, line range, and excerpt.

These findings are reviewer-origin until the plugin publishes semantic
candidates, so `candidate_ids` stays empty. `unverified_dependency` must remain
`UNVERIFIED`; the other cross-document types are `CONFIRMED` only after
semantic review. The Host rejects unknown relationships, unrelated document
IDs, one-sided evidence, forged source identity, and mismatched resolution
owners.


To correct an accepted checkpoint before the WorkItem Decision is committed,
resubmit the exact same WorkItem, collection, and `itemIds`, replace only the
semantic payload, and add
`"supersedesCheckpointId": "<current-checkpoint-id>"`. The Host appends the
correction and retains the replaced record for traceability.

After every applicable required collection is complete, submit only the
remaining finalization fields. Do not resend candidate decisions,
`cross_document_review`, or `checklist_review`:

```json
{
  "readiness_context": {
    "mandatory_dimensions_checked": true,
    "unresolved_blockers": false,
    "escalations": []
  },
  "reviewer_origin_decisions": []
}
```

`reviewer_origin_decisions` is reserved for admissible single-document
findings missed by the scanner. Cross-document Decisions supplied here are
rejected because they would bypass the relationship checkpoint.

The plugin assembles the persisted fragments into review schema `1.3.0`
inside the Host. Every checklist row must carry the v1.3 applicability,
observation, gap, impact, recommendation, owner, next-action, and confidence
fields. Missing fields are rejected; the Host never silently downgrades a new
Run to the legacy `1.2.0` envelope. The Agent does not send this assembled
envelope.

Rules:

- Handle every scanner candidate exactly once. `SUPPRESSED`, `MERGED`, and
  `UNVERIFIED` decisions require a non-empty `review_note` and no final
  severity. `MERGED` must point to a validated `CONFIRMED` finding.
- Checkpoint decisions are validated before persistence. Invalid coverage,
  duplicate identity, malformed Findings, or untraceable Evidence leaves no
  checkpoint or operation in the ledger.
- A single-document reviewer-origin finding may omit candidate IDs only when
  it is `CONFIRMED`; every evidence excerpt must occur verbatim in the target
  Spec. The cross-document exception is defined above.
- `CONFIRMED` requires one business object, P1/P2/P3 severity, direct evidence,
  a concrete gap, material impact, recommendation, and closure evidence.
- Every `CHK-01` through `CHK-18` item is checkpointed exactly once and cites
  at least one immutable source chunk. The platform maps `PASS` to
  `satisfied`, `REWORK` to `violated`, `ESCALATE` to `conflicted`, and
  `UNVERIFIED` to `unresolved` in the Decision findings projection.
