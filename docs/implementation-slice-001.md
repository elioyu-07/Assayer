# 垂直切片 001：协议与 Operation Core

## 范围

本切片只实现 Host Core 的确定性边界，不连接浏览器、凭据通道或持久化数据库：

- Bootstrap/Session 封套校验；
- 工具级 input 校验；
- Scan/Run 会话身份和 `expectedRunRevision` 门禁；
- Operation 幂等键、请求摘要和 `IDEMPOTENCY_CONFLICT`；
- `get_operation` 只读查询；
- 未接入适配器的工具默认返回 `UNIMPLEMENTED_TOOL`，不尝试副作用。

实现入口：[src/agent_f_host/core.py](../src/agent_f_host/core.py)。行为测试：[tests/test_core.py](../tests/test_core.py)。

## 明确不在范围内

浏览器导航、对象发现、网络拦截、凭据消费、账本落盘、Case 恢复和正式判定提交必须在后续切片中接入；本切片不能被当作已支持这些能力。

## 进入下一切片的门槛

1. 为 Host Core 增加持久化事件存储适配器，并保持 Operation 幂等语义不变；
2. 将 `start_audit` 接到一次性 credentialHandle 和登录适配器；
3. 为 `inspect_page` 接入只读页面快照适配器；
4. 增加 MCP/CLI 两种传输层的契约测试。
