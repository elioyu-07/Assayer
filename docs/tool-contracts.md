# 工具级协议契约

封套字段由 [`schemas/protocol/envelope.schema.json`](../schemas/protocol/envelope.schema.json) 约束；工具专用 input/output 由 [`schemas/protocol/tool-contracts.schema.json`](../schemas/protocol/tool-contracts.schema.json) 约束。两者必须同时通过，不能用宽泛的 `input: {}` 代替工具契约。

| 工具 | Input 定义 | Output 定义 | OperationKind |
|---|---|---|---|
| `start_audit` | `startAuditInput` | `startAuditOutput` | `bootstrap` |
| `inspect_page` | `inspectPageInput` | `inspectPageOutput` | `read` |
| `inspect_object` | `inspectObjectInput` | `inspectObjectOutput` | `read` |
| `begin_case` | `beginCaseInput` | `beginCaseOutput` | `lifecycle` |
| `perform_action` | `performActionInput` | `performActionOutput` | `browser_action` |
| `restore_case` | `restoreCaseInput` | `restoreCaseOutput` | `recovery` |
| `inspect_source` | `inspectSourceInput` | `inspectSourceOutput` | `read` |
| `capture_evidence` | `captureEvidenceInput` | `captureEvidenceOutput` | `read` |
| `prepare_decision` | `prepareDecisionInput` | `prepareDecisionOutput` | `decision_preparation` |
| `commit_decision` | `commitDecisionInput` | `commitDecisionOutput` | `decision_commit` |
| `get_operation` | `getOperationInput` | `getOperationOutput` | 不创建 Operation |
| `complete_audit` | `completeAuditInput` | `completeAuditOutput` | `lifecycle` |

语义校验仍由 Host Core 负责：引用闭合、`runRevision`、对象重绑定、规则适用性、覆盖门槛、恢复屏障和 Issue 派生不能仅依赖 JSON Schema。
