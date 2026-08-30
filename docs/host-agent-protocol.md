# agent-f Host–Agent 协作协议

## 1. 目的和适用范围

本文档定义 Codex Agent 与 agent-f Host（审计执行器）之间的协作协议，承接：

- [产品契约](product-contract.md)
- [顶层架构](architecture.md)

协议同时适用于 MCP 和 CLI。MCP/CLI 只是传输适配层，必须调用同一套 Host Core，不得各自实现浏览器操作、安全判断、证据生成或落账逻辑。

本文档定义“消息和边界”，不定义具体 Python 类、MCP SDK 用法或浏览器驱动细节。

## 2. 核心原则

1. **Host 是事实和安全边界**：页面、请求、源码、截图、对象和证据 ID 只能由 Host 生成或验证。
2. **Agent 是调查控制面**：Agent 选择对象、规范、反向 Case 和补证路径，并解释结论。
3. **Skill 是判断依据**：规范文件和 Policy 定义适用性、覆盖要求、反向 Case 和判定标准。
4. **请求/响应必须结构化**：不使用自然语言作为工具协议；自然语言只存在于 Agent 的解释字段。
5. **引用必须闭合**：Agent 只能引用当前扫描中 Host 已返回的对象、Case、证据和截图 ID。
6. **拒绝优先**：安全不确定、状态过期、引用非法或违反生命周期时，Host 拒绝请求，不猜测放行。
7. **运行态优先**：源码用于补证和解释，不能覆盖已观察到的运行态事实。

## 3. 交互模型

```text
Agent 读取 Skill/规则
        ↓
Agent → Host：结构化工具请求
        ↓
Host：校验扫描身份、状态、引用和安全策略
        ↓
Host：执行或拒绝，并写入不可变事实
        ↓
Host → Agent：精简证据包/拒绝原因
        ↓
Agent：规划下一步或提交判定
        ↓
Host：校验判定并落账
```

Host 不主动给 Agent 下业务结论；Host 只返回事实、能力结果、拒绝原因和完整性校验结果。Agent 不直接访问浏览器、文件系统、网络或完整源码仓库。

## 4. 公共消息封套

每一次 MCP 调用和 CLI JSON 请求都使用同一封套：

```json
{
  "protocolVersion": "1.0",
  "requestId": "req-uuid",
  "scanId": "scan-uuid",
  "runId": "run-uuid",
  "agentTurnId": "turn-uuid",
  "tool": "inspect_object",
  "idempotencyKey": "scan-uuid:turn-uuid:inspect_object:obj-123",
  "expectedStateVersion": 18,
  "input": {}
}
```

### 4.1 封套字段

| 字段 | 要求 |
|---|---|
| `protocolVersion` | 当前固定为 `1.0`；不支持的版本直接拒绝 |
| `requestId` | 每个请求唯一，用于日志和错误关联 |
| `scanId` / `runId` | 必须匹配当前活动扫描；跨扫描引用拒绝 |
| `agentTurnId` | 标识一次 Agent 决策回合 |
| `tool` | 必须是注册工具名 |
| `idempotencyKey` | 同一逻辑请求重试时保持不变；Host 返回相同结果，不重复执行有副作用的动作 |
| `expectedStateVersion` | Agent 看到的页面/账本版本；过期时返回 `STALE_STATE`，不执行动作 |
| `input` | 工具专用结构化参数 |

Host 响应也必须带回 `protocolVersion`、`requestId`、`scanId`、`runId` 和新的 `stateVersion`。

## 5. 统一响应格式

成功响应：

```json
{
  "protocolVersion": "1.0",
  "requestId": "req-uuid",
  "scanId": "scan-uuid",
  "runId": "run-uuid",
  "stateVersion": 19,
  "status": "ok",
  "result": {},
  "evidenceRefs": ["ev-001"],
  "diagnosticRefs": []
}
```

拒绝或失败响应：

```json
{
  "protocolVersion": "1.0",
  "requestId": "req-uuid",
  "scanId": "scan-uuid",
  "runId": "run-uuid",
  "stateVersion": 19,
  "status": "rejected",
  "error": {
    "code": "ACTION_BLOCKED",
    "message": "潜在写操作未获 Host 安全策略放行",
    "retryable": false,
    "requiredNextStep": "inspect_source_or_record_needs_review"
  },
  "evidenceRefs": [],
  "diagnosticRefs": ["diag-044"]
}
```

### 5.1 错误码

