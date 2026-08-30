# 垂直切片 023：通用真实 URL Host 全链路验收（B12）

> C01 解释修正：本切片完成的是固定规划的 deterministic smoke runner，用于证明 Host 全生命周期可运行。它没有接入 LLM 调查控制面，不代表 Agent 已能自主选择对象、设计 Case、补证和作出语义判定。

## 目标

B10/B11 已经证明可以启动真实 Chromium、加载同源只读数据、探索 Host 发现的入口并采集对象 Evidence。B12 将这些能力接成一次完整的 Host smoke 运行：从对象候选开始，穿过固定 observation Case、受控 `focus`、Evidence、恢复屏障、确定性临时判定、正式提交，最后生成账本和派生报告。

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

运行器只消费 Host 协议返回的 `potentialRules`、冻结规则和 Evidence；不从 URL 或业务文案猜测规则结果。规则语义由 Python `RuleEvaluationEngine` 计算，这仅是 smoke oracle，将在 C06 退出正式产品路径。

## 规则判定边界

- smoke oracle 尝试从结构化 Evidence 中计算 FUA-10 的四个维度；这不是 LLM 语义判断。
- 四维全部有运行态事实支持时才允许 `scanned_no_issue`。
- 缺少控件、查询/重置语义或唯一列表绑定时输出 `needs_review`，并携带结构化 blocker。
- B07c 暂缓期间，未脱敏 Raw Visual 不得升级为 `issue_found`；运行器不会伪造问题截图。
- 运行时动作、请求、恢复和提交失败会保留错误并在覆盖证明中体现，不会被强行标记为完成。
- 本切片把 `plannedCoverageDimensions` 汇总为 `coverageComplete` 的做法只适用于当时的 Host 闭环 smoke，不能进入 C02 后的正式覆盖语义；正式覆盖必须区分 attempted、resolved 和 unresolved。

## 验收门槛

- 至少一个真实页面完成上述全部工具调用，`audit-ledger.json` 中存在对应 Operation、Case、Evidence、Assessment 和覆盖证明。
- 同源只读请求可以被归因，写请求、跨源请求、WebSocket 等继续由 Host 发送前阻断。
- 没有候选或证据不足时只能得到可解释的 `needs_review`/`partial`，不能得到伪造的通过或问题结论。
- 生产运行器不包含任何 `lease`、租赁业务文案或特定系统选择器；验收 URL 只出现在命令示例和试跑记录中。
- 全量测试、真实 Chromium 回归和 `git diff --check` 通过；不生成 HTML。

## 结果解释

`completed` 表示 Host smoke 已闭合当时声明的入口、对象和规则处理流程，并不等于所有对象均通过，也不证明 LLM 已完成自主调查。C02 后正式覆盖以逐维 Finding 为准；当前 `needs_review` 只能解释为 smoke oracle 的保守结果。

## 黑盒试跑记录

2026-08-31 使用未带任何站点配置的 `assayer audit` 对用户提供的 `http://localhost:8081/#/lease-mock` 运行：

- 探索 5 个 PageState，发现并唯一绑定 3 个 `filter_region`；
- 3 个对象均完成 Case、focus 动作、结构化 Evidence、Raw Visual、九维恢复、prepare/commit 和最终收束；
- 生成 3 条 `runtime_dom`、3 条 `runtime_visual` 和 3 张 `sanitizationStatus=not_performed` 的真实对象截图；
- 3 个 Case 的恢复结果均为 `restored`，没有观察到写请求，运行错误为 0；
- 3 条 FUA-10 Assessment 均为 `needs_review`：查询和重置事实存在，但当前通用证据不能唯一证明筛选区与业务列表的绑定；没有把页面文案或“看起来像列表”强行解释为通过；
- Scan 正常 `completed`，生成账本和 JSON/Markdown 派生报告，Issue 为 0。

该结果证明完整 Host 运行链路可用，也客观保留了当前 FUA-10 交互绑定能力的边界。后续由 C02/C03 增加通用 control/list 引用、DOM/交互/请求归属和逐维 Finding，由 C04 接入 LLM 调查循环；不能为本样本增加分支。
