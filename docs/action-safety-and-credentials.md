# agent-f 动作安全、请求拦截与凭据契约

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | 设计收敛中 |
| Owner | Host Core / Security Owner |

## 1. 目的

本文档把“禁止真实危险操作”和“凭据不进入 Agent”落实为 Host 可机械执行的安全契约。页面标签、Agent 意图和 HTTP 方法都不能单独构成放行依据。

## 2. 安全决策层次

每个动作依次通过四层门禁：

1. **生命周期门禁**：当前 Scan、Object 和 Case 状态允许该动作；
2. **意图门禁**：动作类型和 Agent 声明用途属于允许范围；
3. **目标门禁**：目标是当前页面中 Host 已验证的对象；
4. **请求门禁**：浏览器产生的每个外发请求在发送前独立分类和放行。

任一层拒绝即停止。上层允许不能覆盖下层拒绝。

## 3. 动作分类

| 类别 | 示例 | 默认策略 |
|---|---|---|
| 观察 | scroll、focus、读取可见状态 | 允许，但仍记录 Operation。 |
| 可逆导航 | 展开、关闭、本地 Tab 切换、打开详情 | 条件允许，必须有恢复方式。 |
| 合成输入 | 在编辑态填写合成值、触发前端校验 | 条件允许，必须记录原状态且禁止提交。 |
| 明确写操作 | 保存、提交、删除、发布、审批、上传、导入 | 拒绝。 |
| 未知动作 | 自定义脚本、任意 selector、无法分类控件 | 拒绝。 |

按钮文本只用于候选意图分类。页面把写操作伪装成“查看”时，请求门禁仍必须阻断。

## 4. 请求发送前拦截

Host 必须在浏览器网络层注册发送前拦截器。未完成分类前请求不得离开浏览器上下文。

### 4.1 分类输入

- URL、origin、method；
- resource type；
- Content-Type 和安全白名单 header；
- GraphQL operation 类型与 operation name；
- 是否含 multipart、文件、FormData；
- 发起该请求的 Operation/Case；
- 页面动作意图；
- 已冻结的站点策略和可选项目适配器结果。

请求体只能在 Host 内存中用于分类；必须先脱敏，且不得写入日志或模型证据包。

### 4.2 默认策略

| 请求 | 策略 |
|---|---|
| 同源、已识别为只读查询 | 可放行。 |
| 跨 origin | 除显式静态资源允许表外拒绝导航和业务请求。 |
| GraphQL `mutation` | 拒绝。 |
| 文件上传、multipart、Beacon | 拒绝。 |
| WebSocket/SSE | 默认只观察连接；任何客户端业务消息默认拒绝，除非项目策略证明只读。 |
| POST/PUT/PATCH/DELETE | 默认拒绝；POST 只读查询只有经 Owner 批准的项目适配器可放行。 |
| GET/HEAD/OPTIONS | 仍需检查目标和上下文；方法本身不证明无副作用。 |
| Service Worker 产生且无法归因的业务请求 | 拒绝并使 Operation 结果不确定。 |
| 无法分类 | 拒绝。 |

规则通过项目适配器放宽时，适配器版本和内容摘要必须在 Scan 启动时冻结并进入账本。

## 5. 阻断和污染判定

- 请求在发送前被成功取消：记录 `request_blocked` 证据，动作结果为 `rejected` 或 `failed_known`；
- 无法证明请求未发送：Operation 进入 `result_unknown`，立即停止普通动作并执行恢复；
- 观察到可能的持久化写入已经离开浏览器：Scan 进入 `failed`，全部正式结论失效；
- 页面本地状态已变化但请求被阻断：仍需执行 Case 恢复；
- 被阻断请求的 URL、method 和脱敏分类原因可以进入诊断，header、body、cookie 和 token 不得进入。

实现约束：Host 持久化的 RequestObservation 只保留去除 query/fragment 的 URL、method、transport、是否已发送和分类结果；请求 header、body、cookie、token 不进入 SQLite。动作参数中的字符串值在进入 ActionAttempt/ReverseCase 前只保留类型和长度，不保留原文。动作适配器未配置属于发送前的确定失败，不得虚构成 `result_unknown`。

