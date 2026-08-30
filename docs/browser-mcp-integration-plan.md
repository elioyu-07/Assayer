# 真实浏览器与 MCP 集成计划

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | B01–B12 Host 集成已完成（B07c 暂缓）；LLM 调查层转入 C01–C07 |
| Owner | Host Core / Security Owner |

## 1. 目标与完成定义

本阶段把已经通过 deterministic Harness 的 Host Core 接到真实测试/预发布页面，并通过 Codex MCP 使用同一协议。完成时必须满足：

- 一个 Scan 独占一个受管理的浏览器 Context，全部浏览器适配器共享该 Context 和串行执行队列；
- 页面、对象、动作、请求、恢复和截图事实均来自该 Context，不使用 fixture 兜底；
- 凭据只经 Host 本地安全通道进入内存，登录结束即清除，MCP/CLI/SQLite/报告均不可见；
- 请求拦截器在任何审计动作前安装，无法证明发送前阻断时 fail-closed；
- MCP 与 CLI 只做传输适配，调用同一个 `HostCore.handle`；
- 真实浏览器集成测试覆盖成功、阻断、超时、崩溃、恢复失败、截图绑定/失败关闭和传输一致性；B07c 另行覆盖自动截图脱敏；
- 不生成 HTML，`audit-ledger.json` 仍是唯一运行事实源。

## 2. 当前缺口

| 缺口 | 当前状态 | 集成要求 |
|---|---|---|
| 浏览器会话所有权 | 各适配器接口独立，没有共享 Session 生命周期 | 以 Scan 为键集中创建、查询、串行使用和销毁 Context |
| 生产默认值 | 页面和对象身份曾默认使用 deterministic fixture | 所有浏览器事实适配器默认不可用，只有显式组装才能运行 |
| 浏览器配置 | `browserProfile` 已进协议但尚未参与 Host 行为 | 解析为受限、版本化配置；不得接受任意启动参数或脚本 |
| 凭据结构 | Vault 当前只保存单个字符串 | 改为 Host 内部类型化秘密；MCP 只接触一次性 handle |
| 登录 | 尚无真实导航、表单绑定、SSO/MFA 收束 | 登录与普通审计动作分域；不支持的流程明确失败 |
| 页面与对象 | 尚无 DOM/ARIA 采集和短期 locator registry | 原始 selector 留在 Host 内存，只向 Core 返回事实摘要与句柄 |
| 请求安全 | 已有分类策略，但没有浏览器发送前拦截 | Context 创建后、首次导航前安装路由；未知请求默认阻断 |
| 动作与恢复 | 已有状态机，没有真实 inverse、刷新和重放 | 动作日志引用 Host 内部恢复句柄，全部检查维度可观测 |
| 视觉证据 | 已有真实对象级截图与文件/绑定门禁，自动像素脱敏尚未实现 | 截图前定位、裁剪、格式、digest 和不可变落盘均在 Host 内完成；B07c 再增加遮挡与脱敏证明 |
| 传输 | 只有 deterministic CLI，没有通用 JSON CLI/MCP Server | 两种传输共享请求校验、Core 实例和错误映射 |
| 预算与退出 | 设计要求超时，尚无浏览器预算配置和可靠关闭 | 所有外部等待有上限；终态、异常和进程退出均关闭 Context |

## 3. 固定设计决策

1. **单一会话聚合**：不创建六套互不知情的 Playwright 适配器。`BrowserSession` 聚合页面、locator、请求、动作、恢复和截图的瞬时状态；薄适配器只把现有 Core 契约映射到同一 Session。
2. **每 Scan 串行化**：同一 Scan 的浏览器命令在专用执行队列中串行执行。不同 Scan 可以隔离并发；不得从 MCP 并发回调直接操作同一个 Page。
3. **生产与 fixture 分离**：deterministic 适配器只能由 Harness/测试显式注入。生产工厂缺少任一必需浏览器能力时拒绝启动，不做部分 fixture 回退。
4. **短期 locator registry**：selector、ElementHandle 和字段原值只存在于 Session 内存。账本保存 opaque `hostLocatorId` 和身份摘要，重渲染后必须重新绑定。
5. **拦截先于导航**：审计 Context 的请求路由必须在第一次页面请求前安装。登录属于单独授权阶段；登录完成后切换到更严格的 audit policy，不能沿用无限制网络权限。
6. **配置白名单**：浏览器类型、headless、视口、语言和预算使用类型化配置；拒绝任意 executable、扩展、代理凭据、启动参数和页面脚本注入。
7. **传输无业务逻辑**：MCP/CLI 不生成 ID、不判断规则、不读取浏览器、不写报告，只管理本地安全输入、实例路由和 `HostCore.handle` 调用。
8. **关闭即失效**：浏览器崩溃、Session 丢失或无法完成关闭/清除时，相关 Scan 进入 failed；不得重新开浏览器并继续沿用旧对象或结论。

## 4. 有序任务

