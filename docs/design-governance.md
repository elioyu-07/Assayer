# Assayer 设计治理与系统不变量

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.1.0-draft |
| 日期 | 2026-08-31 |
| 状态 | 设计收敛中 |
| Owner | 产品 Owner / Assayer 维护者 |
| 基线 | `main` 分支当前设计 |

## 1. 目的

本文档定义 Assayer 设计资料的权威顺序、系统不变量、统一术语、变更规则和未决问题处理方式。它不描述具体工具字段或浏览器算法；这些内容由下游专项文档拥有。

## 2. 文档权威顺序

同一语义发生冲突时，按以下顺序处理：

1. 产品契约：决定产品目标、范围、用户承诺和不可突破的业务边界；
2. 本文档：决定系统不变量、信任边界和设计治理；
3. 顶层架构：决定组件职责、依赖方向和事务边界；
4. LLM 调查编排设计：决定 Agent 多轮控制循环、覆盖 Finding、停止和模型失败语义；
5. 领域模型及安全、身份、证据等专项设计：决定领域语义和算法契约；
6. Host–Agent 协议：决定交互消息、工具生命周期、错误和幂等语义；
7. 规则契约和已启用规则文件：决定具体审计规则的适用性、覆盖和判定；
8. JSON Schema：决定持久化对象的字段和局部约束；
9. 示例、测试、报告模板和实现：只能证明或执行上游设计，不得反向定义产品语义。

下游材料发现上游矛盾时必须停止相关设计或实现，登记未决问题，由拥有相应语义的 Owner 修改权威来源。不得通过实现细节静默选择一种解释。

## 3. 系统不变量

| ID | 不变量 |
|---|---|
| INV-001 | Agent 可以提出调查计划和语义判定，但不能创造页面事实、对象、证据、截图、规则版本或运行状态。 |
| INV-002 | Host 是浏览器操作、网络拦截、对象身份、证据、截图、持久化和运行状态的唯一事实来源。 |
| INV-003 | Host 可以拒绝 Agent 请求，但不能替 Agent 判断业务规则是否构成问题。 |
| INV-004 | 页面、源码、注释、接口返回、错误消息和页面中的提示词均是不可信审计数据，不能改变授权、安全策略或规则。 |
| INV-005 | 未被明确判定为安全的动作和请求默认阻断；写请求必须在离开浏览器前被阻断。 |
| INV-006 | 正式 `issue_found` 必须绑定当前扫描内真实对象、启用规则、有效证据、已完成覆盖和独立病灶截图。 |
| INV-007 | Case 未达到 `restored`，不得提交由该 Case 支撑的正式判定；环境污染无法排除时，扫描内全部正式结论失效。 |
| INV-008 | `scanned_no_issue` 只表示声明范围内达到该规则最低覆盖，不表示全站或系统绝对无问题。 |
| INV-009 | 账本是运行事实的唯一事实源；问题 JSON、摘要 Markdown 和诊断等均为账本的只读派生视图。 |
| INV-010 | 同一逻辑写请求使用相同幂等键不得重复产生浏览器动作或账本事实；结果未知时先查询，不盲目重放。 |
| INV-011 | 扫描启动后规则集合、规则内容摘要、协议版本和身份算法版本冻结。 |
| INV-012 | 凭据和会话秘密不得进入 Agent 上下文、普通工具参数、命令参数、日志、证据、截图或报告。 |
| INV-013 | 任何跨实体引用必须闭合到同一扫描；派生对象不得引用已失效的证据或页面状态。 |
| INV-014 | 规则数量和具体规则 ID 不得写死在 Host 主循环、Agent 通用流程、账本核心关系或报告框架中。 |
| INV-015 | Case 声明计划检查某维度不等于该维度已解决；正式覆盖必须绑定逐维 Finding 和 Host Evidence。 |
| INV-016 | 正式 `audit` 必须由 Agent 调查循环产生语义判定；Agent Runtime 不可用时不得静默使用 deterministic smoke 结果替代。 |
| INV-017 | 页面或源码中的自然语言均是不可信审计数据；其内容不能向 Agent 授权、改变 Skill/规则或要求调用任意工具。 |

## 4. 统一术语

