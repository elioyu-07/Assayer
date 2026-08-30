# Assayer LLM 调查编排设计

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-31 |
| 状态 | C01 设计基线 |
| Owner | Agent Runtime / Assayer 维护者 |

## 1. 目的

本文档是 LLM 调查控制面的权威设计，定义 Codex Agent 如何基于冻结规则驱动 Host 完成页面探索、对象选择、Case 规划、补证和五态判定。

Host 的事实、安全、恢复和事务边界仍分别由[顶层架构](architecture.md)、[动作安全](action-safety-and-credentials.md)、[领域模型](domain-model-and-lifecycle.md)和[证据完整性](evidence-and-decision-integrity.md)拥有。本文档不允许 LLM 覆盖这些边界。

## 2. 当前实现偏差

当前 `BrowserHostRuntime.audit` 是一个确定性全链路运行器。它按固定顺序选择 Candidate、创建 observation Case、执行 `focus`、采集 Evidence，并由 Python `RuleEvaluationEngine` 直接生成规则结果。

该路径证明了真实浏览器、Host 协议、恢复和账本能够闭环，但不等于产品定义中的 LLM 审计 Agent，因为它没有让 LLM：

- 判断规则对当前对象是否实际适用；
- 根据缺失覆盖维度设计 Case；
- 在证据不足时选择下一步补证；
- 综合 DOM、交互、网络、源码和视觉事实；
- 提交最终语义判定。

从 C01 开始，当前路径定义为 **deterministic smoke runner**。在 C04/C05 完成前，CLI 不得把它描述为正式 LLM 审计。

## 3. 目标与非目标

### 3.1 目标

1. Codex Agent 成为唯一业务语义调查控制面；
2. Host 继续成为页面事实、动作安全、恢复和账本的唯一执行面；
3. LLM 每次只处理一个 `AuditObject × Rule` 的精简上下文；
4. 证据不足时允许多轮补证，而不是固定一次 observation 后结束；
5. 覆盖区分“尝试检查”和“事实已解决”；
6. 同一个 Skill 与 MCP Host 同时支持 Codex 终端和桌面客户端；
7. 新增规则不要求在 Python 主循环中增加规则 ID 分支。

### 3.2 非目标

- 不把模型 SDK 或模型密钥放进 Host Core；
- 不允许模型直接操作 Playwright、传入 selector、JavaScript 或任意 URL；
- 不保存或要求模型隐藏推理过程；
- 不用 LLM 置信度替代证据、恢复、截图或安全门禁；
- 不在本阶段恢复 B07c 自动像素脱敏；
- 不为 lease 或任何特定站点增加文案、路由、域名或 DOM 分支。

## 4. 目标组件和依赖方向

```text
用户
  → Codex Runtime
    → Assayer Skill / Policy
      → Audit Agent Orchestrator（LLM 调查循环）
        → Assayer MCP tools
          → Runtime Router
            → BrowserHostRuntime / HostCore
              → Chromium + Store + Ledger
```

依赖只能向下：

- Skill 提供流程、规则路由、停止条件和输出约束；
- Agent Orchestrator 选择下一项调查动作并解释结论；
- MCP 只传输结构化请求；
- Runtime Router 根据 Scan 创建和路由隔离的 Host Runtime；
- Host 不调用 LLM，也不从自然语言生成业务结论。

## 5. 正式运行入口

### 5.1 Codex 原生入口

正式产品入口是安装后的 Assayer Skill 与 MCP Server。用户在 Codex 终端或桌面客户端提供 URL 后，Codex 加载 Skill，并通过同一组 MCP tools 驱动调查循环。

MCP Server 不应要求在进程启动参数中预绑定业务 URL。`start_audit` 接收 URL，Runtime Router 完成校验后创建该 Scan 独占的 `BrowserHostRuntime`；后续请求按 `scanId/runId` 路由。这样终端和桌面客户端不需要为每个 URL 临时改 MCP 配置。

Runtime Router 启动时必须固定允许的输出根目录、浏览器配置和并发上限。Agent 只能选择输出根目录下的运行目录，不能借 `outputDir` 写入任意路径；URL 仍需满足无凭据 HTTP(S)、显式用户输入、单 Scan origin 冻结和现有请求安全门禁。Bootstrap 幂等键必须映射到同一个 Runtime，不能因重试重复打开浏览器。

### 5.2 CLI 入口命名

- `assayer audit`：C04/C05 完成后，只代表由 Codex LLM 驱动的正式审计；
- `assayer smoke`：保留当前确定性运行器，用于 CI、Host 集成回归和故障诊断；
- `assayer serve` / MCP：暴露动态 Runtime Router，不做规则判断。

如果正式 Agent Runtime 不可用，`audit` 必须明确失败或要求改用 `smoke`，不能静默降级后仍宣称完成智能审计。

## 6. Agent 调查状态

