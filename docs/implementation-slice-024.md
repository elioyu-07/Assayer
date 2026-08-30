# 垂直切片 024：协议与覆盖模型升级

## 目标

完成 LLM 调查层 C02，使 Host 能提供冻结规则、可重建进度和可引用对象结构，并以不可变 `DimensionFinding` 取代 Case 计划维度作为正式覆盖依据。

## 已实现

- 新增 `get_rule_contract`：只读取当前 Scan 冻结启用规则，校验规则文件位于 `rules/` 下且 SHA-256 与注册表一致；读取不递增 revision。
- 新增 `get_audit_progress`：返回页面、入口、对象、活动 Case、PendingDecision 和逐对象/规则 Finding 覆盖快照；不生成业务建议。
- 新增 `record_findings`：一次事务写入一个或多个不同维度 Finding，批次只递增一次 revision；替代引用必须指向同对象、规则、维度的最新 Finding。
- `inspect_object` 返回不透明 `controlRef/listRef` 和最小机械属性，不返回 selector、DOM path 或字段值。确定性适配器提供协议样本，真实浏览器发现留给 C03。
- PendingDecision、RuleAssessment 和最终 Ledger 增加 `findingRefs` / `dimensionFindings`。
- Coverage 改为 `required/attempted/resolved/unresolved/complete`；只有 `satisfied/violated` 属于 resolved，未恢复 Case 上的 staged Finding 不参与覆盖。
- 通用机器门禁不包含规则 ID 分支：无问题要求全部必需维度 satisfied；问题至少一个 violated；待复核至少一个 unresolved/blocked/conflicted；不适用和噪声至少一个有效 Finding。

## 验证

- 原有完整测试与新增 C02 回归通过，共 159 个 unittest。
- Playwright/浏览器/Harness 定向回归 32 项通过；完整 pytest 为 `159 passed, 12 subtests passed`。
- 新增覆盖冻结规则 digest、进度重建、staged Finding、supersede、未解决维度门禁、MCP 工具枚举和 opaque refs。
- 四个示例账本已迁移到 `dimensionFindings` 和新 Coverage 结构。
- 对 `http://localhost:8081/#/lease-mock` 的通用 smoke 回归访问 5 个页面状态、识别 3 个对象并持久化 12 条 Finding；3 个 Assessment 均因 `binding_to_list` 未解决而为 `needs_review`，Scan 正确收束为 `partial`，无站点专属分支。

## 非目标

本切片不实现查询、重置、合成输入、列表前后差异或网络绑定证据；这些属于 C03。`BrowserHostRuntime.audit` 仍是 deterministic smoke，不是正式 LLM Agent 审计入口。
