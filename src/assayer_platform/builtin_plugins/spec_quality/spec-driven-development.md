# Spec Driven Development 执行规范

**Version**: 2.0

> **规范归属**：本文件是 Speckit/SDD profile 的规则与生命周期说明，不是默认十二章产品 Spec 的第二套强制模板。是否启用本 profile、哪些条款适用于当前审计，以 `references/authority.md` 为准。
>
> 本文件中的 `PASS / FAIL / EXEMPT / BLOCKED` 是 Speckit profile 内部的阶段 Gate 状态，不是审计报告的最终 readiness；最终状态只按 `references/authority.md` 输出。

> **适用范围**：本规范定义基于 Speckit 工作流的规约驱动开发（SDD）流程，明确各阶段交付物的职责边界和质量约束。
> **约束效力**：本规范是 `constitution.md` 中"规约先行"和"规约-代码-测试追溯链"的下位执行文件；与宪法冲突时，以宪法为准。
> **工作流工具**：Speckit（`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`），详见 `.specify/templates/` 下的模板文件。

---

## 1. SDD 交付目录与 Speckit 阶段映射

每个业务模块通过 Speckit 工作流产出以下交付物：

```text
specs/{domain}/{module}/{feature}/
├── spec.md            # 阶段 0: /speckit-specify
├── plan.md            # 阶段 1: /speckit-plan
├── research.md        # 阶段 1: /speckit-plan（技术调研）
├── data-model.md      # 阶段 1: /speckit-plan（涉及持久化时）
├── quickstart.md      # 阶段 1: /speckit-plan（验证步骤）
├── contracts/         # 阶段 1: /speckit-plan（涉及 API 时）
│   └── api.md
├── tasks.md           # 阶段 2: /speckit-tasks
└── checklists/        # 可选: /speckit-checklist
    └── *.md
```

| 文件 | 来源 | 职责 | 是否强制 |
|---|---|---|---|
| `spec.md` | `/speckit-specify` | 面向产品的功能规范：用户故事、功能需求（FR）、验收标准（GWT）、关键实体、成功标准（SC） | 是 |
| `plan.md` | `/speckit-plan` | 面向工程的技术方案：技术栈、项目结构、数据流、领域分析模型（Mermaid 类图）、测试策略 | 是 |
| `research.md` | `/speckit-plan` | 技术选型调研与决策记录 | 涉及新技术选型时 |
| `data-model.md` | `/speckit-plan` | 技术设计模型：Mermaid `classDiagram`（① 核心 Entity 类：含字段+关系，用于替代 `erDiagram` 展示对象关系；② Service 层：仅展示非简单 CRUD 的核心业务方法，简单增删改查不展示；③ 辅助类：枚举/事件类/值对象等可按需展示）。禁止放入 Dto/Param/Converter/Mapper 等纯工程映射层类。表结构 DDL、字段约束、索引策略、MyBatis-Plus 注解 | 涉及数据持久化时 |
| `quickstart.md` | `/speckit-plan` | 验证步骤和关键命令 | 是 |
| `contracts/api.md` | `/speckit-plan` | API 契约：请求/响应格式、校验规则、错误码 | 涉及 API 时 |
| `tasks.md` | `/speckit-tasks` | 可执行任务清单：BT 编号、依赖排序、Spec→Task→Code→Test 追溯矩阵 | 是 |
| `checklists/` | `/speckit-checklist` | 基于规约拆解的检查清单 | 可选 |

### 1.1 职责边界

```
spec.md          → 产品视角：用户要什么（WHAT）
plan.md          → 技术视角：怎么实现（HOW）
data-model.md    → 数据视角：表怎么建（DATA）
tasks.md         → 执行视角：谁先谁后（WHEN）
```

---

## 2. spec.md 标准结构（对齐 Speckit 模板）

`spec.md` 由 `/speckit-specify` 生成，遵循 `.specify/templates/spec-template.md`。必须包含：

