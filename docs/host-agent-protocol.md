# Assayer Host-Agent Collaboration Protocol

> C05 status: dynamic Runtime Router, fixed output root, per-Scan isolation, Agent lease supervision, project MCP configuration, and formal `audit`/deterministic `smoke` separation are implemented. The authoritative loop is [LLM Investigation Orchestration](llm-agent-orchestration.md).

## 1. Scope

This protocol defines collaboration between the Codex Agent and Assayer Host. It applies equally to MCP and CLI; both are transport adapters over one Host Core and must not implement separate browser, safety, evidence, or ledger logic.

The transport process accepts no business URL at startup. The first `start_audit` creates a Scan-owned Runtime; its `outputDir` must be literal `auto`, rewritten beneath a fixed root. Session requests must match the bound `scanId/runId`; cross-Scan routing is rejected.

This document owns messages and boundaries, not Python classes, SDK details, or browser-driver algorithms. Domain state, recovery, safety, and evidence semantics belong to their specialist contracts.

## 2. Principles

1. Host is the fact and safety boundary: it generates or verifies page, request, source, screenshot, object, and Evidence IDs.
2. Agent is the investigation control plane: it chooses objects, rules, reverse Cases, evidence paths, and explains conclusions.
3. Skill defines semantics: rule files and policy define applicability, coverage, Cases, and decision criteria.
4. Requests and responses are structured; natural language appears only in Agent rationale fields.
5. References close to Host-returned entities in the current Scan.
6. Reject on uncertainty, stale state, invalid references, or lifecycle violation.
7. Runtime facts outrank source intent.

## 3. Interaction Model

```text
Agent reads Skill/rules
  -> structured tool request
Host validates identity, state, references, and safety
  -> executes or rejects and persists immutable facts
Agent receives minimal Evidence pack or reason
  -> plans next step or decision
Host validates and commits the decision
```

Host returns facts, capability results, rejection reasons, and integrity results; it does not make business conclusions. Agent never directly accesses browser, filesystem, network, or a source repository. Deterministic Harness is explicit test mode only; real `smoke` collects Host facts and never writes Assessment/Issue.

## 4. Message Envelopes

### 4.1 Bootstrap

Before `start_audit`, no Scan exists:

```json
{"protocolVersion":"1.0","requestId":"req-uuid","agentTurnId":"turn-uuid","tool":"start_audit","idempotencyKey":"bootstrap:request-uuid","input":{}}
```

Bootstrap must not provide `scanId`, `runId`, or `expectedRunRevision`. Success creates them; repeating the same idempotency key returns the same Operation/result. Failure before Scan creation returns no session IDs; after creation it uses the complete Session envelope.

### 4.2 Session

All other tools use:

```json
{"protocolVersion":"1.0","requestId":"req-uuid","scanId":"scan-uuid","runId":"run-uuid","agentTurnId":"turn-uuid","tool":"inspect_object","idempotencyKey":"scan-uuid:turn-uuid:inspect_object:obj-123","expectedRunRevision":18,"input":{}}
```

`protocolVersion` is `1.0`; `requestId` is unique; Scan/Run must be active; `agentTurnId` identifies a decision turn; `tool` is a registered name; idempotency keys remain stable across retries; `expectedRunRevision` is the global Host revision and stale mutations return `STALE_STATE`; `input` is tool-specific structured data. Except pre-creation bootstrap failures, responses include protocol/request/session IDs and current revision. Every tool except `get_operation` creates an Operation; reads use `operationKind=read` and do not increment revision.

### 4.3 Responses and Errors

Success uses `status=ok`, `result`, `evidenceRefs`, and `diagnosticRefs`. Rejection uses `status=rejected` and an error with `code`, `message`, `retryable`, and `requiredNextStep`.

