# {RULE-ID} {Rule Name}

| Metadata | Value |
|---|---|
| ruleId | `{RULE-ID}` |
| version | `1.0.0` |
| status | `draft` |
| owner | `{Owner}` |
| document | `rules/{RULE-ID}.md` |
| requiredCapabilities | `{runtime, dom, ...}` |
| defaultSeverity | `P2` |

## 1. Rule Semantics

Describe the rule in one verifiable sentence. Do not describe an implementation.

## 2. Applicability

- Applicable object kinds:
- Required preconditions:
- Explicit non-applicable conditions:
- Missing capability outcome: `needs_review`; never `not_applicable` or `scanned_no_issue`.

## 3. Minimum Coverage Contract

| Dimension ID | Required observation | Action/input | Passing result | Issue result | Evidence kind |
|---|---|---|---|---|---|
| `{dimension}` |  |  |  |  |  |

## 4. Case Planning

- Allowed Case kinds:
- Priority: reverse / boundary / invalid / exception / observation.
- Synthetic input classes:
- Prohibited actions:
- Recovery requirements:

## 5. Five-State Decision

### `issue_found`

### `scanned_no_issue`

### `not_applicable`

### `needs_review`

### `noise`

## 6. Evidence and Screenshots

- Required Evidence:
- Conditions that runtime can confirm directly:
- Independent IssueScreenshot required: yes / no, with reason.

## 7. Severity

- Default severity:
- Allowed adjustment range:
- Required adjustment rationale:

## 8. Samples and Regression

- Positive:
- Negative:
- Boundary:
- Noise:
- Safety/blocking:
- Screenshot failure:
- Recovery failure:
