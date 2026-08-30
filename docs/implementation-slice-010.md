# 垂直切片 010：确定性报告派生

## 交付内容

- 新增纯函数式 `DerivedReportBuilder`，只读取已经通过 Schema 校验的 `audit-ledger.json`，不查询浏览器、不重新判断规则、不修改账本。
- 输出 `issues.json`：只展示有效 `issue_found`；Scan 失败或 Issue 已失效时从主问题列表隐藏，并保留 invalidatedIssueRefs。
- 输出 `page-element-judgement.json`：按页面、对象、规则保留全部五态 Assessment、覆盖、Evidence、Case、截图和 blocker 引用。
- 输出 `run-diagnostics.json`、`run-diagnostics.md`、`audit-summary.md` 和不含请求参数/正文的稳定 `audit.log`。
- 全部 JSON 使用排序键和稳定缩进，并记录规范化源账本 SHA-256；Markdown 对换行和尖括号做中和，不解释或执行账本文本。
- 账本和全部派生产物作为一个发布批次预检；任何同名内容冲突都会拒绝发布，新创建文件在失败时回滚。
- `complete_audit` 返回相对 `artifactPaths`；所有文件权限为 `0600`，不暴露 Host 的绝对 outputDir。

## 输出原则

当前版本明确不生成 HTML。派生报告不能添加账本中不存在的问题、严重度、证据或结论，也不能把 `needs_review`、`noise`、`not_applicable` 或 `scanned_no_issue` 混入主问题列表。

## 切片边界

本切片完成只读报告派生；完整端到端 Harness、真实浏览器适配器和可发布运行入口属于最后一个任务。
