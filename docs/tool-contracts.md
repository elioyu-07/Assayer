# 工具级协议契约

> C02 实现说明：冻结规则读取、调查进度、`controlRef/listRef`、逐维 Finding 和 Finding 驱动 Coverage 已落地。真实查询、重置、合成输入和列表绑定观察属于 C03，不在本切片内。

封套字段由 [`schemas/protocol/envelope.schema.json`](../schemas/protocol/envelope.schema.json) 约束；工具专用 input/output 由 [`schemas/protocol/tool-contracts.schema.json`](../schemas/protocol/tool-contracts.schema.json) 约束。两者必须同时通过，不能用宽泛的 `input: {}` 代替工具契约。

| 工具 | Input 定义 | Output 定义 | OperationKind |
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
| `capture_evidence` | `captureEvidenceInput` | `captureEvidenceOutput` | `read` |
| `record_findings` | `recordFindingsInput` | `recordFindingsOutput` | `finding_record` |
| `prepare_decision` | `prepareDecisionInput` | `prepareDecisionOutput` | `decision_preparation` |
| `commit_decision` | `commitDecisionInput` | `commitDecisionOutput` | `decision_commit` |
| `get_operation` | `getOperationInput` | `getOperationOutput` | 不创建 Operation |
| `complete_audit` | `completeAuditInput` | `completeAuditOutput` | `lifecycle` |

语义校验仍由 Host Core 负责：引用闭合、`runRevision`、对象重绑定、规则适用性、覆盖门槛、恢复屏障和 Issue 派生不能仅依赖 JSON Schema。

C02 的对象输出不会暴露 selector、DOM path 或实际字段值；只返回不透明 `controlRef/listRef` 及最小机械属性。`record_findings` 可在一次事务内写入多个不同维度 Finding，新判断只能 supersede 同对象、同冻结规则、同维度的最新 Finding。读取工具不递增 `runRevision`，Finding 批次成功写入只递增一次。

`prepare_decision` 必须显式提交 `findingRefs`，且 `evidenceRefs/caseRefs` 覆盖这些 Finding 的引用并集。Coverage 固定返回 `requiredDimensions`、`attemptedDimensions`、`resolvedDimensions`、`unresolvedDimensions` 和 `complete`；`plannedCoverageDimensions` 不再参与正式覆盖计算。

每条启用规则必须在注册表声明五种结果的 `decisionGates`。Host 用通用的全维度状态、任一状态最小数量和最小 Finding 数解释器执行门禁；主循环不得出现 `ruleId` 条件分支。
