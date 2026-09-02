# Canonical Spec review contract

Place this object under `details.review` in each Spec WorkItem decision. It is
an Agent-owned semantic review document; the Host validates its shape and
evidence boundary before committing the decision.

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
- A reviewer-origin finding may use an empty `candidate_ids` array only when
  it is `CONFIRMED`; every evidence excerpt must occur verbatim in the target
  Spec named by the evidence manifest.
- `CONFIRMED` requires one business object, a final P1/P2/P3 severity, direct
  evidence, a concrete gap, material impact, recommendation, and closure
  evidence. Do not use aggregate labels such as `FR-001~FR-016`.
- `checklist_review` must contain exactly CHK-01 through CHK-18 in order. The
  platform maps `PASS` to `satisfied`, `REWORK` to `violated`, `ESCALATE` to
  `conflicted`, and `UNVERIFIED` to `unresolved` in the decision Findings.
