# Canonical Spec review contract

Checkpoint each reviewed page by calling `advance_plugin_run` with one
`reviewCheckpoint`. Its `itemIds` are the candidate IDs returned for that
page, and its payload contains only the review decisions for those IDs:

```json
{
  "workItemId": "<work-item-id>",
  "collectionId": "candidate-findings",
  "itemIds": ["<candidate-id>"],
  "payload": {"decisions": [{"finding_id": "...", "status": "SUPPRESSED", "candidate_ids": ["<candidate-id>"], "review_note": "..."}]}
}
```

The payload decisions must cover `itemIds` exactly once. For the final
`advance_plugin_run` decision, send this small finalization object. The Host
binds the persisted checkpoint IDs automatically:

To correct an accepted checkpoint before the WorkItem Decision is committed,
resubmit the exact same WorkItem, collection, and `itemIds`, replace only the
semantic payload, and add `"supersedesCheckpointId": "<current-checkpoint-id>"`.
The Host appends the correction and retains the replaced record for traceability.

```json
{
  "readiness_context": {
    "mandatory_dimensions_checked": true,
    "unresolved_blockers": false,
    "escalations": [],
    "checklist_review": [{"check_id": "CHK-01", "status": "PASS", "note": "..."}]
  },
  "reviewer_origin_decisions": []
}
```

The plugin assembles these persisted fragments into the following canonical
`details.review` document inside the Host; the Agent does not resend it:

```json
{
  "review_schema_version": "1.0.0",
  "readiness_context": {
    "mandatory_dimensions_checked": true,
    "unresolved_blockers": false,
    "escalations": [],
    "checklist_review": [
      {"check_id": "CHK-01", "status": "PASS", "note": "..."}
    ]
  },
  "decisions": [
    {
      "finding_id": "finding-<stable-id>",
      "status": "CONFIRMED",
      "severity": "P2",
      "object_id": "FR-001",
      "dimension": "testability",
      "gap": "The concrete decision that is missing or conflicting.",
      "impact": "The material implementation, test, acceptance, governance, security, or data impact.",
      "recommendation": "The smallest actionable change.",
      "closure_evidence": "The objective evidence that closes the finding.",
      "candidate_ids": ["<candidate_id>"],
      "merged_into": null,
      "evidence": ["<direct source excerpt, including line when available>"],
      "review_note": null
    }
  ]
}
```

Rules:

- Handle every scanner candidate exactly once. `SUPPRESSED`, `MERGED`, and
  `UNVERIFIED` decisions require a non-empty `review_note` and no final
  severity. `MERGED` must point to a `CONFIRMED` finding in the same review.
- Checkpoint decisions are validated before persistence. Invalid candidate
  coverage, duplicate finding identity, malformed Findings, or Evidence that
  does not trace to the selected candidates is rejected without changing the
  durable ledger.
- A reviewer-origin finding may use an empty `candidate_ids` array only when
  it is `CONFIRMED`; every evidence excerpt must occur verbatim in the target
  Spec named by the evidence manifest.
- `CONFIRMED` requires one business object, a final P1/P2/P3 severity, direct
  evidence, a concrete gap, material impact, recommendation, and closure
  evidence. Do not use aggregate labels such as `FR-001~FR-016`.
- `MERGED` may target a `CONFIRMED` finding accepted in an earlier checkpoint
  or the same checkpoint; it cannot point forward to an unvalidated finding.
- `checklist_review` must contain exactly CHK-01 through CHK-18 in order. The
  platform maps `PASS` to `satisfied`, `REWORK` to `violated`, `ESCALATE` to
  `conflicted`, and `UNVERIFIED` to `unresolved` in the decision Findings.
