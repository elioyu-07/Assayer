# Spec 自查标准清单

This file transcribes the organization checklist supplied by the user. Preserve the category, number, wording, order, and check method in audit reports.

> **规范归属**：本文件是组织提供的 18 项原始基线。其来源优先级、替换条件以及与 skill 扩展规则的边界，以 `references/authority.md` 为准。

## 1. 完整性与结构性

| No. | 检查项 | 检查方式 |
|---:|---|---|
| 1 | 章节是否完整覆盖？每个章节是否有实质内容，而非“无”或“待补充” | 逐章扫描 |
| 2 | 新人拿到这份 Spec 能否独立理解需求，不依赖口头解释 | 找一名未参与讨论的成员试读 |
| 3 | 是否包含术语表？所有缩写、专有名词是否有统一定义 | 扫描术语定义一致性 |
| 4 | 是否明确 Out of Scope？本期不做什么是否显式列出 | 扫描边界说明章节 |
| 5 | 各章节之间是否存在矛盾或重复？如状态机与功能描述中的状态定义是否一致 | 跨章节交叉比对 |
| 6 | Spec 版本号与修订历史是否记录？每次变更是否有日期、作者、变更摘要 | 扫描文档头或修订记录表 |

## 2. 需求描述质量

| No. | 检查项 | 检查方式 |
|---:|---|---|
| 7 | 是否存在“尽量”“大约”“可能”“较好”等模糊词？验收标准是否可量化、可验证 | 关键词扫描 |
| 8 | 是否只有快乐路径？异常场景是否穷举（网络超时、并发冲突、数据异常、权限不足等） | 场景覆盖度审查 |
| 9 | 数据口径是否明确？金额精度、单位、格式、null 处理、四舍五入是否有统一定义 | 数据实体章节审查 |
| 10 | 状态流转是否完整？每个状态转换是否有触发条件、前置条件、后置条件说明 | 状态模型章节审查 |
| 11 | 业务规则是否与现有系统冲突？是否考虑了与周边系统的兼容性 | 对照现有系统文档审查 |

## 3. 可验证性

| No. | 检查项 | 检查方式 |
|---:|---|---|
| 12 | 验收标准是否可转化为自动化测试用例？是否存在“功能正常”“展示正确”等不可验证描述 | 人工审查 + 尝试转化为脚本 |
| 13 | 性能指标是否显式数值化？如“响应&lt;200ms”“首屏&lt;1s”，而非“尽量快” | 关键词扫描 |
| 14 | 权限模型是否清晰？谁可以看、谁可以改、数据隔离粒度是否有明确定义 | 权限章节审查 |

## 4. 可追溯性与可维护性

| No. | 检查项 | 检查方式 |
|---:|---|---|
| 15 | 每条功能需求是否可追溯到用户故事与验收标准，形成闭环 | 需求 ID 编号对照审查 |
| 16 | 设计决策是否记录理由？为什么选 A 不选 B，避免重复讨论 | 扫描“决策记录”标记 |
| 17 | 依赖与假设是否明确？上下游依赖、第三方服务、前置条件是否列出 | 依赖章节扫描 |
| 18 | Clarifications 章节是否有实质内容？是否只记录“结论”而未记录“问题 + 决策 + 理由”的完整上下文 | 审查每条澄清记录的完整性 |

## Interpretation boundary

- The checklist defines inspection questions and methods. It does not define severity levels, weights, a numerical score, or an overall readiness decision.
- Keep supplementary template conformance and project-specific AC/CASE rules visibly separate from the source checklist.
- When a check method requires people, external documents, or system behavior not available in the audit scope, report `UNVERIFIED（未核验）` and identify the missing evidence.
