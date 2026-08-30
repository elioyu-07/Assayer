# 垂直切片 019：通用 JSON CLI 与 MCP 传输（B08）

## 交付内容

- 新增 JSON Lines Host 传输：每行一个完整 Assayer 协议请求，每行返回一个协议响应；空行跳过，单行 JSON 解析失败不会终止后续请求。
- 新增 `assayer-json --stdio` CLI 入口；传输层只负责 framing 和错误封套，不创建 Scan/Operation/实体 ID，不读取浏览器，不判断规则，不写报告。
- 新增 MCP `McpToolTransport` 与可选官方 SDK stdio 入口 `assayer-mcp`：工具列表来自 Host 工具目录，调用必须携带完整协议封套并且 `tool` 与 MCP 工具名一致；返回 `structuredContent` 与文本内容均为同一 Host 响应。
- HostError 映射为稳定的 rejected/failed 协议响应；不回显凭据、请求原文、堆栈或内部异常细节。

## 验收

- JSON Lines malformed line 隔离测试，后续合法请求仍能处理。
- CLI/MCP 调用都验证只委托同一个 `HostCore.handle`，不生成业务字段。
- MCP 工具名不匹配、未知工具和不完整封套明确拒绝。
- 错误响应不泄露秘密或堆栈；B07c 脱敏门禁、Case 恢复和 `issue_found` 截图门禁保持不变。
- 全量单元测试和 `git diff --check` 通过。

命令入口：`assayer-json --stdio`；安装 `assayer[mcp]` 后使用 `assayer-mcp` 启动本地 stdio Server。

## 边界

- 本切片只实现本地 stdio MCP Server，不实现网络 MCP、远程多租户路由或浏览器能力注入；嵌入调用方可在同一受控 Host 进程内提供 Core 实例。
- MCP/CLI 不是第二事实源，不能绕过 Host Core 的封套、工具参数、生命周期和完整性校验。
