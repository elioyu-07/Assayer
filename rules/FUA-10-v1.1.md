# FUA-10 List Filters Must Provide Both Query and Reset

| Metadata | Value |
|---|---|
| ruleId | `FUA-10` |
| version | `1.1.0` |
| status | `enabled` |
| owner | `product-owner` |
| document | `rules/FUA-10-v1.1.md` |
| requiredCapabilities | `runtime, dom` |
| defaultSeverity | `P2` |

The `enabled` status means only that the design baseline is active. After implementation, registry publication must still be reconfirmed by regression results.

## 1. Rule Semantics

When a real page provides filter conditions above or near a business list, the frontend filter region must provide explicit query and reset capabilities together.

This rule audits frontend behavior only: controls, action entrypoints, enabled state, and the visual or DOM ownership between the filter region and the business list. It does not audit whether a request is sent, request parameters, backend responses, returned-data correctness, or whether the list content changes after an action.

## 2. Applicability

The applicable object is a `filter_region` on a page that contains both a business-list result region and one or more visible filter conditions. An isolated search box, static display, or local control on a detail page is not applicable.

If the region cannot be confirmed as controlling the current list, return `needs_review`; do not treat it as not applicable.

## 3. Minimum Coverage Contract

| Dimension | Required observation | Evidence |
|---|---|---|
| `filter_present` | The filter region contains at least one visible, enabled, semantically clear filter control. | runtime_dom / runtime_visual |
| `query_action` | The region has an explicit entrypoint that initiates a query. | runtime_dom |
| `reset_action` | The region has an explicit entrypoint that resets, clears, or restores default conditions. | runtime_dom |
| `binding_to_list` | Frontend layout, container structure, or Host-aligned visual/logical list evidence uniquely establishes that the filter region belongs to the current business list. | runtime_dom / runtime_visual |

A FUA-10 `scanned_no_issue` result requires all four dimensions. Seeing only a query button does not prove complete coverage.

## 4. Case Planning

- Use an `observation` Case by default to read the filter region, button semantics, and logical-list ownership in the current viewport.
- When the DOM summary is insufficient, use `observe_page` and establish frontend ownership from object bounds, list bounds, column names, and spatial relationships.
- Synthetic filter input, post-query list changes, network requests, and backend results are not required as passing conditions.
- Any necessary button click is used only to confirm that the frontend entrypoint is reachable and enabled, and must remain within the read-only safety boundary.
- If an interaction is performed, every Case must restore frontend state and cross the recovery barrier.

## 5. Five-State Decision

### `issue_found`

Applicability and `binding_to_list` are confirmed; a filter condition and query entrypoint exist; but no visible reset entrypoint or equivalent ability to clear and restore default conditions exists. Evidence is sufficient and an independent issue screenshot is available.

### `scanned_no_issue`

All four minimum coverage dimensions are complete: filter controls plus query and reset entrypoints are visible and enabled, the filter region has unique visual or DOM ownership of the business list, and no contrary evidence exists.

### `not_applicable`

The object is not a business-list filter region, or it is only a page-level search, static display, or local control on a detail page.

### `needs_review`

Frontend ownership between the filter region and list, button semantics, object identity, or page-observation capability cannot be confirmed. Backend unavailability, absence of a network request, or unchanged list content after a click must not by itself cause `needs_review`.

### `noise`

The candidate region contains inputs or buttons but represents page-level search, sorting, pagination, or static display rather than FUA-10 business-list filtering.

## 6. Evidence and Screenshots

- `scanned_no_issue` must reference frontend runtime DOM or visual Evidence covering all four dimensions. Interaction Evidence is optional supporting material.
- `issue_found` must reference runtime Evidence showing the missing reset capability and must create an independent IssueScreenshot of the filter region.
- Source code may supplement button ownership or event intent, but it cannot override the runtime fact that no reset entrypoint is present.

## 7. Severity

The default is `P2`. If the list is a critical business-operation entrypoint and the inability to restore filter conditions causes significant operating cost, the Agent may justify an adjustment to `P1`. Button-label differences alone must not change severity.

## 8. Samples and Regression

- Positive: filter conditions, query, and reset are all present.
- Negative: filter conditions and query are present, but reset or clear is absent.
- Not applicable: an input on a detail page that does not control list results.
- Noise: pagination, sorting, or page-level keyword search only.
- Missing capability: DOM or visual ownership cannot be read → `needs_review`.
- Identity ambiguity: two similar filter regions cannot be bound uniquely to a list → `needs_review`.
- A backend endpoint is unavailable or the list does not change after a click, but all four frontend facts are clear → the FUA-10 decision is unaffected.
- Screenshot failure: an issue may still exist, but the formal result can only be `needs_review`.