```md
# 功能规范: [FEATURE NAME]

## 用户场景与测试 *(必填)*
  ### 用户故事 N - [标题] (优先级: P1/P2/P3)
    - 优先级原因
    - 独立测试
    - 验收场景: Given-When-Then

## 边界情况

## 需求 *(必填)*
  ### 功能需求
    - FR-001: ...
    - FR-002: ...
  ### 关键实体

## 成功标准 *(必填)*
  ### 可衡量的结果
    - SC-001: ...

## 假设
```

**治理补充要求**（不改变 speckit 模板，仅追加质量约束）：

| 章节 | 治理约束 |
|---|---|
| 用户故事 | 每个故事必须有独立测试说明和至少 1 个 GWT 验收场景 |
| 功能需求 | FR 编号为 `FR-{NNN}`，每条必须可判断、可测试；禁止"尽量/适当/合理"等模糊词 |
| 关键实体 | 使用统一术语表中的领域名称（参照 `fcas-unified-glossary.md`） |
| 成功标准 | 必须是可衡量的结果，不能是功能需求的复述 |
| 边界情况 | 至少覆盖空值、非法参数两类场景；当写操作涉及共享可变状态（多用户/多请求可并发修改同一实体）时，追加并发冲突场景；纯查询或单人独占操作（如 `manager_code` 归属）无需列并发冲突 |

---

## 3. plan.md 标准结构（对齐 Speckit 模板）

`plan.md` 由 `/speckit-plan` 生成，遵循 `.specify/templates/plan-template.md`。必须包含：

```md
# 实施计划: [FEATURE]

## 摘要
## 技术背景
## 章程检查 (Constitution Gate G-01~G-13)
## 项目结构
## 复杂度跟踪
```

**治理补充要求**：

| 章节 | 治理约束 |
|---|---|
| 技术背景 | 语言/版本、主要依赖、存储、测试框架必须与 `tech-stack.md` 白名单一致 |
| 章程检查 | 逐一标注 PASS/FAIL/EXEMPT，存在 FAIL 且无豁免时 BLOCKED |
| 项目结构 | 包路径遵循 `project-spec.md` 约定的分层结构（controller/application/service/mapper/model/integration/job/config） |
| 测试策略 | 明确哪些方法需要单元测试、哪些场景触发集成测试（参照 `test-spec.md` §4.5） |

### 3.1 领域分析模型（Mermaid 类图）

`plan.md` 中应包含 Mermaid 类图，展示业务领域的概念模型。与 `data-model.md` 的职责边界：

| 维度 | plan.md 分析模型 | data-model.md 设计模型 |
|---|---|---|
| **目标读者** | 业务人员、开发者 | 开发者、DBA |
| **抽象层次** | 高（领域概念） | 低（技术实现） |
| **包含内容** | 实体、关系、业务方法 | Java 类、注解、字段类型、方法签名 |
| **技术细节** | ❌ 禁止（表名、@TableName 等） | ✅ 必须 |
| **数据库映射** | ❌ 不涉及 | ✅ 明确标注 |
| **Mermaid 格式** | `classDiagram` | `classDiagram`（含 Java 类型） |

---

## 4. 规约 ID 命名

本项目使用两级规约 ID 体系：**spec.md 层**（面向产品）和 **task/代码层**（面向工程）。

### 4.1 spec.md 层：功能需求编号（Speckit 原生）

| 类型 | 前缀 | 示例 | 来源 | 用途 |
|---|---|---|---|---|
| 功能需求 | `FR-{NNN}` | `FR-001` | spec.md §功能需求 | 产品级功能需求，由 `/speckit-specify` 生成 |
| 成功标准 | `SC-{NNN}` | `SC-001` | spec.md §成功标准 | 可衡量的业务结果 |

### 4.2 task/代码层：补充规约 ID（治理扩展）

