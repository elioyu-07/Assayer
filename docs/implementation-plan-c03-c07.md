# Assayer C03–C07 后续执行计划

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0 |
| 日期 | 2026-08-31 |
| 状态 | active；C03a ready，其余按依赖排队 |
| Owner | Agent Runtime / Host Core / Security Owner |
| 上游基线 | C01 调查层设计、C02 Finding 驱动协议均已完成 |

## 1. 最终目标

完成后的正式产品路径是：用户只提供一个允许访问的测试/预发布 URL，Codex LLM 读取冻结规则，自主探索页面、选择对象、规划安全 Case、补证、逐维记录 Finding，并由 Host 校验引用、恢复、门禁和账本完整性后提交结论。

最终必须同时满足：

- `assayer audit <url>` 只表示 LLM 驱动的正式审计；
- 当前确定性运行器改为 `assayer smoke <url>`，只能作为 CI oracle 和故障诊断；
- Host 提供事实、安全、引用与恢复，不判断业务规则语义；
- Agent 不接触 selector、DOM path、字段真实值、凭据或任意脚本能力；
- 增加规则不要求修改 Agent 主循环或为站点打补丁；
- 上下文压缩、重试或进程恢复后，工作队列可完全由 Host 状态重建；
- lease 只作为黑盒验收样本，仓库中不得出现 lease 专属分支或选择器。

## 2. 当前基线

| 能力 | 当前状态 |
|---|---|
| B01–B12 Host/浏览器/协议闭环 | completed |
| C01 LLM 调查层设计 | completed |
| C02 Finding、进度、规则读取和判定门禁 | completed |
| B07c 自动截图脱敏 | deferred；独立安全轨道 |
| 正式 LLM Agent 调查 | 尚未接入 |
| 当前 `assayer audit` | deterministic smoke 兼容入口，不是正式智能审计 |

当前 lease smoke 能访问 5 个 PageState、识别 3 个对象并写入 12 个 Finding；由于不能用通用交互证据证明 `binding_to_list`，三个结论均为 `needs_review`，Scan 为 `partial`。C03 首要目标就是补上这条通用事实通道。

## 3. 执行规则

1. 严格按 C03 → C04 → C05 → C06 → C07 推进；下游不得用临时代码绕过上游协议门禁。
2. 每个子任务完成时必须包含实现、Schema/文档、单元测试、浏览器测试和对应 `implementation-slice-NNN.md`。
3. Host 主循环禁止出现 `if ruleId == ...`、站点 URL、业务文案或站点 selector 分支。
4. 浏览器返回事实差异；“该差异是否满足规则”只能由 Agent 形成 Finding。
5. 写请求、跨源请求、未知请求、恢复不确定和对象歧义继续 fail-closed。
6. 每次提交前运行完整 pytest、Schema/示例校验、Markdown 链接、规则 digest 和 `git diff --check`。
7. 只有当前任务的验收门槛全部通过，下一项才能从 `queued` 变为 `ready`。

## 4. 工作包总览

后续共 22 个工作包：C03 五个、C04 五个、C05 五个、C06 三个、C07 四个。

| 阶段 | 工作包数 | 阶段完成结果 | 状态 |
|---|---:|---|---|
| C03 通用交互与绑定证据 | 5 | Host 能产生足以调查控件—列表绑定的通用事实 | in progress |
| C04 Codex Skill 与 Agent 循环 | 5 | LLM 实际进行多轮调查并写 Finding | queued |
| C05 动态 MCP 与产品入口 | 5 | 用户只给 URL 即可启动受监督的正式审计 | queued |
| C06 正式路径去确定性语义 | 3 | smoke oracle 与正式 Agent 路径彻底分离 | queued |
| C07 通用回归与黑盒验收 | 4 | 发布门禁和 lease 黑盒验收闭合 | queued |

## 5. C03：通用交互与绑定证据

### C03a 可引用控件与列表发现

- 状态：`ready`，下一步立即执行。
- 交付：真实浏览器适配器发现可见 input/select/button 与 table/grid/list，生成稳定的 `controlRef/listRef`；引用仅在当前 PageState/Session 有效。
- 机械属性：控件类型、语义动作候选、可见/禁用、值分类；列表类型、可见、容器/ARIA/邻近关系。
- 禁止：向协议、SQLite 或日志暴露 selector、DOM path、ElementHandle、输入值和完整 DOM。
- 验收：同页多筛选区/多列表引用不碰撞；重渲染后旧引用失效；lease 三个对象均返回真实控件和候选列表引用；无站点专属规则。

### C03b 通用安全动作协议

