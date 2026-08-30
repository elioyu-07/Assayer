# agent-f

agent-f 是面向测试/预发布 Web 站点的前端质量审计垂直智能体。Host Core、真实 Chromium 适配器、JSON/MCP 传输和“给 URL 即运行”的入口已经贯通，不依赖 Codex 桌面客户端的内置浏览器连接。

## 当前状态

- 产品边界、顶层架构、Host–Agent 协议和核心账本 Schema 已形成可执行基线；
- Host Core 已实现 Scan/Operation、凭据消费、页面发现、对象身份、Case、安全动作、恢复屏障、证据、判定事务、审计收束、账本导出和 JSON/Markdown 派生报告；
- 确定性端到端 Harness 已覆盖完整生命周期，并提供模块与已安装 CLI 两种入口；
- 浏览器侧登录、页面、对象身份、动作、恢复和截图默认 fail-closed；Harness 只用于契约演示和 CI，不代表真实站点审计；
- Playwright 只读适配器已可在显式安装可选依赖后读取同源页面并唯一绑定首个 `filter_region`；
- 按[真实浏览器与 MCP 集成计划](docs/browser-mcp-integration-plan.md)推进：B07a/B07b、B08、B09、B10 已完成；B11 页面探索与真实数据审计已接入；B07c 自动敏感区域识别与像素脱敏暂缓。

公开页面可直接执行真实只读试跑：

```bash
agent-f audit 'http://localhost:8081/#/lease-mock' --output-dir ./agent-f-output
```

真实浏览器路径可以在受控测试数据环境生成对象级 PNG Raw Visual，并明确记录 `sanitizationStatus=not_performed`；它不能伪装成已脱敏图片，仍禁止据此提交正式 `issue_found`。自动敏感区域识别与像素脱敏属于后续 B07c。确定性 Harness 中的截图只用于契约与回归测试。

## 运行确定性 Harness

请使用一个尚未包含同名报告的新输出目录：

```bash
PYTHONPATH=src python3 -m agent_f_host --output-dir ./audit-output
```

通用 JSON CLI 使用 JSON Lines（每行一个完整协议封套）：

```bash
agent-f-json --url 'http://localhost:8081/#/lease-mock' --stdio < requests.jsonl
```

MCP 通过可选依赖提供本地 stdio Server：

```bash
pip install 'agent-f-host[mcp]'
agent-f-mcp --url 'http://localhost:8081/#/lease-mock'
```

需要同时验证问题、Raw Visual 和独立问题截图链路时：

```bash
PYTHONPATH=src python3 -m agent_f_host --output-dir ./audit-issue-output --result issue_found
```

安装项目后也可运行 `agent-f-harness --output-dir ./audit-output`。命令向标准输出写入机器可读 JSON 摘要，输出目录包含唯一事实源 `audit-ledger.json`、JSON/Markdown 派生报告和必要截图；不会生成 HTML。

## 推荐阅读顺序

1. [产品契约](docs/product-contract.md)
2. [设计治理与不变量](docs/design-governance.md)
3. [顶层架构](docs/architecture.md)
4. [领域模型与生命周期](docs/domain-model-and-lifecycle.md)
5. [Host–Agent 协议](docs/host-agent-protocol.md)
6. [对象身份与恢复](docs/identity-and-recovery.md)
7. [动作安全与凭据](docs/action-safety-and-credentials.md)
8. [证据与判定完整性](docs/evidence-and-decision-integrity.md)
9. [规则契约](docs/rule-contract.md)
10. [设计验收与追溯](docs/verification-and-traceability.md)
11. [工具级协议契约](docs/tool-contracts.md)
12. [真实浏览器与 MCP 集成计划](docs/browser-mcp-integration-plan.md)
13. [垂直切片 001](docs/implementation-slice-001.md)
14. [垂直切片 002](docs/implementation-slice-002.md)
15. [垂直切片 003](docs/implementation-slice-003.md)
16. [垂直切片 004](docs/implementation-slice-004.md)
17. [垂直切片 005](docs/implementation-slice-005.md)
18. [垂直切片 006](docs/implementation-slice-006.md)
19. [垂直切片 007](docs/implementation-slice-007.md)
20. [垂直切片 008](docs/implementation-slice-008.md)
21. [垂直切片 009](docs/implementation-slice-009.md)
22. [垂直切片 010](docs/implementation-slice-010.md)
23. [垂直切片 011](docs/implementation-slice-011.md)
24. [垂直切片 012](docs/implementation-slice-012.md)
25. [垂直切片 013](docs/implementation-slice-013.md)
26. [垂直切片 014](docs/implementation-slice-014.md)
27. [垂直切片 015](docs/implementation-slice-015.md)
28. [垂直切片 016](docs/implementation-slice-016.md)
29. [垂直切片 017](docs/implementation-slice-017.md)
30. [垂直切片 018](docs/implementation-slice-018.md)
31. [垂直切片 019](docs/implementation-slice-019.md)
32. [垂直切片 020](docs/implementation-slice-020.md)
33. [垂直切片 021](docs/implementation-slice-021.md)
34. [垂直切片 022](docs/implementation-slice-022.md)
35. [数据 Schema](schemas/README.md)

## 规范性来源

发生冲突时，按 [设计治理与不变量](docs/design-governance.md) 中的权威顺序处理。示例、测试和派生报告不得覆盖产品契约、安全不变量、协议或 Schema。

## 仓库结构

```text
docs/       产品、架构、协议和专项设计
rules/      规则模板及独立 FUA 规则
schemas/    持久化数据的 JSON Schema
examples/   账本和协议示例
src/        Host Core 实现
tests/      Host Core 行为测试
```

## 生产边界

`run_deterministic_harness` 显式注入静态测试适配器，不接受真实凭据，也不访问真实浏览器。直接构造 `HostCore()` 时，登录、页面、对象身份、动作和恢复适配器保持不可用并 fail-closed；接入真实浏览器前不得把 Harness 产物解释为目标站点审计结论。
