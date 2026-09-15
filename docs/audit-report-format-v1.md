# Assayer Formal Audit Report Format v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-14 |
| Status | Frozen |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1.2 and Audit Plugin Contract v1.2 |
| Applies to | Host report projection, terminal delivery, report artifacts, Agent adapters, and product adapters |

## 1. Purpose and authority

This document defines the single formal user-facing report for an Assayer audit
Run. The immutable platform ledger remains authoritative. The report is a
deterministic Host projection and cannot introduce, remove, weaken, strengthen,
or reinterpret a recorded conclusion.

Plugins provide domain meaning and typed issue content. The Host owns report
structure, source location, Evidence projection, ordering, de-duplication,
coverage, audit metadata, paging, and publication. Agent and product adapters
deliver the report; they do not author it.

The keywords **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, and
**MAY** are normative as described by RFC 2119 and RFC 8174.

## 2. Fixed report structure

The report title MUST be `# 审计报告`. Sections occur in the following
order. No adapter or plugin may rename, reorder, or add a parallel section.

| Order | Section | Required | Purpose |
|---:|---|---|---|
| 1 | `一、审计结论` | Yes | State terminal validity, conclusion, issue count, pending count, and coverage state. |
| 2 | `二、问题明细` | Yes | Present each confirmed problem once. |
| 3 | `三、待确认事项` | Only when non-empty | State unresolved matters and the information required to resolve them. |
| 4 | `四、覆盖情况` | Yes | State checked and unchecked scope and the effect on the conclusion. |
| 5 | `五、审计信息` | Yes | Identify the audited object, Check, plugin version, and durable report artifact. |

Each section uses one Markdown table with these exact columns:

| Section | Columns |
|---|---|
| 审计结论 | `执行状态 | 审计结论 | 确认问题数 | 待确认事项数 | 覆盖状态` |
| 问题明细 | `严重程度 | 问题位置 | 问题说明 | 判定依据 | 整改要求` |
| 待确认事项 | `涉及范围 | 未决事项 | 未决原因 | 补充要求` |
| 覆盖情况 | `覆盖状态 | 已检查范围 | 未检查范围 | 结果影响` |
| 审计信息 | `审计对象 | 检查项 | 插件版本 | 报告位置` |

When no confirmed problem exists, the Issue Details table contains the single
formal statement `未发现确认问题`. The Pending Matters section MUST be
omitted when it has no rows.

## 3. Issue record and de-duplication

The report is organized by problem, not by internal review object.

- A confirmed Candidate Finding is the ordinary problem-level record.
- A reviewer-origin Finding attached to a violated Dimension is a problem-level
  record when semantic review discovers a material issue without a deterministic
  Candidate. It lists every affected Dimension and appears once on its primary
  Dimension.
- A rejected Relationship is a problem-level record when it represents an
  independent relationship defect.
- A violated Dimension is primarily a coverage fact. It becomes a fallback
  problem row only when the WorkItem has no Candidate or Relationship record
  representing that violation.
- A suppressed Candidate produces no row. A merged Candidate is represented by
  its effective target and produces no additional row.
- Equivalent problem-level records with the same WorkItem, domain meaning, and
  remediation are merged. Their locations and Evidence are combined without
  repeating the explanation.
- A Dimension, Candidate, Relationship, Finding, or Evidence projection MUST
  NOT cause the same root cause to appear in multiple rows or sections.

Severity determines the primary order: P0, P1, P2, P3, P4, then unclassified.
The remaining order is deterministic. Presentation order cannot change the
recorded outcome.

## 4. Problem table semantics

| Column | Required content | Excluded content |
|---|---|---|
| 严重程度 | The domain severity of the confirmed problem. | Confidence or execution status. |
| 问题位置 | Host-derived source label, heading path, and bounded line range where available. | Absolute local paths, internal IDs, digests, or Agent-supplied locations. |
| 问题说明 | One concise statement of what is wrong and why it violates the rule. | Repeated Evidence, impact prose already expressed by severity, or remediation steps. |
| 判定依据 | The minimum frozen Evidence excerpt or factual basis needed to verify the judgment. | Hidden reasoning, unsupported inference, or a dump of the source body. |
| 整改要求 | A direct corrective action and, when declared by the domain, its verifiable closure condition. | A second explanation of the problem or platform implementation instructions. |

Confidence is not a problem-table column. A conclusive issue carries the
validated confidence internally. Insufficient confidence is reported under
Pending Matters with the missing information, rather than as a weakly stated
confirmed issue.

## 5. Terminal and coverage semantics

A failed Run has `无有效结论` and no valid problem or pending rows derived from
its invalidated decisions. A partial Run identifies uncovered or failed scope
and MUST NOT imply a complete pass. A completed Run with unresolved matters has
the conclusion `待确认` unless a confirmed issue already requires `不通过`.

Coverage reports audited objects, not Agent batches or internal protocol
records. Batch count, cursor, correction history, WorkItem IDs, Evidence IDs,
digests, and receipts are excluded from the formal report.

## 6. Delivery and publication

The terminal Host result MUST expose the exact report as `auditReport`.
Reports within the inline byte limit are returned as text. Longer reports are
returned as a `chunked_text` section and MUST be reconstructable losslessly by
following `nextCursor` through `get_plugin_result`.

The Host MUST publish the same bytes as `<runId>.audit-report.md`. Terminal
replay and process recovery MUST reproduce the same report and verify that the
terminal payload and durable artifact agree.

Agent and product adapters MUST present the reconstructed report verbatim.
They may explain an existing row in response to a later user question, but
MUST NOT silently rewrite the report, create an alternative summary, or derive
a second conclusion from internal result arrays.

## 7. Conformance

The report projection is conformant only when:

- all required sections and exact column names are present in order;
- Pending Matters is omitted precisely when empty;
- each represented root cause appears once;
- source locations and Evidence are Host-derived and contain no internal
  identity or absolute local path;
- failed and partial Runs retain their validity and coverage semantics;
- inline and paged delivery reconstruct the exact durable report; and
- plugin, Agent, and product adapters cannot replace the report with a parallel
  format.
