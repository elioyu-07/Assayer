# 垂直切片 002：只读页面发现

## 交付内容

- `ReadOnlyPageAdapter` 只暴露 `observe(pageStateId)`，接口没有动作入口；
- `inspect_page` 校验当前 PageState，只允许观察当前活动状态；
- Host 将适配器结果固化为不可变 PageState、Entrypoint 和 PageCandidate；
- Candidate 具备独立 ID、定位摘要和潜在规则，但不是 AuditObject；
- 读取不会增加 `runRevision`；同一 PageState 后续读取复用已固化快照，不重新调用适配器；
- 适配器异常、非当前 PageState 和输出不符合 Schema 时 fail-closed；
- 未配置真实浏览器登录适配器时默认失败，不会把测试适配器行为带入生产路径；
- Operation 结果持久化，进程重启后相同幂等请求返回原结果。

实现入口：[page.py](../src/assayer_host/page.py)、[core.py](../src/assayer_host/core.py)。

## 验收边界

这一切片还不会生成正式 `AuditObject`，也不会执行点击、输入、导航或网络请求。下一切片负责 `inspect_object(candidateId)` 的唯一身份验证和 AuditObject 建立。
