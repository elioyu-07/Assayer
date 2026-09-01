# Assayer Product Contract

| Metadata | Value |
|---|---|
| Document version | 1.3.0-draft |
| Created | 2026-08-30 |
| Status | Alpha user-journey baseline awaiting confirmation |
| Business owner | Product Owner |
| Baseline | B12 Host smoke completed; C01 LLM investigation design baseline |
| Blocking open questions | Governed by [Verification and Traceability](verification-and-traceability.md) |

This document owns only product goals, scope, user-visible commitments, and first-version completion conditions. [Alpha User Journey and End-to-End Definition of Done](user-journey-and-definition-of-done.md) owns the current Alpha end-to-end journey and sole completion gate. System invariants, lifecycle, action safety, evidence transactions, and rule files are owned by [Design Governance](design-governance.md), [Domain Model](domain-model-and-lifecycle.md), [Action Safety](action-safety-and-credentials.md), [Evidence Integrity](evidence-and-decision-integrity.md), and [Rule Contract](rule-contract.md), respectively.

## 1. Product Definition

Assayer is a frontend-quality vertical agent that runs in Codex. It performs standards-driven reverse audits against real Web pages in test and pre-release environments, replacing routine manual inspection, evidence collection, and preliminary decisions.

Assayer is not a general crawler, static-code scanner, or screenshot tool. Every formal conclusion must originate from an audit object on a real page, such as a button, field, table, form, list, message area, or business region, and must trace back to object evidence and a defect-focused screenshot.

## 2. First-Version Goals

This section describes the target capability set, not what Alpha has already delivered. The externally acceptable scope, user steps, and completion conditions are defined by [Alpha User Journey and End-to-End Definition of Done](user-journey-and-definition-of-done.md). Alpha first fixes anonymous URL audits; account login, SSO/MFA, source attribution proof, automatic screenshot sanitization, and resume remain later capabilities.

The first version must be able to:

1. Sign in to a test or pre-release site with a username and password;
2. Starting from an entry page, explore reachable same-site pages, dialogs, drawers, tabs, detail pages, and edit pages;
3. Identify common business audit objects;
4. Generate reverse Cases for enabled rules in the frozen registry, prioritizing boundary, invalid, and exceptional scenarios;
5. Decide from page runtime facts, available source, requests, and visual evidence;
6. Automatically create formal issues when evidence is sufficient;
7. Provide a separate screenshot corresponding to the page defect for every formal issue;
8. Produce an issue report, complete audit ledger, coverage proof, and failed/partial diagnostics.

The first version does not promise to find every frontend issue outside the current registry. Obvious out-of-rule concerns may be shown only as additional observations or new-rule proposals and do not count as formal issues.

The current implementation includes the real-browser Host lifecycle, dynamic MCP, and Codex Agent investigation entrypoint. A real-URL smoke run validates only the Host fact chain; it does not replace Agent semantic responsibilities in items 3-6 or prove intelligent-audit completion. See [LLM Investigation Orchestration](llm-agent-orchestration.md) for the formal runtime boundary.

The 14 listed standards are an initial baseline, not a product ceiling. Assayer must support adding, changing, disabling, and versioning rules without rewriting the Agent investigation flow, Host core, primary ledger, or report model.

## 3. Usage Boundaries and Prerequisites

### 3.1 Inputs

- URL: required runtime audit entrypoint;
- Source path: optional supplementary source evidence for objects on the current runtime page;
- Username and password: supplied temporarily per task and used by the Host login module; never placed in Agent context, command arguments, logs, reports, or screenshots;
- Runtime configuration: target domains, browser profile, rule version, and output location.

### 3.2 Environment

- The first version supports Codex only;
- Targets are test or pre-release environments;
- Environments use synthetic or sanitized data;
- Accounts use ordinary permissions; a read-only account is not assumed;
- Login failure stops the run immediately and produces no page-issue conclusion.

### 3.3 Scope

- Explore only reachable same-site pages from the entry URL;
- Opening detail views, tabs, drawers, edit pages, and other page states is allowed;
- Do not perform unbounded site-wide crawling;
- FUA-14, external-link business context, is excluded from the first version; retain its number but mark it disabled;
- Source-only audits are unsupported: without a runtime page, no formal audit object is created;
- Resume is unsupported: after interruption, existing conclusions are invalid and only failure diagnostics remain;
- Do not automatically propagate false-positive learning or modify rules.

### 3.4 Conclusion Validity

