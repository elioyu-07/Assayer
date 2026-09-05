# Spec Audit Quality Contract

| Metadata | Value |
|---|---|
| Document version | 0.1.0 |
| Status | Slices A-E implemented; real-model owner acceptance pending |
| Date | 2026-09-05 |
| Owner | Assayer Spec Quality maintainers |
| Scope | Spec plugin semantic quality and platform result-integrity gates |

## 1. Problem statement

The latest real-model run completed its declared lifecycle, but its audit value
was weak:

- 68 navigation units were reviewed and all were suppressed with generic
  structural explanations;
- the independent checklist review marked 17 of 18 dimensions `REWORK` and one
  `UNVERIFIED`;
- the result contained no confirmed semantic Findings or remediations, while
  the canonical projection synthesized checklist-level Findings;
- several claims were contradicted by the source, including the existence of
  stable `FR-G01` through `FR-G08` identifiers and the explicit no-migration
  assumption `AS-006`;
- a global/shared model Spec was judged as if it were a standalone feature
  Spec, despite explicitly delegating feature detail to child Specs.

This is a quality-integrity problem, not a navigation or lifecycle failure.
Coverage and checkpoint completion currently prove that every required slot was
filled, but they do not prove that the semantic conclusion is applicable,
source-grounded, internally consistent, or actionable.

## 2. Goals and non-goals

### Goals

1. Make document scope and type explicit before checklist judgment.
2. Make every checklist status evidence-bound and semantically explainable.
3. Make every `REWORK` result map to a precise, actionable Finding, unless the
   reviewer explicitly records a governed document-level gap.
4. Prevent conclusions that contradict directly observable source facts.
5. Keep platform responsibilities domain-neutral while enforcing generic result
   integrity.
6. Preserve incremental navigation, checkpointing, recovery, and canonical
   output compatibility where possible.

### Non-goals

- Do not add new document formats in this slice; Markdown remains the only
  supported source format.
- Do not make Python heuristics decide semantic Spec quality.
- Do not require one chapter layout in the default content-first profile.
- Do not infer cross-document consistency from filenames or links alone.
- Do not redesign the generic platform lifecycle or add persistent caching.

## 3. Ownership boundaries

| Responsibility | Owner |
|---|---|
| Parse Markdown, preserve source identity, line ranges, digests, and bounded excerpts | Markdown capability provider |
| Discover navigation units and maintain deterministic coverage | Platform/provider layer |
| Classify document type and declared responsibility | Spec plugin, with Agent semantic review |
| Decide whether a checklist dimension applies | Spec plugin, with Agent semantic review |
| Judge ambiguity, contradiction, omission, and semantic sufficiency | Agent through the Spec plugin contract |
| Enforce evidence identity, source drift, status shape, and cross-object consistency | Host/platform |
| Render summaries and reports from canonical result | Platform renderers |

The Host remains a trust and integrity boundary. It must not decide whether a
Spec is compliant, but it must reject a decision that cannot be proven to be a
valid, evidence-bound decision.

## 4. Document context contract

Before checklist review, the plugin must produce a `DocumentContext` for each
anchor document. The context is semantic input to the Agent and is persisted as
an immutable review checkpoint.

Required fields:

| Field | Meaning |
|---|---|
| `documentType` | One of `feature_spec`, `shared_contract`, `data_model`, `interface_contract`, `rfc_prd`, or `other` |
| `scopeStatement` | Bounded statement of what the document owns |
| `responsibilityBoundaries` | Explicitly owned, delegated, and excluded concerns |
| `relatedDocuments` | Declared documents and relationship basis, if any |
| `selectedProfile` | `product-spec` by default, or an explicitly selected profile such as `strict-12-chapter` |
| `classificationEvidence` | Exact source references supporting the context |
| `classificationConfidence` | `high`, `medium`, or `low` |
| `unresolvedClassificationQuestions` | Questions that prevent safe applicability decisions |

The Agent may propose this context. The Host verifies that every referenced
source chunk exists, is from the frozen source digest, and belongs to the
declared anchor. Low-confidence or unresolved classification must not be
silently treated as a complete feature Spec.

### Classification rules

- An explicit scope statement has priority over heading shape.
- A document that declares shared concepts or delegates feature details to
  child Specs is `shared_contract` unless contrary evidence is found.
- A document that only defines entities, fields, types, and persistence rules
  is `data_model` unless it also owns executable behavior.
- A document can have one primary type and multiple responsibility tags; the
  primary type selects default applicability.
- `other` is allowed, but dimensions whose applicability cannot be established
  become `UNVERIFIED`, not automatic `REWORK`.

