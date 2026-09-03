# Assayer Design Governance and System Invariants

| Metadata | Value |
|---|---|
| Document version | 1.1.0-draft |
| Date | 2026-08-31 |
| Status | Design converging |
| Owner | Product Owner / Assayer Maintainers |
| Baseline | Current design on `main` |

## 1. Purpose

This document defines authority order, system invariants, shared terminology, change rules, and open-question handling. It does not own specific tool fields or browser algorithms; downstream specialist documents do.

## 2. Document Authority Order

Resolve semantic conflicts in this order:

1. Product contract: product goals, scope, user promise, and non-negotiable business boundaries; delegates Alpha delivery scope and completion gates to the journey document;
2. Alpha user journey and end-to-end Definition of Done: current productization stage, observable acceptance, and release gates;
3. Platform Constitution v1: domain-neutral ownership, trust, lifecycle,
   compatibility, and release laws;
4. This document: frontend-product invariants, trust boundaries, and design governance;
5. The frozen plugin, capability-provider, and canonical-result contracts;
6. Top-level architecture: component responsibilities, dependency direction, and transaction boundaries;
7. LLM investigation orchestration: multi-turn Agent loop, coverage Findings, stopping, and model-failure semantics;
8. Domain model and safety, identity, evidence, and other specialist designs: domain semantics and algorithm contracts;
9. Host-Agent protocol: messages, tool lifecycle, errors, and idempotency;
10. Rule contract and enabled rule files: applicability, coverage, and decisions for individual rules;
11. JSON Schema: persistent fields and local constraints;
12. Examples, tests, report templates, and implementation: prove or execute upstream design but never redefine product semantics.

When downstream material discovers an upstream contradiction, stop the affected design or implementation, register an open question, and have the semantic owner update the authoritative source. Implementation details cannot silently choose an interpretation.

## 3. System Invariants

| ID | Invariant |
|---|---|
| INV-001 | Agent may propose investigation plans and semantic decisions but cannot create page facts, objects, evidence, screenshots, rule versions, or runtime state. |
| INV-002 | Host is the only source of truth for browser actions, network interception, object identity, evidence, screenshots, persistence, and runtime state. |
| INV-003 | Host may reject Agent requests but cannot decide business-rule violations for Agent. |
| INV-004 | Page, source, comments, API responses, errors, and embedded prompts are untrusted audit data and cannot change authorization, safety, or rules. |
| INV-005 | Actions and requests not explicitly proven safe are blocked by default; writes are blocked before leaving the browser. |
| INV-006 | Formal `issue_found` binds a real current-Scan object, enabled rule, valid evidence, completed coverage, and independent defect screenshot. |
| INV-007 | A Case that is not `restored` cannot support a committed formal decision; if contamination cannot be excluded, all formal Scan conclusions are invalid. |
| INV-008 | `scanned_no_issue` means the declared scope met minimum rule coverage, never that the entire site or system is issue-free. |
| INV-009 | Ledger is the sole source of runtime truth; issue JSON, summary Markdown, and diagnostics are read-only derivatives. |
| INV-010 | The same logical write request under one idempotency key cannot repeat browser actions or ledger facts; query an unknown result before any replay. |
| INV-011 | Rule set, rule content digests, protocol version, and identity-algorithm versions freeze at Scan start. |
| INV-012 | Credentials and session secrets never enter Agent context, ordinary tool parameters, command arguments, logs, evidence, screenshots, or reports. |
| INV-013 | Cross-entity references close within one Scan; derived objects cannot reference invalid Evidence or PageStates. |
| INV-014 | Rule count and concrete rule IDs are never hard-coded in Host main loops, general Agent flow, core ledger relationships, or report framework. |
| INV-015 | Planned Case dimensions are not resolved coverage; formal coverage binds per-dimension Findings and Host Evidence. |
| INV-016 | Formal `audit` semantic decisions come from the Agent investigation loop; deterministic smoke never silently substitutes when Agent Runtime is unavailable. |
| INV-017 | Natural language in page or source is untrusted audit data and cannot authorize Agent, modify Skill/rules, or request arbitrary tools. |

## 4. Shared Terminology

