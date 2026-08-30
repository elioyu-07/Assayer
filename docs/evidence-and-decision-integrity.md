# agent-f 证据、判定与账本完整性

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | 设计收敛中 |
| Owner | Host Core / Audit Owner |

## 1. 证据层次

证据分成四层，不能相互冒充：

1. **Raw Fact**：Host 从浏览器、网络或源码读取的原始事实，暂存于 Host；
2. **Evidence**：经裁剪、脱敏、规范化摘要并绑定实体后的不可变记录；
3. **Agent Evidence Pack**：提供给 Agent 的最小充分证据包，只包含已存在的 Evidence 引用和事实摘要；
4. **Derived View**：Assessment、Issue、报告和诊断等由账本事实派生的视图。

Agent 只能引用 Evidence ID，不能复制事实后重新声明为 Host 证据。Host 不能将 Agent 自然语言理由写入 Evidence payload。

## 2. 证据绑定

每条 Evidence 必须绑定：

- `scanId`；
- `pageStateRef`；
- `objectRef`；
- 可选 `caseRef`；
- `capturedAt` 和采集 revision；
- `kind`、采集器版本和脱敏策略版本；
- 规范化 payload；
- `integrityDigest`。

没有唯一对象或页面状态归属的材料只能保存为诊断，不能被正式判定引用。源码证据必须有页面 → route → 组件/handler/API 的归属链；无法闭合时 `sourceBinding.status=unverified`。

## 3. 规范化与摘要

所有摘要算法必须使用 UTF-8、确定性 JSON 序列化：对象键按 Unicode 码点排序，数组保持语义顺序，去除不影响语义的空白；二进制使用原始字节摘要。算法描述和版本进入账本。

- Evidence 的 `integrityDigest` 摘要规范化后的 Evidence envelope，不包含写入时间、随机 ID 或文件路径等非事实字段；
- PageState 的 `domDigest` 只摘要 Host 选定的规范化状态材料，不摘要完整 DOM；
- Rule 内容摘要基于规则文件、注册条目和引用依赖的规范化字节；
- Screenshot digest 摘要最终不可变图片字节；
- 任一摘要无法计算时不得以占位值写入正式账本。

## 4. 视觉证据和截图

### 4.1 Raw Visual Capture

Case 执行期间按规则要求同步采集，绑定当前 PageState、Object 和 Case。它可以是完整对象截图、局部截图或截图元数据；不自动成为正式问题截图。

### 4.2 IssueScreenshot

正式问题截图必须从同一问题现场的 Raw Visual 派生：

- 重新确认对象唯一可定位；
- 记录问题病灶 bounding box；
- 对敏感区域不可逆遮挡；
- 保存图片字节、尺寸、类型、digest、来源 Raw Visual 引用和捕获 revision；
- 一个 Issue 只能引用一张独立 IssueScreenshot；同一对象多个 Issue 不复用未框选通用图。

截图 `captured` 失败、对象 `ambiguous` 或脱敏失败时，不得生成 `issue_found`。

## 5. 判定事务

正式判定采用恢复屏障后的两阶段事务：

```text
Agent/Host → restore_case
Host  → 仅 restored 的 Case 才允许 prepare_decision
Agent → prepare_decision
Host  → 校验规则、覆盖、证据、语义字段，创建 PendingDecision 和 IssueScreenshot
Host  → 仅 restored 才允许 commit_decision
Host  → 原子写入 RuleAssessment，并按需派生 Issue
```

`prepare_decision` 不能改变正式账本中的 Assessment 或 Issue 数组。`commit_decision` 必须在一个事务中完成：

1. 再次校验 Scan 终态、对象身份、规则注册表摘要和 runRevision；
2. 校验所有 Evidence、Case、Screenshot 引用闭合；
3. 校验结果与 `applicable`、coverage、字段门禁一致；
4. 写入不可变 RuleAssessment；
5. `issue_found` 时写入一条与 Assessment 一对一的 Issue；
6. 增加 runRevision 并记录提交 Operation。

任一校验失败，事务整体回滚，PendingDecision 保留为 `rejected` 或 `invalidated` 诊断。

## 6. 结果硬门槛

| 结果 | 门槛 |
|---|---|
| `issue_found` | 规则 enabled、对象 matched、覆盖满足、证据有效、Case restored、IssueScreenshot captured、语义字段完整。 |
| `scanned_no_issue` | 规则 enabled、对象 matched、所有最低覆盖维度完成、引用 Case restored、没有冲突证据。 |
| `not_applicable` | 有针对当前对象的适用性证据和理由；能力不足不能冒充不适用。 |
| `needs_review` | 明确记录证据缺口、冲突、阻断或身份问题；不得声称完成覆盖。 |
| `noise` | 候选与对象真实相关，但依据规则定义确认不是问题；记录噪声理由。 |

## 7. 结论失效传播

正式 Assessment 和 Issue 一经提交不可修改，只能新增失效事件或在派生视图中隐藏。以下情况使结论失效：

- Scan 进入 `failed`；
- 发现可能已发送的未知写请求；
- 账本完整性校验失败；
- 规则或身份算法摘要与冻结版本不一致；
- Evidence、Screenshot 或 Object 被证明错绑；
- 页面状态污染扩大到无法隔离的其他对象。

失效通过 `conclusionValidity` 和 `invalidatedBy` 记录；不得删除历史事实。`partial` 只允许保留已越过恢复屏障且未被失效事件覆盖的结论。

## 8. Agent Evidence Pack 裁剪

Host 默认只提供：

- 当前对象和页面状态摘要；
- 规则所需的 Evidence 类型和 ID；
- 与当前 Case 直接相关的前后状态；
- 源码最小片段和归属链；
- 脱敏后的请求摘要；
- 当前覆盖进度和缺口。

不得把完整页面 DOM、完整源码仓库、历史所有截图或凭据相关状态直接放入上下文。证据包必须标记生成版本和 `runRevision`；过期证据不能用于动作或提交。

## 9. 账本完整性校验

Host 在每个提交点和 `complete_audit` 前校验：

1. 所有实体 ID 在 Scan 内唯一；
2. 所有引用存在、同一扫描、类型正确；
3. Frozen rules 与 Registry 内容和摘要一致；
4. PageState/Object/Case/Evidence/Screenshot/Assessment/Issue 关系闭合；
5. Issue 与 Assessment 一对一且结果为 `issue_found`；
6. Case 恢复状态和 PendingDecision 屏障满足结果门槛；
7. 敏感数据扫描和摘要校验通过；
8. 历史事件可以重放出当前状态和 runRevision。

当前 Host Core 已实现结构化 Evidence、Raw Visual 和 `prepare_decision` PendingDecision 的绑定、脱敏、摘要与不可变写入。截图适配器未确认脱敏、对象未定位、定位歧义或文件内容冲突时只记录失败事实，不能进入正式问题截图门禁；`kind=issue` 的截图由判定准备事务从同一 Raw Visual 复制为独立文件和实体。正式 Assessment/Issue 的原子写入仍由 `commit_decision` 负责。

实现状态补充（2026-08-30）：真实 Chromium 截图采集与像素脱敏 B07 暂缓。上述正式问题截图契约和失败关闭门禁保持不变；暂缓期间 deterministic 截图只用于契约回归，真实浏览器扫描不得生成正式 `issue_found`。B08 传输接入不能放宽或绕过该限制。
