# 垂直切片 014：Playwright 只读页面与对象身份

## 交付内容

- 新增可选 `browser` 依赖和懒加载 `PlaywrightBrowserBackend`；未安装 Playwright/Chromium 时启动失败，不会回退 deterministic fixture。
- Chromium 启动只使用 B02 的受限 `BrowserProfile`，不接受 executable、代理、扩展或任意启动参数。
- 新增 `BrowserReadOnlyPageAdapter`，通过固定、版本化的 Host 探针读取 URL、route、标题、DOM 摘要材料、可见文本、安全入口和 `filter_region` 候选。
- 导航仅允许冻结 origin；跨 origin 请求或跳转会被阻断。持久化 PageState 会移除 URL query/fragment，避免 ticket/token 类值进入账本。
- B04 首次实现时 `networkSummary.status` 为 `unavailable_until_B05`；B05 通过共享 `BrowserNetworkGuard` 覆盖该摘要，未安装安全动作 bundle 时仍保持 fail-closed。
- 新增仅驻留 Session 内存的 `BrowserLocatorRegistry`。原始 locator material 只用于计算 Core 已有的 `locatorDigest`；selector、ElementHandle 和 locator material 不进入 SQLite、协议或报告。
- `BrowserObjectIdentityAdapter` 在每次对象验证前重新运行固定探针；零个候选返回 `not_found`，多个候选返回 `ambiguous`，只在恰好一个候选时返回 `matched`。
- `create_readonly_browser_adapters` 保证页面和对象适配器共享同一 Session 与 locator registry。

## 安装与验证

```bash
python3 -m pip install -e '.[browser]'
python3 -m playwright install chromium --no-shell
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_browser_playwright -v
```

后端固定使用 `channel="chromium"` 的现代 headless 模式，不下载或选择其他浏览器 channel。真实浏览器测试启动一个仅监听 `127.0.0.1` 随机端口的本地站点，通过 HostCore 完成 bootstrap、`inspect_page` 和 `inspect_object`，并验证 query 中的测试 ticket 不进入 PageState。没有可选依赖时测试标记 skipped，不算作真实浏览器通过。

## 安全边界

- 固定探针代码来自 Host，不接受页面或 Agent 提供的 JavaScript；页面内容只作为不可信审计数据处理。
- 当前通用识别器只发现可见的 search/form/filter 区域并映射为 `filter_region`。扩大对象种类必须增加 Host 固定识别器和回归样本，不能接受用户 selector。
- B04 只证明真实页面读取和初始对象唯一绑定；B05 增加发送前网络拦截、安全动作和动作后的强重新绑定，完整恢复证明属于 B06。
- B07a 真实结构化 Evidence 继续实施；仅 B07b 真实截图与像素脱敏暂缓。在 B07b 恢复并完成验收前，不得由这条真实浏览器路径生成正式 `issue_found`。
- 本切片没有通用真实站点登录适配器；测试使用本地无认证站点。类型化凭据仍被消费和清除，真实登录由站点配置支持后才能运行。

## 验收

- 缺少 Playwright/Chromium 时 fail-closed；
- 同源页面可被 HostCore 读取并生成真实候选；跨 origin 导航/跳转被拒绝；
- URL query/fragment 和 locator material 不进入持久化 PageState/AuditObject；
- 对象验证前刷新 locator registry，DOM 中对象消失后稳定返回 `not_found`；
- 唯一候选升级为 AuditObject，重复候选稳定 `ambiguous`，不选择最相似对象；
- 本地 Chromium 集成测试、全量单元测试、Schema 和差异检查全部通过。
