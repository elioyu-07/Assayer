# 垂直切片 003：对象身份验证与升级

## 交付内容

- `ObjectIdentityAdapter` 只返回结构化身份验证结果，不暴露可执行 selector；
- `inspect_object(candidateId)` 只接受当前 Scan 的 Candidate 或 AuditObject；
- `matched`：必须恰好一个候选且存在完整匹配维度，事务内升级为 `status=eligible` 的 AuditObject；
- `not_found`：候选数为 0，不生成 AuditObject；
- `ambiguous`：候选数至少 2，不选择任何对象，不能进入 Case；
- `changed`：记录变化维度并生成新的 PageCandidate，不冒充原对象；
- 所有验证结果持久化为 ObjectVerification，并支持 Operation 幂等重试；
- 适配器异常或自相矛盾的结果统一 fail-closed。

实现入口：[object_identity.py](../src/agent_f_host/object_identity.py)、[core.py](../src/agent_f_host/core.py)。

## 尚未实现

当前身份适配器是确定性测试实现，尚未读取真实 DOM，也不会执行页面动作。下一项接入安全动作前，必须把真实浏览器产生的候选结构映射到同一 `ObjectVerification` 契约。
