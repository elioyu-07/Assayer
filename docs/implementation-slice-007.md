# 垂直切片 007：判定准备与独立问题截图

## 交付内容

- 新增 `pending_decisions` 持久化表和 `pending-decision.schema.json`；PendingDecision 是可重启读取的临时账本事实，不等于正式 Assessment/Issue。
- 接入 `prepare_decision`：Host 校验对象身份、冻结规则、Evidence/Case 引用闭合和规则覆盖，不信任 Agent 自报覆盖完成。
- `issue_found` 必须引用当前对象的 captured Raw Visual；Host 从同一图片字节派生新的 `kind=issue` Screenshot，使用独立实体 ID 和独立文件路径，不能复用 Raw Visual。
- `scanned_no_issue` 只有在规则注册表声明的全部最低覆盖维度均由已恢复 Case 覆盖时才允许准备。
- `needs_review` 必须携带结构化 blocker；`not_applicable`、`noise` 等结果仍保留明确理由和绑定引用。
- PendingDecision 成功写入增加一次 `runRevision`，幂等键重试返回同一 PendingDecision 和问题截图，不写正式 Assessment/Issue。

## 切片边界

本切片不实现 `commit_decision`，也不写入 `RuleAssessment` 或 `Issue`。正式判定提交、对象处理状态更新和提交时的最终 revision 校验属于后续切片。
