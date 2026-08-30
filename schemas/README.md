# agent-f 数据 Schema

这些文件使用 JSON Schema Draft 2020-12，描述一次审计的可持久化实体和规则注册表。相对 `$ref` 以本目录为基准。

## 文件

- `common.schema.json`：ID、时间、摘要、规则引用、结果状态、严重度、坐标和源码位置等共享类型。
- `scan-run.schema.json`：一次扫描的输入边界、冻结规则集合、状态和覆盖证明。
- `page-state.schema.json`：可复盘的页面/弹窗/抽屉/Tab/详情/编辑状态。
- `audit-object.schema.json`：真实运行页面中发现的待检查对象。
- `rule-assessment.schema.json`：一个对象 × 一条规则的固定五态判定。
- `reverse-case.schema.json`：Agent 规划、Host 安全执行的反向 Case，以及动作前基线、反向动作、定向恢复、验证结果和刷新兜底记录。
- `evidence.schema.json`：Host 生成的不可变、已脱敏证据。
- `screenshot.schema.json`：对象病灶截图及其定位状态。
- `issue.schema.json`：由 `issue_found` 产生的正式问题项。
- `rule-registry.schema.json`：可扩展、可版本化的规则注册表。
- `audit-ledger.schema.json`：把上述实体聚合为完整审计账本。

## Schema 与 Host 语义校验的边界

JSON Schema 能校验字段类型、枚举、必填项和局部条件，但不能可靠表达跨数组的引用闭合。因此 Host 在落账前还必须做语义校验：

1. 所有实体 ID 在当前账本内唯一，引用必须指向同一扫描中的实体；
2. `scan.frozenRules` 必须存在于 `ruleRegistry.rules`，且摘要与注册表内容一致；
3. 对象、页面状态、Case、证据、截图、判定和问题的关联必须闭合；
4. `issue_found` 必须引用当前对象的有效证据和独立的 `captured` 截图，截图对象和页面状态必须匹配；
5. `scanned_no_issue` 必须满足规则注册表声明的覆盖契约；
6. 登录失败或运行中断时，正式问题结论必须标记为无效；
7. 规则 ID 不得复用，规则语义变化必须使用新版本；扫描期间注册表快照冻结。
8. Case 恢复中，定向尝试为 `uncertain` 或 `failed` 时必须存在后续刷新重放尝试；最终只有全部必检项为 `match` 的 `restored` 才允许继续调查。

## 扩展规则

增加规则只需新增注册表条目、规则文档和回归样本；只要复用现有 Host 能力，就不需要修改这些核心 Schema。规则不得新增判定状态，也不得绕过证据和截图门槛。