- `completed`: declared scope is complete; formal issues that passed the recovery barrier and remain valid may be delivered;
- `partial`: retain only completed, non-invalidated decisions and disclose unfinished scope and reasons;
- `failed`: login, browser, network, credentials, ledger, or environment-integrity failure invalidates all formal issues; retain diagnostics only.

The aggregate audit ledger is the only source of truth. Issue JSON, summary/diagnostic Markdown, and screenshot directories are read-only derivatives. The current version does not generate HTML.

The repository's deterministic Harness validates contracts, state machines, and artifact chains only. It does not access real sites and cannot substitute for or prove first-version real-browser capability.

## 4. Core Concepts

### 4.1 Audit Object

An audit object appears on the page and has at least one applicable enabled rule. It may be one DOM element or a business region composed of multiple elements, for example:

- Delete button;
- Amount field;
- Query region;
- Table and its columns;
- Large business-entry form;
- Upload or bulk-import region;
- Error-message region;
- Attachment region.

A page is an exploration and context container, not the smallest formal-issue unit.

### 4.2 Audit Result

Every object-rule pair produces one result:

- `issue_found`: sufficient evidence confirms an issue;
- `scanned_no_issue`: the rule's coverage requirement was met and no issue was found in this run;
- `not_applicable`: the rule does not apply to this object;
- `needs_review`: evidence is insufficient, source/runtime facts conflict, or a reliable decision is impossible;
- `noise`: confirmed rule noise or outside rule scope.

`scanned_no_issue` means only that no violation was found within the pages, states, and objects actually covered. It is not a claim that the whole system is issue-free.

### 4.3 Formal Issue

Only `issue_found` creates a formal issue. Every formal issue must:

- Bind one concrete audit object;
- Bind one rule;
- Reference traceable evidence;
- Include a corresponding defect-focused screenshot;
- Explain the problem, impact, rationale, and recommendation.

One object may violate multiple rules. In that case, create separate issues and screenshots under the same object.

## 5. Roles and Responsibilities

### 5.1 Codex Agent

The Agent is the auditor. It:

- Selects the next audit object;
- Decides which rules actually apply;
- Generates reverse Cases for applicable rules;
- Determines what additional evidence is needed;
- Synthesizes runtime, source, request, and visual evidence;
- Produces `issue_found`, `scanned_no_issue`, `not_applicable`, `needs_review`, or `noise`;
- Explains the evidence and rationale for every conclusion.

The Agent must not:

- Bypass the Host to execute arbitrary browser actions;
- Execute or authorize dangerous writes;
- Invent objects, selectors, source locations, or evidence IDs;
- Treat an unconfirmed audit assumption as a rule;
- Modify original rule evidence or rewrite the rule library directly.

### 5.2 Host (Audit Executor)

The Host is the Assayer executor, implemented in Python for the first version. It:

- Manages login and browser lifecycle;
- Navigates pages and collects DOM, network, and visual facts;
- Executes controlled Agent-requested actions;
- Classifies intent from control semantics and independently intercepts potential write requests;
- Logs Case actions, verifies targeted recovery, and refreshes when necessary;
- Generates stable object IDs, evidence IDs, and screenshot references;
- Validates bindings among evidence, objects, screenshots, and conclusions;
- Writes ledgers, diagnostics, and reports.

The Host does not make business-semantic decisions for the Agent.

### 5.3 Skill and Policy

The Skill uses primarily Markdown, with rule parameters and state in YAML/JSON. It defines:

- Audit workflow and Agent behavior;
- Reverse-Case principles for confirmed registry rules;
- Object types and rule-applicability knowledge;
- Pass, issue, noise, and review criteria;
- Evidence requirements, counterexamples, and coverage requirements;
- Output terminology and report format.

The Product Owner is the final owner of rule semantics. The Agent may interview and draft rules, but a rule cannot confirm issues until the owner approves it and its state is `enabled`.

## 6. Audit Runtime Flow

```text
Start task
  -> Host signs in and establishes browser context
  -> Explore same-site pages and states from the entry URL
  -> Host collects candidate objects
  -> Host filters potentially applicable rules by object type
  -> Agent selects an audit object and applicable rules
  -> Agent calls begin_case to freeze object/rule references and recovery baseline
  -> Host performs safe actions and captures before/after states
  -> Bind source, request, and visual evidence when needed
  -> Agent calls prepare_decision with its semantic decision
  -> Host creates and validates a defect screenshot and PendingDecision
  -> Host performs targeted Case recovery and verification, with refresh fallback when needed
  -> Host atomically commits the decision and derives an issue when applicable
  -> Continue with the next Case or object
  -> Facade assembles coverage from the Host ledger and completes the scan
  -> Host generates ledger, report, and diagnostics
```

