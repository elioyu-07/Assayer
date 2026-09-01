# Assayer Rule Contract

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-30 |
| Status | Design converging |
| Owner | Rule Owner / Product Owner |

## 1. Responsibility of Rule Files

A rule file is the normative source for Agent semantic decisions. The registry is the source for rule metadata and required Host capabilities. The Host does not derive business conclusions from rule files. A rule cannot alter the fixed five states, action safety, evidence bindings, or ledger references.

## 2. Required Structure

Every rule includes:

1. Stable `ruleId`, semantic version, state, and owner;
2. One-sentence rule and applicable object types;
3. Applicability criteria and explicit non-applicable conditions;
4. Required Host capabilities;
5. Minimum coverage dimensions and observable evidence for each dimension;
6. Reverse-Case principles, input categories, and safety boundaries;
7. Criteria for `issue_found`, `scanned_no_issue`, `not_applicable`, `needs_review`, and `noise`;
8. Default severity, permitted range, and rationale required for adjustment;
9. Positive, negative, boundary, noise, and regression samples;
10. Summaries or paths for rule documentation, coverage contract, and regression suite.

## 3. Applicability and Missing Capabilities

- Host first supplies candidates by object type; Agent confirms applicability from page facts;
- If a rule applies but a required capability is unavailable, the result cannot be `not_applicable` or `scanned_no_issue`; it defaults to `needs_review` and records the missing capability;
- `not_applicable` still references an observation Case proving that the object and rule prerequisites were observed;
- When the rule explicitly does not apply and evidence proves that fact, use `not_applicable`;
- When a candidate relates superficially to a rule but is confirmed rule noise, use `noise`;
- Rules in `draft`, `owner_approved`, `validated`, or `deprecated` state cannot produce formal `issue_found`; only `enabled` rules create formal issues.

## 4. Coverage Contract

Minimum coverage is a named set of rule-defined dimensions, not a fixed Case count. Every dimension states:

- Object or page facts to observe;
- Whether an action is required;
- Permitted input categories;
- Distinguishable pass and issue outcomes;
- Evidence kinds;
- Failures that can only yield `needs_review`.

Agent chooses the Case count, but it cannot cover fewer than the required dimensions. Equivalent inputs, repeated clicks, and visually duplicate screenshots do not add coverage.

A Case's `plannedCoverageDimensions` expresses investigation intent only. Formal coverage requires one `DimensionFinding` per dimension with `satisfied`, `violated`, `unresolved`, `blocked`, or `conflicted` state, a concise public rationale, and same-Scan Evidence references. Only `satisfied` and `violated` are resolved; every other state appears in the unresolved set.

The registry also declares machine-checkable gates for each of the five results, such as allowed Finding combinations, required Evidence kinds, and screenshot requirements. Host interprets these through a generic structure; the main loop must not branch on `ruleId`.

## 5. Direct Runtime Confirmation

A rule may permit direct confirmation without an interactive Case for explicitly declared conditions, such as a visible technical error, a field with no constraints, or actually truncated table content without a full-content affordance. Direct confirmation requires:

- Every permitted condition listed in the rule file;
- All required runtime Evidence present;
- No bypass of the recovery barrier or IssueScreenshot gate;
- Source intent not treated as runtime fact.

## 6. Five-State Decision Template

| Result | The rule file must answer |
|---|---|
| `issue_found` | Which observable facts confirm an issue, its impact, and required Evidence and screenshot. |
| `scanned_no_issue` | Which minimum dimensions are complete and what pass fact resolves each one. |
| `not_applicable` | Which prerequisite the object lacks and how the evidence proves this is not a capability gap. |
| `needs_review` | Which evidence, capability, source, or recovery condition is missing and who must review it. |
| `noise` | Why the candidate matches superficial features but not the rule semantics. |

## 7. Rule Lifecycle

```text
draft -> owner_approved -> validated -> enabled -> deprecated
```

- `owner_approved` means the owner confirmed the semantics;
- `validated` means positive/negative, safety, and screenshot regressions passed;
- Only `enabled` participates in formal audits and formal issues;
- A material semantic change creates a new version; old versions remain only for historical ledgers;
- Rule IDs are never reused;
- The registry is frozen when the Scan starts and cannot be hot-updated during a run.

## 8. Rule-Onboarding Acceptance

A new rule must prove that it:

- Reuses the general Agent investigation loop;
- Adds no decision state;
- Requires no bypass of Host safety or evidence gates;
- Fails closed when a capability is missing;
- Has regressions for positive, negative, boundary, noise, blocked, screenshot-failure, and recovery scenarios;
- Displays rule version and evidence chain in the unified ledger and reports.
