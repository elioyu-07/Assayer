# agent-f 数据 Schema

这些文件使用 JSON Schema Draft 2020-12，描述一次审计的可持久化实体和规则注册表。相对 `$ref` 以本目录为基准。

## 文件

- `common.schema.json`：ID、时间、摘要、规则引用、结果状态、严重度、坐标和源码位置等共享类型。
- `scan-run.schema.json`：一次扫描的输入边界、冻结规则集合、状态和覆盖证明。
- `page-state.schema.json`：可复盘的页面/弹窗/抽屉/Tab/详情/编辑状态。
- `entrypoint.schema.json`：Coverage Universe 中可处理、跳过或待处理的页面入口。
- `page-candidate.schema.json`：页面发现阶段产生、尚未升级为正式 AuditObject 的候选。
- `object-verification.schema.json`：Candidate 升级或 AuditObject 重绑的机械身份判定记录。
- `audit-object.schema.json`：真实运行页面中发现的待检查对象。
- `operation.schema.json`：Host 请求的幂等执行记录和结果已知性。
- `rule-assessment.schema.json`：一个对象 × 一条规则的固定五态判定。
- `reverse-case.schema.json`：Agent 规划、Host 安全执行的反向 Case，以及动作前基线、反向动作、定向恢复、验证结果和刷新兜底记录。
- `action-attempt.schema.json`：一次 Case 动作的 Host 安全决策、目标和请求观察引用。
- `request-observation.schema.json`：外发请求的脱敏机械分类与是否已发送事实。
- `recovery` 结构嵌入 `reverse-case.schema.json`；恢复适配器必须为全部必检维度提供 `match/mismatch/unknown` 检查。
- `evidence.schema.json`：Host 生成的不可变、已脱敏证据。
- `screenshot.schema.json`：对象病灶截图及其定位状态。
- `issue.schema.json`：由 `issue_found` 产生的正式问题项。
- `pending-decision.schema.json`：`prepare_decision` 生成的、尚未进入正式账本的临时判定。
- `rule-registry.schema.json`：可扩展、可版本化的规则注册表。
- `audit-ledger.schema.json`：把上述实体聚合为完整审计账本。

`audit-ledger.schema.json` 描述的聚合账本是唯一运行事实源。主问题报告、`page-element-judgement.json`、诊断 Markdown 和 HTML 都必须由账本确定性派生，不能反向修改账本。

## Schema 与 Host 语义校验的边界

JSON Schema 能校验字段类型、枚举、必填项和局部条件，但不能可靠表达跨数组的引用闭合。因此 Host 在落账前还必须做语义校验：

1. 所有实体 ID 在当前账本内唯一，引用必须指向同一扫描中的实体；Coverage Proof 的入口引用必须指向 `entrypoints`；
2. `scan.frozenRules` 必须存在于 `ruleRegistry.rules`，且摘要与注册表内容一致；
3. 对象、页面状态、Case、证据、截图、判定和问题的关联必须闭合；
4. `issue_found` 必须引用当前对象的有效证据和独立的 `captured` 截图，截图对象和页面状态必须匹配；
5. `scanned_no_issue` 必须满足规则注册表声明的覆盖契约；
6. 登录失败或运行中断时，正式问题结论必须标记为无效；
7. 规则 ID 不得复用，规则语义变化必须使用新版本；扫描期间注册表快照冻结。
8. Case 恢复中，定向尝试为 `uncertain` 或 `failed` 时必须存在后续刷新重放尝试；最终只有全部必检项为 `match` 的 `restored` 才允许继续调查。
9. Operation 的 `idempotencyKey` 在 Scan 内唯一；同键请求摘要必须一致，`result_unknown` 不允许盲目重放。
10. `begin_case` 的同一对象、同一规则活动唯一性由 Host 语义校验和 SQLite 唯一索引双重保证；动作必须引用真实 Case。
11. `prepare_decision` 只生成 PendingDecision 和必要的独立 IssueScreenshot，不写 Assessment/Issue；正式 Assessment 只能由已越过恢复屏障的 PendingDecision 提交；Issue 与 `issue_found` Assessment 一对一。
12. `failed` Scan 的所有 Assessment 和 Issue 在派生视图中必须视为失效；`partial` 只保留未被失效事件覆盖的结论。
13. 身份、恢复、脱敏和规范化算法版本必须与 Scan 冻结版本一致。
14. Evidence/Screenshot 只能绑定当前 Scan 中真实的 PageState 和唯一验证对象；Raw Visual 失败记录不能作为 captured IssueScreenshot 使用。
15. `complete_audit` 必须让全部入口恰好归入 processed、skipped 或 unprocessed，并由 Host 从正式 Assessment 重算规则摘要；最终 `audit-ledger.json` 必须通过聚合 Schema 后原子写出。

## 摘要与规范化

- 所有 JSON 摘要使用 UTF-8 和确定性序列化：对象键排序，数组保留语义顺序；
- 实体摘要排除自身 digest 字段、随机实体 ID、写入时间和本地路径等非事实字段；
- 规则 `contentDigest` 摘要规则文件原始 UTF-8 字节；
- 注册表 `digest` 摘要移除顶层 `digest` 后的规范化注册表；
- Screenshot digest 摘要最终不可变图片字节；
- 具体算法版本和设计约束见 `docs/evidence-and-decision-integrity.md`。

## 协议与账本的边界

协议请求、响应和 PendingDecision 是运行时交互对象，不等于最终账本实体。Operation 保存幂等执行事实；PendingDecision 只存在于提交事务完成前，不进入最终正式账本。协议封套的机器约束见 `schemas/protocol/envelope.schema.json`，工具专用 input/output 见 `schemas/protocol/tool-contracts.schema.json`。

## 扩展规则

增加规则只需新增注册表条目、规则文档和回归样本；只要复用现有 Host 能力，就不需要修改这些核心 Schema。规则不得新增判定状态，也不得绕过证据和截图门槛。
