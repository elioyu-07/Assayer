# 垂直切片 017：真实结构化 Evidence（B07a）

## 交付内容

- 新增 `BrowserEvidenceAdapter`，复用 B04/B05/B06 的 Page、对象身份、Session 和网络守卫。
- 通过固定 Host 探针采集目标区域的最小 DOM/ARIA 事实：role、可访问名称是否存在、控件类型与状态类别、route、页面层级和网络摘要。
- 输入框只记录 `empty/non_empty` 等类别，不读取或保存字段原值；文本只保留长度/身份摘要所需事实，不保存完整 DOM。
- Evidence 采集前重新绑定对象并校验 fingerprint、PageState 和 origin；绑定失败直接 fail-closed。
- 扩展结构化字符串脱敏规则，覆盖 Bearer、参数秘密、邮箱、手机号和长数字标识。
- `includeRawVisual=true` 由 B07b 截图适配器处理；本切片仍只覆盖结构化 Evidence。

## 验收

- fake Page 测试验证 Evidence 只输出状态类别，不包含原始业务文本或秘密。
- 真实 Chromium HostCore 测试完成 bootstrap、inspect、对象绑定和 `capture_evidence`，持久化实体为 `runtime_dom`，不生成图片文件。
- 全量单元测试和真实 Chromium 回归通过。

## 边界

- B07a 只能支持结构化 Evidence，不能作为正式 `issue_found` 的截图替代品。
- B07b 的真实对象截图见垂直切片 018；B07c 敏感区域识别、不可逆像素遮挡和脱敏确认继续暂缓。
- B08 传输层不得绕过 Evidence、Screenshot、Case 恢复和 Decision 门禁。
