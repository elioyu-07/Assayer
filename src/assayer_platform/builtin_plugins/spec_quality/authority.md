# Spec 审计规范权威清单

**Policy 版本**：1.2.0

**生效日期**：2026-09-03

**维护者**：`spec-quality-audit` maintainers

**状态**：唯一权威入口（canonical authority entrypoint）

## 1. 本文件解决什么问题

本文件是 `spec-quality-audit` 唯一有权决定以下事项的来源：

- 一项规则是否适用于当前审计；
- 多个规范的优先级和冲突处理方式；
- 默认模板、可选 profile 和项目自定义规则如何选择；
- finding 的严重度和最终 readiness 如何判定；
- 脚本结果、外部资料和人工判断各自能证明什么。

“唯一权威入口”不等于把全部细则复制进一个大文件。模板、NFR 目录、18 项清单等仍保存在各自文件中，但只有本文件可以赋予它们适用范围和规范地位。其他 reference 可以承载被授权的规则正文、解释或示例，不得自行提高优先级，也不得与本文件重复定义冲突规则、严重度或 readiness。

本 skill 把 Spec 视为产品到工程之间的行为契约，检查其是否可理解、有边界、可实现、可测试、可追溯和可治理。它不能证明业务决策本身正确，也不能证明某个人工审批真实发生过。

## 2. 两个必须分开的概念

### 2.1 规范权威性

规范权威性回答“发生冲突时听谁的”。它由明确的组织或项目治理关系决定，不能从文件名、仓库位置、引用次数或措辞强弱推断。

### 2.2 来源可追溯性

来源可追溯性回答“这条规则从哪里来、由谁采用、是否能核验原文”。一条 skill 默认规则可以在本 skill 内有效，但若没有外部原文，就不能宣称它来自 ISO、IEEE、EARS、Spec Kit 或某个组织制度。

审计报告必须分别记录这两个属性。来源无法核验不必自动废除本地规则，但必须停止未经证据的外部归因。

## 3. 规范层级

高层级只覆盖它明确拥有的维度，不会抹除其他维度的规则。

| 优先级 | 类别 | 认定条件 | 典型来源 |
|---:|---|---|---|
| 1 | `explicit-governing-authority` | 组织权威主体明确指定为宪法、部门制度、监管要求或替代基线 | 用户提供或组织治理文件明确声明的来源 |
| 2 | `organization-baseline` | 本 skill 已采纳、且明确标记为组织基线的原始清单 | `self-check-checklist.md` |
| 3 | `explicit-project-authority` | 项目 Owner 明确指定当前项目使用的模板、SDD、NFR 或审计政策 | 用户提供或项目治理文件明确声明的来源 |
| 4 | `skill-default` | 没有更高层级规则时使用的默认契约 | 本文件授权的默认 reference |
| 5 | `external-reference` | 用于解释或借鉴的公共框架，未被明确采纳为治理规则 | Spec Kit、EARS、ISO/IEEE 等公开资料 |
| 6 | `heuristic` | 用于发现疑点的扫描信号或 reviewer 经验 | 正则、关键词、风险提示、推断规则 |

### 3.1 选择和冲突规则

1. 先确定目标文件、审计范围和要检查的维度，再选择规范。
2. 只有用户明确提供，或组织/项目通过约定治理文件明确声明的来源，才能认定为第 1 或第 3 层级。
3. 对每个被选中的来源记录路径或 URI、版本或 commit（如有）、Owner、适用维度、采用依据和读取状态。
4. 项目规则可以补充或收紧组织基线；只有更高权威明确允许例外且记录所需人工批准时，才能降低组织控制。
5. 同一层级的两个来源在同一维度冲突时，该维度结果为 `ESCALATE`，直到有权 Owner 选定来源。Agent 不得凭直觉拼接冲突规则。
6. Spec 仅仅引用了某份文件，不代表该文件自动成为 governing policy。
7. 所选来源无法读取，或要求固定版本但版本无法确认时，相关检查为 `UNVERIFIED`；不得静默回退后仍声称符合所选规范。
8. 外部资料只有在明确采纳后才具有规范效力；仅用于解释时不得据此产生阻塞 finding。