| # | 任务 | 主要交付 | 验收门槛 |
|---|---|---|---|
| B01 | 生产适配器边界硬化 | `UnavailablePageAdapter`、`UnavailableObjectIdentityAdapter`、显式 fixture 注入 | 只注入登录或页面时不能产生 fixture 页面/对象；全量回归通过 |
| B02 | BrowserSession 与配置契约 | Session registry、adapter bundle、受限配置、生命周期/并发测试 | 每 Scan 独占、同 Scan 串行、终态可靠释放；无浏览器依赖也可做契约测试 |
| B03 | 安全凭据通道与登录状态机 | 类型化内存秘密、本地无回显输入、登录阶段策略、清除证明 | 明文不进入参数/环境/日志/SQLite；失败与超时均清除 |
| B04 | Playwright 只读垂直切片 | Context 启动、同源导航、PageObservation、对象候选与唯一绑定 | 本地测试站点可完成 bootstrap/inspect；歧义稳定拒绝；无 fixture 回退 |
| B05 | 发送前拦截与安全动作 | Context route、请求归因、白名单动作、RequestObservation | 写/跨源/未知请求发送前阻断；竞态或已发送事实使结果 unknown/failed；已完成（垂直切片 015） |
| B06 | 真实恢复屏障 | inverse、刷新重放、九维检查、对象重新绑定 | 定向成功、兜底成功、uncertain、污染失败均有可复算记录；已完成（垂直切片 016） |
| B07a | 真实结构化 Evidence | DOM/ARIA 最小事实、对象/Case/页面绑定、字段值分类与结构化脱敏 | 已完成（垂直切片 017） |
| B07b | 真实对象级截图 | Playwright 对象裁剪、重绑定、边界/格式/digest 校验、不可变落盘、Raw Visual→IssueScreenshot 派生链 | 已完成（垂直切片 018）；图片明确记录 `sanitizationStatus=not_performed`，正式 `issue_found` 仍被门禁拒绝 |
| B07c | 自动截图脱敏增强（暂缓） | 敏感区域识别、不可逆像素遮挡、脱敏完成证明 | 后续实现；不得回填或伪造既有未脱敏截图的状态 |
| B08 | 通用 JSON CLI 与 MCP 传输 | JSON Lines invoke、可选 SDK stdio MCP tools、Scan/Core 路由、协议一致性测试 | 同一请求两种传输产生等价响应；凭据值不经过 MCP/命令参数 |
| B09 | 发布级集成与故障注入 | 本地测试站点、浏览器 E2E、超时/崩溃/冲突测试、确定性韧性扫描、运行手册 | 已完成（垂直切片 020）；安全门槛通过，产物无 HTML |
| B10 | 真实 URL 可运行闭环 | anonymous 模式、BrowserHostRuntime、`assayer audit/serve`、真实 JSON/MCP 装配 | 已完成（垂直切片 021）；给定公开 URL 可自动启动 Chromium 并进入同一 Host 协议，Session 与 Scan 生命周期闭合 |
| B11 | 页面探索与真实数据审计 | `explore_entrypoint`、固定 Tab 识别/切换、同源只读 XHR 窗口、结构摘要与有界遍历 | 已完成（垂直切片 022）；给定 URL 可遍历 Host 发现的 Tab；同源只读数据不被误杀；页面状态保留真实结构、错误和 Evidence；写/跨源/WebSocket 仍阻断 |
| B12 | 通用真实 URL Host 全链路验收 | 固定规划的 Case/动作/Evidence/恢复/判定/账本 smoke runner；结构化 Evidence 事实通道 | 已完成（垂直切片 023）；不依赖站点文案或专属选择器，真实 URL 可完成 Host 生命周期；它不包含 LLM 自主规划，不能作为正式智能审计完成声明 |

B07 拆分为 B07a、B07b 与 B07c：B07a/B07b 已完成，B07c 暂缓。B07b 只证明“能安全捕获并绑定原始对象图片”，不证明图片已脱敏；在 B07c 完成前，真实路径仍不能生成正式 `issue_found`。B08 已完成 JSON/MCP 传输，B09 已完成发布级故障注入，B10 已完成真实 URL 产品装配；传输层、产品入口和故障处理均不能绕过截图或脱敏门禁。

B01–B12 证明的是 Host、浏览器和协议执行面。LLM 语义控制面按 [LLM Agent 调查层实施计划](llm-agent-integration-plan.md)的 C01–C07 推进；C01–C02 已完成，后续 22 个工作包见 [C03–C07 后续执行计划](implementation-plan-c03-c07.md)。当前 `BrowserHostRuntime.audit` 从产品语义上属于 smoke，C05 才把正式 `audit` 切换为 Codex Agent 驱动。

## 5. B07 拆分与脱敏状态决策

- 决策日期：2026-08-30。
- 已完成范围（B07a）：真实 Chromium DOM/ARIA 最小事实采集、对象/Case/PageState 强绑定、字段状态分类、结构化脱敏和完整性摘要。
- 已完成范围（B07b）：真实 Chromium 对象级裁剪、重绑定、边界/格式/digest 校验、不可变落盘和独立 IssueScreenshot 派生。
- 暂缓范围（B07c）：敏感区域识别、不可逆像素遮挡及脱敏完成证明。
- 不变安全门禁：不删除 Screenshot Schema，不降低 `issue_found` 的独立截图要求，不用未脱敏图片或 deterministic fixture 冒充真实证据。
- 暂缓期间允许：受控测试数据环境采集并审计 `sanitizationStatus=not_performed` 的 Raw Visual；继续实现 B08 与 B09 故障测试。
- 暂缓期间禁止：把未脱敏图片标记为 `sanitized`，或据此生成/提交/发布正式 `issue_found`；把整页截图、录屏或人工截图作为自动脱敏替代品。
- B07c 恢复时必须新增自动遮挡和脱敏证明测试，不能改变 B07b 已有的对象对位和失败关闭门禁。

## 6. B01 变更说明

B01 把 `HostCore` 的默认页面和对象身份适配器改为不可用实现。deterministic Harness 和测试必须显式注入 fixture 适配器。这条变更不会降低当前测试能力，但消除了“只配置登录后静默读取假页面”的危险组合。

## 7. 非目标

- 不在本阶段支持生产环境、无边界爬取、任意脚本或用户提供 selector；
- 不在浏览器适配器中判断 FUA 规则结论；
- 不允许用 post-hoc 请求日志冒充发送前阻断；
- 不用录制的 Cookie、storage state 或浏览器 profile 文件替代凭据安全通道；
- 不增加 HTML 报告或第二事实源。