## 5. Applicability contract

Each of the 18 dimensions must receive exactly one applicability state before a
quality status is accepted:

| State | Meaning | Required evidence |
|---|---|---|
| `APPLICABLE` | The document owns this concern and it must be judged | Scope or responsibility evidence |
| `NOT_APPLICABLE` | The concern is explicitly outside this document's responsibility | Exclusion/delegation evidence and rationale |
| `CONDITIONAL` | Applicable only when a declared trigger or feature exists | Trigger evidence and selected condition |
| `UNVERIFIED` | Scope or required authority is insufficient to decide | Missing authority or unresolved question |

`NOT_APPLICABLE` is not a free pass. The rationale must identify the delegated
owner or explicit exclusion. A dimension marked `REWORK` must have been
`APPLICABLE` or an explicitly failed `CONDITIONAL` condition.

For a `shared_contract` or `data_model` document, feature-level scenarios,
user workflows, and atomic AC/CASE are normally `CONDITIONAL` or
`NOT_APPLICABLE`; shared invariants, terms, identifiers, lifecycle rules,
dependencies, and traceability remain applicable when the document claims to
own them.

## 6. Evidence-bound checklist result

Every checklist dimension result must contain:

```json
{
  "checkId": "CHK-15",
  "applicability": "APPLICABLE",
  "status": "REWORK",
  "observation": "FR-G01 through FR-G08 are present, but no source-to-FR mapping is defined.",
  "gap": "The document does not map source goals to each FR and downstream acceptance evidence.",
  "impact": "A reviewer cannot verify bidirectional coverage or detect orphaned requirements.",
  "recommendation": "Add sourceRef and downstream AC/CASE references for each FR.",
  "evidenceRefs": ["source:a9b7407f404c:13:13"],
  "findingRefs": ["finding:..."],
  "owner": "Spec owner",
  "nextAction": "Add the mapping or record an approved delegation.",
  "confidence": "high"
}
```

Normative rules:

- `PASS` requires positive evidence, not merely absence of a candidate.
- `NOT_APPLICABLE` requires a rationale and scope evidence.
- `UNVERIFIED` requires a named missing authority, source, or unresolved
  question.
- `REWORK` requires a concrete gap, impact, recommendation, and at least one
  bounded evidence reference.
- Generic reasons such as “mandatory content dimensions are not all covered”
  are invalid as the sole explanation.
- Evidence must be narrow enough to support the observation. A whole-document
  reference is allowed only when the claim is explicitly document-wide.

## 7. Finding contract

A formal semantic Finding is the actionable unit of audit output. It must bind
to one WorkItem, one checklist dimension or declared cross-document
relationship, and immutable source evidence.

Required semantic fields:

- stable `findingId`;
- `dimension` or relationship ID;
- `status`: `CONFIRMED`, `UNVERIFIED`, `SUPPRESSED`, or `MERGED`;
- precise `gap` statement;
- observed source fact;
- impact/risk;
- remediation recommendation;
- affected semantic elements (for example `FR-G03`, `lifeStage`, or a field);
- exact evidence references and excerpts;
- owner and next action;
- confidence and reviewer note.

`REWORK` checklist results must reference one or more `CONFIRMED` Findings, or
an explicit `document_gap` Finding that identifies why the gap is structural
and cannot be attached to a single candidate. A checklist result with
`REWORK` and no Finding is rejected by Host admission.

## 8. Source-fact consistency gate

The plugin must derive a lightweight fact index from Markdown structure and
send it to semantic review as non-authoritative navigation context. The index
does not decide quality; it only makes direct contradictions rejectable.

The minimum fact index includes:

- stable identifiers matching configured patterns (`FR-*`, `AC-*`, `CASE-*`,
  `AS-*`, decision IDs);
- headings and heading paths;
- table headers and row labels;
- explicit negations such as “not applicable” or “no migration”; and
- declared related-document links.

If a decision says that an identifier, assumption, or section is absent while
the frozen source fact index proves it present, Host rejects the decision as
`INVALID_SEMANTIC_CLAIM` and requests correction. This is a factual integrity
check, not a semantic compliance decision.

## 9. Host admission and finalization gates

Host must enforce the following before finalization:

1. A valid `DocumentContext` exists.
2. Every dimension has an applicability state and a status.
3. Every `REWORK` has a precise Finding reference.
4. Every Finding evidence reference resolves to the frozen source digest and
   bounded line range.
5. Checklist status and Finding status agree; no Finding may be orphaned or
   silently synthesized only during canonical projection.