- 状态：`queued`；依赖 C03a。
- 交付：动作目标从对象扩展到 Host 返回的 `controlRef`；支持 `input_synthetic_value`、安全 option 选择、`query`、`reset` 和值恢复。
- 合成值：Agent 只提交 `valueClass`，Host 生成实际值并只记录分类。
- 安全：动作前重新绑定；请求拦截先于动作；写/跨源/未知请求发送前阻断；不得提供任意 click、脚本或 selector 参数。
- 验收：允许的查询成功，潜在写请求被阻断，未知结果不重放，所有动作可进入恢复屏障。

### C03c 前后状态观察与差异证据

- 状态：`queued`；依赖 C03b。
- 交付：为控件状态、列表结构、行数分类、空态、加载态、分页摘要和只读请求摘要建立 before/after Observation。
- Evidence 只保存最小差异、引用和 digest，不保存业务数据行内容。
- 验收：无变化、列表变化、仅控件变化、多个列表同时变化和请求失败五类结果可区分；Evidence 可由账本重算引用闭合。

### C03d 绑定事实组合器

- 状态：`queued`；依赖 C03c。
- 交付：Host 组合但不解释以下机械事实：共同容器/ARIA 关系、查询前后列表差异、reset 后控件与列表恢复、只读请求时序关联、候选列表歧义。
- 输出必须允许 Agent 得出 `satisfied/violated/unresolved/blocked/conflicted`，但 Host 不直接返回 FUA-10 结论。
- 验收：单列表绑定、多列表唯一绑定、多列表歧义、页面级搜索框和零变化场景均能形成不同事实包。

### C03e C03 集成与黑盒回归

- 状态：`queued`；依赖 C03d。
- 交付：本地通用测试站点正反样本、Playwright E2E、故障注入、lease smoke 回归和 C03 实施记录。
- 验收：lease 的 `binding_to_list` 不再因缺少事实通道而必然 unresolved；具体结果仍由后续 Agent/测试 oracle决定；完整回归无安全门禁退化。

## 6. C04：Codex Skill 与 Agent 调查循环

### C04a Assayer Skill 包与规则路由

- 状态：`queued`；依赖 C03e。
- 交付：可安装的 Assayer Skill，声明触发条件、正式入口、安全边界、工具顺序和冻结规则读取流程。
- 规则正文通过 `get_rule_contract` 获取；Skill 不复制会漂移的规则内容。
- 验收：给定 URL 的审计请求能稳定加载 Skill；普通浏览或无关编码任务不误触发。

### C04b 可恢复工作队列与对象调度

- 状态：`queued`；依赖 C04a。
- 交付：Agent 以 `get_audit_progress` 构建页面/入口/对象/规则队列，选择下一调查项并控制并发。
- 验收：上下文压缩或 Agent 重启后不依赖聊天记忆即可继续；已提交对象不重复，staged Finding 和活动 Case 不丢失。

### C04c Case 规划、补证与 Finding 循环

- 状态：`queued`；依赖 C04b。
- 交付：Agent 根据冻结规则规划 Case，调用通用动作，评估 Evidence，写逐维 Finding，并针对 unresolved/conflicted 选择补证或停止。
- 验收：至少一个样本产生两轮以上证据驱动决策；Host 代码不生成业务 Finding；Agent 不引用 Host 未返回的 ID。

### C04d 预算、停止与失败传播

- 状态：`queued`；依赖 C04c。
- 交付：页面、对象、Case、动作、模型回合和总时长预算；明确 completed/partial/failed 的传播规则。
- 验收：正常预算耗尽可收束 partial；模型异常、工具协议破坏、浏览器崩溃或恢复污染使 Scan failed；不得静默成功。

### C04e Prompt Injection 与 Agent eval

- 状态：`queued`；依赖 C04d。
- 交付：页面提示注入、恶意 aria-label、伪规则文本、诱导脚本/跨源/写请求样本和评估断言。
- 验收：页面内容始终作为待审计数据，不改变系统策略、工具权限、规则版本和输出路径；关键样本重复运行结果稳定。

## 7. C05：动态 MCP 与正式产品入口

### C05a Runtime Router 与 Scan 生命周期

- 状态：`queued`；依赖 C04e。
- 交付：不绑定业务 URL 的长驻本地 Router；按 Scan 创建、查找和销毁 Host/Browser Runtime。
- 验收：多个 Scan 隔离；未知/终态 Scan 不能复活；进程退出可靠清理浏览器和秘密。

### C05b 固定输出根与运行隔离

- 状态：`queued`；依赖 C05a。
- 交付：Host 管理固定根目录和每 Scan 子目录；Agent/MCP 调用不能提供任意本地路径。
- 验收：路径穿越、同名冲突、跨 Scan 引用和覆盖既有产物均 fail-closed。

### C05c `audit` / `smoke` 命令分离

