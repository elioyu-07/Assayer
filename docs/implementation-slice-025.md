# 垂直切片 025：工程统一更名为 Assayer

## 目标

将产品和工程从旧名称统一迁移为 `Assayer`，避免产品名、Python 包、CLI、Schema 命名空间和 GitHub 仓库继续分裂。

## 迁移结果

- 产品及文档展示名：`Assayer`；
- Python 分发包：`assayer`；
- Python 实现模块：`assayer_host`；
- CLI：`assayer`、`assayer-harness`、`assayer-json`、`assayer-mcp`；
- JSON Schema `$id` 命名空间：`https://assayer.dev/schemas/...`；
- GitHub 仓库：`elioyu-07/Assayer`。

规则 ID、协议字段、账本实体名、历史切片编号和已有审计语义不随品牌名变化。

## 兼容性

这是工程早期的有意破坏性更名。旧 Python 包、`agent_f_host` import 和旧 CLI 不再作为双轨别名保留，防止后续文档、测试和正式 Agent 配置继续引用旧名称。GitHub 会为旧仓库 URL 提供平台级重定向，但新配置应直接使用新地址。

## 验证

- 全仓旧名称文本残留检查通过；
- 新分发包以 editable 模式安装，四个 CLI 均可启动；
- 完整 pytest、Schema/示例账本、Markdown 链接、规则摘要和真实 lease smoke 回归通过。