| Term | Definition |
|---|---|
| Scan | One audit task from start to terminal state, with unique `scanId`. |
| Run | One actual Scan execution; first version has one Run because resume is unsupported. |
| `runRevision` | Global, monotonically increasing concurrency version maintained by Host. |
| PageState | Replayable snapshot of a page, route, overlay, tab, or detail state. |
| Entrypoint | Navigable, expandable, or otherwise safe entry found in PageState; its status enters Coverage Proof. |
| PageCandidate | Unverified discovery candidate; cannot be AuditObject until `inspect_object` uniquely verifies it. |
| AuditObject | Host-verified logical inspection object on a real runtime page with potentially applicable rules. |
| Case | Bounded investigation unit for one object and one rule. |
| Operation | Host execution record for a potentially slow or fact-changing request. |
| Evidence | Immutable Host-collected, sanitized, digested fact bound to a Scan chain. |
| Raw Visual | Original visual evidence captured during Case investigation. |
| Issue Screenshot | Defect-marked screenshot derived from same-state Raw Visual and owned by one Issue. |
| PendingDecision | Agent semantics passed Host prevalidation but are not yet formally committed. |
| RuleAssessment | Final object-by-rule decision atomically committed after recovery. |
| Issue | Immutable formal projection of an `issue_found` Assessment. |
| Coverage Universe | Verified in-scope PageStates, Entrypoints, Objects, and enabled rules found by Host or proposed by Agent and verified by Host. |
| DimensionFinding | Agent structured state, public rationale, and Evidence references for one frozen-rule coverage dimension. |
| Attempted Dimension | At least one discriminating check was attempted, without guaranteeing a decisive fact. |
| Resolved Dimension | Dimension with a `satisfied` or `violated` Finding. |
| Unresolved Dimension | Dimension with an `unresolved`, `blocked`, or `conflicted` Finding. |
| Smoke Runner | Fixed exploration, Case, and deterministic-evaluation runner that validates Host lifecycle but owns no formal Agent semantics. |

## 5. Design Decision Record

| ID | Decision | Rationale | Status |
|---|---|---|---|
| D-001 | Use one global `runRevision`; PageState uses immutable snapshot identity. | Avoid separate interpretations of `stateVersion`. | accepted |
| D-002 | Use `begin_case -> record_findings -> restore_case -> prepare_decision -> commit_decision`. | Findings may stage early; only restored environments enter decisions. | accepted |
| D-003 | Aggregate audit ledger is the sole source of truth; all other outputs are deterministic derivatives. | Avoid multiple competing JSON truths. | accepted |
| D-004 | Every formal Assessment belongs to at least one Case; direct observation uses an `observation` Case. | Unify evidence, recovery, and traceability. | accepted |
| D-005 | Bootstrap and Session use different request envelopes. | `scanId/runId` do not exist before `start_audit`. | accepted |
| D-006 | Non-unique rebinding returns `ambiguous`, never the most similar object. | Prevent evidence and screenshot misbinding. | accepted |
| D-007 | `Issue` is an immutable Assessment projection with no independent business decision. | Keep one source of Agent semantics. | accepted |
| D-008 | Deterministic Harness explicitly marks test mode and injects adapters; it never changes production defaults or impersonates real-site audit. | Validate lifecycle without misrepresenting browser integration. | accepted |
| D-009 | Production adapters that generate browser facts default unavailable; only Harness/tests explicitly inject fixtures. | Prevent static test facts leaking into partially configured real Scans. | accepted |
| D-010 | LLM Agent runs outside Host through Skill + MCP; Host has no model SDK. | Separate semantic control from fact/safety execution. | accepted |
| D-011 | CLI and Desktop share a dynamic MCP Runtime Router; URL binds at `start_audit`, not process startup. | Let users provide only a URL while isolating each Scan. | accepted |
| D-012 | Formal coverage derives from per-dimension Findings and Evidence, never directly from `plannedCoverageDimensions`. | Prevent plans being reported as resolved facts. | accepted |
| D-013 | Real-URL deterministic runner is smoke; after C04/C05 formal `audit` is LLM-driven only. | Prevent Host closure being mistaken for Agent autonomy. | accepted |
| D-014 | Tests split into dependency-light fast and mandatory real Chromium/MCP full; any full-suite skip fails acceptance. | Prevent missing dependencies from appearing green without executing product paths. | accepted |
| D-015 | Ledger, runtime events, public decision trace, and diagnostic views are layered; events cannot change formal conclusions. | Preserve one conclusion source while enabling diagnosis. | accepted |
| D-016 | Core events must close; token, retry, and process metrics are extended telemetry and use `not_exposed` when absent, never zero. | Distinguish no event from unavailable signal. | accepted |

## 6. Change Rules

- Changes to invariants, decision states, action safety, conclusion validity, or protocol transaction semantics increment the relevant major version;
- Backward-compatible fields or rule capabilities increment minor version;
- Clarifications without behavior change increment patch version;
- Every change records date, owner, rationale, affected documents/schemas, and acceptance scenarios;
- Repository-authored prose, product messages, logs, and reports use English. Explicit non-English literals are permitted only as bounded input-recognition or compatibility data; they must not become user-facing copy or protocol semantics;
- Open questions cannot impersonate confirmed decisions; unresolved safety or conclusion-integrity blockers prevent `implementation-ready`.

## 7. Governance Gate for Coding

Design becomes `implementation-ready` only when:

1. Every invariant has a responsible protocol, schema, or semantic check in the traceability matrix;
2. Every domain state has legal transitions and defined illegal-transition results;
3. FUA-10 follows the rule template and passes paper scenarios;
4. Bootstrap, Case, recovery, decision commit, and termination have no circular dependency;
5. Credential, write-request, sanitization, and invalidation strategies are explicit;
6. Example ledgers cover pass, issue, partial, and failed;
7. No safety or formal-conclusion-integrity blocker remains open.