以下 ID 用于 spec.md 之外的治理文档和代码注释：

| 类型 | 前缀 | 示例 | 用途 |
|---|---|---|---|
| 接口契约 | `API-{MODULE}-{NNN}` | `API-ASSET-001` | plan.md 或 contracts/api.md 中的 API 定义 |
| 页面交互 | `UX-{MODULE}-{NNN}` | `UX-ASSET-001` | 前端页面状态与交互 |
| 数据模型 | `DM-{MODULE}-{NNN}` | `DM-ASSET-001` | data-model.md 中的表、字段、索引 |
| 验收标准 | `AC-{MODULE}-{NNN}` | `AC-ASSET-001` | Given-When-Then 验收场景编号 |
| 非功能需求 | `NFR-{MODULE}-{NNN}` | `NFR-ASSET-001` | 性能、安全、可观测性 |

### 4.3 执行层编号

| 类型 | 前缀 | 示例 | 来源 | 用途 |
|---|---|---|---|---|
| 后端任务 | `BT{NNN}` | `BT001` | tasks.md（`/speckit-tasks` 生成） | 后端实现任务 |
| 前端任务 | `FT{NNN}` | `FT001` | tasks.md（可选） | 前端实现任务 |

### 4.4 ID 规则

- spec.md 层使用 speckit 原生的 `FR-{NNN}`，治理文档引用时保持此格式。
- 补充 ID（API-/UX-/DM-/AC-/NFR-）一经发布不得复用；废弃时标记 `@deprecated`。
- BT 编号在执行阶段自动分配，FR 与 BT 在 tasks.md 的追溯矩阵中建立映射。
- `@spec` 注释同时支持 FR 和补充 ID：`@spec FR-001` 或 `@spec API-ASSET-001`。

---

## 5. tasks.md 标准结构（对齐 Speckit 模板）

`tasks.md` 由 `/speckit-tasks` 生成，以用户故事为组织单元，遵循 `.specify/templates/tasks-template.md`。

### 5.1 必须包含

- **任务清单**：按 Phase（Setup → Foundational → User Stories → Polish）组织
- **BT 编号**：每个后端任务分配唯一 BT{NNN}
- **依赖标注**：`[P]` 标记可并行任务
- **文件路径**：每个任务包含确切的代码/测试文件路径
- **执行契约**：单一结果、关联 FR/AC、非目标、验证命令、回滚、预计范围与依赖

### 5.2 追溯矩阵（治理强制）

tasks.md 必须包含 Spec → Task → Code → Test 追溯矩阵：

```md
| Spec | Task(s) | Code Target(s) | Test Target(s) |
|---|---|---|---|
| FR-001~FR-005 | BT017~BT025 | AssetService.java, ... | AssetServiceTest.java, AssetApplicationIT.java |
```

**规则**：
- 每条 FR 必须至少映射到一个 BT
- 每个测试文件必须出现在 Test Target(s) 列
- 涉及多表事务的 FR 必须有 `*IT.java` 测试
- 矩阵覆盖率必须达到 100%

### 5.3 任务粒度上限

为避免单个 Agent 任务一次性生成过多代码，每个 BT/FT 默认应满足：

| 指标 | 上限 | 超限处理 |
|---|---:|---|
| 生产代码净新增/修改行数 | 500 行 | 拆分任务，或在任务中说明不可拆分原因 |
| 生产代码文件数 | 10 个 | 拆分任务，或在任务中说明不可拆分原因 |

规则：
- 任务应按可独立验证的业务能力拆分，避免只按文件层级拆分（如仅拆成 Entity/Mapper/Controller）。
- 超过上限但未说明原因的任务，不得进入实现。

---

## 6. 业务规则写法

业务规则（spec.md §功能需求中的 FR）必须是可判断、可测试的陈述：

