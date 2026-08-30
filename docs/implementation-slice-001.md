# 垂直切片 001：协议与 Operation Core

## 范围

本切片只实现 Host Core 的确定性边界，不连接浏览器：

- Bootstrap/Session 封套校验；
- 工具级 input 校验；
- Scan/Run 会话身份和 `expectedRunRevision` 门禁；
- Operation 幂等键、请求摘要和 `IDEMPOTENCY_CONFLICT`；
- `get_operation` 只读查询；
- SQLite 持久化 Scan/Operation 状态，支持进程重启后的幂等查询；
- 一次性、带 TTL 的 credentialHandle 和可替换登录适配器；
- 未接入浏览器适配器的工具默认返回 `INTERNAL_FAILURE`，不尝试副作用。

实现入口：[src/assayer_host/core.py](../src/assayer_host/core.py)。行为测试：[tests/test_core.py](../tests/test_core.py)。

当前代码不仅校验请求；对已实现的 Bootstrap 响应也会再次通过工具 Output Schema，防止 Host 自己产生无法被 Agent 消费的结果。

## 明确不在范围内

浏览器导航、对象发现、网络拦截、账本事件落盘、Case 恢复和正式判定提交必须在后续切片中接入；本切片仅持久化启动/Operation 元数据，不能被当作已支持这些能力。

## 进入下一切片的门槛

1. 为 Host Core 增加持久化事件存储适配器，并保持 Operation 幂等语义不变；
2. 将 `start_audit` 接到一次性 credentialHandle 和登录适配器；
3. 为 `inspect_page` 接入只读页面快照适配器；
4. 增加 MCP/CLI 两种传输层的契约测试。

## Slice 002 启动事务

`start_audit` 当前按以下边界执行：

1. 校验封套、工具参数和请求的规则注册表版本；版本不匹配时不创建 Scan；
2. 在一个 SQLite 事务中写入 `authenticating` Scan、`running` Bootstrap Operation 和全局 Bootstrap 幂等键；
3. 原子消费带 TTL 的凭据句柄，凭据值仅作为登录适配器的瞬时参数；
4. 登录成功时将 Scan 更新为 `exploring`、Operation 更新为 `succeeded`，并将 `runRevision` 增加一次；
5. 句柄无效、登录失败或适配器异常时将 Scan 更新为 `failed`、Operation 更新为 `failed_known`，同样只增加一次 revision；
6. 进程重启后的相同幂等请求返回已持久化的同一 Scan 和 Operation，不再次消费凭据或执行登录。