| Code | Meaning | Agent response |
|---|---|---|
| `INVALID_REQUEST` | Invalid parameters/schema | Correct request; do not retry unchanged |
| `UNKNOWN_TOOL` | Missing or disabled tool | Do not guess a substitute |
| `STALE_STATE` | Global revision changed | Re-read page/object |
| `UNKNOWN_REFERENCE` | Missing object/Case/Evidence/Screenshot | Never invent an ID |
| `ACTION_BLOCKED` | Safety policy rejected action | Gather safe evidence or `needs_review` |
| `NAVIGATION_BLOCKED` | Disallowed cross-site navigation | Record skip |
| `CASE_NOT_RESTORED` | Recovery and refresh fallback failed | Stop object; record contamination |
| `INSUFFICIENT_EVIDENCE` | Decision references lack evidence | Gather evidence or `needs_review` |
| `INVALID_DECISION` | Result/rule/fields violate contract | Correct and resubmit |
| `RUN_TERMINAL` | Scan is completed, partial, or failed | Stop ordinary calls |
| `INTERNAL_FAILURE` | Host failure | Follow `retryable` |
| `INVALID_LIFECYCLE_TRANSITION` | Tool disallowed in entity state | Re-read or stop |
| `IDEMPOTENCY_CONFLICT` | Same key, different digest | New key and replan |
| `OPERATION_RESULT_UNKNOWN` | Action result not known | Call `get_operation`; never replay |
| `REQUEST_BLOCKED` | Potential write blocked before send | Recover Case or `needs_review` |
| `SANITIZATION_FAILED` | Evidence cannot be sanitized | Do not save; gather alternate evidence |
| `EVIDENCE_INSUFFICIENT` | Required observation missing | Gather required observation |

## 5. Tool Catalog

The JSON CLI uses one JSON request per line; MCP carries the same envelope. Both call `HostCore.handle` and never generate IDs, decide rules, read browsers, or alter gates.

### `start_audit`

Creates Scan, receives temporary credentials through one-time `credentialHandle`, and attempts login. Anonymous pages use `authMode=anonymous` without a handle. The result includes Scan/Run IDs, login status, current PageState, registry digest, frozen rules, capabilities, revision, and algorithm versions. Failure yields diagnostics only and no issue conclusion.

### `inspect_page` and `explore_entrypoint`

`inspect_page` reads current PageState, candidates, safe entrypoints, and snapshot references without interaction. A Candidate is not an AuditObject and cannot enter a Case or decision until `inspect_object` uniquely verifies it. `explore_entrypoint` accepts only Host-issued `pageStateId` and `entrypointId` (B11 initially supports tabs); no selector, script, URL, or arbitrary click parameters. Successful exploration creates a new immutable PageState, records its parent, updates current state, and increments revision. Only attributable same-origin GET/HEAD/OPTIONS are permitted.

### `inspect_object`

Reads one Host-verified object or Candidate result, including identity, type, PageState, potential rules, visible/ARIA/structural context, Evidence references, revision, and algorithm versions. Returns opaque `controlRef` and `listRef` with visibility, disabled state, value class, and optional `readOnly=true`. Agent cannot submit selectors, DOM paths, or natural-language locators.

### `begin_case`

Creates a Case around a verified object and frozen rule, atomically saving recovery baseline and planned dimensions. One active Case is allowed per object-rule pair. Case actions must reference its `caseId`.

### `perform_action`

Allowed types are `scroll`, `focus`, `expand`, `switch_tab`, `open_detail`, `open_edit`, `input_synthetic_value`, `select_synthetic_option`, `activate_query`, `activate_reset`, and `refresh`. Actions target only Host-issued references. Agent supplies `valueClass`, not values; Host generates synthetic values and keeps restoration state in Session memory. Host blocks delete, cancel, unbind, save, submit, approve, publish, import, upload, and other writes; uncertainty rejects. Results include Operation, before/after PageStates, request observations, and `runtime_interaction` Evidence. `result_unknown` permits only `get_operation`.

### `restore_case`

Host uses only the Case action log and baseline, never Agent selectors/scripts. It reverses actions, checks route/layer/tab/overlay/control/object/pending/write dimensions, records `restored`, `uncertain`, or `failed`, and on uncertainty/failure refreshes the URL and replays safe entrypoints. Only every required check `match` with no unknown is `restored`; otherwise `CASE_NOT_RESTORED` stops the object.

