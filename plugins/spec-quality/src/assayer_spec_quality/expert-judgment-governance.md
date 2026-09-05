# Expert Judgment Governance

> **规范归属**：本文件是 `references/authority.md` 的 finding admission、降噪和措辞指南。它不单独决定规范优先级、固定数量门槛或 readiness。

Use this reference when converting deterministic spec-check hits into final review findings. The goal is for the skill to behave like an experienced requirements and architecture reviewer: understand the intent, identify the material gap, and explain it in plain language.

## Core stance

- A checker hit is a clue, not yet a finding.
- A final finding must answer: what is unclear or inconsistent, why it matters, who is affected, and what smallest change closes it.
- Prefer fewer high-value findings over many mechanically correct but low-value findings.
- Judge by business, implementation, testing, acceptance, governance, security, and data-consistency impact.

## Finding admission gate

Before reporting any issue, confirm all of the following:

1. The affected business object or requirement is identifiable.
2. The missing or conflicting decision is specific enough to describe.
3. The issue can plausibly change implementation, test cases, acceptance judgment, compliance/governance handling, or delivery scope.
4. The reason can be explained in plain reviewer language without relying on rule IDs or scanner terminology.

Scanner coverage is not a ceiling. A reviewer may add a scanner-missed confirmed finding with an empty `candidate_ids` list only when its direct excerpt can be located verbatim in the target Spec recorded by the evidence manifest. Otherwise keep it unadmitted and record the missing evidence or review limitation.
5. The recommendation is small enough that the spec owner can act on it.

For a `CONFIRMED` finding, also require a non-empty direct excerpt that can be located in the audited source, and an object label that identifies exactly one business object. Do not confirm aggregate labels such as `FR-001~FR-016` or conclusions that exceed the evidence (for example, treating a valid file format as proof of business authenticity). If these checks fail, split the finding, downgrade it, mark it `UNVERIFIED`, or suppress it.

If any item fails, merge it into a broader finding, downgrade it, mark it unverified, or suppress it.

## Business-object first

Interpret the object before applying a generic rule.

- Text/name fields usually need length, allowed characters, whitespace handling, duplicate/unique scope, duplicate response, and empty-value behavior. Do not ask for enum values unless the spec defines the field as enum-like.
- Identifier/reference fields need generation source, uniqueness scope, nullable behavior, referenced object existence, and duplicate/non-existent/malformed ID handling. Do not downgrade an `id` to a plain number just because storage uses `bigint`.
- Enum/status/type fields need the allowed value set, value meaning, illegal value response, transition rules, and historical/unknown-value compatibility when relevant.
- Numeric fields need min/max, unit, precision, rounding, zero/negative behavior, unit conversion, and boundary behavior.
- Date/time fields need format, timezone, default, valid start/end or earliest/latest window, future/past handling, and boundary behavior.
- Permission-related requirements need role, data scope, allowed action, denied response, and whether denied data is hidden or visible but disabled.
- Dependency-related requirements need timeout/failure behavior, retry or no-retry decision, degradation, user-visible result, and consistency handling.

## Ambiguity and contradiction

Do not flag ambiguity merely because a line contains words such as “相关”, “全部”, “批量”, “自动”, “实时”, or “人工介入”.

Report ambiguity or contradiction when the spec creates divergent interpretations, for example:

- the same object has different counts, states, names, or thresholds in different sections;
- one section says the system performs an action automatically while another requires manual intervention;
- FR, AC, field definitions, decisions, NFRs, or stage differences use incompatible truth sources;
- a process step depends on a condition that another section excludes or leaves unresolved;
- a success criterion measures a behavior that the FR does not define.

For conflict findings, show each conflicting source separately and state the exact decision that must be unified.

## Gap explanation pattern

Each finding should use a targeted explanation rather than a generic template:

```text
对象：<specific FR/AC/field/decision/state/dependency>
缺口：<the specific missing or inconsistent decision>
判断原因：<what the current text says and what it does not allow the reviewer to determine>
影响：<implementation/test/review/governance risk>
建议：<smallest spec change that closes the gap>
证据：<direct excerpt or conflict comparison>
```

Avoid generic statements such as “缺少关键决策” or “字段约束不足” unless followed by the exact missing decision.

## Noise reduction

- Aggregate repeated findings with the same root cause under one object or decision.
- Keep representative evidence rather than every identical occurrence.
- Downgrade wording-only issues when the meaning is already clear.
- Do not report checks that are technically true but irrelevant to the object type.
- Do not inflate severity to force action; severity must match actual risk.
- If a finding would require the reader to guess the scanner's intent, rewrite or suppress it.

## Severity calibration

Use the severity definitions in `references/authority.md`; this guide does not redefine them. Calibrate to the concrete impact described by the finding, and do not raise severity merely because a deterministic rule used a blocking label.

## Writing style

- Use short, concrete, human-readable sentences.
- Put the conclusion before the evidence.
- Say what is missing, not which rule was triggered.
- Tailor the reason to the object and excerpt.
- Avoid repeated boilerplate across rows.
- Prefer “测试无法判断未授权用户应看到空列表还是错误提示” over “权限规则不完整”.