## 6. 超时与重试

所有等待必须有显式预算，最终数值由运行配置给出并写入 Scan。第一版至少配置：

- 页面导航预算；
- 单次 Operation 预算；
- 网络静默窗口；
- 定向恢复预算；
- 刷新重放预算；
- 源码查询预算；
- Agent 回合预算。

默认原则：

- 读取类操作只对明确的瞬时错误有限重试；
- 浏览器动作和恢复动作不自动重放，先查 Operation；
- 非幂等或无法证明幂等的外部请求不重试；
- 每类重试必须有最大次数和总时间预算；
- 超出预算返回确定错误，不无限等待。

## 7. 凭据输入通道

凭据不属于普通协议消息。`start_audit` 只接收一个 Host 生成的一次性 `credentialHandle`，Agent 看不到句柄对应内容。

推荐流程：

```text
1. 用户通过 Host 本地安全提示输入凭据
2. Host 在内存凭据库生成一次性 credentialHandle
3. 调用方只把 handle 关联到 bootstrap 请求
4. Host 登录模块消费 handle
5. 登录成功或失败后立即清除凭据值和 handle
6. Agent 只收到 loginStatus 与脱敏诊断
```

约束：

- 不允许通过 CLI 参数、环境变量、普通 JSON、MCP 参数或日志提供明文凭据；
- 内存凭据设置最短可行 TTL，只允许消费一次；
- 浏览器 Cookie、Token、local/session storage 不导出；
- 登录截图默认禁用；若必须诊断，先遮挡全部输入和值区域；
- 凭据清除失败属于安全致命错误，Scan 失败。

当前实现使用 `LoginSecret` 管理 Host 所有的用户名/密码缓冲，Vault 只接受类型化秘密并在过期、丢弃或 Host 关闭时清零。`LoginCoordinator` 在成功、失败和适配器异常路径中都执行清除；清除无法证明时返回 `CREDENTIAL_CHANNEL_FAILED`，不得接受登录成功。Python/浏览器驱动边界产生的短暂不可变字符串不能提供操作系统级零残留保证，因此真实适配器还必须避免缓存、记录或回传这些字符串。

## 8. 脱敏和日志

Host 在证据落盘前执行结构化脱敏：

- header：移除 Authorization、Cookie、Set-Cookie 和项目声明秘密；
- URL：移除 token、code、ticket 等敏感 query；
- DOM/文本：遮挡密码、令牌、个人标识和项目敏感字段；
- 请求体：默认不落盘，只允许经类型化采集器输出最小摘要；
- 截图：对敏感区域应用不可逆像素遮挡，不使用可还原覆盖层；
- 日志：只记录 ID、阶段、结果、耗时和脱敏原因码。

`sanitized=true` 只有在脱敏器成功完成并记录 `sanitizationPolicyVersion` 后才能写入。脱敏状态不确定时拒绝保存证据。

## 9. 安全错误

| 错误码 | 含义 | 结果 |
|---|---|---|
| `ACTION_BLOCKED` | 动作意图或目标不允许 | Case 可补证或 needs_review |
| `REQUEST_BLOCKED` | 外发请求被发送前拦截 | 必须恢复 Case |
| `REQUEST_RESULT_UNKNOWN` | 无法证明请求是否发送/生效 | 恢复；无法证明干净则 Scan failed |
| `CREDENTIAL_CHANNEL_FAILED` | 凭据句柄或清除失败 | Scan failed |
| `SANITIZATION_FAILED` | 证据无法可靠脱敏 | 不保存证据，相关规则 needs_review |
| `CROSS_ORIGIN_BLOCKED` | 非允许 origin 导航或请求 | 记录跳过/阻断 |

## 10. 设计验收问题

安全设计必须能明确回答：

- 一个叫“查询”但实际发送 mutation 的按钮会怎样？
- 请求超时后如何证明没有持久化副作用？
- 浏览器崩溃发生在点击后、响应前时怎样收束？
- 页面通过 Beacon 或 WebSocket 写入时怎样阻断？
- 用户密码是否可能出现在 MCP、命令行、日志、截图或证据 payload？
- 脱敏器无法识别自定义敏感字段时是否会 fail closed？