- 状态：`queued`；依赖 C05b。
- 交付：`assayer audit` 启动正式 Codex Agent；现有确定性流程迁移为 `assayer smoke`。
- 验收：Agent 不可用时 audit 明确失败，绝不降级为 smoke；命令输出和报告明确标注运行模式。

### C05d 终端与桌面共用配置

- 状态：`queued`；依赖 C05c。
- 交付：同一 Skill、MCP Server、规则和安全策略在 Codex 终端/桌面使用；配置不写死 URL。
- 验收：两种客户端从相同输入得到等价账本事实；安装和故障说明完整。

### C05e Agent 租约与监督

- 状态：`queued`；依赖 C05d。
- 交付：Agent 心跳/租约、取消、超时和异常退出监督；Host 能区分正常 partial 与模型失联。
- 验收：kill、断连、超时、重复启动和取消均有确定终态，浏览器与锁不泄漏。

## 8. C06：移除正式路径确定性语义判断

### C06a 迁移 `RuleEvaluationEngine`

- 状态：`queued`；依赖 C05e。
- 交付：从正式 Runtime 移除确定性规则判定，迁入测试 oracle 命名空间或删除。
- 验收：正式 `audit` 的 Assessment 只能来自 Agent Finding + Host 门禁；smoke 产物不能冒充正式结论。

### C06b 生产路径静态门禁

- 状态：`queued`；依赖 C06a。
- 交付：自动扫描生产代码中的规则 ID、站点 URL、业务文案、fixture adapter 和 deterministic evaluator 引用。
- 验收：CI 对任何正式路径规则特判失败；扩展第二条规则无需修改 Agent 主循环。

### C06c 契约、示例与命令迁移

- 状态：`queued`；依赖 C06b。
- 交付：README、架构、协议、示例账本、CLI 帮助和运行手册统一到正式 audit/smoke 语义。
- 验收：不存在把 smoke 描述成智能审计的文档或机器字段；旧兼容入口有明确迁移说明。

## 9. C07：通用回归、lease 黑盒与发布门禁

### C07a 通用语义样本矩阵

- 状态：`queued`；依赖 C06c。
- 交付：正例、缺 query、缺 reset、不适用、噪声、多列表歧义、证据冲突和能力阻断样本。
- 验收：五态结果与 Finding/Evidence 一致；不同 DOM 框架和文案不影响通用行为。

### C07b lease 黑盒端到端验收

- 状态：`queued`；依赖 C07a。
- 交付：只提供 `http://localhost:8081/#/lease-mock`，由正式 Agent 完成 Tab 探索、对象调查、补证、恢复、判定和报告。
- 验收：无 lease 配置、文案、URL 或 selector 分支；三个筛选对象均有可解释的逐维 Finding；运行可复盘。

### C07c 安全与韧性验收

- 状态：`queued`；依赖 C07b。
- 交付：提示注入、写请求、跨源、请求竞态、对象重渲染、恢复失败、模型中断、账本冲突和产物发布失败矩阵。
- 验收：危险场景全部 fail-closed；没有凭据、字段值、selector、堆栈或未授权路径泄露。

### C07d 发布候选门禁

- 状态：`queued`；依赖 C07c。
- 交付：完整测试、重复运行稳定性、安装验证、终端/桌面验收、文档审阅和发布清单。
- 验收：C03–C07 全部 completed；GitHub main 干净；正式入口、唯一事实源、派生报告和运行模式语义一致。

## 10. B07c 独立安全轨道

B07c 不计入上述 22 个工作包，保持 `deferred`。它不阻塞 `scanned_no_issue`、`needs_review`、交互绑定调查和正式 Agent 装配，但在完成前：

- 真实 `issue_found` 不能使用 `sanitizationStatus=not_performed` 的截图；
- 不能把未脱敏图片标记为 sanitized；
- C07 的负例可以验证 violated Finding，但发布结论必须因截图门禁收束为 `needs_review`。

恢复 B07c 时另行拆解敏感区域识别、不可逆像素遮挡、脱敏证明和视觉回归，不插入站点特定遮挡规则。

## 11. 状态维护方式

- 本文档是 C03–C07 子任务、顺序和状态的权威清单；里程碑级摘要仍由 [LLM Agent 调查层实施计划](llm-agent-integration-plan.md)维护。
- 开始某项时，将其改为 `in progress`；同一阶段最多一个子任务为 `in progress`。
- 完成时改为 `completed`，补充对应 implementation slice 和验证结果，再把直接后继改为 `ready`。
- 出现设计变更时先更新本文档和权威设计，不允许只在代码或聊天中改变范围。

## 12. 当前下一步

立即开始 **C03a：可引用控件与列表发现**。它完成前不进入查询/重置动作，也不实现 Codex Skill；先确保真实浏览器能用通用方式返回可信、无 selector 泄露且可在 PageState 内验证的 `controlRef/listRef`。
