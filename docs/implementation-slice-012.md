# 垂直切片 012：BrowserSession 与配置边界

## 交付内容

- 新增受限 `BrowserProfile`：只允许 Chromium、headless、视口、locale、时区和有限等待预算；拒绝 executable、代理凭据、任意启动参数和未知字段。
- 新增 `BrowserSession`：一个 Scan 独占一个浏览器 Context，打开/关闭显式管理，后端异常会把 Session 标记为 failed。
- 新增 `run_serial`：同一 Scan 的浏览器回调在 Session 锁内串行执行，回调异常后不再继续使用不可信 Context。
- 新增 `ScanSessionRegistry`：禁止同一 Scan 重复注册，只有 completed/partial/failed 终态才能释放，会话关闭可重复调用且只关闭一次。
- 生产默认页面和对象身份适配器已在 B01 改为不可用实现；本切片不连接 Playwright，不产生真实页面事实。

## 设计边界

`BrowserBackend` 是未来 Playwright 或其他浏览器驱动的唯一注入点。适配器不能绕过 Session 直接持有全局 Page，也不能把浏览器启动参数、Cookie、代理密码或 ElementHandle 写入 Core、账本或 MCP 消息。真实浏览器接入前必须完成 B03 的凭据状态机和 B04 的只读页面适配器。

## 验收

- 配置未知字段、非 Chromium、越界视口和错误类型均拒绝；
- 缺少后端时 Session fail-closed；后端回调异常使 Session 失效；
- 五个并发回调的最大同时执行数稳定为 1；
- 同一 Scan 重复注册和非终态释放均拒绝；终态释放后后端恰好关闭一次；
- 所有测试通过，且本切片不引入 HTML、Playwright 或真实凭据。