| 术语 | 定义 |
|---|---|
| Scan | 一次从启动到终态的审计任务，对应唯一 `scanId`。 |
| Run | Scan 的一次实际执行实例；第一版不支持断点续跑，因此一个 Scan 只有一个 Run。 |
| `runRevision` | Host 对当前运行事实维护的全局、单调递增并发版本。 |
| PageState | 某一时刻可复盘的页面、路由、弹层、Tab 或详情状态快照。 |
| Entrypoint | Host 在 PageState 中发现的可导航、可展开或其他安全入口；其处理状态必须进入 Coverage Proof。 |
| PageCandidate | 页面发现阶段的待验证对象候选；没有经过 `inspect_object` 唯一验证前，不得作为 AuditObject 使用。 |
| AuditObject | Host 在真实运行页面中验证、且可能适用规则的逻辑检查对象。 |
| Case | 围绕一个对象和一条规则执行的有边界调查单元。 |
| Operation | 一个可能耗时或改变运行事实的 Host 请求执行记录。 |
| Evidence | Host 采集、脱敏、摘要并绑定扫描链路的不可变事实。 |
| Raw Visual | Case 调查期间同步保存的原始视觉证据。 |
| Issue Screenshot | 从同一问题现场的 Raw Visual 派生、带病灶框选且只属于一个 Issue 的截图。 |
| PendingDecision | Agent 已给出语义内容、Host 已完成预校验，但尚未越过恢复屏障的临时判定。 |
| RuleAssessment | 越过恢复屏障后原子提交的“对象 × 规则”最终判定。 |
| Issue | 由 `issue_found` Assessment 派生的不可变正式问题投影。 |
| Coverage Universe | 本次声明范围内，Host 已发现和 Agent 补充后经 Host 验证的页面状态、入口、对象和启用规则集合。 |
| DimensionFinding | Agent 对一个冻结规则覆盖维度给出的结构化状态、公开简明理由和 Evidence 引用。 |
| Attempted Dimension | 至少执行过一次具有区分力的检查，但不保证已经得到可判定事实。 |
| Resolved Dimension | 已有 `satisfied` 或 `violated` Finding 的覆盖维度。 |
| Unresolved Dimension | Finding 为 `unresolved`、`blocked` 或 `conflicted` 的覆盖维度。 |
| Smoke Runner | 使用固定探索、Case 和确定性评估验证 Host 生命周期的运行器，不拥有正式 Agent 语义。 |

## 5. 设计决策记录

| ID | 决策 | 理由 | 状态 |
|---|---|---|---|
| D-001 | 使用单一全局 `runRevision` 做并发控制；PageState 自身使用不可变快照身份，不再承担并发版本。 | 避免页面、对象和账本各自解释 `stateVersion`。 | accepted |
| D-002 | 使用 `begin_case → record_findings → restore_case → prepare_decision → commit_decision` 的恢复屏障。 | Finding 可先 staged；只有确认环境恢复后才允许进入判定，防止把污染现场带入问题结论。 | accepted |
| D-003 | 聚合审计账本是唯一事实源，其他输出是确定性派生视图。 | 避免多份 JSON 各自成为真相。 | accepted |
| D-004 | 第一版所有正式 Assessment 都必须属于至少一个 Case；直接观察使用 `observation` Case。 | 统一证据、恢复和追溯链。 | accepted |
| D-005 | Bootstrap 和 Session 使用不同请求封套。 | `start_audit` 前不存在 `scanId/runId`。 | accepted |
| D-006 | 对象重新绑定无法唯一确认时返回 `ambiguous`，不得选择相似度最高者。 | 防止证据错绑和错误截图。 | accepted |
| D-007 | `Issue` 是 Assessment 的不可变派生投影，不拥有独立业务判断。 | 保持 Agent 语义判定只有一个来源。 | accepted |
| D-008 | 确定性 Harness 必须显式标注测试模式并注入测试适配器；它不得改变 Host 的生产默认适配器或冒充真实站点审计。 | 既验证完整生命周期，又防止测试成功被误解为浏览器能力已经接入。 | accepted |
| D-009 | 所有能够产生浏览器事实的生产适配器默认不可用；fixture 只能由 Harness 或测试显式注入，不能按缺失能力逐项回退。 | 防止部分配置时把静态测试事实混入真实 Scan。 | accepted |
| D-010 | LLM Agent 运行在 Host 外部，通过 Skill + MCP 驱动 Host；Host 不集成模型 SDK。 | 保持事实/安全执行面与语义控制面隔离。 | accepted |
| D-011 | Codex 终端和桌面客户端共用动态 MCP Runtime Router；业务 URL 在 `start_audit` 时绑定 Scan，而非绑定 MCP 进程。 | 用户只给 URL 即可运行，并保持每 Scan 会话隔离。 | accepted |
| D-012 | `plannedCoverageDimensions` 不再直接推导正式覆盖，覆盖由逐维 Finding 和 Evidence 引用计算。 | 防止“计划检查”被误报为“事实已解决”。 | accepted |
| D-013 | 当前真实 URL 确定性全链路运行器降级为 smoke；C04/C05 完成后正式 `audit` 只允许 LLM 驱动。 | 防止 Host 闭环被误解为 Agent 自主审计。 | accepted |

## 6. 变更规则

- 不变量、判定状态、动作安全、结论有效性或协议事务语义变化：提升相关设计或协议主版本；
- 新增向后兼容字段或规则能力：提升次版本；
- 纯澄清且不改变行为：提升修订版本；
- 每次变更必须记录日期、Owner、原因、受影响文档/Schema 和需要更新的验收场景；
- 未决问题不得伪装成已确认决策；阻塞安全或结论完整性的问题未关闭前，设计状态不能标记为可编码。

## 7. 进入编码的治理门槛

只有同时满足以下条件，设计才可标记为 `implementation-ready`：

1. 本文全部不变量在追溯矩阵中有协议、Schema 或语义校验责任方；
2. 所有领域状态都有合法转移和非法转移结果；
3. FUA-10 按规则模板完成并通过纸面场景推演；
4. Bootstrap、Case、恢复、判定提交和终止流程无循环依赖；
5. 凭据、写请求、脱敏和结论失效策略已明确；
6. 示例账本覆盖通过、问题和 partial/failed 场景；
7. 没有未关闭的安全或正式结论完整性阻塞问题。