| 错误码 | 含义 | Agent 行为 |
|---|---|---|
| `INVALID_REQUEST` | 参数或 Schema 不合法 | 修正请求，不重试原请求 |
| `UNKNOWN_TOOL` | 工具不存在或未启用 | 不得猜测替代工具 |
| `STALE_STATE` | 页面/账本版本已变化 | 重新 `inspect_page` 或 `inspect_object` |
| `UNKNOWN_REFERENCE` | 对象、Case、证据或截图 ID 不存在 | 不能创造替代 ID |
| `ACTION_BLOCKED` | 动作被安全策略阻断 | 补源码/页面证据或记录 `needs_review` |
| `NAVIGATION_BLOCKED` | 跨站或非允许导航 | 记录跳过原因 |
| `CASE_NOT_RESTORED` | 定向恢复及刷新兜底后仍无法可靠恢复 | 停止当前对象并记录状态污染 |
| `INSUFFICIENT_EVIDENCE` | 判定引用缺少必要证据 | 补证或输出 `needs_review` |
| `INVALID_DECISION` | 判定状态、规范或字段不符合契约 | 修正后重新提交 |
| `RUN_TERMINAL` | 扫描已完成、partial 或 failed | 不再发送普通工具请求 |
| `INTERNAL_FAILURE` | Host 内部故障 | 按 `retryable` 决定重试或终止 |

## 6. 工具目录

### 6.1 `start_audit`

创建扫描、接收临时凭据并尝试登录。

输入：

```json
{
  "url": "https://test.example.com/orders",
  "sourcePath": "/workspace/app",
  "ruleRegistryVersion": "2026-08-30",
  "outputDir": "/workspace/output",
  "browserProfile": "chromium-default"
}
```

凭据通过 Host 的安全输入通道提供，不作为普通 `input` 字段传入 Agent 调用。

成功结果至少包含：

- `scanId`、`runId`；
- `loginStatus`；
- `currentPageStateId`；
- `ruleRegistryDigest`；
- `capabilities`；
- `stateVersion`。

失败时只返回登录失败诊断，不创建页面问题结论。

### 6.2 `inspect_page`

读取当前页面状态和候选对象，不执行交互。

输入：

```json
{
  "pageStateId": "page-001",
  "include": ["route", "visibleText", "objects", "safeEntrypoints", "networkSummary"]
}
```

结果至少包含：

- 当前 URL、route、标题和页面状态 ID；
- 可见候选对象摘要；
- Host 推断的对象类型和可能适用规范；
- 可安全尝试的入口；
- 已发现但未处理的入口；
- 页面 DOM/状态快照引用。

Host 不把候选对象直接当作正式待检查对象。

### 6.3 `inspect_object`

读取一个已验证对象的局部上下文。

输入必须引用 Host 已返回的 `objectId` 或 Agent 提交、Host 尚未验证的新对象候选；新候选只能得到验证结果，不能直接用于判定。

结果至少包含：

- 对象身份、类型、页面状态和定位信息；
- 可见文本、ARIA、结构上下文和关联对象；
- Host 推荐的适用规范；
- 已有运行态、请求、源码和截图证据引用；
- 对象当前状态版本。

### 6.4 `perform_action`

执行受控浏览器动作。

允许的动作类型：

- `scroll`
- `focus`
- `expand`
- `switch_tab`
- `open_detail`
- `open_edit`
- `input_synthetic_value`
- `refresh`

每个请求必须包含：

- 当前 `pageStateId`；
- 目标 `objectId`；
- 动作类型和结构化参数；
- Agent 说明动作意图；
- `expectedStateVersion`；
- 幂等键。

安全规则：

- 删除、作废、取消业务、解绑、移除、保存、提交、审批、发布、导入、上传等动作不得真实执行；
- Agent 的意图声明不能放行 Host 判定为潜在写操作的请求；
- Host 无法判断时拒绝；
- 编辑页可以打开和填写合成值，但保存/提交永远阻断；
- 动作完成后 Host 返回前后页面状态和请求观察。

### 6.5 `restore_case`

结束一个 Case 后调用。Host 不接受 Agent 自行拼接的 selector 或反向脚本，只依据本次 Case 已记录的动作日志和恢复基线执行。

输入至少包含：

```json
{
  "caseId": "case-003",
  "pageStateId": "page-004",
  "objectId": "obj-123",
  "expectedStateVersion": 27,
  "fallback": "refresh_and_replay_safe_entrypoints"
}
```

Host 的恢复顺序固定为：

