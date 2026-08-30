# agent-f

agent-f 是运行在 Codex 中、面向测试/预发布 Web 站点的前端质量审计垂直智能体。项目当前已完成**第十一条 Host Core 垂直切片**和全部 14 项设计/核心/Harness 任务；真实浏览器适配器仍未接入。

## 当前状态

- 产品边界、顶层架构、Host–Agent 协议和核心账本 Schema 已形成可执行基线；
- Host Core 已实现 Scan/Operation、凭据消费、页面发现、对象身份、Case、安全动作、恢复屏障、证据、判定事务、审计收束、账本导出和 JSON/Markdown 派生报告；
- 确定性端到端 Harness 已覆盖完整生命周期，并提供模块与已安装 CLI 两种入口；
- 浏览器侧登录、页面、对象身份、动作、恢复和截图默认 fail-closed；Harness 只用于契约演示和 CI，不代表真实站点审计；
- 下一阶段按[真实浏览器与 MCP 集成计划](docs/browser-mcp-integration-plan.md)推进，当前完成 B01/9。

## 运行确定性 Harness

请使用一个尚未包含同名报告的新输出目录：

```bash
PYTHONPATH=src python3 -m agent_f_host --output-dir ./audit-output
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
24. [数据 Schema](schemas/README.md)

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