```md
FR-001 系统必须支持按资产编号查询该资产下的业态列表。
FR-002 系统必须支持按业态组 ID 查询该业态下的资产条目列表。
FR-003 资产编号全局唯一，使用唯一索引约束。
```

规则：
- 禁止使用"尽量""适当""合理"等不可验证词语（`/speckit-specify` 默认遵守）
- 每条 FR 对应一个独立的、可验证的功能点
- 复杂规则可包含 Given-When-Then 子场景

---

## 7. 状态机规约

有状态业务对象必须在 spec.md 的用户故事或 plan.md 中正式定义状态机。

```md
### 状态定义

| 状态 | 含义 | 是否终态 |
|---|---|---|
| DRAFT | 草稿 | 否 |
| SUBMITTED | 已提交 | 否 |
| CONFIRMED | 已确认 | 是 |

### 状态转换

| 当前状态 | 操作 | 目标状态 | 允许角色 | 规约 ID |
|---|---|---|---|---|
| DRAFT | 提交 | SUBMITTED | 经办人 | FR-010 |
| SUBMITTED | 确认 | CONFIRMED | 审核人 | FR-011 |
```

Service 层测试必须覆盖每条状态转换及至少一个非法转换场景。

---

## 8. API 契约写法

API 契约定义在 `contracts/api.md` 中，格式如下：

```md
# API 契约: [模块名]

## API-ASSET-001 查询资产业态列表

Method: GET
Path: /api/v1/asset/{assetCode}/property-groups
Response: Result<List<PropertyGroupDto>>

业务规则映射：
- FR-001
- FR-003
```

Controller 类必须在 Swagger `@Operation` 中引用契约 ID。

---

## 9. 前端交互规约

前端交互规约定在 plan.md 或 contracts/ 中，格式遵循 `design-system.md`，覆盖：

- 页面状态：加载中、空数据、错误、正常
- 用户交互：按钮 Loading、不可重复提交、离开页面保护
- 可访问性：表单 label 关联、焦点管理、键盘导航

---

## 10. 验收标准写法

验收标准采用 Given-When-Then 格式，定义在 spec.md 的用户故事中：

```md
1. **给定** 系统中存在资产 "ASSET001"，**当** 用户请求查询业态列表时，**那么** 系统返回所有业态组信息
2. **给定** 资产不存在，**当** 用户请求查询时，**那么** 系统返回空列表
3. **给定** 用户无权限访问该资产，**当** 用户请求查询时，**那么** 系统返回权限错误
```

规则：
- 至少覆盖正常流、异常流、权限边界
- 每条验收标准可映射到一个 FR
- 验收标准编号使用 `AC-{MODULE}-{NNN}`（可选，非 speckit 原生）

---

## 11. 规约追溯矩阵

### 11.1 矩阵位置

追溯矩阵维护在 `tasks.md` 中（参见 §5.2），格式为：

```md
| Spec | Task(s) | Code Target(s) | Test Target(s) |
|---|---|---|---|
| FR-001 | BT017~BT020 | AssetQueryService.java | AssetQueryServiceTest.java |
```

### 11.2 追溯链

```
spec.md FR-xxx
    → plan.md（技术方案映射）
    → tasks.md BT{NNN}（任务分解）
    → Code（`@spec FR-xxx` 注释）
    → Test（方法名 `_FRxxx` 后缀或 `@DisplayName`）
```

### 11.3 合规要求

- **覆盖率**：每条 FR 在追溯矩阵中有对应行 → 目标 100%
- **代码标注**：核心业务方法必须包含 `@spec FR-xxx` JavaDoc 注释
- **测试映射**：测试方法名或注释必须能反查到对应的 FR 编号（参照 `test-spec.md` §3.2.2）

---

## 12. 暂存功能统一规约

> 暂存功能的完整规范已抽离为独立文件：[staging-spec.md](staging-spec.md)。
> 所有涉及多字段录入、复杂交互、审批流程的表单页面均需遵循该规约。

---

## 13. AI 执行 Gate

