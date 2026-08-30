# 垂直切片 018：真实对象级截图（B07b）

## 交付内容

- `BrowserEvidenceAdapter` 在 `includeRawVisual=true` 时通过 Host 固定探针重新绑定对象并采集 Playwright PNG 裁剪图；Agent 不提供 selector、脚本或截图坐标。
- 截图前校验 PageState、origin、对象 fingerprint 和 bounding box；对象消失、歧义、身份变化、边界变化或 Page 不支持截图时 fail-closed，不落盘图片。
- Raw Visual 保存图片类型、PNG 头、实际宽高、对象来源边界、裁剪后问题边界和 SHA-256 digest；文件以 `0600` 权限不可变创建，内容冲突拒绝覆盖。
- Evidence、PageState、AuditObject、Case、revision 和 Screenshot 引用闭合；`prepare_decision(issue_found)` 从同一 Raw Visual 派生独立 `kind=issue` Screenshot，禁止复用通用文件。
- 图片脱敏状态与结构化 Evidence 脱敏分离：B07b 明确写入 `sanitizationStatus=not_performed`、`sanitized=false`（适配器内部），不得伪装成已完成脱敏。

## 验收

- fake Page 验证裁剪参数、源边界/问题边界、PNG 尺寸解析和未脱敏状态。
- 真实 Chromium 验证对象级截图成功、digest 与文件一致、文件权限为 `0600`，以及对象边界绑定。
- 真实路径仍拒绝以未脱敏截图准备正式 `issue_found`，错误码为 `SCREENSHOT_SANITIZATION_REQUIRED`。
- 全量单元测试、真实 Chromium 回归、`git diff --check` 通过。

## 边界

- B07b 不实现敏感区域识别、像素遮挡或脱敏完成证明；这些能力属于 B07c。
- 仅在受控测试数据环境允许保存 `not_performed` 图片；不得将其作为生产发布结论的视觉证明。
