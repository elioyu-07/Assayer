# Spec-quality Evaluation Corpus

This directory is owned by the Spec-quality plugin. `corpus.json` records the
expected deterministic candidates, semantic dispositions, findings, and final
readiness for fixed Markdown inputs.

The platform owns only the generic `evaluation-corpus.schema.json` envelope,
identifier and reference validation, and safe loading mechanics. It does not
interpret Spec concepts such as requirements, acceptance criteria,
contradictions, or ambiguity.

The initial baseline covers:

- a strong Spec with no deterministic candidates;
- a weak Spec with structural and testability gaps;
- ambiguous wording requiring semantic confirmation;
- contradictory statements that cannot both govern;
- duplicate signals that merge into one root-cause finding;
- a textual scanner hit that semantic review suppresses as noise.
- a large deterministic input with no candidate drift;
- an interrupted semantic checkpoint with an explicit resume contract;
- a shared/global contract with `FR-G01` through `FR-G08`, explicit child-Spec
  delegation, and an `AS-006` no-migration decision;
- a feature Spec with a genuine executable-CASE and risk-scenario omission,
  distinct from valid shared-contract non-applicability;
- an anchored cross-document comparison carrying relationship evidence,
  document identities, impact, and resolution ownership.

Expected outcomes are versioned policy assets and must not be silently changed
to accommodate an implementation regression. Cross-document cases are exposed
to semantic review and are not collapsed into a single-file scanner result.

For single-document calibration, the automated check freezes each scanner
candidate as an exact `(candidateId, ruleId)` pair. The semantic oracle then
distinguishes four materially different reviewer outcomes:

- ambiguous wording is confirmed only when multiple unresolved
  interpretations change an observable outcome;
- mutually exclusive statements about the same contextual object are one
  contradiction finding with both source candidates;
- repeated symptoms with one remediation are consolidated into one finding;
- quoted or explicitly non-normative wording is suppressed as noise.

These expectations calibrate Agent instructions and review regressions. They
do not claim that deterministic candidate matching has evaluated semantic
correctness. Model-level conformance still requires an explicit evaluation Run
against the corpus.

For context-sensitive Cases, `contextAssertions` also freezes the expected
document type, objective source facts, explicit statement kinds, and selected
checklist applicability/status outcomes. Deterministic inspection verifies that
the facts are visible to the Agent. The semantic comparator then verifies that
the Agent preserved the document classification and required applicability
decisions. Python still does not decide whether the prose is sufficient.

## Model-level comparison contract

`evaluate_semantic_review_case(case, actual_review)` accepts the canonical
review projection produced after semantic decisions:

```json
{
  "reviewed_findings": [
    {
      "finding_id": "finding-1",
      "status": "CONFIRMED",
      "severity": "P2",
      "candidate_ids": ["candidate-1"]
    }
  ],
  "readiness": {"status": "REWORK"}
}
```

The comparator ignores wording and generated finding IDs. For a
single-document Case, it checks complete candidate coverage, suppression and
unverified sets, root-cause candidate clusters after resolving `MERGED`
decisions, severity, and readiness. For a cross-document Case, the projection
also contains `cross_document_review`; the comparator checks exact
relationship coverage, each relationship outcome, and the semantic finding
type, status, severity, two document identities, and resolution owner. Its
portable result follows `semantic-evaluation-result.schema.json` and reports
missing and unexpected structures instead of a bare score.

This is intentionally not a semantic judge. Explanation quality, whether two
phrases truly conflict, and scanner recall beyond the fixed oracle still
require a model or human evaluation. The contract prevents structural drift
from being mistaken for semantic success.

`load_semantic_review_from_ledger(path, work_item_id=...)` extracts this
projection, including relationship reviews when present, from a completed or
partial `assayer.spec-quality` platform ledger.
The ledger is used because it retains the complete accepted review decisions
and receipt readiness; the portable canonical result intentionally exposes a
smaller cross-plugin projection. A multi-WorkItem ledger requires the caller
to select one WorkItem explicitly.

`evaluate_semantic_review_corpus(corpus, actual_reviews)` evaluates a mapping
of Case IDs to these projections. Its suite result follows
`semantic-evaluation-corpus-result.schema.json`. Missing Cases, unexpected
Case IDs, and evaluated failures remain separate counts, so an interrupted or
partially supplied model Run cannot be reported as a corpus pass.
