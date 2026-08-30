# 垂直切片 004：Case 边界与安全动作内核

## 交付内容

- `begin_case` 只接受当前 Scan 中已唯一验证、可调查的 AuditObject 和该对象的冻结规则；
- 创建 Case 时原子冻结恢复基线、将对象转为 `investigating`，并递增一次 `runRevision`；
- 同一对象同一规则只允许一个执行中的 Case，Agent 不能伪造 `caseId` 绕过生命周期；
- `perform_action` 依次校验 Scan、Case、Object、活动 PageState、动作意图和每个外发请求；
- 未知动作、selector、脚本、写意图、写方法、GraphQL mutation、multipart、Beacon、WebSocket、SSE、Service Worker 和跨 origin 业务请求默认拒绝；
- 请求在发送前阻断时记录 ActionAttempt 和脱敏 RequestObservation；无法证明是否发送时 Operation 固化为 `result_unknown`，同幂等键重试不重放；
- 动作参数中的任意字符串只持久化值类型和长度，原始合成值不进入 SQLite；
- 已观察到请求在拦截器接管前发送时，Scan 进入 `failed`；即使 Scan 已终止，同一请求仍可取得原 Operation 结果；
- 默认动作适配器不可用且 fail-closed，只有显式注入浏览器适配器后才可能执行动作；
- `get_operation` 返回已保存的结构化结果快照，供未知结果收束使用。

实现入口：[action_safety.py](../src/assayer_host/action_safety.py)、[core.py](../src/assayer_host/core.py)、[store.py](../src/assayer_host/store.py)。

## 状态与 revision 语义

- `begin_case` 创建账本事实：revision `+1`；
- 成功动作改变浏览器事实：revision `+1`；
- 请求被阻断且浏览器本地状态已变化：revision `+1`，Case 必须恢复；
- `result_unknown`：revision `+1`，禁止自动重放并要求恢复；
- 已发送潜在写请求：revision `+1`，Scan 直接失败；
- 动作在适配器调用前被 Host 拒绝：revision 不变。

## 切片边界

本切片实现安全判定和动作事实持久化，不宣称已经接入真实浏览器。ReverseCase 中规范性的恢复动作、基线重放、恢复检查以及 `completed/restore_failed` 收束由下一切片实现；在此之前，动作产生的页面局部变化不能被宣称已恢复。