1. 按动作日志逆序执行可逆动作，例如关闭本 Case 打开的弹窗、收起展开行、恢复字段原值和切回原 Tab；
2. 校验 URL/route、页面层级、Tab、弹窗/抽屉、被修改控件、待检查对象和残留请求等恢复基线；
3. 为本次尝试记录 `restored`、`uncertain` 或 `failed`，并附验证检查和恢复证据；
4. 定向恢复为 `uncertain` 或 `failed` 时，刷新当前 URL、重放已记录的安全入口并再次验证；
5. 兜底仍未达到 `restored` 时返回 `CASE_NOT_RESTORED`，当前对象不得继续调查。

恢复验证不要求整页 DOM 完全一致，也不能由 Agent 将 `uncertain` 覆盖为成功。前端重渲染后，Host 仍须重新验证原逻辑对象的运行实例；匹配不唯一时必须失败或进入复核。

结果判定必须是机械的：全部必检项为 `match` 才是 `restored`；没有已确认差异但存在 `unknown` 时是 `uncertain`；出现关键 `mismatch`、反向动作失败、写请求或对象丢失时是 `failed`。局部视觉结果只能补证，不能覆盖结构检查失败。

### 6.6 `inspect_source`

只围绕当前运行页面对象查找源码归属和实现证据。

输入：

```json
{
  "objectId": "obj-123",
  "queries": ["component", "handler", "api", "validation", "loading"],
  "maxSnippets": 8
}
```

Host 必须建立页面 → route → 组件 → handler/API 的归属链。无法唯一绑定时返回 `sourceBindingStatus: "unverified"`，不得用名称相似文件替代。

### 6.7 `capture_evidence`

在当前 Case 或对象状态下保存页面状态、对象位置和截图。

输入必须引用当前 `pageStateId`、`objectId` 和可选 `caseId`，不能传入任意 selector 作为事实。

结果至少包含：

- `evidenceId`；
- `screenshotId`；
- 当前对象定位和 bounding box；
- 截图是否成功、截图方法和失败原因；
- 页面状态和对象状态版本。

同一对象对应多个问题时，每个问题项必须调用一次独立截图流程，不复用一张未框选的通用页面图作为多个问题截图。

### 6.8 `record_decision`

提交一个“对象 × 规范”的审计结果。

输入：

```json
{
  "objectId": "obj-123",
  "ruleId": "FUA-02",
  "ruleVersion": "1.0.0",
  "result": "issue_found",
  "reasonText": "必填字段在当前页面没有可见标识",
  "evidenceRefs": ["ev-001", "ev-002"],
  "caseRefs": ["case-003"],
  "screenshotId": "shot-003",
  "severity": "P1"
}
```

Host 校验：

- `objectId`、`ruleId`、版本、Case、证据和截图均存在且属于当前扫描；
- 规范已启用且适用于该对象；
- `issue_found` 有页面病灶截图；
- `scanned_no_issue` 已满足该规范最低覆盖要求；
- `needs_review` 说明证据缺口、冲突或阻断原因；
- `not_applicable` 有适用性理由；
- `noise` 有噪声原因；
- 一个对象违反多条规范时，按规范分别提交，不合并成无规范的问题。

### 6.9 `complete_audit`

提交 Agent 的覆盖证明并请求 Host 结束扫描。

输入至少包含：

- 已访问页面和状态 ID；
- 已处理对象 ID；
- 已处理和跳过的入口及原因；
- 各规范覆盖摘要；
- 尚未处理入口；
- Agent 判断已完成的理由；
- 如果未完成，声明 `partial` 原因。

Host 最终校验：

- 登录成功；
- 页面/状态/对象处理记录闭合；
- 所有 `issue_found` 都有证据和独立截图；
- 跳过项有原因；
- 规则版本和注册表摘要稳定；
- 没有未处理的安全入口被 Agent 无理由遗漏。

## 7. Agent 调查循环协议

每个对象按以下循环运行：

```text
inspect_page
  → 选择一个 Host 已验证对象
  → inspect_object
  → 判断适用规范
  → 加载对应 Skill 规则
  → 生成满足最低覆盖要求的反向 Case
  → perform_action / inspect_source
  → capture_evidence
  → 判断证据是否足够
       ├─ 不足：申请补证或 needs_review
       ├─ 噪声：record_decision(noise)
       ├─ 不适用：record_decision(not_applicable)
       ├─ 无问题：record_decision(scanned_no_issue)
       └─ 有问题：record_decision(issue_found)
  → restore_case（定向恢复并验证，必要时刷新兜底）
  → 继续下一个对象
```

### 7.1 Case 选择

- Agent 决定 Case 顺序和具体合成输入；
- 每条规范的最低覆盖维度不可跳过；
- Happy Path 只用于进入目标状态；
- 正式审计优先使用反向、边界、非法和异常 Case；
- Case 不能要求真实写入数据；
- 每个 Case 必须在执行期间同步保存前后状态和候选截图；结束后必须调用 `restore_case` 并保存恢复结果。

