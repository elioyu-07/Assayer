# Assayer C03-C07 Execution Roadmap

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-08-31 |
| Status | Historical roadmap; superseded by the completed C03-C07 implementation slices |
| Owner | Agent Runtime / Host Core / Security |

## Purpose

This roadmap defined the work required to turn Assayer into a complete end-to-end
frontend audit journey. A user supplies an allowed test or staging URL; Codex
reads the frozen rule, explores the page, selects objects, plans safe Cases,
collects evidence, records dimension Findings, and submits a Host-validated
conclusion.

## Governing principles

- `assayer audit <url>` is the formal LLM-driven path; `assayer smoke <url>` is a deterministic CI and diagnostic oracle.
- Host owns facts, safety, references, recovery, coverage gates, and ledger integrity. The Agent owns semantic rule decisions.
- Agent and MCP never receive selectors, DOM paths, element handles, raw field values, credentials, or arbitrary script access.
- New rules must not require site-specific branches or edits to the Agent main loop.
- Host state must reconstruct the work queue after context compaction, retry, or process recovery.
- Writes, cross-origin requests, unknown requests, ambiguous objects, and uncertain recovery fail closed.
- B07c screenshot sanitization remains an independent deferred security track.

## Phase sequence

```text
C01 -> C02 -> C03 -> C04 -> C05 -> C06 -> C07 -> C07.1 -> C07.2 -> C07.3
```

| Phase | Scope | Roadmap outcome | Final status |
|---|---|---|---|
| C03 | Generic interaction and binding evidence | Host returns reusable control/list references and before/after facts | completed |
| C04 | Codex Skill and Agent loop | The model runs recoverable multi-round investigations and writes Findings | completed |
| C05 | Dynamic MCP and product entrypoint | A URL-only request starts an isolated, supervised browser Scan | completed |
| C06 | Remove deterministic semantic evaluation from formal paths | `audit` and `smoke` semantics are separated and enforced | completed |
| C07 | Generic regression, black-box acceptance, and release gates | Chromium/MCP no-skip verification and end-to-end delivery are closed | completed |

## Required acceptance areas

1. Discover visible controls and lists with PageState-scoped opaque references.
2. Perform only allow-listed synthetic input, option selection, query, and reset actions.
3. Record minimal before/after observations for controls, lists, pagination, loading state, and read-only requests.
4. Combine mechanical binding facts without having Host interpret a business rule.
5. Rebuild page, object, Case, and rule queues from durable progress.
6. Bound page, object, Case, action, model-turn, and elapsed-time budgets.
7. Resist prompt injection and keep page text as untrusted audit data.
8. Isolate Scan output directories and supervise leases, cancellation, timeout, and abnormal exit.
9. Verify semantic five-state outcomes, evidence references, recovery barriers, and ledger integrity.
10. Run the complete test suite, installation checks, terminal/desktop checks, and documentation review before release.

## Maintenance

The current status and authoritative implementation details live in the
[LLM Agent Investigation-Layer Implementation Plan](llm-agent-integration-plan.md),
the implementation-slice records, and the [User Journey and Definition of Done](user-journey-and-definition-of-done.md).
This document is retained as the historical C03-C07 execution roadmap.
