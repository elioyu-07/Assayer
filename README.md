# agent-f

agent-f 是运行在 Codex 中、面向测试/预发布 Web 站点的前端质量审计垂直智能体。项目当前已完成**第四条 Host Core 垂直切片**；真实浏览器适配器仍未接入。

## 当前状态

- 产品边界、顶层架构、Host–Agent 协议和核心账本 Schema 已形成可执行基线；
- Host Core 已实现 Scan/Operation、凭据消费、只读页面发现、对象身份、最小 Case 和安全动作内核；
- 动作执行默认 fail-closed；下一切片实现 Case 恢复，之后再推进证据、判定和真实浏览器适配器。

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
12. [垂直切片 001](docs/implementation-slice-001.md)
13. [垂直切片 002](docs/implementation-slice-002.md)
14. [垂直切片 003](docs/implementation-slice-003.md)
15. [垂直切片 004](docs/implementation-slice-004.md)
16. [数据 Schema](schemas/README.md)

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