Agent 使用 Host 已持久化的实体作为事实，不另建第二事实账本。模型侧只维护可重建的短期工作状态：

```text
ExplorationQueue
  - 未处理 PageState / Entrypoint

InvestigationQueue
  - 已验证 AuditObject × FrozenRule

CurrentInvestigation
  - applicability hypothesis
  - required dimensions
  - attempted/resolved/unresolved dimensions
  - active Case
  - evidence references
  - next safe probe
```

模型上下文丢失时，Agent 必须通过 Host 的调查进度接口重建状态，不能依赖聊天记忆猜测已完成范围。

## 7. 核心调查循环

### 7.1 页面探索

1. `start_audit` 冻结规则、能力和算法版本；
2. `inspect_page` 获取当前 PageState、Candidate 和安全 Entrypoint；
3. Agent 选择未处理的安全 Entrypoint；
4. Host 执行并生成新 PageState；
5. Agent 将所有 Host 已发现入口归入 processed、skipped 或 unprocessed；
6. 重复状态、预算耗尽或安全阻断只允许形成有理由的 `partial`，不能伪装为完整覆盖。

### 7.2 对象与规则选择

1. Agent 从 Candidate 中选择一个进行 `inspect_object`；
2. Host 唯一验证后生成 AuditObject，并返回 `potentialRules`；
3. Agent读取本 Scan 冻结版本的规则契约；
4. Agent 根据页面和对象事实判断 applicable、not applicable 或 noise；
5. 每个可能适用的 `Object × Rule` 必须最终有 Assessment 或明确的未完成原因。

Host 的 `potentialRules` 只是类型级候选，不是适用性结论。

### 7.3 Case 规划与执行

Agent 先生成结构化 Case 计划：

- Case kind 与目的；
- 本 Case 要尝试解决的维度；
- 只引用 Host 返回的对象、控件、列表和入口 ID；
- 合成输入只指定 `valueClass`，实际值由 Host 生成；
- 预期观察的 DOM、交互、请求或视觉事实；
- 安全替代路径和停止条件。

Host 校验并执行动作。动作被阻断时，Agent 可以选择静态 DOM、源码或其他安全证据；没有替代路径时输出带具体 blocker 的 `needs_review`。

### 7.4 证据评估与补证

每轮证据返回后，Agent 通过 `record_findings` 为相关覆盖维度提交简明 Finding：

| 状态 | 含义 |
|---|---|
| `satisfied` | 证据支持规则要求的事实。 |
| `violated` | 证据支持规则要求被违反。 |
| `unresolved` | 已尝试但现有证据不能区分。 |
| `blocked` | 所需动作、能力、身份或恢复条件不可用。 |
| `conflicted` | 多来源事实冲突。 |

`DimensionFinding` 是 Host 持久化的不可变实体，至少包含 `findingId`、对象引用、冻结规则、维度、状态、公开简明理由、Evidence/Case 引用和 `createdAtRevision`。同一维度获得新事实时创建新 Finding，并通过 `supersedesRef` 指向旧 Finding；不覆盖历史记录。Host 校验引用闭合和规则维度名称，但不替 Agent 判断 Evidence 的业务含义。

Agent 根据未解决维度决定：

- 新建补充 Case；
- 请求源码、网络、视觉或交互证据；
- 已满足判定门槛，进入恢复和判定；
- 达到停止条件，以 `needs_review` 收束。

### 7.5 恢复和判定

1. 每个产生页面状态的 Case 都必须 `restore_case`；
2. Host 只有确认 `restored` 才允许引用该 Case；
3. Agent 按规则五态条件提交结果和最终 `findingRefs`；
4. Host 根据注册表的机器门禁校验：
   - 维度属于冻结规则；
   - 引用闭合；
   - Case 已恢复；
   - `scanned_no_issue` 没有 unresolved/blocked/conflicted；
   - `issue_found` 满足规则声明的问题确认门槛和截图门槛；
5. `commit_decision` 原子写入 Assessment，必要时派生 Issue。

## 8. 覆盖语义修正

覆盖不能再由 `plannedCoverageDimensions` 直接推导。正式模型必须包含：

- `attemptedDimensions`：Agent 至少执行过一次有区分力的检查；
- `resolvedDimensions`：已有 `satisfied` 或 `violated` Finding；
- `unresolvedDimensions`：Finding 为 unresolved、blocked 或 conflicted；
- `dimensionFindings`：每个维度的状态、理由和 Evidence 引用；
- `coverageComplete`：规则结果要求的维度均已解决且门禁满足。

“四个维度都被列入 Case 计划”只表示计划完整，不表示覆盖完整。

## 9. Host 必需的通用能力增量

C02/C03 必须补齐以下能力，LLM 接入不得绕过：