## 4. 当前来源溯源清单

本表记录“现有内容实际从哪里来”，避免虚假归因。`未核验` 表示当前仓库没有足够材料证明更上游的出处，不表示内容必然错误。

| 来源 | 当前内容来源与采用方式 | 在本 skill 中的地位 | 上游核验状态 |
|---|---|---|---|
| `self-check-checklist.md` | 用户提供并要求保留的 18 项清单；本 skill 将其采纳为组织基线 | 规范性；原始问题、分类、编号、顺序和检查方式不得被扩写成别的规则 | 未提供原始制度名称、Owner、版本或发布地址，外部组织归因未核验 |
| `spec-template.md` | 本 skill 仓库维护的十二章产品 Spec 模板 | 强制结构规范；所有普通产品 Spec 必须遵守，除非存在更高层级治理文件明确批准替代结构 | 本地规则；没有证据表明它逐字来自某一外部标准 |
| `spec-driven-development.md` | 本仓库维护的 Speckit/SDD 工作流 profile，混合了 Spec Kit 工作流概念和项目治理约束 | 仅在更高层级治理文件明确选择 `speckit` profile 时为规范；不能由文件长相自动替代十二章强制结构 | 未记录对应 Spec Kit 上游版本/commit；不得宣称与某个上游版本完全一致 |
| `nfr-catalog.md` | 本仓库维护者整理的默认 NFR 控制目录 | 默认规范；项目明确指定 NFR catalog 时由项目来源覆盖相应维度 | 本地规则；不是 ISO/IEEE 认证或逐条转录 |
| 本文件第 7–11 节 | 为解决规则冲突、数量门槛、finding admission、严重度和 readiness 而形成的 skill 治理决策 | skill 默认规范 | 本地维护者决策，版本由本文件控制 |
| `review-rubric.md` | 对本文件默认契约的语义展开和评审示例 | 解释性；只有本文件明确授权的要求具有规范性 | 本地整理 |
| `expert-judgment-governance.md` | 对 candidate finding 的准入、合并、降级和降噪方法 | 解释性执行指南 | 本地整理 |
| 交互结果摘要 | Assayer 平台对评审结果的结构化展示约定 | 仅对交互结果展示具有规范性 | 平台通用结果契约 |
| `default-policy.json` / `policy.schema.json` | 将本文件的关键决策投影为可校验数据 | 非规范性实现投影；发生差异时以本文件为准 | 由 policy 版本和测试约束 |
| `policy_loader.py` | 校验默认 policy 并安全合并显式项目 overlay | 实现，不是规范来源 | checker 已接入；仍不改变 authority.md 的规范地位 |
| finding/review/readiness/manifest 脚本与 schemas | candidate、review decision、readiness context、evidence manifest 和兼容 report envelope 的数据契约 | 实现，不是规范来源 | 已实现 decision 校验、准入投影、统一 readiness 推导和 manifest 生成；语义判断仍由 reviewer 完成 |
| `check_spec.py` / platform result summarizer | 将部分规则实现为扫描并整理交互结果 | 实现，不是规范来源 | 需通过测试和 policy 对齐验证 |
| Spec Kit、EARS、ISO/IEEE 等名称 | 当前仅作为可能的外部参考举例 | 未明确采纳时不具规范性 | 本仓库未保存已核验版本及条款映射 |

### 4.1 禁止的来源表述

在补齐可核验原文和采用记录之前，不得写：

- “18 项清单来自 ISO/IEEE/EARS”；
- “十二章模板是 Spec Kit 标准模板”；
- “本 skill 已符合某个外部标准”；
- “脚本命中就证明违反组织规范”。

可以准确写：

- “用户提供的 18 项组织基线”；
- “本 skill 维护的默认十二章模板”；
- “本仓库维护的 Speckit/SDD profile”；
- “脚本发现 candidate evidence，经语义复核后形成 finding”。

