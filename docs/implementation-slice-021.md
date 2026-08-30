# 垂直切片 021：真实 URL 可运行闭环（B10）

> C01 解释修正：本切片当时把正式完整判定留给后续 Agent 协议；B12 随后增加的固定全链路仍只是 deterministic smoke。正式 LLM Agent 入口由 C04/C05 实现，不能从 B10/B12 的命令可运行性推导为已经完成。

## 问题与目标

B01–B09 证明了 Host Core、真实 Playwright 适配器和传输组件分别可用，但缺少把它们组装成用户入口的运行时。B10 的完成定义是：用户只提供一个公开 HTTP(S) URL，Host 自动启动真实 Chromium、组装 Page/Identity/Action/Recovery/Evidence 适配器，并能从 CLI、JSON Lines 或 MCP 继续同一协议流程。

## 交付内容

- `start_audit` 新增 `authMode=anonymous|credential`；anonymous 明确禁止同时提供 `credentialHandle`，旧请求默认保持 credential 模式。
- anonymous 使用独立无凭据协调路径，不创建伪用户名或密码；credential 路径的消费、脱敏和清零语义不变。
- 新增 `BrowserHostRuntime`，预分配并强绑定一个真实 `BrowserSession`、一个 Scan、URL、输出目录、浏览器配置、共享 adapter bundle 和 `HostCore`；拒绝配置漂移和第二个 Scan，终态/退出时关闭。
- 新增 `assayer audit <url>`：执行匿名启动、页面发现、首个候选唯一绑定和对象级 Raw Visual 试跑，输出单一 JSON 结果。
- 新增 `assayer serve <url>` 与 `assayer-json --url <url> --stdio`，供 Agent 在同一长驻进程内继续完整 Host 协议。
- `assayer-mcp --url <url>` 使用同一真实运行时，不依赖 Codex/桌面客户端的内置浏览器连接。
- WebSocket 继续默认阻断：路由不连接服务器且丢弃页面消息；禁止在 Playwright 同步路由回调中调用同步 `close()`，避免 HMR 页面导航重入死锁。
- SPA hash 路由只记录不含 query 的安全路径（如 `#/lease-mock?token=...` 记录为 `/lease-mock`）。

## 验收

- 无凭据 Vault 的 anonymous `start_audit` 成功；credential 模式缺少 handle 仍失败关闭。
- 真实 Chromium URL 可完成启动、inspect_page、对象绑定、结构化/视觉 Evidence。
- HMR/WebSocket 页面在明确预算内完成导航，服务端未收到 WebSocket 握手。
- CLI 进程结束、终态、启动失败均关闭 Browser Session。
- 在 `http://localhost:8081/#/lease-mock` 上执行一次真实只读试跑并记录客观结果。
- B07c 仍暂缓：图片必须是 `sanitizationStatus=not_performed`，不得生成正式 `issue_found`。

## 非目标

- B10 不实现登录页面自动识别、SSO/MFA 或凭据获取；这些仍需 credential 模式与 Host 本地安全输入。
- `assayer audit` 是只读发现/Evidence 试跑，不伪造规则结论；正式完整判定由长驻 JSON/MCP 协议中的 Agent 驱动。

## 真实站点验收记录

2026-08-30 对 `http://localhost:8081/#/lease-mock` 执行 `assayer audit`：HTTP 200，真实 Chromium 在 2 秒内完成匿名启动和页面发现，标题为“租赁管理 Mock”，安全路由为 `/lease-mock`。当前固定探针只把 `[role=search]`、`form` 或带 filter 标识的可见区域识别为 `filter_region`；该页面返回 0 个候选和 0 个入口，因此没有创建对象 Evidence，也没有伪造规则结论。这是对象发现范围的客观限制，不是浏览器连接失败。

真实 Chromium 回归覆盖普通页面、对象绑定与 Raw Visual、WebSocket/HMR 阻断、hash 路由、导航超时和 Context 崩溃。B07c 仍按既有决策暂缓。
