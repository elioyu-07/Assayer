# 垂直切片 022：页面探索与真实数据审计（B11）

## 背景

B10 已能给定 URL 启动真实 Chromium，但原始 `audit` 只观察首屏且只识别 `form`/`role=search`，正常 SPA 页面会被错误地报告为“0 个对象”。B11 将页面入口探索正式纳入 Host 协议，并让页面启动/Tab 切换期间的同源只读 XHR 在有限窗口内可被归因和观察。

## 交付

- 新增 `explore_entrypoint` 协议工具。它只接受 Host 在当前 PageState 发现的 `tab` Entrypoint，切换后创建新的 PageState，保留父页面引用和 runRevision 变化。
- 固定探针识别可见 Tab、筛选区域，并输出结构摘要：Tab、按钮、字段、表格、链接、对话框和页面错误提示数量；普通结构没有规则时只作为摘要，不伪造成可判定对象。
- `assayer audit` 最多遍历 16 个 Host 发现的 Tab，按标签去重；每个新 PageState 重新观察、唯一绑定首个规则候选并尝试 Raw Visual Evidence。
- 页面启动和 Tab 切换各有一个受限 network operation window：同源 GET/HEAD/OPTIONS 可归因，写请求、跨源、WebSocket、SSE、Service Worker 未归因请求继续 fail-closed。
- Hash 路由仅记录不含 query 的安全路径；Evidence/截图继续遵循 B07b 的 `sanitizationStatus=not_performed` 门禁。

## 验收

- 真实 Chromium 测试页的只读 XHR 被服务端收到且 `unknownRequests=0`，Tab 遍历产生两个 PageState。
- `http://localhost:8081/#/lease-mock` 真实试跑遍历 5 个 Tab；3 个筛选区域完成对象唯一绑定和 Raw Visual，页面返回的真实 `500 服务不可达` 错误被结构摘要记录；Host 没有产生 `Network Error` 假错误，也没有发出写请求。
- 全量测试、真实 Chromium 回归和 resilience scan 通过。

## 客观限制

当前规则注册表只有 `FUA-10`/`filter_region`，因此业务卡片、普通按钮和表格只进入结构摘要，不会自动进入规则判定或截图对象。要审计这些对象，必须先增加对应规则契约和固定识别器；不能因为 DOM 中存在元素就伪造 `potentialRules`。
