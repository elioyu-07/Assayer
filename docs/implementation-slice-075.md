# Vertical Slice 075: Actionable Result Contract

## Goal

Make a valid terminal audit result useful for remediation without moving domain
judgment into the platform. The platform must distinguish immutable
dimension-level Findings from root-cause remediation items and must reject a
plugin's claim that a result is actionable when the required explanation and
Evidence linkage are incomplete.

This slice advances M3 result conformance and the J05 result journey. The Spec
golden journey is its first acceptance case, but the contract is domain neutral.

## Problem demonstrated by the Spec golden journey

The platform currently proves lifecycle, coverage, Decision, receipt, and
Evidence-reference closure. It does not yet prove that a person can act on the
published result. A completed Spec Run can therefore contain all 18 mandatory
dimension Findings while its summary still:

- hides confirmed remediation work behind a `needs_review` outcome;
- leaves affected elements, ownership, and next action empty;
- presents a true but insufficient source citation for an absence claim; or
- leaves the relationship between scanner candidates, root causes, and
  checklist dimensions implicit.

These are not lifecycle failures. They are result-quality contract gaps.

## Semantic separation

The platform keeps two different public concepts:

1. **Dimension Finding** records one Check dimension's state (`satisfied`,
   `violated`, `unresolved`, `blocked`, or `conflicted`). It proves coverage and
   Decision validity.
2. **Remediation** records one root cause that a person can resolve. It may map
   to one or more violated Dimension Findings and must say what is affected,
   why it matters, what to do next, and what Evidence would close it.

One Remediation may cover several dimensions. A `needs_review` result may carry
both Remediations for already confirmed defects and review blockers for facts
that remain unresolved. Neither list may suppress the other.

The platform owns the Remediation structure, reference closure, publication,
and completeness status. A plugin owns the domain meaning of the root cause,
affected elements, severity, recommendation, and closure evidence.

## Contract shape

A plugin may attach a platform-owned `result_delivery` envelope to a committed
Decision. The envelope is versioned independently of plugin-specific details.

```json
{
  "schema_version": "1.0.0",
  "remediations": [
    {
      "remediation_id": "missing-executable-cases",
      "title": "Executable acceptance scenarios are missing",
      "severity": "P1",
      "dimensions": ["CHK-08", "CHK-12", "CHK-15"],
      "affected_elements": ["B33", "B34", "B35", "B36", "B37"],
      "evidence_refs": ["evidence:..."],
      "problem": "Acceptance bullets do not define reproducible Cases.",
      "impact": "Independent pass/fail verification is not possible.",
      "recommendation": "Add stable AC and CASE records.",
      "next_action": "Assign an owner and add Cases for each critical flow.",
      "closure_evidence": "Reviewed AC-to-CASE mappings with executable inputs and outcomes.",
      "owner": {
        "status": "unassigned",
        "reason": "The audited source does not identify an accountable owner."
      }
    }
  ]
}
```

Platform validation requires:

- unique Remediation identity within a Decision;
- non-empty title, affected elements, problem, impact, recommendation, next
  action, and closure evidence;
- severity `P1`, `P2`, or `P3`;
- at least one mapped dimension and one same-Investigation Evidence reference;
- every mapped dimension exists in the Decision and is `violated` or
  `conflicted`;
- every actionable dimension is covered by at least one Remediation when the
  envelope declares itself complete;
- explicit owner state: an assigned identity, or an unassigned reason. The
  platform must not force an Agent to invent a person;
- no Remediation publication from an invalidated failed Run.

The canonical result adds `remediations` as a sibling of `findings`. Dimension
Findings remain the coverage truth. Remediations are the actionable root-cause
view and contain canonical Finding references derived by the platform.

## Evidence precision follow-up

Reference closure and semantic sufficiency are separate gates. Slice 075 first
prevents empty or untraceable remediation records. Slice 076 adds a generic
Evidence-claim contract with these claim kinds:

- `direct`: one or more source facts directly establish the claim;
- `absence`: a bounded search or inspected scope establishes missing content;
- `derived`: several cited facts support an explicit public derivation;
- `external_unverified`: the required authoritative source is unavailable.

Plugins define how to collect and evaluate those claims for their domains. The
platform validates source identity, frozen version, locator closure, and the
required shape for each claim kind. It does not decide whether a CASE, browser
binding, API retry policy, or database constraint is semantically sufficient.

## Compatibility and migration

The first implementation is additive:

- existing Dimension Findings and canonical-result v1 fields remain unchanged;
- Decisions without `result_delivery` remain valid but their canonical
  actionability status is explicitly `not_declared`;
- the Spec plugin adopts the envelope first and becomes the reference fixture;
- current built-in plugins are migrated before `result_delivery` becomes a
  mandatory plugin-release gate;
- external v1 plugins are not silently rejected by a patch release.

After all built-in plugins migrate, the platform contract receives a versioned
release gate that forbids `issue_found` or a mixed `needs_review` result from
claiming full actionability without complete Remediation coverage.

## Delivery slices

### 075A — Structure and publication

- add the shared Remediation model and validator;
- project validated Remediations into the canonical result;
- expose explicit actionability status;
- preserve failed-Run suppression and privacy filtering;
- add generic conformance tests.

### 075B — Spec adoption

- convert confirmed Spec root causes into `result_delivery.remediations`;
- map each root cause to all affected REWORK dimensions;
- require explicit assigned/unassigned ownership;
- show confirmed remediation and unresolved blockers together;
- add the latest real-Run shape as a regression fixture.

### 076 — Evidence claims and negative Evidence

- add bounded direct, absence, derived, and external-unverified claim shapes;
- make source locators and inspected scope portable;
- require absence claims for whole-document missing-content conclusions;
- render cited excerpts and checked scope in the human summary.

The first implementation is additive: `result_delivery.evidenceClaims` is
optional, while any declared claim is fail-closed on identity, EvidenceRef,
claim kind, and bounded line scope. Remediations may reference claims through
`claimRefs`; those references must resolve to claims in the same Decision and
carry every EvidenceRef used by the claim. The platform does not infer that a
missing claim is proof of absence, and it does not judge whether a plugin's
search strategy was semantically complete.

### 077 — Built-in migration and release gate

- migrate configuration and frontend issue results;
- make actionable-result conformance a package/release gate;
- keep non-issue and failed outcomes free of invented remediation work;
- prove the same conformance suite across at least two domains.

## Acceptance

Slice 075 is accepted when:

- a `needs_review` Spec result publishes both confirmed Remediations and
  unresolved review blockers;
- no confirmed Remediation has null affected elements, Evidence references,
  next action, closure evidence, or owner state;
- each published Remediation maps to canonical violated Finding identities;
- incomplete or foreign references fail before terminal publication;
- a failed Run publishes no Remediations;
- old v1 plugin results remain readable and explicitly report that actionable
  delivery was not declared;
- focused schema, canonical-result, Spec, frontend-compatibility, and result
  conformance tests pass.

Real CLI acceptance remains owner-run and is not performed by this slice.