### 7.2 状态过期和重试

- Host 每次改变页面或账本后递增 `stateVersion`；
- Agent 使用旧版本请求动作时，Host 返回 `STALE_STATE`；
- Agent 必须重新读取当前状态，不得直接重放旧 selector；
- 使用相同 `idempotencyKey` 的重复请求不得重复执行动作；
- 读取类请求可有限重试；动作执行请求在结果未知时不得盲目重试，必须先查询动作结果。
- `restore_case` 的定向反向动作也必须具备幂等性；恢复结果未知时先查询恢复状态，不得盲目重复点击或输入。

### 7.3 探索停止

Agent 自主判断探索完整，但必须提交覆盖证明。Host 检测到重复状态或停滞时提醒 Agent 重新规划；多次无进展后终止并标记 `partial`。技术保护是死循环保险，不是用来宣称“预算已达到所以完整”。

## 8. 判定与截图门禁

### 8.1 判定门禁

`issue_found` 必须同时满足：

- 当前对象真实存在并可定位；
- 当前规范已启用且适用于对象；
- 反向 Case 和最低覆盖要求已完成，或规范明确允许直接运行态确认；
- 引用的证据由 Host 生成，且与对象和页面状态一致；
- Agent 的理由解释具体病灶和影响；
- 已保存独立病灶截图。

Agent 置信度不能替代任何门禁。

### 8.2 `scanned_no_issue` 门禁

只有达到该规范最低覆盖要求，且所有相关 Case 没有发现违规，才能提交 `scanned_no_issue`。覆盖不足必须是 `needs_review`，不能用“本次没看到”判定通过。

### 8.3 截图门禁

正式问题的截图必须：

- 来自问题对应的页面状态；
- 框选当前问题病灶；
- 能追溯到对象和 Case；
- 不是入口页、相似元素或事后猜测区域。

截图失败时 Host 拒绝 `issue_found`，改为 `needs_review` 或返回补证请求。

## 9. MCP 与 CLI 映射

### 9.1 MCP

每个工具名映射一个 Host Core 能力。MCP 服务器负责：

- 校验封套格式；
- 注入当前会话身份；
- 调用 Host Core；
- 返回统一响应；
- 不修改业务规则或安全策略。

### 9.2 CLI

CLI 使用 JSON 输入输出，适合测试和批处理：

```bash
agent-f host invoke inspect_page \
  --input request.json \
  --output response.json
```

CLI 不把密码放在命令参数中。需要凭据时使用 Host 的交互式安全输入通道或本地安全输入句柄。

## 10. 规则注册表协议

规则数量不能写死为 14。Host 在 `start_audit` 时加载版本化注册表并冻结本次规则集合。

注册表条目至少包含：

```json
{
  "ruleId": "FUA-02",
  "version": "1.0.0",
  "status": "enabled",
  "owner": "product-owner",
  "document": "rules/FUA-02.md",
  "objectKinds": ["field", "form"],
  "requiredCapabilities": ["runtime", "visual"],
  "coverageContract": "rules/FUA-02.md#coverage",
  "defaultSeverity": "P2",
  "regressionSuite": "fixtures/rules/FUA-02"
}
```

新增规则只要复用现有 Host 能力，应只增加规则文件、注册信息、对象映射和回归样本；需要新能力时，先扩展通用 Host 能力，再让规则声明依赖。规则不能自行增加新的判定状态或绕过截图门禁。

## 11. 协议版本与兼容性

- `protocolVersion` 发生不兼容变化时提升主版本；
- 工具增加可选字段时保持向后兼容；
- 判定状态、证据引用和动作安全语义发生变化时必须提升版本并重新验证；
- 每个报告记录协议版本、规则注册表摘要、规则版本和 Skill 版本；
- 旧报告只读，不因新协议或新规则自动重算。

## 12. 协议测试门槛

编码前必须至少准备：

- 每个工具的正常、缺参、未知引用、过期状态和重复请求测试；
- 危险动作、未知动作、跨站导航和潜在写请求拒绝测试；
- Agent 伪造对象、证据、selector、截图和规则 ID 的拒绝测试；
- Case 定向恢复成功、恢复不确定、刷新兜底成功、刷新兜底失败和状态污染测试；
- `issue_found` 无截图、无证据、未覆盖最低 Case 时的拒绝测试；
- MCP 和 CLI 对同一请求产生一致结果的契约测试；
- 新增规则不修改 Host 主流程的扩展测试。
