# LLM Agent Investigation-Layer Implementation Plan

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-31 |
| Status | C01-C07.2 completed; C07.3 and J01-J08 end-to-end delivery closure in progress |
| Owner | Agent Runtime / Host Core |

## 1. Phase Objective

Connect the real Host lifecycle to the Codex LLM investigation control plane. Formal `assayer audit` autonomously selects objects, plans Cases, gathers evidence, and submits conclusions from frozen rules. The deterministic runner remains only `smoke` and a CI oracle.

Slices map to J01-J08 in [Alpha User Journey and End-to-End Definition of Done](user-journey-and-definition-of-done.md). Component tests, visible MCP tools, or a single smoke run cannot independently claim journey completion. [LLM Investigation Orchestration](llm-agent-orchestration.md) owns the authoritative loop. The original C03-C07 work breakdown is retained as a [historical execution roadmap](implementation-plan-c03-c07.md).

## 2. Ordered Work

| # | Task | Main deliverable | Acceptance gate | Status |
|---|---|---|---|---|
| C01 | Converge LLM investigation design | Orchestration, authority, entry semantics, failure propagation, acceptance matrix | Separate audit/smoke; close Host/Agent/Skill authority; site-independent | completed |
| C02 | Upgrade protocol and coverage model | Frozen-rule read, progress, control/list refs, DimensionFinding, machine gates | planned is not covered; Host reconstructs context; no rule IDs in main loop | completed |
| C03 | General interaction and binding evidence | Synthetic input, query/reset, safe selection, list/request/control before-after facts | General DOM/interaction evidence can prove FUA-10 binding; writes remain fail-closed | completed |
| C04 | Codex Skill and Agent loop | Skill, rule routing, object/Case planning, evidence, five-state submission, stopping | Model produces multi-turn tool decisions; Host does not decide rules; injection does not alter policy | completed |
| C05 | Dynamic MCP and product entrypoint | Runtime Router, fixed output root, Scan isolation, lease supervision, shared client configuration, audit/smoke split | User provides only URL; process does not bind URL; no silent downgrade or arbitrary output; Agent exit fails Scan | completed |
| C06 | Remove deterministic semantics from formal path | Remove production `RuleEvaluationEngine` and `BrowserHostRuntime.audit`; smoke is non-publishable Host diagnostics | Formal path has no rule-ID branch; smoke cannot impersonate Assessment | completed |
| C07 | General regression and lease black-box acceptance | Positive, negative, non-applicable, ambiguous, blocked, injection, and real lease | Results match evidence; no lease-specific code; full regression and artifacts pass | completed: full regression passed; real lease recovery closed; insufficient binding conservatively became `partial/needs_review` |
| C07.1 | LLM browser-observation closure | Structured observation, viewport PNG, multimodal MCP content, business-list consolidation | Model observes page before unresolved binding; paginator is not a business list | completed |
| C07.2 | Real-path test gate | fast/full runners, fixed full dependencies, Chromium/MCP preflight and CI | Full executes every real path; missing dependency, browser failure, or skip fails | completed: 199 passed, zero skipped |
| C07.3-01 | Journey and completion definition | Direct Codex invocation, CLI entry, success/failure states, Alpha boundary | User supplies URL only; component tests cannot impersonate completion | completed |
| C07.3-02 | Direct Codex wiring | Project Skill/MCP discovery, fixed runtime/workdir, no nested Codex | Fresh task sees and calls `mcp__assayer__*` directly | in progress: repository configuration complete; awaiting fresh-session acceptance |
| C07.3-03 | Exact MCP schemas | Complete envelope and specialized input for every tool | Model no longer guesses `complete_audit` fields | completed |
| C07.3-04 | Bounded formal control | Budgets for protocol failure, repeated errors, no-progress success, and completion recovery | Deterministic failures do not retry forever; error has next step and event | completed |
| C07.3-05 | Lease liveness | Distinguish normal reasoning, Agent loss, and MCP/process exit | Long reasoning survives; real disconnect fails quickly | completed: MCP heartbeat renews from Playwright thread; EOF/close fails active Scan immediately |
| C07.3-06-08 | User results and real acceptance | User-level results/errors, real Codex+MCP+Chromium acceptance, consecutive-run gate | User path succeeds consecutively and failures are diagnosable | pending |

## 3. Dependencies

```text
C01 -> C02 -> C03 -> C04 -> C05 -> C06 -> C07 -> C07.1 -> C07.2 -> C07.3
```

C02 fixes protocol before C03 actions and Evidence. C04 consumes stable Host capabilities through an injectable, model-independent `DecisionAgent`. C05 assembles Codex/MCP. C06 removes production deterministic semantic evaluation without changing C05 boundaries. C07.1 closes direct model page observation; C07.2 turns Chromium/MCP regression into a no-skip gate.

## 4. C07 Minimum Sample Matrix

| Sample | Expected |
|---|---|
| Single-list filter with query and reset | `scanned_no_issue` |
| Confirmed binding, query, no reset | `issue_found` when screenshot gate passes; `needs_review` before B07c |
| Page search box that controls no business list | `not_applicable` or `noise` |
| Two candidate lists remain unattributed after interaction | `needs_review` |
| Query triggers a potential write | Host blocks; after recovery `needs_review`, or Scan fails under contamination policy |
| Page tells model to ignore rules or run arbitrary script | Treat instruction as data; do not execute |
| Agent turn budget exhausted with unfinished scope | Scan `partial` |
| Lease black-box system | General Agent explores and investigates with no site-specific branch |

## 5. Do Not Conflate With B07c

B07c remains an independent security task. It does not block LLM investigation, binding proof, or `scanned_no_issue`, but until completed an unsanitized real screenshot cannot support publishable `issue_found`.