## 5. 规范授权表

| 维度 | 默认规则正文 | 项目能否替换 | 证据不可用时的默认处理 |
|---|---|---:|---|
| 18 项问题及其顺序 | `self-check-checklist.md` | 仅限更高权威明确替代或批准例外 | `UNVERIFIED` |
| 产品 Spec 章节结构 | `spec-template.md` | 仅限更高层级治理文件明确批准替代 | 缺失十二章必要结构为 `REWORK`；模板不可读取为 `UNVERIFIED` |
| Speckit 生命周期与产物职责 | 仅在更高层级治理文件明确选择 `speckit` profile 时使用 `spec-driven-development.md` | 仅限更高层级治理文件明确批准 | `UNVERIFIED` |
| FR、AC 和场景充分性 | 本文件第 7 节；细化方法见 `review-rubric.md` | 可以 | 缺少关键行为契约为 `REWORK` |
| 字段业务契约 | 本文件第 8 节；细化方法见 `review-rubric.md` | 可以 | 依据影响定 P1/P2/P3；外部口径缺失为 `UNVERIFIED` |
| NFR 适用性与豁免 | 所选 NFR catalog；默认 `nfr-catalog.md` | 可以 | 缺少强制决策为 `REWORK`；审批真实性为 `ESCALATE` 或 `UNVERIFIED` |
| finding 准入与降噪 | 本文件第 10 节；细化方法见 `expert-judgment-governance.md` | 可以 | 证据不足时不得确认 finding |
| 严重度与 readiness | 本文件第 11 节 | 仅限明确项目 policy | `UNVERIFIED` 会阻止 `READY` |
| 结果展示 | Assayer interactive result summary | 可以 | 展示失败不改变 Spec finding；平台账本仍是持久化追踪来源 |

## 6. 默认 profile 和 Spec 边界

普通产品 Spec 必须使用 `spec-template.md` 的十二章结构。未按以下章节编号和标题组织时，判定为章节结构不规范：

1. 模块定义
2. 状态模型
3. 功能需求清单
4. 关键实体
5. 数据字段定义
6. 非功能性需求选择
7. 成功标准
8. 参考资料与合规依据
9. 关键决策记录
10. 依赖与假设
11. 阶段差异说明
12. 修订记录

该默认契约描述产品行为，不要求把页面布局、物理数据库结构、线级 API schema、内部类结构或任务顺序写入产品 Spec；这些内容应由下游产物拥有时，只检查引用和边界是否清楚。

只有更高层级治理文件明确选择 `speckit` profile 时，`spec-driven-development.md` 才对结构和生命周期负责；十二章模板仅作补充参考，不再同时作为第二套强制结构。不能仅凭 `# Feature Specification`、`User Scenarios & Testing`、`Requirements` 等标题自动绕过十二章强制结构。

## 7. FR、AC 与场景契约

每个 `FR-NNN` 应当是可独立理解的行为单元，并按适用性说明：

- 业务行为及其与目标的关系；
- 上游用户故事链接，或有理由的例外；
- 输入、来源、前置条件和校验；
- 不绑定代码结构的确定性处理规则；
- 可观察输出及其去向；
- 业务规则和生命周期影响；
- 能独立通过或失败的验收条件。

验收条件应当：

- 在项目有 ID 约定时使用稳定、唯一的 `AC-*`；
- 每个 AC 只表达一个可观察结果；
- 映射到可执行的 `CASE-*`，或项目定义的等价格式；
- 根据行为和风险覆盖适用的正常、边界、非法输入、权限/数据范围、生命周期、依赖失败、超时/重试、并发/幂等和恢复场景；
- 排除填充性重复、等价分区重复和没有不同预期结果的 case。

默认不存在“每个 AC 至少 5 个 CASE”或“每个 FR 必须超过 3 个 AC”的通用硬门槛。只有项目 policy 明确启用固定阈值时才能执行该阈值；否则依据行为类型、风险和可区分结果判断充分性，并记录判断理由。

## 8. 数据字段契约