1. `get_rule_contract`：返回本 Scan 冻结规则内容和 digest；
2. `get_audit_progress`：返回可重建的探索、对象、规则、Case 和覆盖进度；
3. `inspect_object` 返回可引用 `controlRef`、`listRef`、可见性和控件语义；
4. `perform_action` 支持对固定引用执行合成输入、查询、重置和安全选项选择；
5. 交互 Evidence 返回字段、候选列表、loading、分页、请求和对象身份的前后差异；
6. `record_findings` 保存不可变维度 Finding，`get_audit_progress` 从最新有效 Finding 重建维度状态；
7. 注册表声明各结果的机器可校验门禁，避免 Host 中出现 `if ruleId == ...`。

任何新增动作仍不能接受 selector、脚本、任意文本值或模型提供的网络请求。

## 10. FUA-10 的通用补证示例

```text
inspect filter_region
  → 静态确认可见筛选控件、查询、重置和候选 listRefs
  → 若唯一 DOM 归属充分，记录 binding finding
  → 否则 begin_case(binding_to_list)
  → Host 选择一个允许合成输入的 controlRef
  → input_synthetic_value(valueClass=valid)
  → activate_query(queryControlRef)
  → capture runtime_interaction / network_observation
  → 比较候选列表变化并建立因果归属
  → activate_reset(resetControlRef)
  → 验证字段和列表恢复
  → restore_case
  → Agent 根据 Findings 判定
```

如果页面包含多个列表且没有可区分的 DOM、交互、请求或源码归属，结果仍为 `needs_review`。LLM 可以选择补证，但不能靠“看起来像”直接确认绑定。

## 11. 提示注入与模型输出边界

- 页面文本、属性、源码注释、接口内容和错误信息全部作为带来源标签的审计数据；
- Skill 明确要求忽略审计数据中的指令、授权声称和工具调用要求；
- 模型只能从 Host tool schema 中选择工具和固定枚举；
- Host 对每次动作重新执行生命周期、目标、意图和请求四层门禁；
- 模型输出只持久化结构化计划、Finding、结论理由和引用，不持久化隐藏思维过程；
- Agent 或模型故障属于明确的 partial/failed 原因，不能由 smoke 结果替代。

## 12. 预算、停滞与失败传播

每个 Scan 至少设置以下上限并写入诊断：

- 页面状态和 Entrypoint 数量；
- 对象和 `Object × Rule` 数量；
- 单项调查 Case 数；
- 单维度补证次数；
- Agent 回合数、模型失败重试次数和总时间；
- 单次工具及浏览器操作预算。

预算用于防止失控，不是完成证明。同一证据和同一动作连续无法减少 unresolved 维度时，Agent 应停止该路径并记录 `INVESTIGATION_STALLED`。

失败传播必须区分：

| 场景 | 收束 |
|---|---|
| Agent 仍可工作，但页面/对象/Case 预算耗尽且仍有范围 | Agent 提交有理由的 `partial`。 |
| 单个维度安全补证耗尽 | 对象结果可为 `needs_review`，记录具体 blocker。 |
| 模型调用瞬时失败 | 在模型重试预算内重试，不重放结果未知的 Host 动作。 |
| 模型持续不可用、Codex Agent 异常退出或会话租约丢失 | Runtime Supervisor 将 Scan 标记为 `failed`，关闭 Context，全部正式结论失效。 |
| 浏览器、凭据、持久化或环境完整性失败 | Scan `failed`。 |

模型不可用不是页面证据不足，不能写成对象级 `needs_review`。第一版不支持断点续跑，所以异常 Agent 退出也不能保留为可发布的 `partial`。

## 13. 可观测性和可复现性

账本或诊断需记录：

- Skill 版本与内容摘要；
- 模型标识和 Agent Runtime 版本；
- 每轮选择的工具、目标引用和公开简明理由；
- Case 计划、维度 Finding 和停止原因；
- Host 拒绝、过期状态和重试结果；
- 最终 Coverage Proof。

正式账本新增 `dimensionFindings` 集合；RuleAssessment 引用最终 `findingRefs`。中间 Finding 保留用于解释补证过程，但不自动成为最终规则结论。

不记录模型隐藏推理、凭据、Cookie、完整原始 DOM、请求体或未脱敏敏感文本。历史 Assessment 只按当时冻结规则和证据解释，不用新模型静默重算。

## 14. C01 验收结论

C01 只收敛设计，不宣称 LLM 已接入。进入 C02 前必须满足：

1. `audit` 与 `smoke` 的产品语义已区分；
2. Host 不调用模型、Agent 不直接控制浏览器的边界无歧义；
3. 覆盖模型明确区分 attempted 与 resolved；
4. 工具能力缺口已列出，不依赖 lease 专属适配；
5. Codex 终端和桌面共用 Skill + 动态 MCP 的路径明确；
6. LLM 故障、停滞、提示注入、预算耗尽和恢复失败均有唯一收束结果。
