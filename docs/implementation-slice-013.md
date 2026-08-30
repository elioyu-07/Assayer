# 垂直切片 013：安全凭据通道与登录收束

## 交付内容

- 新增 `LoginSecret`，把用户名和密码保存在 Host 拥有的 `bytearray` 中；对象的 `repr/str` 永远只显示 `[REDACTED]`，清除后不能再次读取。
- `CredentialVault` 只接受未清除的类型化秘密；handle 必须符合协议 ID 格式且不可覆盖，TTL 上限为 300 秒并只能消费一次，过期、主动丢弃、Host 关闭和 `clear_all` 都会清零受管缓冲。
- 新增 `LocalCredentialIntake`，用户名和密码通过不同本地 reader 获取，默认密码 reader 为 `getpass.getpass`；只返回随机一次性 handle。
- 新增 `LoginCoordinator`，固定执行 `credential_consumed → authenticating → succeeded/failed → credential_cleared/clear_failed`；适配器异常、无效状态和凭据清除失败统一 fail-closed。
- 登录失败诊断会替换当前用户名、密码和常见 credential assignment；异常对象文本不会写入协议错误。
- 成功结果必须包含页面状态和合法、去重的 capability tuple；失败结果不得夹带页面或 capability 事实。
- Host Core 只通过 Coordinator 调用登录适配器，且关闭时清除所有尚未消费的秘密。

## 安全边界

Python 和浏览器驱动需要短暂创建不可变字符串才能填写登录字段，无法承诺操作系统级内存取证下不存在历史副本。本切片保证的是：Vault 长期拥有的缓冲可显式清零；秘密不进入普通协议、环境变量、命令参数、SQLite、日志或报告；适配器返回后 Host 不再允许读取该 `LoginSecret`。

`LocalCredentialIntake` 目前是可复用的本地入口组件，尚未暴露为通用 CLI/MCP 命令。B08 接入传输时只能在 Host 本地触发它，MCP 工具参数仍只能接收 `credentialHandle`。

## 验收

- 明文字符串不能放入 Vault，重复 handle 被拒绝；
- consume 只能成功一次，过期/discard/Host close 均清零；
- 登录成功、失败和适配器异常后秘密均不可再次读取；
- 清除失败使原本成功的登录变成 `CREDENTIAL_CHANNEL_FAILED`；
- 失败诊断不包含用户名、密码或 token assignment；
- 失败登录不能夹带 PageState/capabilities，成功登录不能缺少 PageState；
- deterministic Harness 继续通过，且所有输出不包含 fixture 密码。
