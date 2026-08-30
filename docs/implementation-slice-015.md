# 垂直切片 015：发送前网络拦截与安全浏览器动作

## 交付内容

- 新增 `BrowserNetworkGuard`，在受管理 Context 创建 Page 之前安装 `route("**/*")`；可用时同时安装 WebSocket 路由。
- 页面启动所需的同源静态/document GET 请求作为 Host 基础事实放行；动作 Operation 之外的 fetch/xhr 等业务请求无法归因，按 fail-closed unknown/abort 处理。
- 同源 GET/HEAD/OPTIONS 业务请求允许；POST/PUT/PATCH/DELETE、跨 origin、GraphQL mutation、multipart、Beacon、SSE、WebSocket、Service Worker 不可归因请求均在发送前阻断或返回 unknown。
- 仅记录脱敏后的 `NetworkRequest`/`RequestObservation`：原始 header、Cookie、body、脚本和 selector 不进入 Core、SQLite 或报告。
- 新增 `BrowserSafeActionAdapter`，只接受 Host 固定的 locator 句柄和白名单动作（focus、scroll、expand、collapse、switch_tab、refresh），拒绝 Agent selector、脚本和任意 JavaScript。
- 动作结束后重新运行固定页面探针并刷新 locator registry；对象无法唯一重新绑定或身份 fingerprint 改变时返回 `result_unknown`。
- 新增 `SafeBrowserAdapterBundle`，保证页面、对象、动作和网络守卫共享同一 `BrowserSession`、Context 与 locator registry。

## 安全判据

1. route 必须在首次 `new_page()` 前安装；没有活动 Operation 的业务请求不能被误记为动作成功。
2. 请求只保留 method、origin-safe URL、transport、发送状态和分类结果；持久化 URL 去除 query/fragment。
3. 拦截器接管前已发送、无法归因或动作后无法确认对象身份时，动作结果只能是 `result_unknown`/失败，不得乐观升级为成功。
4. POST 写请求必须在发送前 abort；真实集成测试通过本地 HTTP 服务器计数证明写请求未到达。

## 安装与验证

```bash
python3 -m pip install -e '.[browser]'
python3 -m playwright install chromium --no-shell
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_browser_action -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_browser_playwright -v
```

全量验收还必须通过：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -q
```

## 边界与后续

- 当前动作适配器不填写输入值、不执行任意点击、不处理登录；完整 inverse、刷新重放和九维恢复检查属于 B06。
- B07 真实截图与像素脱敏已暂缓；在其恢复并完成验收前不得由此路径生成正式 `issue_found`。
- B05 只提供 Host Core 动作适配器，不代表已经接入 Codex MCP；通用传输属于 B08。
