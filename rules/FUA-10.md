# FUA-10 列表筛选必须同时提供查询和重置

| 元信息 | 内容 |
|---|---|
| ruleId | `FUA-10` |
| version | `1.0.0` |
| status | `enabled` |
| owner | `product-owner` |
| document | `rules/FUA-10.md` |
| requiredCapabilities | `runtime, dom, interaction` |
| defaultSeverity | `P2` |

当前 `enabled` 仅表示设计基线已启用；实现完成后仍必须以回归执行结果重新确认注册表发布状态。

## 1. 规则语义

当一个真实页面上的业务列表存在可改变结果集的筛选条件时，筛选区域必须同时提供查询和重置能力。

## 2. 适用性

适用对象是页面中同时包含列表结果区和一个或多个筛选条件的 `filter_region`。只有一个孤立搜索框、静态展示、详情页局部筛选或不改变列表结果的展示控件不适用。

如果无法确认该区域确实控制当前列表，输出 `needs_review`，不能按不适用处理。

## 3. 最低覆盖契约

| 维度 | 要观察的事实 | 证据 |
|---|---|---|
| `filter_present` | 至少一个筛选控件，且其值会影响列表查询语义 | runtime_dom / runtime_interaction |
| `query_action` | 区域有明确发起查询的入口 | runtime_dom |
| `reset_action` | 区域有清空筛选并恢复默认条件的入口 | runtime_dom / runtime_interaction |
| `binding_to_list` | 筛选区与当前列表结果区有唯一归属关系 | runtime_dom / runtime_interaction |

FUA-10 的 `scanned_no_issue` 必须完成全部四个维度。只看到“查询”按钮不能证明完整覆盖。

## 4. Case 生成原则

- 默认使用 `observation` Case 读取筛选区、列表和按钮语义；
- 必要时使用合成筛选值触发查询，但不得提交真实写操作；
- 查询请求可以被观察，但不得因审计而改变持久化数据；
- 若点击重置会触发网络查询，Host 必须确认请求为只读并记录前后状态；
- 每个 Case 都必须恢复筛选值、列表状态和 pending request。

## 5. 五态判定

### `issue_found`

适用性和 `binding_to_list` 已确认，筛选条件存在且查询入口存在，但没有可见重置入口或等价的清空并恢复默认条件能力；证据充分并有独立病灶截图。

### `scanned_no_issue`

四个最低覆盖维度均完成，查询和重置入口均存在，且没有相反证据。

### `not_applicable`

对象不是控制列表结果的筛选区域，或不存在可改变结果集的筛选条件。

### `needs_review`

无法确认筛选区和列表的绑定、按钮语义、对象身份或所需交互能力；不得以“没有看到重置”直接确认问题。

### `noise`

候选区域包含输入框或按钮，但它是页面级搜索、排序、分页或静态展示，不满足 FUA-10 的列表筛选语义。

## 6. 证据和截图

- `scanned_no_issue` 至少引用覆盖四个维度的 Evidence；
- `issue_found` 必须引用缺少重置能力的 runtime Evidence，并生成当前筛选区的独立 IssueScreenshot；
- 源码只能补充按钮归属或事件意图，不能覆盖运行态没有重置入口的事实。

## 7. 严重度

默认 `P2`。若列表是关键业务操作入口且无法恢复筛选条件会造成明显操作成本，可由 Agent 在理由中说明后调整为 `P1`；不得仅因按钮命名差异调整严重度。

## 8. 样本与回归

- 正例：筛选条件 + 查询 + 重置均存在；
- 负例：筛选条件 + 查询存在，但没有重置或清空入口；
- 不适用：详情页内一个不控制列表结果的输入框；
- 噪声：只有分页、排序或页面级关键词搜索；
- 能力缺失：无法读取 DOM 或交互归属 → `needs_review`；
- 身份歧义：两个相似筛选区无法唯一绑定列表 → `needs_review`；
- 截图失败：问题仍可能存在，但正式结果只能为 `needs_review`。
