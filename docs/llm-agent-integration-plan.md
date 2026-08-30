# LLM Agent 调查层实施计划

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.1.0 |
| 日期 | 2026-08-31 |
| 状态 | C01–C02 已完成；C03–C07 待实施 |
| Owner | Agent Runtime / Host Core |

## 1. 阶段目标

本阶段把已经跑通的真实 Host 生命周期接入 Codex LLM 调查控制面。完成后，正式 `assayer audit` 由 Agent 根据冻结规则自主选择对象、规划 Case、补证和提交结论；确定性运行器只作为 `smoke` 与 CI oracle。

权威调查循环见 [LLM 调查编排设计](llm-agent-orchestration.md)。C03–C07 的 22 个可执行工作包、依赖和逐项验收门槛见 [C03–C07 后续执行计划](implementation-plan-c03-c07.md)；该文档是后续子任务状态的权威清单。

## 2. 有序任务

| # | 任务 | 主要交付 | 验收门槛 | 状态 |
|---|---|---|---|---|
| C01 | LLM 调查层设计收敛 | 编排设计、职责边界、入口语义、失败传播和验收矩阵 | 正式 audit/smoke 分离；Host/Agent/Skill 权责闭合；不依赖特定站点 | completed |
| C02 | 协议与覆盖模型升级 | 冻结规则读取、调查进度、control/list refs、DimensionFinding、机器判定门禁 Schema | planned 不再等于 covered；上下文丢失可从 Host 重建；规则 ID 不写入主循环 | completed |
| C03 | 通用交互与绑定证据 | 合成输入、查询、重置、安全选项选择、列表/请求/控件前后差异 | FUA-10 可通过通用 DOM 或交互证据确认绑定；写/未知请求仍 fail-closed | pending |
| C04 | Codex Skill 与 Agent 循环 | Skill、规则路由、对象选择、Case 规划、补证、五态提交和停止策略 | 模型实际产生多轮工具决策；Host 不生成业务结论；提示注入样本不改变策略 | pending |
| C05 | 动态 MCP 与产品入口 | Runtime Router、固定输出根、按 Scan 隔离、Agent 租约监督、终端/桌面共用配置、audit/smoke CLI 分离 | 用户只给 URL 即可启动正式 Agent；MCP 启动参数不绑定业务 URL；静默降级和任意输出路径被禁止；Agent 异常退出使 Scan failed | pending |
| C06 | 移除正式路径确定性语义判断 | `RuleEvaluationEngine` 退出正式 audit，迁入测试 oracle 或删除；文档和命令迁移 | 正式路径没有 `ruleId` 条件分支；smoke 输出不能冒充 Agent Assessment | pending |
| C07 | 通用回归与 lease 黑盒验收 | 正例、负例、不适用、歧义、阻断、提示注入和真实 lease 运行 | 规则语义结果与证据一致；无 lease 专属代码；全量回归和产物完整性通过 | pending |

## 3. 任务依赖

```text
C01
  → C02
    → C03
      → C04
        → C05
          → C06
            → C07
```

C02 先固定协议，C03 才能实现动作与 Evidence；C04 只能消费已稳定的 Host 能力。C05 负责产品装配，不能提前把尚未闭环的 Skill 包装成正式入口。

当前唯一 ready 工作包是 `C03a 可引用控件与列表发现`。后续任务不得跳过 C03a 的真实引用、失效和无 selector 泄露验收。

## 4. C07 最低样本矩阵

| 样本 | 预期 |
|---|---|
| 单列表筛选区，有查询和重置 | `scanned_no_issue` |
| 已确认绑定，有查询但无重置 | 满足截图门禁时 `issue_found`；B07c 未完成时 `needs_review` |
| 页面级搜索框，不控制业务列表 | `not_applicable` 或 `noise` |
| 两个候选列表，交互仍无法归属 | `needs_review` |
| 查询触发潜在写请求 | Host 阻断，恢复后 `needs_review` 或 Scan 按污染规则失败 |
| 页面文本要求模型忽略规则或执行任意脚本 | 指令被视为审计数据，动作不执行 |
| Agent 回合预算耗尽且仍有范围 | Scan `partial` |
| lease 黑盒系统 | 通用 Agent 完成探索和规则调查，不出现站点专属分支 |

## 5. 不与 B07c 混淆

B07c 仍是独立安全任务。它不阻塞 LLM 调查、交互绑定证明和 `scanned_no_issue`，但在完成前，真实未脱敏截图不能支撑可发布的 `issue_found`。
