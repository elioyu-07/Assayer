# Tool-Level Protocol Contract

> C05 implementation note: the dynamic MCP/JSON Router exposes one complete tool catalog, including `get_operation`. It creates a Scan Runtime on `start_audit`, routes by `scanId/runId`, and never interprets rules or generates conclusions.

Envelope fields are constrained by [`schemas/protocol/envelope.schema.json`](../schemas/protocol/envelope.schema.json); tool-specific input/output is constrained by [`schemas/protocol/tool-contracts.schema.json`](../schemas/protocol/tool-contracts.schema.json). Both must pass; a broad `input: {}` cannot replace a tool contract.

| Tool | Input definition | Output definition | OperationKind |
|---|---|---|---|
| `start_audit` | `startAuditInput` | `startAuditOutput` | `bootstrap` |
| `get_rule_contract` | `getRuleContractInput` | `getRuleContractOutput` | `read` |
| `get_audit_progress` | `getAuditProgressInput` | `getAuditProgressOutput` | `read` |
| `inspect_page` | `inspectPageInput` | `inspectPageOutput` | `read` |
| `explore_entrypoint` | `exploreEntrypointInput` | `exploreEntrypointOutput` | `browser_action` |
| `inspect_object` | `inspectObjectInput` | `inspectObjectOutput` | `read` |
| `begin_case` | `beginCaseInput` | `beginCaseOutput` | `lifecycle` |
| `perform_action` | `performActionInput` | `performActionOutput` | `browser_action` |
| `restore_case` | `restoreCaseInput` | `restoreCaseOutput` | `recovery` |
| `inspect_source` | `inspectSourceInput` | `inspectSourceOutput` | `read` |
| `observe_page` | `observePageInput` | `observePageOutput` | `read` |
| `capture_evidence` | `captureEvidenceInput` | `captureEvidenceOutput` | `read` |
| `record_findings` | `recordFindingsInput` | `recordFindingsOutput` | `finding_record` |
| `prepare_decision` | `prepareDecisionInput` | `prepareDecisionOutput` | `decision_preparation` |
| `commit_decision` | `commitDecisionInput` | `commitDecisionOutput` | `decision_commit` |
| `get_operation` | `getOperationInput` | `getOperationOutput` | does not create an Operation |
| `complete_audit` | `completeAuditInput` | `completeAuditOutput` | `lifecycle` |

Host Core remains responsible for semantic checks: reference closure, `runRevision`, object rebinding, rule applicability, coverage gates, recovery barriers, and Issue derivation cannot rely on JSON Schema alone.

Object output exposes no selector, DOM path, or actual field value. It returns only opaque `controlRef/listRef` values and minimal mechanical properties. `readOnly` safely rejects text input into read-only facades such as custom dropdowns before browser action. C03 `perform_action` accepts only these references and constrained `valueClass`; Host generates actual values and retains restoration copies only in Session memory. Successful observation returns `interactionEvidenceRef`. One `record_findings` transaction may write multiple dimensions; a new Finding can supersede only the latest Finding for the same object, frozen rule, and dimension. Read tools do not increment `runRevision`; one successful Finding batch increments it once.

`prepare_decision` explicitly supplies `findingRefs`, while `evidenceRefs/caseRefs` cover their referenced unions. Coverage always returns `requiredDimensions`, `attemptedDimensions`, `resolvedDimensions`, `unresolvedDimensions`, and `complete`; `plannedCoverageDimensions` no longer contributes to formal coverage.

Every enabled registry rule declares `decisionGates` for all five results. Host uses a generic interpreter for all-dimension state, minimum any-state counts, and minimum Finding count; the main loop never branches on `ruleId`.

C04 `assayer_agent.AgentLoop` accepts allowlisted structured tool decisions only, creates a complete Session envelope each turn, and propagates the latest `runRevision`. After `result_unknown`, the next turn may only call `get_operation` with the returned reference; the unknown-result action cannot be replayed under a new idempotency key. Page, DOM, source, and Evidence content is untrusted model-context audit data and cannot extend this table's tools or fields.

C05 adds product assembly constraints to `startAuditInput`: formal MCP accepts only `outputDir="auto"`, `browserProfile="default"`, and `authMode="anonymous"`. Router generates the absolute path beneath a fixed root and passes it to Host Core. Direct HostCore/Harness protocol tests may still use controlled absolute output paths. Router lease failure is an internal supervision event; it adds no Agent tool and cannot be triggered by page content.
