# Spec Quality Review Rubric

> **规范归属**：本文件解释 `references/authority.md` 中默认政策的语义评审方法。若本文件与 `authority.md` 或明确选定的项目 policy 冲突，以后者为准；本文件中的数量门槛只有在所选 policy 显式启用时才生效。

## Contents

1. Review stance
2. Chapter rubric
3. Cross-section checks
4. Severity and readiness

## 1. Review stance

Treat `spec.md` as the authoritative system-behavior contract between upstream business intent and downstream engineering design.

The document owns:

- module behavior and boundaries;
- business states and rules;
- functional requirements and critical acceptance conditions;
- business entity and field semantics;
- feature-level NFR decisions;
- measurable success outcomes;
- assumptions, decisions, delivery-stage scope, and document history.

The document does not own page layout, front-end implementation, physical database design, wire-level API schemas, technical implementation choices, or task sequencing. Verify references to those artifacts without demanding their content in `spec.md`.

Judge evidence, not writing volume. A concise section can be complete; a long section can remain ambiguous.

## 2. Chapter rubric

### Metadata

Confirm:

- the branch, creation date, status, owner, baseline, input sources, and related documents are concrete;
- the baseline identifies the system version against which behavior was specified;
- any blocking question names the question and owner;
- a spec with unresolved blocking questions is not declared ready;
- related documents form one directional ownership chain rather than duplicate truth.

### 1. Module definition

Confirm:

- the one-sentence objective states a business capability or outcome, not an implementation;
- included and excluded capabilities form a clear boundary;
- adjacent-module ownership is unambiguous;
- every external or upstream dependency states the depended-on behavior and observable failure/degradation behavior;
- read/write semantics, result-unknown handling, idempotency expectations, and relevant NFR references are explicit when the dependency can create side effects;
- an unavailable dependency cannot silently become success unless the business explicitly defines that fallback.

Distinguish §1.3 operational dependencies from §10 assumptions and design constraints.

### 2. State model

Apply this chapter when a business object has a lifecycle. Otherwise require an explicit `不适用` reason.

Confirm:

- states are mutually distinguishable and use domain terminology;
- entry conditions and allowed operations are defined;
- initial and terminal states are visible;
- every diagram transition has a trigger and a corresponding rule or FR;
- roles and preconditions for sensitive transitions are defined somewhere authoritative;
- illegal transitions and concurrent/state-conflict behavior have an observable response;
- state rules do not contradict FRs or phase scope.

A diagram alone is not sufficient evidence when guards, roles, or failure behavior are material.

### 3. Functional requirements

Treat each `FR-NNN` as the primary executable behavior unit.

Confirm:

- FR IDs are unique and stable;
- grouping reflects business domains rather than code layers;
- the delivery stage is explicit;
- each FR links to an upstream user story or gives a justified exception;
- inputs identify source, required conditions, and relevant validation;
- processing describes deterministic system behavior without prescribing code structure;
- outputs identify observable destinations and results;
- business rules use precise, testable language;
- every critical AC has a stable unique `AC-*` ID and is nested under exactly one FR;
- each AC expresses one observable business outcome; split conditions joined into independently passable or fail-able outcomes rather than accepting a compound AC;
- if separate clauses have independent triggers, actions, expected results, failure reasons, or release decisions, split them into separate ACs; do not merge them to satisfy the case-count gate;
- every AC maps to enough materially distinct `CASE-*` records for its applicable profile and risk; a fixed count applies only when the selected policy explicitly enables it;
- each case states enough precondition or input, action, and expected observable result to execute; Given/When/Then is preferred but not mandatory;
- case selection draws from applicable dimensions such as the normal path, boundary values, invalid input, permission or lifecycle state, dependency failure, concurrency, and idempotency, according to the AC's behavior and risk;
- paraphrases, equivalent input partitions, cosmetic data changes, and cases with no materially different expected behavior do not count toward any configured threshold;
- critical ACs and their cases cover success plus material failure or boundary behavior and can distinguish a correct implementation from an incorrect one;
- cross-FR ordering, prerequisites, and shared rules are consistent;
- vague terms such as “适当”, “合理”, “尽量”, “支持”, or “基本实现” have measurable meaning or are revised.

For each FR, perform usage-scenario completeness analysis. Infer the FR type and apply the relevant scenario set:

| FR type | Scenario types to consider |
|---|---|
| 查询/查看 | normal result, empty/not-found result, invalid query input, permission/data-scope denial, dependency failure |
| 新增/提交/保存 | normal success, missing required fields, invalid input, permission, lifecycle state, duplicate submit/concurrency, dependency failure |
| 编辑/更新 | normal success, field boundary, invalid input, permission, lifecycle state, concurrent modification, missing target data |
| 删除/作废/取消 | normal success, permission, lifecycle state, related-data constraint, duplicate operation |
| 审批/流转 | normal transition, permission, lifecycle state, illegal transition, duplicate approval, concurrency |
| 导入/导出/批量 | normal success, empty file/data, format error, volume limit, permission, partial failure, retry/recovery |
| 外部集成/通知/同步 | normal success, timeout, failure response, retry, idempotency, data inconsistency |

For scenario AC coverage, map `FR -> applicable scenario -> AC/CASE`. Report missing scenario types explicitly. Treat missing normal, invalid-input, or permission/state coverage for a core write or flow operation as a material finding. Use a fixed AC-count threshold only when the selected project policy explicitly enables it; otherwise judge whether each FR has enough independently passable ACs for its behavior and risk. Do not count empty, duplicate, unrelated, or purely generic ACs.

Do not require repeated prose from `user-stories.md`. Require enough linkage and behavioral detail for the FR to stand as an implementation contract.

Use this reviewable structure unless repository governance defines an equivalent machine-readable convention:

```markdown
**关键 AC**：

**AC-FR001-01：授权用户可查询保单**

1. **CASE-01**：Given 保单存在且用户有权查看，When 使用有效保单号查询，Then 返回对应保单摘要。
2. **CASE-02**：Given 保单不存在，When 查询该保单号，Then 返回“不存在”且不返回保单内容。
3. **CASE-03**：Given 用户无查看权限，When 查询存在的保单，Then 返回权限错误且不泄露保单内容。
4. **CASE-04**：Given 保单号格式非法，When 发起查询，Then 返回字段校验错误且不调用下游。
5. **CASE-05**：Given 保单主数据不可用，When 发起有效查询，Then 返回依赖不可用错误且不伪装成功。
```

`CASE-01` may be reused under another AC because case IDs are scoped by their parent AC; within one AC they must be unique. The deterministic checker verifies structure, grouping, counts, and obvious duplicates. The reviewer must still reject semantically compound ACs and padded or non-executable cases.

### 4. Key entities

Confirm:

- each entity represents a domain concept rather than a table, DTO, or UI component;
- the meaning, core attributes, and relationships are sufficient to interpret the FRs;
- names conform to the unified glossary;
- every material entity is used by at least one FR or state rule;
- diagrams and tables agree.

### 5. Data field definitions

Treat this section as the single source of truth for business field semantics and business validation.

Confirm:

- field identifiers and business meanings are unambiguous;
- logical type, length or precision, requiredness, default, allowed range, and validation agree;
- conditional requiredness states the condition;
- enum values, units, time zones, precision, and null semantics are explicit when material;
- defaults are valid under the stated constraints;
- sensitive fields are identifiable and covered by applicable NFRs;
- FR inputs and outputs use the same field names and meanings;
- the section does not compete with DB types/indexes, UI timing/messages, or API request/response ownership assigned to downstream documents.

### 6. NFR selection

Read the current `nfr-catalog.md`; do not rely on remembered catalog contents.

Confirm:

- all general mandatory NFRs are listed;
- trigger-based NFRs are considered for writes, external integrations, sensitive data, batch, query, and reporting scenarios;
- every conclusion is `采用`, `不适用`, or `豁免`;
- every conclusion has a checkable applicability reason;
- every adopted NFR has a concrete target and verification method or evidence plan;
- every exemption states risk, alternative control, named human approver, and expiry date;
- an agent is never listed as the exemption approver;
- external-call NFRs cover explicit time budgets, retry classification, bounded attempts or total budget, side-effect safety, failure mapping, and verification when applicable;
- unknown client, SDK, gateway, queue, or platform retry behavior is marked unresolved rather than assumed safe.

Do not approve the business target or exemption; assess whether the record is complete and routed to the proper human authority.

### 7. Success criteria

Confirm:

