# FUA-10 List Filters Must Provide Both Query and Reset

| Metadata | Value |
|---|---|
| ruleId | `FUA-10` |
| version | `1.0.0` |
| status | `enabled` |
| owner | `product-owner` |
| document | `rules/FUA-10.md` |
| requiredCapabilities | `runtime, dom, interaction` |
| defaultSeverity | `P2` |

The `enabled` status means only that the design baseline is active. Registry publication must be reconfirmed by regression results after implementation.

## 1. Rule Semantics

When a real page list has filter conditions that change its result set, the filter region must provide both query and reset capabilities.

## 2. Applicability

The applicable object is a `filter_region` that contains one or more filter conditions and controls a list result region. An isolated search box, static display, local detail-page filter, or display control that does not change list results is not applicable.

If ownership of the current list cannot be confirmed, return `needs_review`; do not treat the object as not applicable.

## 3. Minimum Coverage Contract

| Dimension | Required observation | Evidence |
|---|---|---|
| `filter_present` | At least one filter control whose value affects list-query semantics. | runtime_dom / runtime_interaction |
| `query_action` | The region has an explicit query entrypoint. | runtime_dom |
| `reset_action` | The region can clear filters and restore default conditions. | runtime_dom / runtime_interaction |
| `binding_to_list` | The filter region has unique ownership of the current list result region. | runtime_dom / runtime_interaction |

A FUA-10 `scanned_no_issue` result requires all four dimensions. A query button alone does not prove complete coverage.

## 4. Case Planning

- Use an `observation` Case by default to read filter-region, list, and button semantics.
- Use synthetic filter values only when necessary; never submit a real write operation.
- Query requests may be observed, but the audit must not change persistent data.
- If reset triggers a network query, Host must confirm that the request is read-only and record before/after state.
- Every Case must restore filter values, list state, and pending requests.

## 5. Five-State Decision

### `issue_found`

Applicability and `binding_to_list` are confirmed; filter conditions and a query entrypoint exist; but no visible reset entrypoint or equivalent clear-and-restore capability exists. Evidence is sufficient and an independent issue screenshot is available.

### `scanned_no_issue`

All four minimum coverage dimensions are complete, query and reset entrypoints exist, and no contrary evidence is present.

### `not_applicable`

The object is not a filter region that controls list results, or no filter condition can change the result set.

### `needs_review`

Filter/list binding, button semantics, object identity, or the required interaction capability cannot be confirmed. Failure to see reset is not sufficient by itself to confirm an issue.

### `noise`

The candidate contains inputs or buttons but represents page-level search, sorting, pagination, or static display rather than FUA-10 list filtering.

## 6. Evidence and Screenshots

- `scanned_no_issue` must reference Evidence covering all four dimensions.
- `issue_found` must reference runtime Evidence showing the missing reset capability and create an independent IssueScreenshot.
- Source code may supplement button ownership or event intent, but it cannot override the runtime fact that reset is absent.

## 7. Severity

The default is `P2`. The Agent may justify `P1` when the list is a critical business-operation entrypoint and inability to restore filters causes significant operating cost. Button-label differences alone must not change severity.

## 8. Samples and Regression

- Positive: filter conditions, query, and reset are present.
- Negative: filter conditions and query are present, but reset or clear is absent.
- Not applicable: a detail-page input that does not control list results.
- Noise: pagination, sorting, or page-level keyword search only.
- Missing capability: DOM or interaction ownership cannot be read → `needs_review`.
- Identity ambiguity: two similar filter regions cannot be bound uniquely to a list → `needs_review`.
- Screenshot failure: an issue may still exist, but the formal result can only be `needs_review`.
