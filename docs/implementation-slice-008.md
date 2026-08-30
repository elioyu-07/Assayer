# 垂直切片 008：正式判定原子提交

## 交付内容

- 接入 `commit_decision`，从 SQLite 读取 PendingDecision 并在提交点重新校验对象身份、冻结规则摘要、覆盖证明、Evidence 完整性、Case 恢复屏障和 IssueScreenshot。
- 在一个 SQLite 事务内写入不可变 `RuleAssessment`；`issue_found` 同步写入一对一 `Issue`，并更新对象的 `assessmentRefs` 与处理状态。
- PendingDecision 成功提交后标记为 `committed`；已提交或失效的 PendingDecision 不可再次提交。
- 同一 Scan、对象和规则只允许一个正式 Assessment；幂等重试返回同一 Operation 结果，不重复写入。
- 任一提交校验失败时不写入 Assessment/Issue，不增加 runRevision；Case 屏障失效会使 PendingDecision 标记为 `invalidated`。
- 提交前重新校验 Evidence integrityDigest、Screenshot 文件摘要和 Schema，防止准备阶段之后的文件或账本篡改进入正式结论。
- 正式提交前要求所有 Case 已处于 `completed` 且 `recovery.finalStatus=restored`；恢复屏障失效时 PendingDecision 会被标记为 `invalidated`。

## 切片边界

本切片仍不实现 `complete_audit` 的全量 Coverage Universe 收束、账本导出和报告派生；收束和账本导出由垂直切片 009 实现，真实浏览器动作、恢复和截图适配器继续默认 fail-closed。