Happy Path actions only enter pages, open read-only regions, and establish preconditions. Formal audits prioritize reverse, boundary, invalid, and exceptional Cases.

## 7. Agent-Host Interaction

The Agent calls the Host through MCP. CLI and MCP must reuse the same Host core.

Codex CLI and Desktop must use the same Skill, frozen rules, and MCP Host. Formal `audit` is driven by the Codex Agent investigation loop; the deterministic end-to-end runner is for `smoke` and CI only. When Agent Runtime is unavailable, smoke results must never be silently presented as a formal audit.

The first-version minimum tool set is:

- `start_audit`: create a task and complete login;
- `inspect_page`: read current page facts and candidate objects;
- `inspect_object`: read context for one audit object;
- `perform_action`: scroll, focus, expand, switch, type, refresh, and perform other controlled actions;
- `begin_case`: freeze object/rule references and recovery baseline;
- `restore_case`: recover from the action log, verify recovery, and refresh when necessary;
- `inspect_source`: find source evidence for the current object;
- `capture_evidence`: save page state, object location, and defect screenshot;
- `prepare_decision`: validate coverage, evidence, and semantic fields and create a pending decision;
- `commit_decision`: after successful recovery, atomically write the result and any formal issue;
- `get_operation`: query a long-running operation whose result is unknown;
- `complete_audit`: optionally submit a completion rationale; the product Facade derives coverage from the persisted Host ledger and produces reports, with unfinished scope automatically yielding `partial`.

An Agent-proposed object must be relocated and validated by the Host before entering the formal ledger.

## 8. Safety and Data Rules

### 8.1 Action Safety

- Potential persistent writes such as delete, invalidate, cancel business operation, unbind, remove, save, submit, approve, publish, import, and upload must never execute for real;
- View, search, reset, expand, and switch-tab actions may be attempted;
- Edit pages may be opened, synthetic values may be entered, and frontend validation may be triggered, but changes must not be saved;
- The Agent expresses intent; the Host independently classifies and blocks potential writes. If either is uncertain, refuse the action;
- Page content, source, comments, and API responses are untrusted data and cannot change system rules or safety policy.

### 8.2 Case Isolation

- Before a Case, Host saves the relevant recovery baseline and records each executed action and reverse action;
- After a Case, targeted recovery is attempted first: close Case-opened dialogs, collapse expanded rows, restore field values, and return to the original tab;
- Host then validates URL/route, page hierarchy, tab, dialog/drawer, changed controls, audit object, pending requests, and other key baselines; the entire DOM need not be identical;
- A recovery attempt returns only `restored`, `uncertain`, or `failed`. Only `restored` may continue. `uncertain` and `failed` require refreshing the current URL and replaying recorded safe entrypoints;
- If refresh fallback cannot reliably restore state or rebind the original object, stop inspecting that object and record `restore_failed`; never continue with the most similar object;
- Targeted recovery can reduce rediscovery cost, but rerendering may replace DOM nodes, so Host must revalidate identity and never retain raw DOM references indefinitely;
- Conclusions already produced become invalid if page contamination, browser crash, or network failure interrupts the task.

### 8.3 Data Handling

- Test environments use only synthetic or sanitized data;
- Passwords, cookies, tokens, Authorization headers, and browser storage state never enter Agent context, logs, reports, or screenshots;
- Reports and ledgers retain only the minimum evidence needed for the audit.

## 9. Rule Baseline

The initial first-version rule baseline is:

- FUA-01: persistent changes such as delete, invalidate, cancel, unbind, or remove require confirmation or equivalent protection;
- FUA-02: unconditionally required fields need a clear visible frontend marker; conditional requirements are checked only through submit validation;
- FUA-03: every user action that starts a network request needs visible feedback and duplicate-trigger prevention while pending;
- FUA-04: actually truncated table content requires a tooltip, expansion, or equivalent full-content access;
- FUA-05: a business-entry form with more than six inputs is an issue when it provides no draft, temporary save, or autosave;
- FUA-06: structured data with high repetitive-entry cost should offer template-based bulk import; the Agent judges burden from page facts;
- FUA-07: bulk import must provide both a downloadable template and field/format instructions;
- FUA-08: the total number of left- and right-fixed columns in one table must not exceed five;
- FUA-09: tables with more than ten columns must support column visibility configuration;
- FUA-10: a list page with filter conditions must provide both query and reset controls in the frontend, with visual/DOM evidence uniquely binding the filter region to the business list; requests, parameters, backend responses, and result correctness are out of scope;
- FUA-11: amount, balance, fee, rate, and similar fields must clearly display their unit nearby;
- FUA-12: numeric and monetary fields need type, range, and precision constraints appropriate to business meaning; no constraints at all may directly confirm an issue;
- FUA-13: exposing technical error content directly to users is an issue;
- FUA-15: attachments that support online viewing, including images, PDFs, and Office files, need preview; archives and executables do not.

