# {RULE-ID} {规则名称}

| 元信息 | 内容 |
|---|---|
| ruleId | `{RULE-ID}` |
| version | `1.0.0` |
| status | `draft` |
| owner | `{Owner}` |
| document | `rules/{RULE-ID}.md` |
| requiredCapabilities | `{runtime, dom, ...}` |
| defaultSeverity | `P2` |

## 1. 规则语义

用一句可验证的话描述规则，不写实现方案。

## 2. 适用性

- 适用对象类型：
- 必需前置条件：
- 明确不适用条件：
- 能力缺失时：`needs_review`，不得输出 `not_applicable` 或 `scanned_no_issue`。

## 3. 最低覆盖契约

| 维度 ID | 要观察的事实 | 动作/输入 | 通过结果 | 问题结果 | Evidence 类型 |
|---|---|---|---|---|---|
| `{dimension}` |  |  |  |  |  |

## 4. Case 生成原则

- 允许的 Case 类型：
- 优先顺序：反向 / 边界 / 非法 / 异常 / observation；
- 合成输入类别：
- 禁止动作：
- 恢复要求：

## 5. 五态判定

### `issue_found`

### `scanned_no_issue`

### `not_applicable`

### `needs_review`

### `noise`

## 6. 证据和截图

- 必需 Evidence：
- 可直接运行态确认的条件：
- 是否需要独立 IssueScreenshot：是 / 否（说明原因）

## 7. 严重度

- 默认严重度：
- 允许调整范围：
- 调整理由要求：

## 8. 样本与回归

- 正例：
- 负例：
- 边界例：
- 噪声例：
- 安全/阻断例：
- 截图失败例：
- 恢复失败例：
