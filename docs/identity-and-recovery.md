# agent-f 页面、对象身份与恢复契约

| 元信息 | 内容 |
|---|---|
| 文档版本 | 1.0.0-draft |
| 日期 | 2026-08-30 |
| 状态 | 设计收敛中 |
| Owner | Host Core Owner |

## 1. 目的

本文档定义 PageState 和 AuditObject 的逻辑身份、重新绑定规则、Case 恢复基线、等价判断及失败传播。目标不是让页面像素或 DOM 完全不变，而是可靠证明调查仍作用于同一逻辑对象，且 Case 没有留下影响后续调查的状态污染。

## 2. 身份算法版本

每次 Scan 冻结以下版本：

- `pageIdentityAlgorithmVersion`
- `objectIdentityAlgorithmVersion`
- `recoveryPolicyVersion`

算法版本进入 ScanRun 和最终账本。算法行为发生不兼容变化时提升主版本；历史账本始终按记录版本解释。

## 3. PageState 身份

PageState 是不可变观察快照，由 Host 生成新的 `pageStateId`。身份材料分为三类：

### 3.1 必需维度

- 规范化 origin；
- 规范化 route，包括参与页面身份的 query 参数；
- 页面层级：page/dialog/drawer/tab/detail/edit；
- 父 PageState 身份；
- 当前活动 Tab 或等价区域；
- 影响对象可达性的关键 overlay 集合。

### 3.2 条件维度

- 与当前 Case 相关的表单字段值类别；
- 展开行、分页、筛选条件和选中项；
- 影响规则判断的权限或业务模式标识；
- 仍在进行的相关请求。

### 3.3 禁止直接参与身份的动态材料

- 时间戳、随机 ID、追踪 ID；
- 动画帧、光标、hover 等瞬时视觉状态；
- 与当前对象和 Case 无关的后台轮询结果；
- 整页原始 DOM hash；
- 可被页面内容注入的“身份说明”。

Host 保存 `identityMaterialDigest` 和用于解释的结构化摘要；摘要不得包含凭据或未脱敏业务数据。

## 4. AuditObject 身份

AuditObject 表示逻辑对象，不等于某个长期存活的 DOM 节点。对象身份材料按以下优先级组合：

1. Host 已验证的组件或业务稳定标识；
2. ARIA role、可访问名称和关联 label；
3. 所属业务区域、表格列或表单路径；
4. 规范化结构路径和相邻稳定锚点；
5. 可见文本摘要；
6. 几何位置只作末级消歧，不得单独确定身份。

`hostLocatorId` 只是当前浏览器实例中的短期句柄，不写入可跨运行复用的选择器。`fingerprint` 是上述规范化身份材料的摘要，不保证仅凭摘要即可重新定位。

## 5. 重新绑定

任何导航、刷新、Tab 切换、overlay 开关、前端重渲染或可能替换 DOM 的动作后，原节点引用均视为过期。Host 必须重新发现候选并给出以下结果之一：

| 结果 | 定义 | 后续行为 |
|---|---|---|
| `matched` | 恰好一个候选满足全部必需身份维度。 | 生成新 locator 句柄并继续。 |
| `not_found` | 没有候选满足必需维度。 | 当前操作/恢复失败。 |
| `ambiguous` | 多个候选均满足必需维度，无法可靠消歧。 | 不得选“最相似”对象；停止当前对象。 |
| `changed` | 找到相关对象，但规则相关语义维度已经变化。 | 生成新对象候选；不得冒充原对象。 |

重新绑定必须保存候选数量、匹配维度和排除理由作为诊断；不向 Agent 暴露可执行 selector。

## 6. Case 恢复基线

`begin_case` 在任何动作前原子保存恢复基线，至少包含：

- 起始 PageState 引用和页面身份摘要；
- 当前对象身份摘要和重新绑定材料；
- 当前活动 Tab、overlay、展开区域；
- 本 Case 可能修改的控件状态；
- 当前 URL/route；
- 相关 pending request 集合；
- 写请求计数必须为 0；
- 可用于刷新后恢复的安全入口链。

基线按 Case 计划声明的影响范围裁剪。Host 可以扩大必检范围，Agent 不能缩小安全必检范围。

## 7. 动作日志与反向动作

每个成功执行的动作记录：

- Operation 和动作 ID；
- 动作前 `runRevision`；
- 目标对象；
- 动作前证据；
- 实际执行结果；
- `inverse`、`noop` 或 `refresh_only` 恢复方式；
- 反向动作参数的 Host 内部引用。

反向动作不能由 Agent 提供 selector 或脚本。输入合成值时必须记录原值的安全恢复句柄；原值如果包含敏感信息，只能保留在 Host 内存，不得写入账本。

## 8. 恢复算法

```text
1. 冻结新的普通动作请求
2. 等待相关只读请求在预算内结束
3. 按动作日志逆序执行定向反向动作
4. 重新绑定页面和对象
5. 对必检维度逐项比较
6. 全部 match → restored
7. 无 mismatch 但存在 unknown → uncertain
8. 任一关键 mismatch/写请求/反向动作失败 → failed
9. uncertain 或 failed → 刷新原 URL并重放安全入口链
10. 再次逐项验证并产生最终结果
```

恢复尝试的检查维度包括：

- `url_route`
- `page_layer`
- `active_tab`
- `overlay_state`
- `control_state`
- `object_identity`
- `pending_requests`
- `write_request`
- `local_visual`（仅作补充）

`restored` 要求所有适用必检维度为 `match`，不能含 `unknown`。视觉匹配不能覆盖结构或对象身份的 `unknown/mismatch`。

## 9. 失败传播

| 情况 | Case | Object | Scan | 已准备判定 |
|---|---|---|---|---|
| 定向失败、刷新恢复成功 | completed | 可继续 | 可继续 | 可提交 |
| 对象无法唯一重新绑定，但无环境污染 | restore_failed | blocked | 通常 partial | invalidated |
| pending request 无法确认结束 | restore_failed | blocked | failed | 全部 invalidated |
| 检测到请求已产生持久化写入 | invalidated | blocked | failed | 全部 invalidated |
| 浏览器崩溃导致状态无法证明 | invalidated | blocked | failed | 全部 invalidated |

只有 Host 能决定恢复状态和污染范围。Agent 不得通过自然语言理由把失败降级为成功。

## 10. 完成判据

身份与恢复实现只有同时满足以下条件才合格：

- 相同对象重渲染后能够可靠重新绑定；
- 两个相似对象无法消歧时稳定返回 `ambiguous`；
- 动态页面内容不会导致无意义的整页不一致；
- Case 修改过的相关状态全部进入必检范围；
- 写请求、未知 pending request 和对象丢失不能被视觉相似掩盖；
- 每次恢复结果都能从账本中的基线、动作和检查记录复算。

当前 Host Core 已实现上述恢复屏障的确定性内核：定向尝试不满足全量 `match` 时固定进入刷新重放，未知 pending/写请求会使 Scan 失败，Host 重启会把遗留动作/恢复 Operation 收束为 `result_unknown`。B04 已实现 Playwright 页面快照、Session 内 locator registry 和动作前的唯一对象绑定；动作后的强重新绑定与完整真实浏览器恢复仍由 B05/B06 实现，并且不能降低上述判据。