### `observe_page`

Read-only multimodal observation of the current verified object. Input references current PageState/Object and optional Case. Host returns `runtime_visual` Evidence with viewport, filter/object bounds, controls, deduplicated logical lists, spatial relationships, and object-to-list binding; MCP also attaches the same immutable PNG. Visual understanding cannot replace unique runtime binding, rule Evidence, or screenshot gates. True `sanitizationStatus` remains visible; B07c is not bypassed.

### `inspect_source`

Searches only source related to the current runtime object. Host must close page -> route -> component -> handler/API attribution; otherwise returns `sourceBindingStatus=unverified` and never substitutes a similarly named file.

### `capture_evidence`

Captures PageState, Object location, and optional Raw Visual for the current Case/object. `prepare_decision(issue_found)` derives an independent IssueScreenshot from the same issue-state Raw Visual. Evidence is structured and sanitized, with object/bounding-box and capture status; arbitrary selectors cannot be supplied.

### `record_findings`, `prepare_decision`, and `commit_decision`

`record_findings` stores immutable per-dimension Findings (`satisfied`, `violated`, `unresolved`, `blocked`, `conflicted`); a new Finding supersedes only the latest same object/rule/dimension. `prepare_decision` creates PendingDecision only, validates final `findingRefs`, Evidence/Case closure, applicability, coverage, screenshot, and semantic fields. `plannedCoverageDimensions` never proves coverage; for `BINDING_UNRESOLVED`, an `observe_page` viewport/logical-list Evidence is required. `commit_decision` revalidates identity, frozen rule, revision, recovery, Evidence, Screenshot, and gates, then atomically writes RuleAssessment and a one-to-one Issue for `issue_found`.

### `get_operation`

Reads Operation request digest and status (`accepted`, `running`, `succeeded`, `rejected`, `failed_known`, `result_unknown`) without executing or replaying the action.

### `complete_audit`

Product MCP may submit only a completion rationale; the Facade derives coverage from the ledger. The underlying protocol accepts visited PageStates, processed objects/entrypoints, skip reasons, rule summaries, unfinished entrypoints, and completion reason. Host validates login, closure, screenshots, skip reasons, frozen registry, and unexplained omissions. Unfinished scope yields `partial`; Agent cannot self-report `completed`.

## 6. Agent Loop and Gates

```text
inspect_page -> explore safe entrypoint -> inspect_object -> load rule
-> begin_case -> perform_action / inspect_source -> capture_evidence
-> record_findings -> restore_case -> prepare_decision -> commit_decision
```

Multiple safe actions and Cases are allowed before recovery. Stale revisions require reinspection; duplicate idempotency keys do not repeat actions; unknown results require `get_operation`. Agent must continue discriminating evidence for unresolved dimensions or state a concrete stop reason. Host stops repeated stalled exploration as `partial`.

`issue_found` requires current matched object, enabled applicable rule, complete issue coverage, valid Evidence, restored Cases, complete rationale fields, and independent correctly aligned screenshot. `scanned_no_issue` requires every minimum dimension's latest Finding `satisfied`, restored Cases, and no unresolved/conflicting Evidence. Screenshot failure or missing coverage rejects the formal issue.

## 7. Registry and Compatibility

Rule count is dynamic. `start_audit` freezes registry version/digest; entries declare object kinds, capabilities, coverage, decision gates, and regression suite. New rules reuse the general loop and cannot add states or bypass safety/evidence gates.

Incompatible protocol, decision-state, Evidence, or action-safety changes increment the major version. Optional fields are backward-compatible. Reports record protocol, registry, rule, and Skill versions; old reports are read-only.

## 8. Required Protocol Tests

Cover every tool's success, missing fields, unknown references, stale state, and duplicate requests; dangerous/unknown/cross-site/write blocking; fabricated objects, Evidence, selectors, screenshots, and rules; targeted and fallback recovery; screenshot/coverage rejection; MCP/CLI equivalence; and new-rule extension without Host-main-loop changes.