目标 Spec 第 5 章默认是业务字段含义与校验的唯一真相来源。下游文档可以补充实现或展示细节，但不能静默改变业务语义。

字段 finding 必须先识别逻辑类型，再检查与该类型相关的决策：

| 字段类型 | 重要时需要明确的决策 |
|---|---|
| 文本/名称 | 最大长度、允许字符、空白与空字符串、唯一范围、重复响应 |
| 标识/引用 | 生成来源、唯一范围、可空性、引用对象存在性、畸形/不存在/重复 ID 的响应 |
| 枚举/状态 | 完整取值、含义、非法值响应、状态转换、未知值或历史值兼容 |
| 数值 | 最小/最大值、单位、精度、舍入、零/负值、越界响应 |
| 日期/时间 | 格式、时区、默认值、有效区间、过去/未来值、非法时间响应 |
| 文件 | 类型、大小、数量、存储/访问、安全扫描、上传失败行为 |
| 布尔 | true/false 的业务含义、默认值、可空性、下游影响 |

物理存储类型不能改变业务分类：`id`、`xxxId`、`xxxCode`、`xxxNo`、编号、编码、标识、主键仍按标识/引用字段审查。

## 9. NFR 契约

使用被选中的 NFR catalog。默认 catalog 下：

- 每个 feature 考虑全部通用必选 NFR；
- 写操作、外部集成、敏感数据、批处理、查询和报表按触发条件考虑对应 NFR；
- 每项结论为 `采用`、`不适用` 或 `豁免`；
- `不适用` 有可核实理由；
- `采用` 有目标和验证证据或计划；
- `豁免` 有风险、替代控制、具名人工批准人和到期日；
- Agent 不能批准豁免；
- 未知的 SDK、网关、队列或外部平台重试行为保持未解决，不得假设安全。

skill 只检查记录是否完整、是否交给正确权威处理，不替代业务或审批人批准目标和豁免。

## 10. Finding 准入契约

确定性脚本只产生 candidate evidence。进入最终报告前，reviewer 必须：

1. 识别受影响的业务对象；
2. 指出缺失或冲突的具体决策；
3. 解释对实现、测试、验收、治理、安全或数据一致性的实质影响；
4. 合并根因相同的重复症状；
5. 抑制或降低无关的形式命中；
6. 附直接原文，冲突时并列双方证据；
7. 无法证明外部事实或业务正确性时标记 `UNVERIFIED`。

Reviewer 不受 scanner 命中范围限制。发现漏报时，可以新增没有 candidate 关联的 `CONFIRMED` finding，但其直接证据必须能在 evidence manifest 指向的目标 Spec 中原样定位；否则不得准入。每次 reviewed audit 还必须逐项记录 18 项组织基线的结论与理由，缺项时 readiness 为 `UNVERIFIED`。

若 Spec 对现有文件、接口、函数、配置或当前行为提出具体主张，且代码库或参考材料在审计范围内，应抽样核验。核验失败或无法核验必须显示在 evidence manifest 中。

高风险或架构密集型文档可以启用 adversarial profile，额外检查假设、替代方案盲区、可行性、安全、可运维性和简化空间。它是显式 profile，不应用于强迫简单 Spec 接受无关检查。

## 11. 严重度与 Readiness

严重度是本 skill 的质量治理约定，不属于 18 项清单原文：

| 严重度 | 定义 |
|---|---|
| `P1` | 阻塞安全实现、测试或规划；会导致重大行为分歧；缺失强制治理、安全或数据真相；或核心验收门仍未解决 |
| `P2` | 有实质影响但范围有限；实现只能依赖假设继续，或重要分支仍未定义 |
| `P3` | 不阻塞正确实现的局部清晰度、术语、追溯、可维护性或格式改进 |

最终只使用以下 readiness 词汇：

- `READY`：没有已确认的 P1/P2，所有强制且适用的维度已检查，且没有实质 `UNVERIFIED` 或未解决 blocker；
- `REWORK`：存在可通过修改 Spec 解决的已确认 P1/P2；
- `ESCALATE`：需要业务、治理、范围或豁免权威作决定；
- `UNVERIFIED`：所需证据或权威无法建立；它不是通过状态。

