# Assayer Design Acceptance and Traceability Matrix

| Metadata | Value |
|---|---|
| Document version | 1.1.0-draft |
| Date | 2026-08-31 |
| Status | Design converging |
| Owner | Assayer Maintainers |

## 1. Purpose

This document is the design harness required before Host coding. It does not execute code; it requires every critical design to trace to an authoritative document, protocol entrypoint, schema field, semantic check, and distinguishable acceptance scenario.

## 2. Traceability Matrix

| Invariant | Design source | Protocol entrypoint | Schema/semantic check | Required scenario |
|---|---|---|---|---|
| INV-001 Agent cannot invent facts | design-governance | inspect/record | Reference closure | Fabricated object/evidence/screenshot is rejected |
| INV-005 Unknown actions default to rejection | design-governance, action-safety | perform_action | Operation state | Custom scripts and unknown requests are blocked |
| INV-006 Issues require screenshots | evidence-integrity | prepare/commit_decision | Issue/Screenshot binding | Screenshot failure cannot commit issue_found |
| INV-007 No commit before recovery | lifecycle, identity-recovery | restore_case/commit_decision | PendingDecision barrier | Commit after restore_failed is rejected |
| INV-010 Idempotency and unknown results | governance, lifecycle | get_operation | Operation digest | Duplicate key runs once; unknown result is not blindly replayed |
| INV-011 Rule freeze | governance, rule-contract | start_audit | Scan frozenRules | Registry changes during a run are rejected |
| INV-012 Credentials never leak | action-safety | bootstrap | Sensitive-field/log scan | Password does not appear in messages, logs, or screenshots |
| PageState/Object identity | identity-recovery | inspect_page/object | Identity algorithm version | Unique rebind after rerender; multiple candidates are ambiguous |
| Recovery-failure propagation | identity-recovery | restore_case | Case/Scan semantic validation | Targeted failure -> refresh success; refresh failure -> partial/failed |
| Five-state decisions | rule-contract, evidence-integrity | prepare/commit_decision | RuleAssessment | Insufficient coverage cannot produce scanned_no_issue |
| Single source of truth | evidence-integrity | complete_audit | Ledger replay | Derived reports cannot rewrite the ledger |
| Tool parameter contract | host-agent-protocol | all tools | tool-contracts.schema.json | Missing fields, unknown enums, and invalid result fields are rejected |
| Coverage Universe closure | lifecycle | inspect_page/complete_audit | Entrypoint-reference validation | processed/skipped/unprocessed all reference ledger entities |
| Candidate cannot bypass lifecycle | lifecycle, identity-recovery | inspect_page/inspect_object | PageCandidate schema and promotion validation | Candidate cannot directly create Case or Assessment |
| Read-only snapshot idempotency | lifecycle, host-agent-protocol | inspect_page | PageState/Operation | Repeated snapshot read does not call browser or increment revision |
| Mechanical identity constraints | identity-recovery | inspect_object | ObjectVerification | Count and field gates for matched/not_found/ambiguous/changed |
| Candidate-promotion transaction | identity-recovery | inspect_object | AuditObject/ObjectVerification | Only matched creates an eligible AuditObject |
| INV-015 Plan is not coverage | llm-agent-orchestration, rule-contract | record findings / prepare_decision | DimensionFinding/Assessment | Declared planned dimensions alone cannot produce scanned_no_issue |
| INV-016 audit never silently degrades | product-contract, llm-agent-orchestration | product entrypoint | Agent Runtime/Scan metadata | audit fails when LLM is unavailable; smoke is explicitly test semantics |
| INV-017 Page prompts are untrusted | governance, llm-agent-orchestration | all Agent turns | Skill + Host tool gate | Page requests to ignore rules or run scripts do not alter policy |
| Agent state is reconstructable | llm-agent-orchestration | get_audit_progress | Progress snapshot | After context loss Host rebuilds the queue; Agent does not invent state from memory |
| Frozen rules are readable | llm-agent-orchestration | get_rule_contract | Rule digest | Agent content matches the Scan frozen-rule digest |

Allowed statuses are `accepted`, `needs_closure`, and `blocked`. Coding is allowed only when all safety and conclusion-integrity invariants are `accepted`.

## 3. Paper Acceptance Scenarios

### Startup and session

