# 垂直切片 023：通用真实 URL 全链路验收（B12）

## 目标

B10/B11 已经证明可以启动真实 Chromium、加载同源只读数据、探索 Host 发现的入口并采集对象 Evidence。B12 将这些能力接成一次完整的规则驱动运行：从对象候选开始，穿过 Case、受控动作、Evidence、恢复屏障、临时判定、正式提交，最后生成账本和派生报告。

`lease-mock` 仅是黑盒验收样本。运行器不包含其域名、路由、Tab 名称、业务文案或专属选择器，也不为任何特定前端框架增加分支。

## 通用运行序列

```text
start_audit
  → inspect_page / explore_entrypoint（有界页面探索）
  → inspect_object（Host 唯一身份绑定）
  → begin_case（规则冻结 + 恢复基线）
  → perform_action(focus)（安全、无副作用动作）
  → capture_evidence（结构化运行态事实）
  → restore_case（定向恢复 + 必要时刷新兜底）
  → prepare_decision（规则覆盖和语义字段校验）
  → commit_decision（Assessment 原子写入）
  → complete_audit（账本和派生报告）
```

运行器只消费 Host 协议返回的 `potentialRules`、冻结规则和 Evidence；不从 URL 或业务文案猜测规则结果。

## 规则判定边界

- FUA-10 的四个维度由结构化 Evidence 中的控件语义和列表绑定事实计算。
- 四维全部有运行态事实支持时才允许 `scanned_no_issue`。
- 缺少控件、查询/重置语义或唯一列表绑定时输出 `needs_review`，并携带结构化 blocker。
- B07c 暂缓期间，未脱敏 Raw Visual 不得升级为 `issue_found`；运行器不会伪造问题截图。
- 运行时动作、请求、恢复和提交失败会保留错误并在覆盖证明中体现，不会被强行标记为完成。

## 验收门槛

- 至少一个真实页面完成上述全部工具调用，`audit-ledger.json` 中存在对应 Operation、Case、Evidence、Assessment 和覆盖证明。
- 同源只读请求可以被归因，写请求、跨源请求、WebSocket 等继续由 Host 发送前阻断。
- 没有候选或证据不足时只能得到可解释的 `needs_review`/`partial`，不能得到伪造的通过或问题结论。
- 生产运行器不包含任何 `lease`、租赁业务文案或特定系统选择器；验收 URL 只出现在命令示例和试跑记录中。
- 全量测试、真实 Chromium 回归和 `git diff --check` 通过；不生成 HTML。

## 结果解释

`completed` 表示 Host 已闭合入口、对象和规则覆盖，并不等于所有对象均通过；正式结果以账本中的每条 RuleAssessment 为准。`needs_review` 是有效的保守结论，表示事实不足而不是浏览器失败。

## 黑盒试跑记录

2026-08-31 使用未带任何站点配置的 `agent-f audit` 对用户提供的 `http://localhost:8081/#/lease-mock` 运行：

- 探索 5 个 PageState，发现并唯一绑定 3 个 `filter_region`；
- 3 个对象均完成 Case、focus 动作、结构化 Evidence、Raw Visual、九维恢复、prepare/commit 和最终收束；
- 生成 3 条 `runtime_dom`、3 条 `runtime_visual` 和 3 张 `sanitizationStatus=not_performed` 的真实对象截图；
- 3 个 Case 的恢复结果均为 `restored`，没有观察到写请求，运行错误为 0；
- 3 条 FUA-10 Assessment 均为 `needs_review`：查询和重置事实存在，但当前通用证据不能唯一证明筛选区与业务列表的绑定；没有把页面文案或“看起来像列表”强行解释为通过；
- Scan 正常 `completed`，生成账本和 JSON/Markdown 派生报告，Issue 为 0。

该结果证明完整运行链路可用，也客观保留了当前 FUA-10 交互绑定能力的边界。后续若增强绑定证明，应增加通用的 DOM/交互/请求归属算法与跨站点回归，不能为本样本增加分支。