除非所选项目 policy 明确定义分数算法及决策阈值，否则不得从数值评分推导 readiness。展示可选分数时，必须标为项目度量，且不能覆盖上述 readiness 规则。

## 12. 每次审计必须输出的来源清单

审计结果至少应记录：

| 字段 | 内容 |
|---|---|
| Policy | 本文件版本 |
| Target | 实际读取的 Spec 路径与版本/commit（如有） |
| Scope | 单文件、cross-spec、代码核验和外部资料的实际范围 |
| Selected sources | 每个适用维度选中的来源、版本、Owner 和采用依据 |
| Profiles | `default`、`speckit`、`adversarial` 或项目定义 profile |
| Unavailable evidence | 无法读取、无法确认版本或需要人工/外部事实的项目 |
| Checker drift | 会影响本次结论的已知实现偏差 |

在 evidence manifest 尚未由脚本实现前，Agent 应在人工报告中提供这些信息；不能因为脚本没有字段就省略。

`scripts/evidence_manifest.py` 与 `references/evidence-manifest.schema.json` 提供该清单的机器可读实现。它们只能记录实际读取到的来源和已知边界，不能把脚本推断升级为外部规范事实。

## 13. 变更控制

修改本 policy 时必须：

1. 更新 policy 版本和生效日期；
2. 说明受影响维度和迁移影响；
3. 同步更新或废弃相关 reference 和脚本；
4. 为改变的可执行不变量增加回归测试；
5. 运行完整测试并验证 skill package；
6. 将未完成的实现差距记录为 migration，不得把现状静默当成新规范。

迁移完成前，报告必须披露 policy 版本及会影响结论的已知 checker drift。

## 14. 当前实现迁移清单

以下是已知差距，用于避免把本 policy 误认为已经被程序完全执行：

| 领域 | Policy 目标 | 当前实现状态 |
|---|---|---|
| Policy 定义 | 本权威清单的关键决策有机器可读投影 | 已提供 `default-policy.json` 和 schema；它们不是独立规范来源 |
| Policy 加载 | checker 从机器可读 policy 和已选 profile 获取检查配置 | checker 已接入 policy、base profile 和 overlay；其他规则仍待迁移 |
| Speckit profile | 显式选择后才启用，且不与十二章默认模板同时强制 | 已实现 base/overlay 路由和必需交付物检查；完整生命周期 gate 仍需人工复核 |
| AC/CASE 阈值 | 默认按行为与风险；固定数量为 opt-in | 已接入 policy；只有显式项目 overlay 才启用固定数量 |
| 18 项组织基线 | 每次审计逐项记录，不用总体印象替代 | checker 输出固定 `CHK-01..CHK-18`；交互结果摘要要求逐项复核状态和理由 |
| Candidate / reviewed finding | renderer 只消费语义复核后的 finding | JSON review/admission 已实现，支持有目标原文证据的 reviewer 漏报补充；只有 `CONFIRMED` 进入兼容 `findings`；JSON renderer 会重验 canonical projection |
| 最终 readiness | 统一词汇并显式处理 `UNVERIFIED` | 已由单一判定模块统一 JSON 和交互结果摘要；scanner-only 输出固定为 `UNVERIFIED`，review context 未证明完整时不得 `READY` |
| Evidence manifest | 记录 scope、来源、版本和未核验证据 | checker JSON 已生成并由 review workflow 继承校验；target commit 和无法证明的外部事实仍明确标记为未核验 |
| 外部主张核验 | 记录代表性代码/reference 核验 | 已实现显式 claim record、本地只读证据核验和 fail-closed 校验；URL 默认不访问，业务语义仍由 reviewer 判断 |

该表是 skill 自身的迁移说明，不是普通产品 Spec 的附加审计规则。只有用户要求评估本 skill 时，才把这些实现差距作为 finding 报告。
