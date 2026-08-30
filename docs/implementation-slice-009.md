# 垂直切片 009：审计收束与账本导出

## 交付内容

- 接入 `complete_audit`，校验登录、规则注册表摘要、未提交 PendingDecision、活动 Operation/Case 和 Coverage Universe 引用闭合。
- 所有 Entrypoint 必须恰好归入 processed、skipped 或 unprocessed；重复、交叉、未知引用和缺少跳过原因都会被拒绝。
- Host 从正式 Assessment 重算每条冻结规则的 assessmentCount、五态 resultCounts 和 coverageComplete，不接受 Agent 伪造汇总。
- processed Object 必须已经完成其全部潜在规则判定；未处理页面、对象、入口或规则覆盖会使 Scan 收束为 `partial`。
- 导出聚合 `audit-ledger.json`，包含 Scan、冻结规则、PageState、Entrypoint、AuditObject、Operation、Assessment、Case、Evidence、Screenshot 和 Issue。
- 导出前使用 `scan-run.schema.json` 和 `audit-ledger.schema.json` 校验；文件以临时文件、fsync 和原子硬链接发布，权限为 `0600`，不覆盖冲突文件。
- 成功收束增加一次 runRevision；相同幂等请求返回同一终态和相对 ledgerPath。

## 切片边界

本切片不生成 HTML。面向人的 Markdown/JSON 摘要和问题视图必须从 `audit-ledger.json` 确定性派生，不能反向修改账本；该派生由垂直切片 010 实现。完整端到端 Harness 和真实浏览器适配仍属于后续任务。
