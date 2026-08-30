# 真实浏览器与 MCP 集成计划

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | implementation-ready；当前完成 B05/9 |
| Owner | Host Core / Security Owner |

## 1. 目标与完成定义

本阶段把已经通过 deterministic Harness 的 Host Core 接到真实测试/预发布页面，并通过 Codex MCP 使用同一协议。完成时必须满足：

- 一个 Scan 独占一个受管理的浏览器 Context，全部浏览器适配器共享该 Context 和串行执行队列；
- 页面、对象、动作、请求、恢复和截图事实均来自该 Context，不使用 fixture 兜底；
- 凭据只经 Host 本地安全通道进入内存，登录结束即清除，MCP/CLI/SQLite/报告均不可见；
- 请求拦截器在任何审计动作前安装，无法证明发送前阻断时 fail-closed；
- MCP 与 CLI 只做传输适配，调用同一个 `HostCore.handle`；
- 真实浏览器集成测试覆盖成功、阻断、超时、崩溃、恢复失败、截图脱敏和传输一致性；
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
| 视觉证据 | 已有文件/绑定门禁，没有真实截图与像素脱敏 | 截图前定位、遮挡、裁剪和格式校验均在 Host 内完成 |
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
| B06 | 真实恢复屏障 | inverse、刷新重放、九维检查、对象重新绑定 | 定向成功、兜底成功、uncertain、污染失败均有可复算记录 |
| B07 | 真实 Evidence 与截图脱敏 | DOM 最小证据、对象截图、敏感区域像素遮挡 | 问题截图与对象/Case 对位；脱敏不确定时不落盘、不升级 Issue |
| B08 | 通用 CLI 与 MCP 传输 | JSON invoke、MCP tools、Scan/Core 路由、协议一致性测试 | 同一请求两种传输产生等价响应；凭据值不经过 MCP/命令参数 |
| B09 | 发布级集成与故障注入 | 本地测试站点、浏览器 E2E、超时/崩溃/冲突测试、运行手册 | 全部安全门槛通过，可在干净环境安装运行，产物无 HTML |

任务必须按顺序推进。B04 之后才允许声称“可读取真实页面”，B07 之后才允许生成真实正式问题，B08 之后才允许声称“可由 Codex MCP 使用”，B09 通过后才进入发布候选。

## 5. B01 变更说明

B01 把 `HostCore` 的默认页面和对象身份适配器改为不可用实现。deterministic Harness 和测试必须显式注入 fixture 适配器。这条变更不会降低当前测试能力，但消除了“只配置登录后静默读取假页面”的危险组合。

## 6. 非目标

- 不在本阶段支持生产环境、无边界爬取、任意脚本或用户提供 selector；
- 不在浏览器适配器中判断 FUA 规则结论；
- 不允许用 post-hoc 请求日志冒充发送前阻断；
- 不用录制的 Cookie、storage state 或浏览器 profile 文件替代凭据安全通道；
- 不增加 HTML 报告或第二事实源。
