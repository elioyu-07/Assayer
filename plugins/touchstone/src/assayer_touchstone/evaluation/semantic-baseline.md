# Spec Semantic Evaluation Baseline

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Status | Minimum semantic oracle for the first fixed corpus |
| Owner | `assayer.touchstone` maintainers |

This document defines the smallest semantic vocabulary needed to build and
judge the first Spec evaluation corpus. It is a plugin policy asset, not a
platform rule. The platform only validates the generic corpus envelope.

## Classification rules

- **Missing**: a decision required by the selected Spec policy is absent.
- **Ambiguity**: the text admits two or more materially different
  interpretations and does not identify the discriminator that selects one.
- **Contradiction**: two in-scope statements about the same subject and
  context require mutually exclusive outcomes; both cannot be true together.
- **Conflict**: two in-scope constraints are incompatible in practice, but the
  text does not form a direct logical negation (for example, a permission rule
  and an operational rule allow different owners).
- **Duplicate**: two statements express the same requirement, subject,
  constraint, and outcome. They should merge into one root-cause finding.
- **Drift**: a related or versioned document changes a term, constraint, state,
  or outcome relative to the anchor without an explicit decision.
- **Unverified dependency**: a required related source is unavailable, stale,
  or not provably the source selected by the anchor; absence of evidence is not
  evidence of consistency.
- **Noise / false positive**: a scanner signal is textually triggered but the
  surrounding source explicitly satisfies the policy or makes the signal
  non-normative. Suppress it with a reason; never silently drop it.

## Evidence and disposition rules

Every formal finding must identify one business subject, state, action, field,
or dependency; include direct excerpts and exact locations; explain impact;
and state the smallest remediation. A cross-document finding includes both
document identities and the relationship that makes comparison in scope.

Candidate dispositions are mutually exclusive: `CONFIRMED`, `SUPPRESSED`,
`MERGED`, or `UNVERIFIED`. Multiple symptoms may merge only when they share a
root cause and remediation; distinct consequences remain separate findings.

## Readiness baseline

- `READY`: all mandatory applicable dimensions were reviewed, no confirmed P1
  or P2 finding remains, and no material evidence is unverified.
- `REWORK`: at least one confirmed P1 or P2 finding is fixable by changing the
  Spec.
- `ESCALATE`: a business, governance, scope, or exemption owner must decide.
- `UNVERIFIED`: required evidence or authority cannot be established.

The fixed corpus stores expected candidates, suppressions, merges, findings,
severity, and readiness for every case. Its expected outcomes are the semantic
oracle; candidate count alone is not a quality metric.

## Version boundary

This baseline is version `1.0.0`. Changing a classification definition,
disposition rule, severity meaning, or readiness condition requires a new
baseline version and a new corpus expectation review. Wording or scanner
implementation changes alone do not silently rewrite expected outcomes.
