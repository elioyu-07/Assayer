# 垂直切片 006：证据采集与 Raw Visual

## 交付内容

- 新增 `EvidenceAdapter`，只接受当前 Scan、当前 PageState 中唯一验证对象的采集请求；
- Evidence 必须绑定 Scan、PageState、AuditObject 和可选 Case，并记录 Host 采集 revision；
- Host 对 payload 执行结构化脱敏、确定性规范化和完整性摘要，适配器不能自行宣称 `sanitized=true`；
- 敏感 key、Bearer token 和常见 query-style secret 在写入 SQLite 前被遮蔽，无法规范化的类型 fail-closed；
- Raw Visual 必须显式请求，记录图片摘要、尺寸、对象 bounding box、注释和相对路径；
- 图片脱敏未确认、对象未定位或定位歧义时，只写入失败 Screenshot 事实，不生成可用截图路径；
- 图片文件以稳定 ID、独占创建和 `0600` 权限写入；同名不同内容冲突拒绝覆盖；
- Evidence 和 Screenshot 在 SQLite 中不可更新，幂等重试和 Host 重启后返回同一事实；
- 成功采集会递增一次 `runRevision`，有关联 Case 时进入 `evidence_captured`。

实现入口：[evidence.py](../src/agent_f_host/evidence.py)、[core.py](../src/agent_f_host/core.py)、[store.py](../src/agent_f_host/store.py)。

## 切片边界

本切片生成结构化 Evidence 和 `kind=raw_visual` 的 Screenshot。正式 `kind=issue` 的独立问题截图必须在后续 `prepare_decision(issue_found)` 中从同一 Raw Visual 派生；Raw Visual 不能直接冒充 IssueScreenshot。
