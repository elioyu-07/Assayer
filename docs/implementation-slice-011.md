# 垂直切片 011：确定性端到端 Harness

## 交付内容

- 新增 `run_deterministic_harness`，通过公开 `HostCore.handle` 协议依次执行 bootstrap、页面检查、对象验证、Case、受控动作、Evidence、恢复、判定准备/提交和审计收束。
- 新增 `python -m assayer_host` 模块入口和安装后的 `assayer-harness` 命令。
- 支持 `scanned_no_issue` 与 `issue_found` 两个自洽 fixture；问题 fixture 同时验证 Raw Visual、独立问题截图和 Issue 追溯链。
- CLI 向标准输出写入机器可读 JSON 摘要，报告目录仍只包含账本、JSON/Markdown 派生视图、日志和截图，不生成 HTML。
- 端到端测试验证十个 Operation、八次最终 revision、覆盖闭合、产物集合、问题链以及 Host 生产默认适配器保持 fail-closed。

## 使用方式

从源码运行无问题 fixture：

```bash
PYTHONPATH=src python3 -m assayer_host --output-dir ./audit-output
```

运行问题与截图 fixture：

```bash
PYTHONPATH=src python3 -m assayer_host --output-dir ./audit-issue-output --result issue_found
```

输出目录应为新目录，或至少不包含同名历史产物。Host 的不可变发布规则会拒绝覆盖内容不同的账本、报告或截图。

## 信任边界

Harness 使用静态、确定性的登录、页面、对象身份、动作、Evidence 和恢复适配器，只用于契约演示、回归测试和 CI 冒烟验证。它不会读取真实凭据或访问真实浏览器，也不得把 fixture 结论描述为真实站点审计结果。

直接构造 `HostCore()` 时，登录、动作和恢复仍由不可用适配器保护并 fail-closed。生产接入必须另行提供浏览器支持的适配器，且不得放宽现有安全、恢复、证据与判定不变量。

## 完成条件

本切片完成了当前 14 项设计、Host Core 和 Harness 计划。它证明现有组件可以端到端协作并产出可验证账本与只读报告；真实浏览器/MCP 集成是下一阶段能力建设，不在 deterministic Harness 的完成定义内。