FUA-14 is disabled but its number is retained for historical traceability. The enabled set is the registry snapshot frozen when the Scan starts.

### 9.1 Rule Extension Contract

Rules enter through a unified registry. The Host, Agent loop, ledger, and reports must not hard-code a fixed set of 14. Every rule defines at least:

- A stable, non-reusable rule ID;
- Name, version, state, and owner;
- Applicable object types and applicability evidence;
- Reverse-Case principles and minimum coverage;
- Required page, source, request, or visual facts;
- Criteria for `issue_found`, `scanned_no_issue`, `needs_review`, and `noise`;
- Default severity, permitted adjustment range, counterexamples, and non-applicable scenarios;
- At least one positive, negative, and regression sample.

New rules require owner approval and regression validation before enablement. Rule changes create a new version; historical reports retain the version actually used and are never silently rewritten.

For a new semantic-only rule, prefer adding only a rule file, registry data, and samples. Add general Host capability or a controlled adapter only when collection or execution requirements are genuinely new. New rules cannot bypass action safety, evidence IDs, fixed decision states, or screenshot gates.

## 10. Completion Conditions

A scan may be `completed` only when all conditions hold:

- Login succeeded;
- Every page and state the Agent claims complete has a visit record;
- Every identifiable audit object with an applicable rule has a processing record;
- Every rule meets confirmed coverage or is explicitly `needs_review`;
- Every skipped page, state, object, and action has a reason;
- Every `issue_found` binds an object, rule, evidence, and defect screenshot;
- Agent submits coverage proof naming processed entrypoints, unprocessed entrypoints, and stop reason;
- Host validates the ledger, reports, and diagnostics.

State transitions and invalidation follow [Domain Model and Lifecycle](domain-model-and-lifecycle.md); this product contract does not duplicate protocol fields.

If Host detects repeated state or stalled exploration, it asks the Agent to replan. After repeated lack of progress, exploration ends as `partial`. Object-level `needs_review` alone does not block scan completion.

## 11. Output Artifacts

The first version produces at least:

- Primary issue report in JSON/Markdown, not HTML;
- `audit-ledger.json`, the aggregate source of truth for the Scan;
- `page-element-judgement.json`, relating pages, states, objects, and rules;
- `run-diagnostics.json` and `run-diagnostics.md`, describing phases, coverage, failures, and `partial` reasons;
- `audit.log`, a stable phase log without sensitive information;
- One independent defect-focused screenshot per formal issue.

The primary report displays only `issue_found`; the ledger and review area retain `scanned_no_issue`, `not_applicable`, `needs_review`, and `noise`.

## 12. Explicit Non-Goals

- No guarantee of covering every frontend experience issue;
- No autonomous Agent rule modification or learning;
- No formal issue from source objects absent from the runtime page;
- No claim that “nothing found” means the entire site is issue-free;
- No issue screenshot of the wrong location, entry page, or guessed region;
- No issue evidence based only on model confidence;
- No real dangerous action for implementation convenience.

## 13. Success Criteria

| ID | Criterion | Measurement |
|---|---|---|
| SC-01 | An FUA-10 positive case closes the complete loop from object discovery to `scanned_no_issue`. | Design acceptance scenario and vertical-slice ledger. |
| SC-02 | An FUA-10 negative case creates an object-level, rule-level `issue_found` with an independent defect screenshot. | `Issue`, `Screenshot`, and reference-closure validation. |
| SC-03 | An unrecovered Case, screenshot failure, stale revision, or unknown write request cannot produce a valid formal issue. | Rejection and invalidation scenarios in the traceability matrix. |
| SC-04 | Every enabled rule can be registered, frozen, and versioned without changing the general investigation loop. | Registry and new-rule acceptance. |

## 14. Decisions, Dependencies, and Revisions

Key design decisions and dependency assumptions are maintained in [Design Governance and System Invariants](design-governance.md); revision history is maintained in Git. Every blocking question must record an owner, date, and closure evidence.