- every `SC-NN` is measurable and independently verifiable;
- the measurement method defines data source, calculation, population, time window, or observation method as needed;
- success criteria express outcomes rather than restating FR completion;
- targets do not contradict NFRs or phase scope;
- the criteria collectively cover the feature objective.

### 8. References and compliance basis

Confirm:

- each cited source is identifiable and relevant;
- regulatory or governance claims point to an authoritative source;
- sources do not contradict the spec without an explicit decision;
- missing or inaccessible evidence is marked `待确认`.

### 9. Key decisions

Confirm:

- each decision preserves the question, conclusion, and date;
- material rejected alternatives or trade-offs are recorded when needed to prevent repeated debate;
- decisions are reflected in FR, state, data, NFR, or phase content;
- an unresolved question is not disguised as a decision.

### 10. Dependencies and assumptions

Confirm:

- assumptions are falsifiable premises, not missing requirements;
- design constraints cite the governing source or owner;
- technical constraints do not prescribe an unapproved implementation in a WHAT document;
- a failed assumption has a stated impact or escalation path when material;
- this section does not duplicate §1.3 operational dependency behavior.

### 11. Stage differences

Confirm:

- prototype, MVP, and full-stage behavior is explicit for each affected FR;
- `-`, `+高级`, `基本实现`, or similar shorthand is defined well enough to test;
- later stages add or change behavior without silently invalidating earlier rules;
- the table states product scope, not task ordering or technical implementation;
- a feature present in multiple stages has one unambiguous current requirement.

### 12. Revision history

Confirm:

- version, date, author, and change summary are present;
- material revisions identify affected FR, state, field, NFR, or SC IDs;
- the revision record and metadata version/status agree;
- deprecated behavior remains traceable when required by governance.

## 3. Cross-section checks

Trace these relationships:

```text
Module goal -> FR -> SC
User story -> FR -> critical AC
Entity/field -> state/rule -> FR
Dependency -> degradation/error behavior -> NFR
Phase scope -> applicable FR/state/data/NFR
Decision/assumption -> affected clauses
```

Report:

- orphaned FRs with no user-story or objective contribution;
- states, fields, entities, NFRs, or SCs not connected to feature behavior;
- duplicated rules with competing sources of truth;
- contradictions across state tables, diagrams, FRs, fields, NFRs, and phase scope;
- circular references that never state the actual rule;
- downstream design details incorrectly used to fill an upstream behavioral gap.

## 4. Intelligent requirement analysis

After structural and scenario checks, summarize expert observations as lists:

- Attention points: core business chain, state changes, critical fields/entities, acceptance criteria, and NFRs that implementation and review must focus on.
- Difficulties: signals such as multiple roles, multiple states, many entities/fields, external dependencies, batch import/export, amount/date precision, concurrent operations, or large phase differences.
- Risks: classify as requirement ambiguity, scope creep, state-transition risk, permission-boundary risk, data-definition risk, external-dependency risk, concurrency/idempotency risk, performance/capacity risk, security/compliance risk, testability risk, or staged-delivery risk.
- Risk coverage: for each risk, check whether FR, AC/CASE, NFR, dependency/degradation behavior, decision, or assumption records provide a concrete mitigation or verification path.

Use `P1` when a high-risk item has no coverage and can block safe planning or implementation, `P2` when coverage exists but lacks AC, measurable target, owner, or verification, and `P3` for improvement suggestions.

## 5. Severity and readiness

The definitions and final decision rules live only in `references/authority.md`. The examples below illustrate application and do not create additional automatic gates.

### P1

Use for a mandatory gate violation or ambiguity that can cause unsafe or materially divergent implementation, including unresolved blocking questions, missing core behavior, contradictory state/FR rules, absent mandatory NFR decisions, unapproved exemptions, or missing business-data truth for a critical path.

Also use P1 when any critical AC lacks a structured AC-to-case mapping or is not atomic. A CASE-count violation is P1 only when the selected policy explicitly makes that threshold a blocking gate and the cases remain insufficient after duplicates and padding are excluded.

### P2

Use for material but bounded incompleteness, including an unhandled failure branch, weak AC, unclear field constraint, incomplete dependency fallback, nonmeasurable SC, or ambiguous stage delta.

### P3

Use for localized consistency, naming, formatting, reference hygiene, or maintainability issues that do not block correct implementation.

### Readiness

Use the canonical readiness vocabulary and decision rules in `references/authority.md`. This rubric does not redefine them.