AI 在生成 `plan.md`、`tasks.md` 或代码前，必须逐项检查：

### 13.1 Speckit 阶段 Gate（`/speckit-plan` 执行前）

| # | 检查项 | 结果 |
|---|---|---|
| SDD-01 | 已读取 `constitution.md` | PASS/FAIL |
| SDD-02 | 已读取 `tech-stack.md`、`test-spec.md`、`design-system.md` | PASS/FAIL |
| SDD-03 | 已读取 `project-spec.md` 和 `fcas-unified-glossary.md` | PASS/FAIL |
| SDD-04 | 当前模块存在 `spec.md`，包含用户故事、FR、GWT | PASS/FAIL |
| SDD-05 | FR 编号完整，每条可判断、可测试 | PASS/FAIL |
| SDD-06 | 核心验收标准采用 Given-When-Then 格式 | PASS/FAIL |
| SDD-07 | 涉及状态流转的对象已在 spec.md 或 plan.md 中定义状态机 | PASS/FAIL/EXEMPT |
| SDD-08 | 涉及 API 的需求已在 contracts/api.md 中定义契约 | PASS/FAIL/EXEMPT |
| SDD-09 | 涉及前端的模块已定义页面状态和异常反馈 | PASS/FAIL/EXEMPT |
| SDD-10 | plan.md 包含领域分析模型（Mermaid 类图） | PASS/FAIL |
| SDD-11 | `data-model.md` 包含 Mermaid `classDiagram`（Entity 类含字段+关系；Service 仅展示非简单 CRUD 的核心业务方法；可按需展示辅助类），禁止使用 `erDiagram`；禁止放入 Dto/Param/Converter/Mapper（涉及持久化时） | PASS/FAIL/EXEMPT |
| SDD-12 | 未发现与治理文件冲突的技术或组件选择 | PASS/FAIL |
| SDD-13 | 适用 NFR 均已按 `nfr-catalog.md` 标记采用 / 不适用 / 豁免，且验证/批准信息完整 | PASS/FAIL |
| SDD-14 | 非纯文档任务已生成并确认 `impact-map.md` | PASS/FAIL/EXEMPT |

### 13.2 实现准入 Gate（`/speckit-implement` 执行前）

| # | 门禁项 | 检查方式 | 违规处理 |
|---|---|---|---|
| RE-01 | `tasks.md` 已生成且所有 `FR-xxx` 已映射到至少一个 `BT{NNN}` | 检查追溯矩阵完整性 | `BLOCKED`，需补运行 `/speckit-tasks` |
| RE-02 | 每个测试文件名已出现在追溯矩阵的 Test Target(s) 列 | 交叉比对矩阵与 `src/test` | 缺少的标记 `TODO`，实现阶段逐项收敛 |
| RE-03 | `data-model.md` 已生成（涉及数据持久化时） | 检查文件存在性 | `BLOCKED` |
| RE-04 | 触发集成测试的场景（≥2表事务、CRUD、工作流、幂等等）已生成对应 `*IT.java` | 检查 [test-spec.md §4.5](test-spec.md) 触发条件 | `BLOCKED` |
| RE-05 | BT/FT 未超过 §5.3 任务粒度上限，或已说明不可拆分原因 | 检查任务说明 | `BLOCKED` |
| RE-06 | 当前任务包含单一结果、精确文件、非目标、验证命令、回滚和预计范围 | 检查 tasks.md 可执行任务字段 | `BLOCKED` |
| RE-07 | `impact-map.md` 已确认，且 `context-bundle.md` 引用有效 Spec/NFR/Task/Map | 运行 feature package 结构检查 | `BLOCKED` |
| RE-08 | 高风险未知项已关闭或有具名 Owner 与人工批准 | 检查 Map / Bundle 人工门 | `BLOCKED` |

存在 `FAIL` 且无明确豁免理由时，禁止进入实现。
