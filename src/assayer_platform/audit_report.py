"""Concise, formal Markdown audit report derived from a terminal ledger.

The ledger remains authoritative.  This module only projects its committed
common-review values into the platform-owned human report format.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .actionable_result import extract_result_delivery_bundle
from .contract import InvestigationPacket, PlatformContractError, PlatformLedger
from .document_source import DocumentSnapshotStore


_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "未分级": 5}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _unique_texts(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _text(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _statements(values: Sequence[object]) -> str:
    statements = [item.rstrip("。；;") for item in _unique_texts(values)]
    return ("；".join(statements) + "。") if statements else ""


def _cell(value: object) -> str:
    text = _text(value) or "—"
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _source_label(identity: object) -> str:
    value = _text(identity)
    if not value:
        return "审计对象"
    if "://" in value:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    path = Path(value)
    return path.name if path.is_absolute() else value


def _packet_source(packet: InvestigationPacket) -> str:
    """Select a public source label from frozen packet data, never an opaque digest."""
    for evidence in packet.evidence:
        payload = _mapping(evidence.payload)
        for chunk in _items(payload.get("sourceChunks")):
            path = _text(chunk.get("document_path", chunk.get("documentPath")))
            if path:
                return _source_label(path)
    metadata_path = _text(_mapping(packet.metadata).get("path"))
    if metadata_path:
        return _source_label(metadata_path)
    for evidence in packet.evidence:
        identity = _source_label(evidence.source_identity)
        if identity and not identity.startswith("sha256:"):
            return identity
    identity = _source_label(packet.work_item.identity)
    return "审计对象" if identity.startswith("sha256:") else identity


def _support(value: object) -> tuple[tuple[str, ...], str]:
    support = _mapping(_mapping(value).get("support"))
    refs = support.get("refs", ())
    normalized_refs = (
        tuple(dict.fromkeys(_text(item) for item in refs if _text(item)))
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes))
        else ()
    )
    return normalized_refs, _text(support.get("reason"))


@dataclass(frozen=True)
class _EvidenceAnchor:
    source: str
    start_line: int | None = None
    end_line: int | None = None
    headings: tuple[str, ...] = ()
    excerpt: str = ""

    @property
    def location(self) -> str:
        parts = [self.source]
        if self.headings:
            parts.append(" → ".join(self.headings))
        if self.start_line is not None and self.end_line is not None:
            line = (
                f"第{self.start_line}行"
                if self.start_line == self.end_line
                else f"第{self.start_line}–{self.end_line}行"
            )
            parts.append(line)
        return " → ".join(parts)


def _evidence_catalog(
    packet: InvestigationPacket, snapshot_root: str | Path | None,
) -> dict[str, _EvidenceAnchor]:
    source = _packet_source(packet)
    catalog: dict[str, _EvidenceAnchor] = {}
    for evidence in packet.evidence:
        catalog[evidence.evidence_id] = _EvidenceAnchor(source)
        payload = _mapping(evidence.payload)
        chunks = _items(payload.get("sourceChunks"))
        excerpts: dict[str, str] = {}
        snapshot = _mapping(payload.get("documentSnapshot"))
        snapshot_id = snapshot.get("snapshotId")
        if snapshot_root is not None and isinstance(snapshot_id, str):
            try:
                _document, stored = DocumentSnapshotStore(snapshot_root).load(snapshot_id)
                indexed = DocumentSnapshotStore.packet_index(
                    stored, work_item_id=packet.work_item.work_item_id,
                )
                excerpts = {
                    str(index["source_chunk_id"]): chunk.text
                    for index, chunk in zip(indexed, stored)
                }
            except (OSError, ValueError, PlatformContractError):
                excerpts = {}
        for chunk in chunks:
            ref = _text(chunk.get("source_chunk_id"))
            if not ref:
                continue
            start = chunk.get("startLine", chunk.get("start_line"))
            end = chunk.get("endLine", chunk.get("end_line"))
            headings = chunk.get("headingPath", chunk.get("heading_path", ()))
            catalog[ref] = _EvidenceAnchor(
                _source_label(
                    chunk.get("documentPath", chunk.get("document_path")) or source,
                ),
                start if isinstance(start, int) and not isinstance(start, bool) else None,
                end if isinstance(end, int) and not isinstance(end, bool) else None,
                tuple(_text(item) for item in headings if _text(item))
                if isinstance(headings, Sequence) and not isinstance(headings, (str, bytes))
                else (),
                _text(excerpts.get(ref) or chunk.get("excerpt") or chunk.get("content")),
            )
    return catalog


@dataclass
class _Issue:
    work_item_id: str
    severity: str
    title: str
    explanations: list[str]
    refs: list[str]
    evidence_reasons: list[str]
    recommendation: str
    anchors: list[_EvidenceAnchor] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)


@dataclass
class _Pending:
    work_item_id: str
    subject: str
    reasons: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


def _append_unique(target: list[str], values: Sequence[object]) -> None:
    for value in _unique_texts(values):
        if value not in target:
            target.append(value)


def _common_review_items(decision: object) -> tuple[Mapping[str, Any], ...]:
    details = _mapping(getattr(decision, "details", {}))
    return _items(details.get("commonReview"))


def _claim_anchor(claim: Mapping[str, Any], fallback_source: str) -> _EvidenceAnchor:
    scope = _mapping(claim.get("scope"))
    source_value = _text(scope.get("documentPath"))
    source = (
        fallback_source
        if not source_value or source_value == "[LOCAL_PATH]"
        else _source_label(source_value)
    )
    start = scope.get("startLine")
    end = scope.get("endLine")
    observed = claim.get("observed", ())
    excerpts = (
        _unique_texts(observed)
        if isinstance(observed, Sequence) and not isinstance(observed, (str, bytes))
        else []
    )
    return _EvidenceAnchor(
        source,
        start if isinstance(start, int) and not isinstance(start, bool) else None,
        end if isinstance(end, int) and not isinstance(end, bool) else None,
        excerpt=excerpts[0] if excerpts else "",
    )


def _remediation_requirement(remediation: Mapping[str, Any]) -> str:
    values = (
        ("整改措施", remediation.get("recommendation")),
        ("执行要求", remediation.get("nextAction")),
        ("完成标准", remediation.get("closureEvidence")),
    )
    parts: list[str] = []
    seen: set[str] = set()
    for label, value in values:
        normalized = _text(value).rstrip("。；;")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(f"{label}：{normalized}。")
    return "<br>".join(parts) or "依据检查要求完成整改并补充验证证据。"


def _delivery_issues(
    decision: object, packet: InvestigationPacket,
) -> tuple[list[_Issue], set[str]]:
    status, remediations, claims = extract_result_delivery_bundle(decision, packet)
    if status not in {"complete", "partial"} or not remediations:
        return [], set()
    claim_by_id = {_text(item.get("claimId")): item for item in claims}
    fallback_source = _packet_source(packet)
    result: list[_Issue] = []
    covered: set[str] = set()
    for remediation in remediations:
        title = _text(remediation.get("title")) or "已确认问题"
        problem = _text(remediation.get("problem"))
        impact = _text(remediation.get("impact"))
        explanations: list[str] = []
        if problem and problem != title:
            explanations.append(problem)
        if impact:
            explanations.append(f"影响：{impact}")
        claim_refs = remediation.get("claimRefs", ())
        linked_claims = [
            claim_by_id[ref] for ref in claim_refs
            if isinstance(ref, str) and ref in claim_by_id
        ] if isinstance(claim_refs, Sequence) and not isinstance(claim_refs, (str, bytes)) else []
        evidence_reasons: list[str] = []
        anchors: list[_EvidenceAnchor] = []
        for claim in linked_claims:
            observed = claim.get("observed", ())
            if isinstance(observed, Sequence) and not isinstance(observed, (str, bytes)):
                _append_unique(evidence_reasons, observed)
            if not evidence_reasons:
                _append_unique(evidence_reasons, (claim.get("conclusion"),))
            anchors.append(_claim_anchor(claim, fallback_source))
        raw_dimensions = remediation.get("dimensions", ())
        dimensions = (
            _unique_texts(raw_dimensions)
            if isinstance(raw_dimensions, Sequence) and not isinstance(raw_dimensions, (str, bytes))
            else []
        )
        covered.update(dimensions)
        raw_refs = remediation.get("evidenceRefs", ())
        refs = (
            _unique_texts(raw_refs)
            if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes))
            else []
        )
        result.append(_Issue(
            str(getattr(decision, "work_item_id")),
            _text(remediation.get("severity")) or "未分级",
            title,
            explanations,
            refs,
            evidence_reasons,
            _remediation_requirement(remediation),
            anchors,
            dimensions,
        ))
    return result, covered


def _issue_rows(
    ledger: PlatformLedger,
    packets: Mapping[str, InvestigationPacket],
) -> list[_Issue]:
    result: list[_Issue] = []
    for decision in ledger.decisions:
        packet = packets.get(decision.work_item_id)
        if packet is not None:
            delivery, covered_dimensions = _delivery_issues(decision, packet)
            if delivery:
                result.extend(delivery)
                for finding in decision.findings:
                    if (
                        finding.status in {"violated", "conflicted"}
                        and finding.dimension not in covered_dimensions
                    ):
                        result.append(_Issue(
                            decision.work_item_id, "未分级",
                            f"检查项“{finding.dimension}”未满足要求",
                            [finding.reason], [], [],
                            "依据检查要求完成整改并补充验证证据。",
                            dimensions=[finding.dimension],
                        ))
                continue
        common = _common_review_items(decision)
        primary: list[_Issue] = []
        relationships: list[_Issue] = []
        violated_dimensions: list[tuple[str, str, tuple[str, ...], str]] = []

        def record_primary(issue: _Issue) -> None:
            duplicate = next((existing for existing in primary if (
                existing.title, existing.recommendation, existing.explanations
            ) == (issue.title, issue.recommendation, issue.explanations)), None)
            if duplicate is None:
                primary.append(issue)
                return
            _append_unique(duplicate.refs, issue.refs)
            _append_unique(duplicate.evidence_reasons, issue.evidence_reasons)
            _append_unique(duplicate.dimensions, issue.dimensions)

        for item in common:
            kind = _text(item.get("kind"))
            value = _mapping(item.get("value"))
            refs, support_reason = _support(value)
            if kind == "candidate" and value.get("disposition") == "confirmed":
                finding = _mapping(value.get("finding"))
                finding_refs, finding_support_reason = _support(finding)
                issue = _Issue(
                    decision.work_item_id,
                    _text(finding.get("severity")) or "未分级",
                    _text(finding.get("title")) or "已确认问题",
                    _unique_texts((finding.get("message") or value.get("reason"),)),
                    list(dict.fromkeys((*refs, *finding_refs))),
                    _unique_texts((finding_support_reason, support_reason)),
                    _text(finding.get("recommendation")) or "依据检查要求完成整改并补充验证证据。",
                    dimensions=(
                        _unique_texts(finding.get("affectedDimensions", ()))
                        if isinstance(finding.get("affectedDimensions"), Sequence)
                        and not isinstance(finding.get("affectedDimensions"), (str, bytes))
                        else []
                    ),
                )
                record_primary(issue)
            elif kind == "relationship" and value.get("verdict") == "rejected":
                relationship = _text(value.get("relationship") or item.get("subject"))
                relationships.append(_Issue(
                    decision.work_item_id, "未分级",
                    f"关系“{relationship}”未满足要求" if relationship else "关系要求未满足",
                    _unique_texts((value.get("reason"),)), list(refs),
                    _unique_texts((support_reason,)),
                    "补充或修正该关系定义，并提供可验证依据。",
                ))
            elif kind == "dimension" and value.get("verdict") == "violated":
                dimension = _text(value.get("dimension") or item.get("subject")) or "未命名检查项"
                violated_dimensions.append((
                    dimension,
                    _text(value.get("reason")), refs, support_reason,
                ))
                for finding in _items(value.get("findings")):
                    finding_refs, finding_support_reason = _support(finding)
                    affected = finding.get("affectedDimensions", ())
                    dimensions = (
                        _unique_texts(affected)
                        if isinstance(affected, Sequence)
                        and not isinstance(affected, (str, bytes))
                        else []
                    ) or [dimension]
                    record_primary(_Issue(
                        decision.work_item_id,
                        _text(finding.get("severity")) or "未分级",
                        _text(finding.get("title")) or "已确认问题",
                        _unique_texts((finding.get("message") or value.get("reason"),)),
                        list(dict.fromkeys((*refs, *finding_refs))),
                        _unique_texts((finding_support_reason, support_reason)),
                        _text(finding.get("recommendation"))
                        or "依据检查要求完成整改并补充验证证据。",
                        dimensions=dimensions,
                    ))

        # Candidate Findings and rejected relationships are problem-level
        # records. Dimension verdicts are coverage facts and are not repeated as
        # separate issues when a problem-level record already exists.
        result.extend(primary)
        result.extend(relationships)
        if primary or relationships:
            continue
        grouped: dict[tuple[str, tuple[str, ...]], tuple[list[str], str, str]] = {}
        for dimension, reason, refs, support_reason in violated_dimensions:
            key = (reason, refs)
            if key not in grouped:
                grouped[key] = ([dimension], reason, support_reason)
            elif dimension not in grouped[key][0]:
                grouped[key][0].append(dimension)
        for (reason, refs), (dimensions, _reason, support_reason) in grouped.items():
            label = "、".join(dimensions)
            result.append(_Issue(
                decision.work_item_id, "未分级", f"检查项“{label}”未满足要求",
                [reason], list(refs), _unique_texts((support_reason,)),
                "依据检查要求完成整改并补充验证证据。",
            ))

        if not common and decision.result == "issue_found":
            for finding in decision.findings:
                if finding.status not in {"violated", "conflicted"}:
                    continue
                result.append(_Issue(
                    decision.work_item_id, "未分级",
                    f"检查项“{finding.dimension}”未满足要求",
                    [finding.reason], [], [],
                    "依据检查要求完成整改并补充验证证据。",
                ))
    return sorted(
        result,
        key=lambda item: (_SEVERITY_ORDER.get(item.severity, 5), item.work_item_id, item.title),
    )


def _pending_rows(ledger: PlatformLedger) -> list[_Pending]:
    result: list[_Pending] = []
    for decision in ledger.decisions:
        common = _common_review_items(decision)
        for item in common:
            kind = _text(item.get("kind"))
            value = _mapping(item.get("value"))
            subject = _text(item.get("subject")) or "审计事项"
            refs, _support_reason = _support(value)
            unresolved = _mapping(value.get("unknown"))
            escalation = _mapping(value.get("escalation"))
            is_pending = (
                kind in {"unknown", "escalation"}
                or kind == "candidate" and value.get("disposition") == "needs_review"
                or kind == "relationship" and value.get("verdict") == "unknown"
                or kind == "dimension" and value.get("verdict") in {
                    "unresolved", "blocked", "conflicted",
                }
            )
            if not is_pending:
                continue
            if kind == "unknown":
                unresolved = value
            elif kind == "escalation":
                escalation = value
            explicit_reasons = _unique_texts((
                unresolved.get("reason"), escalation.get("reason"),
            ))
            reasons = explicit_reasons or _unique_texts((value.get("reason"),))
            missing = unresolved.get("missingInformation", escalation.get("missingInformation", ()))
            requirements = (
                _unique_texts(missing)
                if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes))
                else []
            )
            _append_unique(requirements, (escalation.get("requiredAction"),))
            if not requirements:
                requirements.append("补充能够消除当前不确定性的有效证据。")
            pending = _Pending(
                decision.work_item_id, subject, reasons or ["现有证据不足以形成确定结论。"],
                requirements, list(refs),
            )
            duplicate = next((existing for existing in result if (
                existing.work_item_id, existing.subject, existing.reasons, existing.requirements
            ) == (
                pending.work_item_id, pending.subject, pending.reasons, pending.requirements
            )), None)
            if duplicate is None:
                result.append(pending)
            else:
                _append_unique(duplicate.refs, pending.refs)
        if not common and decision.result == "needs_review":
            unresolved = [
                item for item in decision.findings
                if item.status in {"unresolved", "blocked", "conflicted"}
            ]
            for finding in unresolved:
                result.append(_Pending(
                    decision.work_item_id, finding.dimension, [finding.reason],
                    ["补充能够消除当前不确定性的有效证据。"], [],
                ))
    return result


def _anchors_for(
    work_item_id: str, refs: Sequence[str], catalogs: Mapping[str, Mapping[str, _EvidenceAnchor]],
) -> list[_EvidenceAnchor]:
    catalog = catalogs.get(work_item_id, {})
    anchors = [catalog[ref] for ref in refs if ref in catalog]
    if not anchors and catalog:
        anchors = [next(iter(catalog.values()))]
    result: list[_EvidenceAnchor] = []
    seen: set[tuple[object, ...]] = set()
    for anchor in anchors:
        key = (anchor.source, anchor.start_line, anchor.end_line, anchor.headings, anchor.excerpt)
        if key not in seen:
            seen.add(key)
            result.append(anchor)
    return result


def _location(
    work_item_id: str, refs: Sequence[str], catalogs: Mapping[str, Mapping[str, _EvidenceAnchor]],
    identities: Mapping[str, str], anchors: Sequence[_EvidenceAnchor] = (),
) -> str:
    resolved = list(anchors) or _anchors_for(work_item_id, refs, catalogs)
    locations = _unique_texts(tuple(item.location for item in resolved))
    return "<br>".join(locations[:3]) or identities.get(work_item_id, "审计对象")


def _basis(
    issue: _Issue, catalogs: Mapping[str, Mapping[str, _EvidenceAnchor]],
) -> str:
    excerpts = _unique_texts(tuple(
        item.excerpt for item in (
            issue.anchors or _anchors_for(issue.work_item_id, issue.refs, catalogs)
        )
        if item.excerpt
    ))
    if excerpts:
        bounded = [text[:180] + ("…" if len(text) > 180 else "") for text in excerpts[:2]]
        return "<br>".join(f"“{text}”" for text in bounded)
    if issue.evidence_reasons:
        return "；".join(issue.evidence_reasons)
    return "依据已冻结证据形成该判定。"


def _issue_description(issue: _Issue) -> str:
    """Join title and explanation once, even when a domain message repeats the title."""
    title = _text(issue.title)
    comparable = title.rstrip("。；;:：")
    details: list[str] = []
    for raw in issue.explanations:
        explanation = _text(raw)
        if not explanation:
            continue
        if explanation.startswith(title):
            explanation = explanation[len(title):].lstrip("。；;:： ")
        elif explanation.startswith(comparable):
            explanation = explanation[len(comparable):].lstrip("。；;:： ")
        if explanation and explanation not in details:
            details.append(explanation)
    rendered = _statements(details)
    return f"{comparable}：{rendered}" if rendered else title


def _conclusion(
    ledger: PlatformLedger, issues: Sequence[_Issue], pending: Sequence[_Pending], complete: bool,
) -> str:
    if ledger.status == "failed":
        return "无有效结论"
    if not complete:
        return "部分完成"
    if issues:
        return "不通过"
    if pending:
        return "待确认"
    results = {item.result for item in ledger.decisions}
    if results and results == {"not_applicable"}:
        return "不适用"
    return "通过"


def render_audit_report(
    ledger: PlatformLedger, *, snapshot_root: str | Path | None = None,
) -> bytes:
    """Render the platform's fixed five-chapter formal report."""
    if ledger.status not in {"completed", "partial", "failed"}:
        raise PlatformContractError(
            "AUDIT_REPORT_NOT_TERMINAL", "An audit report requires a terminal ledger",
        )
    packet_by_id = {
        packet.work_item.work_item_id: packet for packet in ledger.investigations
    }
    identities = {
        item.work_item_id: (
            _packet_source(packet_by_id[item.work_item_id])
            if item.work_item_id in packet_by_id else _source_label(item.identity)
        ) for item in ledger.work_items
    }
    catalogs = {
        work_item_id: _evidence_catalog(packet, snapshot_root)
        for work_item_id, packet in packet_by_id.items()
    }
    issues = _issue_rows(ledger, packet_by_id) if ledger.status != "failed" else []
    pending = _pending_rows(ledger) if ledger.status != "failed" else []
    decided_ids = {item.work_item_id for item in ledger.decisions}
    failed_ids = {
        item.work_item_id for item in ledger.failures if item.work_item_id in identities
    }
    unprocessed = set(identities) - decided_ids - failed_ids
    complete = ledger.status == "completed" and not unprocessed and not ledger.failures
    execution = {
        "completed": "已完成", "partial": "部分完成", "failed": "执行失败",
    }[ledger.status]
    coverage = "完整" if complete else "部分完成" if ledger.status != "failed" else "无有效覆盖"

    lines = [
        "# 审计报告", "", "## 一、审计结论", "",
        "| 执行状态 | 审计结论 | 确认问题数 | 待确认事项数 | 覆盖状态 |",
        "|---|---|---:|---:|---|",
        f"| {execution} | {_conclusion(ledger, issues, pending, complete)} | {len(issues)} | {len(pending)} | {coverage} |",
        "", "## 二、问题明细", "",
        "| 严重程度 | 问题位置 | 问题说明 | 判定依据 | 整改要求 |",
        "|---|---|---|---|---|",
    ]
    if issues:
        for issue in issues:
            location = _location(
                issue.work_item_id,
                issue.refs,
                catalogs,
                identities,
                issue.anchors,
            )
            lines.append(
                f"| {_cell(issue.severity)} | {_cell(location)} "
                f"| {_cell(_issue_description(issue))} "
                f"| {_cell(_basis(issue, catalogs))} | {_cell(issue.recommendation)} |"
            )
    else:
        lines.append("| — | — | 未发现确认问题 | — | — |")

    if pending:
        lines.extend([
            "", "## 三、待确认事项", "",
            "| 涉及范围 | 未决事项 | 未决原因 | 补充要求 |",
            "|---|---|---|---|",
        ])
        for item in pending:
            lines.append(
                f"| {_cell(_location(item.work_item_id, item.refs, catalogs, identities))} "
                f"| {_cell(item.subject)} | {_cell(_statements(item.reasons))} "
                f"| {_cell(_statements(item.requirements))} |"
            )

    checked = len(decided_ids | failed_ids)
    unchecked = len(unprocessed)
    checked_dimensions = sum(len(item.findings) for item in ledger.decisions)
    failed_dimensions = sum(
        finding.status in {"violated", "conflicted"}
        for item in ledger.decisions for finding in item.findings
    )
    if ledger.status == "failed":
        effect = "本次执行不形成有效审计结论。"
    elif unprocessed:
        effect = "未检查范围不形成通过结论。"
    elif ledger.failures:
        effect = "存在执行失败事项，相关范围不形成通过结论。"
    elif failed_dimensions:
        effect = (
            f"{failed_dimensions} 个检查项未通过，"
            f"归并为 {len(issues)} 个独立问题。"
        )
    else:
        effect = "无覆盖缺口。"
    lines.extend([
        "", "## 四、覆盖情况", "",
        "| 覆盖状态 | 已检查范围 | 未检查范围 | 结果影响 |",
        "|---|---|---|---|",
        f"| {coverage} | 已检查 {checked}/{len(identities)} 个审计对象"
        f"{f'、{checked_dimensions} 个检查项' if checked_dimensions else ''} | "
        f"{_cell(f'{unchecked} 个审计对象未完成' if unchecked else '无')} | {_cell(effect)} |",
        "", "## 五、审计信息", "",
        "| 审计对象 | 检查项 | 插件版本 | 报告位置 |",
        "|---|---|---|---|",
        f"| {_cell('、'.join(_unique_texts(tuple(identities.values()))) or '无')} "
        f"| {_cell(ledger.run.check_id)} | {_cell(f'{ledger.run.plugin_id} {ledger.run.plugin_version}')} "
        f"| {_cell(f'{ledger.run.run_id}.audit-report.md')} |",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


__all__ = ["render_audit_report"]
