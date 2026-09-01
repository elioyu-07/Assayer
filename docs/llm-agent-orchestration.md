# Assayer LLM Investigation Orchestration Design

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-31 |
| Status | C01-C07 implemented; C07 real lease black-box acceptance completed |
| Owner | Agent Runtime / Assayer Maintainers |

## 1. Purpose

This is the authoritative design for the LLM investigation control plane: how the Codex Agent uses frozen rules to drive Host page exploration, object selection, Case planning, evidence gathering, and five-state decisions. Host fact, safety, recovery, and transaction boundaries remain owned by [Architecture](architecture.md), [Action Safety](action-safety-and-credentials.md), [Domain Model](domain-model-and-lifecycle.md), and [Evidence Integrity](evidence-and-decision-integrity.md). LLM cannot override them.

## 2. Migration Status

The former `BrowserHostRuntime.audit` was deterministic: fixed Candidate order, observation Case, `focus`, Evidence, and Python `RuleEvaluationEngine` result. C06 removed it. `assayer smoke` now calls `BrowserHostRuntime.smoke` and collects Host facts only (`mode=host_smoke`, `publishable=false`) without Findings, decision tools, or formal ledger export. Formal `assayer audit` starts Codex Skill plus dynamic MCP; no silent downgrade exists.

## 3. Goals and Non-Goals

Goals: Agent is the semantic control plane; Host remains the execution plane; each turn handles one `AuditObject x Rule` minimal context; evidence gaps permit multiple discriminating probes; coverage distinguishes attempted from resolved; one Skill/MCP path serves CLI and Desktop; new rules require no rule-ID branch in Python.

Non-goals: model SDK/API keys in Host; direct Playwright, selectors, JavaScript, or arbitrary URLs from the model; hidden reasoning storage; confidence replacing evidence/recovery/screenshot/safety gates; restoring B07c; site- or lease-specific branches.

## 4. Components and Dependency Direction

```text
User -> Codex Runtime -> Assayer Skill/Policy -> Agent Orchestrator
     -> Assayer MCP -> Runtime Router -> BrowserHostRuntime/HostCore
     -> Chromium + Store + Ledger
```

Skill supplies workflow, rule routing, stop conditions, and output constraints. Agent selects actions and explains decisions. MCP transports structure. Router isolates each Scan runtime. Host never calls LLM or derives business conclusions from natural language.

### 4.1 Page Observation (C07.1)

Before confirming page relationships, Agent may call read-only `observe_page`. It sends the real viewport as MCP image content to the multimodal model and returns aligned filter regions, controls, logical lists, spatial relationships, and operation references. Image aids layout understanding; structure supplies precise references. Host still executes actions, records requests, and recovers Cases. “Looks like” never replaces unique binding evidence.

## 5. Formal Entrypoints

Installed Skill and MCP are the product entrypoint. User provides a URL in Codex CLI or Desktop; Codex loads Skill and the same MCP tools drive the loop. `start_audit` binds URL to a Scan; Router freezes output root, browser config, concurrency, and origin. Bootstrap idempotency maps to one Runtime. `audit` is LLM-driven formal audit; `smoke` is non-publishable Host diagnostics; `serve`/MCP exposes Router only. If Agent Runtime is unavailable, `audit` fails or asks for explicit `smoke`; it never claims intelligent completion.

## 6. Agent Working State

Host-persisted entities are the fact source. Agent keeps reconstructable short-lived state:

```text
ExplorationQueue: unprocessed PageStates and Entrypoints
InvestigationQueue: verified AuditObject x FrozenRule pairs
CurrentInvestigation: applicability, required/attempted/resolved/unresolved dimensions,
                     active Case, Evidence references, next safe probe
```

After context loss, Agent calls progress and rebuilds queues; it never relies on chat memory.

## 7. Core Loop

### 7.1 Explore pages

1. `start_audit` freezes rules, capabilities, and algorithm versions;
2. `inspect_page` returns PageState, Candidates, and safe Entrypoints;
3. Agent selects an unprocessed safe Entrypoint;
4. Host executes and creates a new PageState;
5. Agent classifies every discovered entrypoint as processed, skipped, or unprocessed;
6. Duplicate states, budget exhaustion, and safety blocks converge with explicit `partial`, never fake complete coverage.

### 7.2 Select object and rule

Agent chooses a Candidate for `inspect_object`; Host uniquely verifies and creates AuditObject with potential rules. Agent reads the frozen rule contract and decides applicable, not applicable, or noise from page/object facts. Every potentially applicable pair eventually receives Assessment or an explicit unfinished reason. `potentialRules` is type-level suggestion, not applicability.

### 7.3 Plan and execute Cases

Agent supplies Case kind/purpose, dimensions to attempt, only Host-issued object/control/list/entry references, `valueClass` instead of actual synthetic values, expected DOM/interaction/request/visual facts, safe alternatives, and stop conditions. Host validates and executes. Blocked actions may be replaced by safe DOM/source/visual evidence; otherwise return concrete-blocker `needs_review`.

### 7.4 Evaluate and supplement Evidence

After each round Agent records per-dimension Finding:

| State | Meaning |
|---|---|
| `satisfied` | Evidence supports the required fact. |
| `violated` | Evidence supports a violation. |
| `unresolved` | Attempted, but facts remain non-discriminating. |
| `blocked` | Required action, capability, identity, or recovery unavailable. |
| `conflicted` | Sources conflict. |

Findings are immutable Host entities with object/rule/dimension, rationale, Evidence/Case references, and revision. New facts create a superseding Finding. Agent then adds a Case, requests source/network/visual/interaction Evidence, proceeds after gates, or stops as `needs_review`.

### 7.5 Recover and decide

Every state-changing Case calls `restore_case`; only `restored` Cases support a decision. Agent submits result and final `findingRefs`. Host checks frozen dimensions, closed references, restored Cases, no unresolved state for `scanned_no_issue`, and issue/screenshot gates for `issue_found`; `commit_decision` atomically writes Assessment and derives Issue.

## 8. Coverage Semantics

Coverage is computed from `attemptedDimensions`, `resolvedDimensions`, `unresolvedDimensions`, immutable `dimensionFindings`, and `coverageComplete`. Listing four planned dimensions in a Case means intent only, not complete coverage.

## 9. Required Generic Host Capabilities

`get_rule_contract`, `get_audit_progress`, opaque `controlRef/listRef`, safe synthetic input/query/reset/selection, interaction Evidence with before/after request/list/control facts, immutable `record_findings`, Finding-driven progress reconstruction, and registry-declared generic decision gates. No new action accepts selectors, scripts, arbitrary text, or model-supplied network requests.

## 10. FUA-10 Supplement Example

```text
inspect filter_region
  -> confirm visible filter controls, query, and reset
  -> observe_page for aligned object/control/deduplicated logical lists
  -> establish unique frontend ownership from layout/container/alignment
  -> optionally click safely to confirm reachability
  -> restore_case
  -> decide from Findings
```

FUA-10 is frontend presentation only: request delivery, parameter correctness, backend success, and result change are out of scope. Backend unavailability or unchanged list does not imply `needs_review`. Multiple business lists that remain unattributed after visual/DOM alignment do. For `BINDING_UNRESOLVED`, Host requires an `observe_page` `runtime_visual` containing viewport observation and logical-list set; `pageListCount`, unmerged `list-*`, or ordinary DOM/screenshot evidence alone yields `EVIDENCE_INSUFFICIENT`.

## 11. Prompt Injection and Output Boundary

Page text, properties, source comments, API content, and errors are source-labeled untrusted audit data. Skill says to ignore embedded instructions, authorization claims, and tool requests. Model selects only schema tools/enums. Host rechecks lifecycle, target, intent, and request gates. Persist only structured plans, Findings, rationale, and references, never hidden reasoning. Agent/model failure is explicit partial/failed, never smoke substitution.

## 12. Budgets, Stalls, and Failure Propagation

Each Scan bounds PageStates/Entrypoints, objects and pairs, Cases, per-dimension probes, Agent turns/model retries/total time, and tool/browser operations. Repeated evidence that cannot reduce unresolved dimensions yields `INVESTIGATION_STALLED`.

| Scenario | Convergence |
|---|---|
| Agent available but page/object/Case budget exhausted with scope left | Agent submits justified `partial`. |
| Single-dimension evidence budget exhausted | Object may be `needs_review` with concrete blocker. |
| Transient model failure | Retry within model budget; never replay unknown Host action. |
| Persistent model failure, Agent exit, or lease loss | Supervisor marks Scan `failed`, closes Context, invalidates all formal conclusions. |
| Browser, credential, persistence, or integrity failure | Scan `failed`. |

Model unavailability is not page evidence insufficiency and cannot become object-level `needs_review`. Resume is unsupported; abnormal Agent exit cannot preserve publishable partial results.

## 13. Observability and Reproducibility

Record Skill version/digest, model and Runtime versions, each tool/target/public rationale, Case plans, Findings, stop reason, Host rejections, stale-state/retry outcomes, and Coverage Proof. Do not record hidden reasoning, credentials, cookies, complete DOM, request bodies, or unsanitized sensitive text. Historical Assessments use their frozen rule and Evidence versions and are not silently recomputed.

## 14. C01 Acceptance

Before C02: audit/smoke semantics are distinct; Host/Agent authority is unambiguous; attempted/resolved coverage is explicit; capability gaps are listed without lease-specific adapters; CLI/Desktop share Skill plus dynamic MCP; LLM failure, stall, injection, budget exhaustion, and recovery failure each have one convergence result.

## 15. C04-C06 Implementation Status

C04 implements the Skill, injectable `assayer_agent.AgentLoop`, complete envelopes, revision/idempotency propagation, public decision trace, untrusted-content boundary, unknown-result `get_operation` rule, duplicate/budget stop reasons, and 11-turn HostCore regression. C05 adds RuntimeRouter, fixed output root, URL binding at `start_audit`, lease supervision, shared `.codex` wiring, and `audit`/`smoke` split. C06 removes production `RuleEvaluationEngine` and `BrowserHostRuntime.audit`; smoke emits only non-publishable Host facts and never Assessment/Issue.