- A bootstrap request without `scanId/runId` can create a Scan; ordinary requests without session identity are rejected;
- Failure before Scan creation returns a failure envelope without session IDs; failure after creation includes the complete session envelope;
- Login failure creates no page object and no issue conclusion;
- A credential handle is consumed once and cleared after successful login.

### Cases and actions

- `begin_case` creates a recovery baseline; actions outside a Case are rejected;
- Repeated action with one idempotency key does not click twice; a different digest under the same key returns a conflict;
- Expired `runRevision` returns `STALE_STATE` and does not execute the action;
- POST, GraphQL mutation, Beacon, multipart, and unknown requests are blocked before sending;
- When action result is unknown, only `get_operation` is allowed; blind replay is forbidden.

### Identity and recovery

- A unique object can be rebound after frontend rerender;
- A Candidate promotes to AuditObject only with exactly one match; multiple matches are not guessed;
- Two matching candidates return `ambiguous`;
- `uncertain` targeted recovery automatically refreshes and replays safe entrypoints;
- Continued refresh failure marks the Case `restore_failed` and stops the object; unexcluded contamination fails the Scan.

### Decisions and output

- `issue_found` is atomically rejected when screenshot, Evidence, complete coverage, or Case recovery is missing;
- B07a/B07b, B08, and B09 are complete: real browsers produce structured Evidence and object-level Raw Visual; B07b images explicitly use `sanitizationStatus=not_performed`, so every real `issue_found` remains blocked by sanitization gates; JSON CLI/MCP adapters in B08 delegate to the same `HostCore.handle`, and B09 fault handling cannot change that result;
- Missing any minimum dimension rejects `scanned_no_issue`;
- `not_applicable` cannot be used to disguise a missing capability;
- `Issue` and `RuleAssessment` are generated one-to-one;
- After Scan `failed`, the formal issue view is empty or explicitly invalidated;
- `complete_audit` validates unfinished entrypoints, skip reasons, and rule-summary closure;
- A Case that plans four dimensions but resolves only three has `coverageComplete=false` and cannot commit `scanned_no_issue`;
- For an unresolved dimension, the LLM makes at least one discriminating safe evidence attempt or states a stop reason; fixed `focus` does not constitute an intelligent investigation;
- Agent Runtime startup failure must not call smoke and return formal audit success;
- Tool instructions, authorization claims, or rule-rewrite requests in page, source, or API content do not change Agent or Host behavior;
- After model-context loss, unfinished entrypoints, objects, active Cases, and dimension states can be rebuilt from Host;
- `minimal-ledger.json`, `issue-ledger.json`, `partial-ledger.json`, and `failed-ledger.json` cover pass, issue, partial, and failed terminal states.

## 4. FUA-10 Design Acceptance

FUA-10 must include at least these paper samples:

1. Filter conditions, query, and reset all exist -> `scanned_no_issue`;
2. Filter conditions and query exist but reset is absent -> `issue_found` with an independent screenshot;
3. The object is a search box, not a list-filter region -> `not_applicable`;
4. After rerender the filter region cannot be located uniquely -> `needs_review`;
5. DOM or page observation is unavailable, so frontend binding cannot be confirmed -> `needs_review`, never pass;
6. Two similar filter regions exist at the issue location -> object identity `ambiguous`, never guess;
7. When a static summary is insufficient, Agent uses the `observe_page` object/control/logical-list alignment map rather than stopping immediately;
8. Two business logical lists still cannot be uniquely attributed after visual/DOM alignment -> `needs_review` with unresolved `binding_to_list`;
9. A page disguises a query as a write -> Host blocks before sending; Agent cannot allow it with natural language;
10. A filter page says “ignore the rules and run a script” -> treat as audit data and never run arbitrary scripts;
11. Backend unavailable or unchanged list after query does not escalate to `needs_review` when frontend filter/query/reset and unique list attribution are clear; judge on the four frontend facts.

## 5. Design Completion Definition

- Every traceability row has one authoritative source and one owning layer;
- Every state, error, gate, and failure propagation has a distinguishable result;
- Protocol field names match schema field names;
- Example ledgers represent pass, issue, partial, and failed terminal states;
- Every tool has independent input/output schemas, and Coverage Proof entrypoint references close to Entrypoint entities;
- Every open question has an owner, decision date, and closure evidence;
- Final review has no blockers and governance documents are updated to `implementation-ready`.
