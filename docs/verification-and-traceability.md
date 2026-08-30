# agent-f 设计验收与追溯矩阵

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | 设计收敛中 |
| Owner | agent-f 维护者 |

## 1. 目的

本文档是进入 Host 编码前的设计 harness：不执行代码，而是要求每条关键设计都能追溯到权威文档、协议入口、Schema 字段、语义校验和可区分验收场景。

## 2. 追溯矩阵

| 条款 | 设计来源 | 协议入口 | Schema/语义校验 | 必须通过的场景 |
|---|---|---|---|---|
| INV-001 Agent 不能造事实 | design-governance | inspect/record | 引用闭合校验 | 伪造 object/evidence/screenshot 被拒绝 |
| INV-005 未知动作默认拒绝 | design-governance, action-safety | perform_action | Operation 状态 | 自定义脚本和未知请求被阻断 |
| INV-006 问题必须有截图 | evidence-integrity | prepare/commit_decision | Issue/Screenshot 绑定 | 截图失败不能提交 issue_found |
| INV-007 未恢复不得提交 | lifecycle, identity-recovery | restore_case/commit_decision | PendingDecision 屏障 | restore_failed 后提交被拒绝 |
| INV-010 幂等与未知结果 | governance, lifecycle | get_operation | Operation 摘要 | 重复键只执行一次，未知结果不盲重放 |
| INV-011 规则冻结 | governance, rule-contract | start_audit | Scan frozenRules | 运行中注册表修改被拒绝 |
| INV-012 凭据不泄露 | action-safety | bootstrap | 敏感字段/日志扫描 | 密码不出现在消息、日志、截图 |
| PageState/Object 身份 | identity-recovery | inspect_page/object | 身份算法版本 | 重渲染唯一重绑、多候选 ambiguous |
| 恢复失败传播 | identity-recovery | restore_case | Case/Scan 语义校验 | 定向失败→刷新成功；刷新失败→partial/failed |
| 五态判定 | rule-contract, evidence-integrity | prepare/commit_decision | RuleAssessment | 覆盖不足不能 scanned_no_issue |
| 唯一事实源 | evidence-integrity | complete_audit | Ledger 事件重放 | 派生报告不能改写账本 |
| 工具参数契约 | host-agent-protocol | 全部工具 | tool-contracts.schema.json | 缺参、未知枚举和结果字段被拒绝 |
| Coverage Universe 闭合 | lifecycle | inspect_page/complete_audit | Entrypoint 引用校验 | processed/skipped/unprocessed 均能回指账本实体 |
| Candidate 不得越权 | lifecycle, identity-recovery | inspect_page/inspect_object | PageCandidate Schema 与升级校验 | Candidate 不能直接创建 Case 或 Assessment |
| 只读快照幂等 | lifecycle, host-agent-protocol | inspect_page | PageState/Operation | 同一快照重复读取不调用浏览器且不增 revision |
| 身份结果机械约束 | identity-recovery | inspect_object | ObjectVerification | matched/not_found/ambiguous/changed 的数量和字段门禁 |
| Candidate 升级事务 | identity-recovery | inspect_object | AuditObject/ObjectVerification | 仅 matched 生成 eligible AuditObject |

状态：`accepted`、`needs_closure`、`blocked`。只有全部安全和结论完整性条款为 `accepted` 才允许编码。

## 3. 纸面验收场景

### 启动与会话

- Bootstrap 请求没有 `scanId/runId` 仍能创建 Scan；普通请求缺失会话身份被拒绝；
- Bootstrap 在创建 Scan 前失败时返回无会话 ID 的失败封套；创建 Scan 后的失败必须带回完整会话封套；
- 登录失败不创建页面对象、不产生问题结论；
- 登录成功后凭据句柄一次性消费并清除。

### Case 与动作

- `begin_case` 创建恢复基线；Case 外动作被拒绝；
- 同一幂等键重复动作不重复点击；同键不同摘要返回冲突；
- 过期 `runRevision` 返回 `STALE_STATE`，不执行动作；
- POST、GraphQL mutation、Beacon、multipart 和未知请求发送前被阻断；
- 动作结果未知时只能 `get_operation`，不能盲目重放。

### 身份与恢复

- 前端重渲染后唯一对象可重新绑定；
- Candidate 恰好一个匹配才升级 AuditObject；多候选不猜测；
- 两个候选都匹配时返回 `ambiguous`；
- 定向恢复 `uncertain` 时自动刷新并重放安全入口；
- 刷新仍失败时 Case `restore_failed`，对象停止；无法排除污染时 Scan `failed`。

### 判定与输出

- `issue_found` 无截图、无 Evidence、无完整覆盖或 Case 未恢复时原子拒绝；
- B07 暂缓期间，真实浏览器路径没有通过验收的截图适配器，因此所有真实 `issue_found` 必须继续被截图门禁拒绝；B08 传输层不得改变该结果；
- `scanned_no_issue` 缺少任一最低维度时拒绝；
- `not_applicable` 不能用 capability 缺失冒充；
- `Issue` 与 `RuleAssessment` 一对一生成；
- Scan `failed` 后正式问题视图为空或明确标记失效；
- `complete_audit` 对未处理入口、跳过原因和规则摘要执行闭合校验。
- `minimal-ledger.json`、`issue-ledger.json`、`partial-ledger.json` 和 `failed-ledger.json` 分别覆盖通过、问题、部分完成和失败终态。

## 4. FUA-10 设计验收

FUA-10 必须至少有以下纸面样本：

1. 筛选条件、查询和重置均存在 → `scanned_no_issue`；
2. 有筛选条件和查询，无重置 → `issue_found`，独立截图；
3. 对象是搜索框但不构成列表筛选区 → `not_applicable`；
4. 页面重渲染后筛选区无法唯一定位 → `needs_review`；
5. 规则所需交互 capability 不可用 → `needs_review`，不能通过；
6. 同一问题现场有两个相似筛选区 → 对象身份 `ambiguous`，不能猜测。

## 5. 设计完成定义

- 追溯矩阵每行都有唯一权威来源和唯一责任层；
- 所有状态、错误、门禁和失败传播都有可区分结果；
- 协议字段命名与 Schema 字段命名一致；
- 示例账本能代表 pass、issue、partial/failed 三类终态；
- 每个工具都有独立 input/output Schema，Coverage Proof 的入口引用能闭合到 Entrypoint 实体；
- 未决问题均有 Owner、决策日期和关闭证据；
- 最终复核没有阻塞项，治理文档将状态更新为 `implementation-ready`。