6. `PASS` and `NOT_APPLICABLE` include positive rationale/evidence.
7. `UNVERIFIED` names the unavailable authority or unresolved question.
8. Source-fact contradiction checks pass.
9. Cross-document claims are only accepted when the declared relationship and
   bilateral evidence are available.
10. Canonical output is derived from the same accepted semantic Findings and
    checklist results; it must not manufacture new substantive Findings.

If a gate fails, the Run remains non-terminal or closes as `failed` with a
machine-readable reason. It must not be presented as a valid quality result.

## 10. Review sequence

The intended sequence is:

1. Parse and freeze Markdown source facts.
2. Establish `DocumentContext` and responsibility boundaries.
3. Establish applicability for all 18 dimensions.
4. Review candidate and navigation evidence, suppressing only with a specific
   reason.
5. Review applicable checklist dimensions in bounded batches.
6. Create or update precise Findings while reviewing each dimension.
7. Run Host source-fact, evidence, and cross-collection consistency gates.
8. Commit the decision and derive canonical output.

Checklist review must not be allowed to bypass context and applicability.
Navigation review and checklist review may remain separate queues, but their
accepted evidence and Findings must share stable source references.

## 11. Implementation slices

### Slice A — Contract and schema

- Add versioned `DocumentContext`, applicability, checklist-result, and Finding
  fields to plugin schemas.
- Keep historical result readers backward-compatible.
- Add Host validation errors for missing evidence, orphan `REWORK`, and invalid
  applicability/status combinations.

Exit gate: deterministic schema and Host tests reject malformed results.

### Slice B — Context and fact index

- Implement Markdown fact extraction as navigation/context data.
- Add Spec plugin context prompt and bounded evidence packet.
- Persist context before checklist review.

Exit gate: the target shared Asset Model Spec is classified as a shared
contract with source-backed delegation evidence; stable IDs and AS-006 are
visible in the packet.

### Slice C — Applicability-aware Agent review

- Update the 18-dimension review prompt and response parser.
- Require exact evidence, observation, gap, impact, and recommendation for
  `REWORK`.
- Require rationale for `NOT_APPLICABLE` and named authority for `UNVERIFIED`.

Exit gate: the shared-contract regression corpus no longer receives feature-
level false positives solely because child-level AC/CASE is elsewhere.

### Slice D — Consistency and canonical projection

- Add source-fact contradiction validation.
- Remove canonical synthesis of substantive Findings not present in accepted
  semantic review.
- Align `confirmedFindings`, checklist results, remediations, and final result.

Exit gate: an objectively false “no FR IDs” claim is rejected, and every
accepted `REWORK` has a visible actionable Finding.

### Slice E — Regression corpus and real-model evaluation

- Add shared-contract, delegated-responsibility, explicit no-migration,
  stable-ID, valid-pass, and real-conflict fixtures.
- Evaluate precision, evidence locality, applicability accuracy, and
  actionable remediation quality.

Exit gate: deterministic tests pass and a real-model run produces no known
  false claims from the current failure set. Real CLI acceptance remains a
  user-run gate.

## 12. Acceptance matrix

| Scenario | Expected result |
|---|---|
| Feature Spec missing invalid-input CASE | `REWORK` with exact source gap, impact, and remediation |
| Shared Spec delegates feature AC/CASE to child Specs | `NOT_APPLICABLE` or `CONDITIONAL` with delegation evidence |
| Source contains `FR-G01..08`, reviewer claims no FR IDs | Host rejects as `INVALID_SEMANTIC_CLAIM` |
| Source contains `AS-006` no-migration assumption | CHK-11 cannot claim migration is wholly unspecified |
| Related documents absent from declared scope | `UNVERIFIED`, not confirmed contradiction or defect |
| Checklist has `REWORK` but no Finding | Host rejects finalization |
| All navigation units suppressed with generic notes | Allowed only if checklist results still provide precise evidence-bound Findings or justified non-applicability |
| Valid shared contract with no material gaps | `scanned_no_issue` only after all applicable dimensions have positive evidence |

## 13. Open decisions and explicit deferrals

- Whether `DocumentContext` should be a plugin-private checkpoint or a generic
  platform collection. Initial implementation should keep semantic fields in
  the plugin while using generic Host evidence validation.
- Whether confidence thresholds should affect final status. Defer automatic
  numeric thresholds; require explicit `UNVERIFIED` when confidence is too low
  to support a claim.
- Cross-document semantic review remains a separate scope declaration and is
  not inferred from Markdown links.
- Full source-to-repository version proof is deferred; unavailable version
  identity remains `UNVERIFIED`.
