"""Canonical Spec-quality candidate scanner.

The bundled ``authority.md`` is the normative entry point.  This module is an
implementation projection only: all deterministic observations are candidates
and remain unresolved until an Agent/reviewer submits an explicit review.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from assayer_platform.contract import (
    CheckContract, DimensionObservation, EvidenceRecord, InvestigationPacket,
    PlatformContext, PlatformContractError, PluginManifest, ReviewCheckpoint,
    WorkItem,
)
from assayer_platform.registry import load_plugin_manifest
from assayer_platform.actionable_result import build_actionable_result
from assayer_platform.navigation import MarkdownNavigationAdapter
from assayer_platform.review_protocol import validate_review_submission
from assayer_platform.evidence_graph import (
    build_candidate_evidence_graph, build_candidate_envelope,
    canonicalize_candidate, render_candidate_evidence_graph,
    validate_candidate_evidence_graph_projection,
)
from assayer_platform.identity import (
    digest_bytes as _digest, document_state_digest as _document_state_digest,
)
from assayer_platform.source_chunking import (
    build_source_chunks as _source_chunks,
    source_ref_for_line as _source_ref_for_line,
)
from assayer_platform.source_fact_index import build_source_fact_index


_ROOT = Path(__file__).parent
_MANIFEST = _ROOT / "manifest.json"
_POLICY = json.loads((_ROOT / "policy.json").read_text(encoding="utf-8"))
_CHECKLIST = json.loads((_ROOT / "checklist.json").read_text(encoding="utf-8"))
_POLICY_ID = str(_POLICY["policy_id"])
_POLICY_VERSION = str(_POLICY["policy_version"])
_AUTHORITY_VERSION = str(_POLICY["authority"]["version"])
_AVAILABLE_PROFILES = tuple(_POLICY["profiles"])
_STRICT_STRUCTURE_PROFILE = "strict-12-chapter"
_MARKDOWN_NAVIGATION = MarkdownNavigationAdapter()
_REQUIRED_CHAPTERS = (
    "模块定义", "状态模型", "功能需求清单", "关键实体", "数据字段定义",
    "非功能性需求选择", "成功标准", "参考资料与合规依据", "关键决策记录",
    "依赖与假设", "阶段差异说明", "修订记录",
)
_ALIASES = {
    "module definition": "模块定义", "模块定义": "模块定义",
    "state model": "状态模型", "状态模型": "状态模型",
    "functional requirements": "功能需求清单", "功能需求清单": "功能需求清单",
    "key entities": "关键实体", "关键实体": "关键实体",
    "data fields": "数据字段定义", "数据字段定义": "数据字段定义",
    "non-functional requirements": "非功能性需求选择", "非功能性需求选择": "非功能性需求选择",
    "success criteria": "成功标准", "成功标准": "成功标准",
    "references and compliance": "参考资料与合规依据", "参考资料与合规依据": "参考资料与合规依据",
    "key decisions": "关键决策记录", "关键决策记录": "关键决策记录",
    "dependencies and assumptions": "依赖与假设", "依赖与假设": "依赖与假设",
    "stage differences": "阶段差异说明", "阶段差异说明": "阶段差异说明",
    "revision history": "修订记录", "修订记录": "修订记录",
}
_VAGUE = re.compile(
    r"尽量|大约|可能|较好|适当|合理|及时|快速|简单|必要时|原则上|基本实现|功能正常|展示正确|"
    r"should be good|as soon as possible|approximately|might|reasonable|try to|basic implementation",
    re.I,
)
_ABSENCE_PATTERN = re.compile(
    r"\b(?:no|not detected|missing|without|absent|lacks?)\b|不存在|缺失|未检测到|没有|缺少|无",
    re.I,
)
_PLACEHOLDER = re.compile(r"(?i)\b(?:TODO|TBD|YYYY-MM-DD)\b|待填写|待补充|示例内容")
_FR = re.compile(r"\bFR[-_][A-Z0-9][A-Z0-9-]*\b", re.I)
_AC = re.compile(r"\bAC[-_][A-Z0-9][A-Z0-9-]*\b", re.I)
_CASE = re.compile(r"\bCASE[-_][A-Z0-9][A-Z0-9-]*\b", re.I)
_DIMENSION_SIGNALS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "CHK-02": (("goal-and-context", re.compile(r"goal|objective|overview|actor|trigger|input|output|workflow|目标|参与者|触发|输入|输出", re.I)),),
    "CHK-03": (("terminology", re.compile(r"terms?|glossary|abbreviation|definition|alias|术语|缩写|定义|别名", re.I)),),
    "CHK-04": (("scope-and-boundary", re.compile(r"scope|boundary|included|excluded|out of scope|responsibilit|范围|边界|包含|不包含|责任", re.I)),),
    "CHK-05": (("consistency-reference", re.compile(r"consisten|contradict|conflict|duplicate|drift|related document|一致|矛盾|冲突|重复|漂移|关联文档", re.I)),),
    "CHK-06": (("baseline-and-revision", re.compile(r"version|revision|change log|baseline|owner|status|版本|修订|变更记录|基线|负责人|状态", re.I)),),
    "CHK-07": (("decidable-language", re.compile(r"within|at least|at most|exactly|condition|threshold|不得|必须|至少|至多|条件|阈值", re.I)),),
    "CHK-08": (("risk-scenario", re.compile(r"boundary|invalid|error|failure|timeout|retry|permission|concurren|idempoten|recover|partial|边界|非法|异常|失败|超时|重试|权限|并发|幂等|恢复|部分成功", re.I)),),
    "CHK-09": (("data-contract", re.compile(r"field|data|entity|type|null|default|precision|round|unit|formula|字段|数据|实体|类型|默认|精度|舍入|单位|公式", re.I)),),
    "CHK-10": (("state-and-lifecycle", re.compile(r"state|status|transition|lifecycle|invariant|terminal|状态|流转|生命周期|不变量|终态", re.I)),),
    "CHK-11": (("compatibility", re.compile(r"compatib|backward|existing system|legacy|migration|switch|rollback|兼容|现有系统|存量|迁移|切换|回滚", re.I)),),
    "CHK-12": (("acceptance", re.compile(r"acceptance|expected result|given.+when.+then|\bAC[-_]|\bCASE[-_]|验收|预期结果", re.I | re.S)),),
    "CHK-13": (("non-functional", re.compile(r"non-functional|\bNFR[-_]|performance|capacity|availability|reliability|resilien|recovery|observability|非功能|性能|容量|可用性|可靠性|韧性|恢复|可观测", re.I)),),
    "CHK-14": (("authorization", re.compile(r"permission|authorization|role|deny|forbidden|tenant|data scope|权限|授权|角色|拒绝|租户|数据范围|数据隔离", re.I)),),
    "CHK-15": (("traceability", re.compile(r"trace|matrix|source|objective|\bFR[-_]|\bAC[-_]|\bCASE[-_]|\bSC[-_]|追溯|矩阵|来源|目标", re.I)),),
    "CHK-16": (("decision", re.compile(r"decision|rationale|trade-off|approved|rejected|superseded|决策|理由|取舍|批准|拒绝|取代", re.I)),),
    "CHK-17": (("dependency-or-assumption", re.compile(r"dependenc|assumption|prerequisite|fallback|degrad|upstream|downstream|依赖|假设|前置|降级|上游|下游", re.I)),),
    "CHK-18": (("open-governance", re.compile(r"open question|blocker|exception|exemption|waiver|unresolved|未决|阻塞|例外|豁免|待解决", re.I)),),
}


def _actionable_result_delivery(
    decisions: Sequence[Mapping[str, Any]],
    checklist_review: Sequence[Mapping[str, Any]],
    packet: InvestigationPacket,
) -> dict[str, Any]:
    """Project confirmed domain root causes into the shared remediation shape.

    The current Spec review model assigns one primary dimension to a confirmed
    root cause.  Until the Agent contract supplies an explicit multi-dimension
    mapping, uncovered REWORK dimensions keep this envelope honestly partial.
    """
    checklist_status = {
        str(item.get("check_id")): str(item.get("status"))
        for item in checklist_review
    }
    evidence_by_dimension = {
        item.name: list(item.evidence_refs) for item in packet.dimensions
    }
    evidence_id = packet.evidence[0].evidence_id if packet.evidence else None
    evidence_payload = packet.evidence[0].payload if packet.evidence else {}
    source_chunks = evidence_payload.get("sourceChunks", ()) if isinstance(evidence_payload, Mapping) else ()
    return build_actionable_result(
        decisions, checklist_status, evidence_by_dimension,
        evidence_id=evidence_id,
        source_chunks=source_chunks,
        work_item_identity=str(packet.work_item.identity),
        document_path=str(packet.metadata.get("path") or packet.work_item.identity),
        confirmed_status="CONFIRMED",
        actionable_statuses=frozenset({"REWORK", "ESCALATE"}),
        absence_pattern=_ABSENCE_PATTERN,
    )


def _scope_error(message: str) -> None:
    raise PlatformContractError("INVALID_SPEC_SCOPE", message)


def _load_cross_document_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve and validate one explicitly bounded anchor comparison scope."""
    raw_anchor = scope.get("anchor")
    raw_related = scope.get("relatedDocuments")
    raw_relationships = scope.get("relationships")
    if not isinstance(raw_anchor, Mapping):
        _scope_error("Cross-document scope requires one anchor document")
    if not isinstance(raw_related, (tuple, list)) or not raw_related:
        _scope_error("Cross-document scope requires at least one related document")
    if not isinstance(raw_relationships, (tuple, list)) or not raw_relationships:
        _scope_error("Cross-document scope requires at least one relationship")

    document_configs = [(raw_anchor, "anchor"), *((item, "related") for item in raw_related)]
    documents: list[dict[str, Any]] = []
    document_ids: set[str] = set()
    document_paths: set[str] = set()
    for index, (raw_document, role) in enumerate(document_configs):
        if not isinstance(raw_document, Mapping):
            _scope_error(f"Cross-document {role} entry {index} must be an object")
        document_id = raw_document.get("documentId")
        raw_path = raw_document.get("path")
        if not isinstance(document_id, str) or not document_id.strip():
            _scope_error(f"Cross-document {role} entry {index} requires documentId")
        if not isinstance(raw_path, str) or not raw_path.strip():
            _scope_error(f"Cross-document {role} entry {index} requires path")
        if document_id in document_ids:
            _scope_error(f"Duplicate cross-document documentId: {document_id}")
        path = Path(raw_path).expanduser().resolve()
        normalized_path = str(path)
        if normalized_path in document_paths:
            _scope_error(f"A cross-document file cannot have multiple identities: {normalized_path}")
        raw = path.read_bytes()
        document_ids.add(document_id)
        document_paths.add(normalized_path)
        documents.append({
            "document_id": document_id,
            "role": role,
            "path": normalized_path,
            "source_digest": _digest(raw),
            "profile": raw_document.get("profile") if role == "anchor" else None,
            "selection_reason": raw_document.get("selectionReason"),
        })

    anchor_id = str(documents[0]["document_id"])
    related_ids = {str(document["document_id"]) for document in documents[1:]}
    relationships: list[dict[str, Any]] = []
    relationship_ids: set[str] = set()
    related_with_relationship: set[str] = set()
    for index, raw_relationship in enumerate(raw_relationships):
        if not isinstance(raw_relationship, Mapping):
            _scope_error(f"Cross-document relationship {index} must be an object")
        relationship_id = raw_relationship.get("relationshipId")
        source_id = raw_relationship.get("fromDocumentId")
        target_id = raw_relationship.get("toDocumentId")
        kind = raw_relationship.get("kind")
        evidence = raw_relationship.get("evidence")
        owner = raw_relationship.get("resolutionOwner")
        if not isinstance(relationship_id, str) or not relationship_id.strip():
            _scope_error(f"Cross-document relationship {index} requires relationshipId")
        if relationship_id in relationship_ids:
            _scope_error(f"Duplicate cross-document relationshipId: {relationship_id}")
        if source_id != anchor_id:
            _scope_error(f"Relationship {relationship_id} must originate from the anchor document")
        if target_id not in related_ids:
            _scope_error(f"Relationship {relationship_id} references an unknown related document")
        if kind not in {"authoritative_for", "implements", "depends_on", "supersedes", "compares_with"}:
            _scope_error(f"Relationship {relationship_id} has an unsupported kind")
        if not isinstance(evidence, str) or not evidence.strip():
            _scope_error(f"Relationship {relationship_id} requires relationship evidence")
        if not isinstance(owner, str) or not owner.strip():
            _scope_error(f"Relationship {relationship_id} requires a resolution owner")
        relationship_ids.add(relationship_id)
        related_with_relationship.add(str(target_id))
        relationships.append({
            "relationship_id": relationship_id,
            "from_document_id": source_id,
            "to_document_id": target_id,
            "kind": kind,
            "evidence": evidence,
            "resolution_owner": owner,
            "selection_reason": raw_relationship.get("selectionReason"),
        })
    uncovered = sorted(related_ids - related_with_relationship)
    if uncovered:
        _scope_error(
            "Every related document requires an anchor relationship: " + ", ".join(uncovered),
        )
    return {
        "kind": "cross-spec",
        "anchor_document_id": anchor_id,
        "documents": documents,
        "relationships": relationships,
    }


def _key(title: str) -> str:
    normalized = re.sub(r"^[#\s\d.:-]+", "", title).strip().casefold()
    return _ALIASES.get(normalized, _ALIASES.get(title.strip(), title.strip()))


def _chapters(text: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^##\s+(?:\d+[.、:]\s*)?(.+?)\s*$", line)
        if match:
            result.setdefault(_key(match.group(1)), []).append(line_no)
    return result


def _chapter_bodies(text: str) -> dict[str, tuple[int, str]]:
    matches = list(re.finditer(r"(?m)^##\s+(?:\d+[.、:]\s*)?(.+?)\s*$", text))
    result: dict[str, tuple[int, str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.setdefault(_key(match.group(1)), (text.count("\n", 0, match.start()) + 1, text[match.end():end]))
    return result


def _line_excerpt(text: str, line: int | None, radius: int = 1) -> str | None:
    if line is None:
        return None
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        return None
    start, end = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(f"{idx}: {lines[idx - 1].strip()}" for idx in range(start, end + 1) if lines[idx - 1].strip()) or None


def _tables(text: str) -> list[tuple[int, list[str], list[tuple[int, list[str]]]]]:
    lines = text.splitlines()
    tables = []
    index = 0
    while index + 1 < len(lines):
        if not lines[index].lstrip().startswith("|") or not lines[index + 1].lstrip().startswith("|"):
            index += 1
            continue
        def cells(value: str) -> list[str]:
            return [cell.strip() for cell in value.strip().strip("|").split("|")]
        headers, separator = cells(lines[index]), cells(lines[index + 1])
        if not separator or not all(re.fullmatch(r":?-{3,}:?", item or "-") for item in separator):
            index += 1
            continue
        rows = []
        cursor = index + 2
        while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
            row = cells(lines[cursor])
            rows.append((cursor + 1, row))
            cursor += 1
        tables.append((index + 1, headers, rows))
        index = cursor
    return tables


def _meaningful(value: str) -> bool:
    value = re.sub(r"[`*_>#]", "", value or "").strip()
    return bool(value) and value not in {"-", "N/A", "不适用", "无", "待定", "待澄清"} and not _PLACEHOLDER.search(value)


def _cross_document_evidence(
    document_scope: Mapping[str, Any], chunks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    chunks_by_document: dict[str, list[Mapping[str, Any]]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(str(chunk.get("document_id")), []).append(chunk)
    evidence: list[dict[str, Any]] = []
    for relationship in document_scope.get("relationships", ()):
        relationship_id = str(relationship["relationship_id"])
        for role_key in ("from_document_id", "to_document_id"):
            document_id = str(relationship[role_key])
            for chunk in chunks_by_document.get(document_id, ()):
                source_chunk_id = str(chunk["source_chunk_id"])
                evidence.append({
                    "evidence_item_id": f"cross-source:{relationship_id}:{source_chunk_id}",
                    "relationship_id": relationship_id,
                    "relationship_kind": relationship["kind"],
                    "relationship_evidence": relationship["evidence"],
                    "resolution_owner": relationship["resolution_owner"],
                    "selection_reason": relationship.get("selection_reason"),
                    "document_id": document_id,
                    "document_role": chunk.get("document_role"),
                    "document_path": chunk["document_path"],
                    "source_digest": chunk["source_digest"],
                    "source_chunk_id": source_chunk_id,
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "excerpt": chunk["excerpt"],
                })
    return evidence


def _dimension_evidence(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build heuristic CHK-to-source mappings without claiming semantic proof."""
    mappings: list[dict[str, Any]] = []
    for item_def in _CHECKLIST:
        check_id = str(item_def["id"])
        signals = _DIMENSION_SIGNALS.get(check_id, ())
        matched = 0
        for chunk in chunks:
            searchable = "\n".join((
                " / ".join(str(value) for value in chunk.get("heading_path", ())),
                str(chunk.get("excerpt", "")),
            ))
            bases = [label for label, pattern in signals if pattern.search(searchable)]
            if not bases:
                continue
            matched += 1
            source_chunk_id = str(chunk["source_chunk_id"])
            mappings.append({
                "mapping_id": f"mapping:{check_id}:{source_chunk_id}",
                "check_id": check_id,
                "mapping_status": "candidate",
                "match_basis": bases,
                "source_chunk_id": source_chunk_id,
                "document_path": chunk["document_path"],
                "source_digest": chunk["source_digest"],
                "heading_path": chunk["heading_path"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "excerpt": chunk["excerpt"],
            })
        if not matched:
            mappings.append({
                "mapping_id": f"mapping:{check_id}:unmapped",
                "check_id": check_id,
                "mapping_status": "unmapped",
                "match_basis": [],
                "source_chunk_id": None,
                "document_path": None,
                "source_digest": None,
                "heading_path": [],
                "start_line": None,
                "end_line": None,
                "excerpt": None,
            })
    return mappings


def _source_fact_index(
    text: str, chunks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Extract objective Markdown facts for semantic review and contradiction checks.

    This index is deliberately descriptive.  It records identifiers and
    explicit statements but never decides whether the Spec satisfies a rule.
    """
    identifier_patterns = {
        "functional_requirements": re.compile(r"\bFR[-_][A-Z0-9-]+\b", re.I),
        "acceptance_criteria": re.compile(r"\bAC[-_][A-Z0-9-]+\b", re.I),
        "test_cases": re.compile(r"\bCASE[-_][A-Z0-9-]+\b", re.I),
        "assumptions": re.compile(r"\bAS[-_][A-Z0-9][A-Z0-9-]*\b", re.I),
        "decisions": re.compile(
            r"\b(?:(?:ADR|DECISION)[-_][A-Z0-9][A-Z0-9-]*|D[-_][A-Z0-9][A-Z0-9-]*)\b",
            re.I,
        ),
    }
    explicit_patterns = {
        "no_migration": re.compile(r"no (?:historical )?data migration|不存在历史数据迁移|系统从零开始", re.I),
        "not_applicable": re.compile(r"not applicable|不适用|不考虑|不做|out of scope|excluded", re.I),
        "delegation": re.compile(r"child spec|sub[- ]module.*spec|子模块.*spec|由各子模块|delegat", re.I),
        "authority": re.compile(r"conflict.*(?:favor|precedence)|冲突时.*以|authoritative|权威", re.I),
    }
    heading_units = [
        unit for unit in _MARKDOWN_NAVIGATION.parse(
            {"raw": text.encode("utf-8"), "path": "<source-facts>"}
        ).get("units", ())
        if unit.get("kind") == "heading" and unit.get("startLine")
    ]
    return build_source_fact_index(
        text, chunks,
        identifier_patterns=identifier_patterns,
        explicit_patterns=explicit_patterns,
        heading_units=heading_units,
    )


def _document_context(
    text: str, chunks: Sequence[Mapping[str, Any]], profile: str,
    cross_scope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a source-backed classification proposal for Agent review."""
    lines = text.splitlines()
    preamble_lines: list[tuple[int, str]] = []
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if line_no > 1 and re.match(r"^#{1,2}\s+", stripped):
            break
        if stripped and not stripped.startswith("#"):
            preamble_lines.append((line_no, stripped.lstrip("> ")))
    scope_line = next(
        (item for item in preamble_lines if re.search(
            r"document positioning|document purpose|scope|responsibilit|文档定位|文档目的|范围|职责",
            item[1], re.I,
        )),
        preamble_lines[0] if preamble_lines else (1, "The document scope is not stated in the preamble."),
    )
    lower = text.casefold()
    if re.search(r"global|shared contract|shared model|全局规约|共享.*(?:概念|规则|模型)|子模块.*spec", lower, re.I):
        document_type, confidence = "shared_contract", "high"
    elif re.search(r"data model|schema|数据模型|字段定义", lower, re.I) and not re.search(r"functional requirement|功能需求", lower, re.I):
        document_type, confidence = "data_model", "medium"
    elif re.search(r"interface contract|api contract|接口契约", lower, re.I):
        document_type, confidence = "interface_contract", "medium"
    elif re.search(r"rfc|prd|product requirement", lower, re.I):
        document_type, confidence = "rfc_prd", "medium"
    else:
        document_type, confidence = "feature_spec", "low"
    classification_ref = _source_ref_for_line(chunks, scope_line[0])
    delegated = [
        line for line in lines
        if re.search(r"child spec|sub[- ]module.*spec|子模块.*spec|由各子模块|delegat", line, re.I)
    ]
    excluded = [
        line for line in lines
        if re.search(r"out of scope|not included|excluded|范围外|不包含|不做|不考虑", line, re.I)
    ]
    related = []
    if isinstance(cross_scope, Mapping):
        related = [
            str(document.get("document_id"))
            for document in cross_scope.get("documents", ())
            if isinstance(document, Mapping) and document.get("document_id")
        ]
    return {
        "schemaVersion": "1.0.0",
        "documentType": document_type,
        "scopeStatement": scope_line[1],
        "responsibilityBoundaries": {
            "owned": [],
            "delegated": delegated[:8],
            "excluded": excluded[:8],
        },
        "relatedDocuments": related,
        "selectedProfile": profile,
        "classificationEvidence": [classification_ref] if classification_ref else [],
        "classificationConfidence": confidence,
        "unresolvedClassificationQuestions": [] if confidence == "high" else [
            "Confirm the document type and responsibility boundary before applying feature-level checks."
        ],
        "isProposal": True,
    }


def _review_document_context(raw_context: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the packet's camelCase context into the review contract shape."""
    boundaries = raw_context.get("responsibilityBoundaries", {})
    if not isinstance(boundaries, Mapping):
        boundaries = {}
    references = []
    for raw_ref in raw_context.get("classificationEvidence", ()):
        if not isinstance(raw_ref, Mapping):
            continue
        references.append({
            key: raw_ref[key]
            for key in ("source_chunk_id", "document_path", "source_digest", "start_line", "end_line")
            if key in raw_ref
        })
    return {
        "document_type": raw_context.get("documentType"),
        "scope_statement": raw_context.get("scopeStatement"),
        "responsibility_boundaries": {
            "owned": list(boundaries.get("owned", ())),
            "delegated": list(boundaries.get("delegated", ())),
            "excluded": list(boundaries.get("excluded", ())),
        },
        "related_documents": list(raw_context.get("relatedDocuments", ())),
        "selected_profile": raw_context.get("selectedProfile"),
        "classification_evidence": references,
        "classification_confidence": raw_context.get("classificationConfidence"),
        "unresolved_classification_questions": list(
            raw_context.get("unresolvedClassificationQuestions", ())
        ),
    }


def _candidate(cid: str, rule: str, severity: str, object_id: str | None, line: int | None,
              chapter: str | None, message: str, evidence: str | None,
              impact: str, recommendation: str) -> dict[str, Any]:
    # camelCase fields are retained only for the existing platform evidence
    # compatibility view; candidateFindings below is the canonical projection.
    return build_candidate_envelope(
        cid, rule, severity, object_id, line, chapter, message, evidence,
        impact, recommendation, policy_id=_POLICY_ID, policy_version=_POLICY_VERSION,
    )


_canonical_candidate = canonicalize_candidate


def _navigation_candidates(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn every parsed Markdown unit into a review pointer, never a finding."""
    result: list[dict[str, Any]] = []
    for unit in units:
        unit_id = str(unit.get("unitId") or "").strip()
        if not unit_id:
            continue
        excerpt = str(unit.get("excerpt") or "").strip()
        heading_path = unit.get("headingPath")
        result.append(_canonical_candidate({
            "candidateId": unit_id,
            "ruleId": "NAV-UNIT",
            "suggestedSeverity": "P3",
            "objectId": unit_id,
            "line": unit.get("startLine"),
            "chapter": heading_path[-1] if isinstance(heading_path, (tuple, list)) and heading_path else None,
            "message": "Review this Markdown unit for Spec quality; navigation is not a semantic decision.",
            "evidence": excerpt,
            "impact": "A semantic conclusion cannot be made until this source unit is reviewed in context.",
            "recommendation": "Assess the unit against the applicable Spec dimensions and cite the precise source.",
            "policyId": _POLICY_ID,
            "policyVersion": _POLICY_VERSION,
            "confidence": None,
        }))
    return result


class SpecQualityPlugin:
    """Discover and inspect Markdown Specs under the canonical authority."""

    manifest: PluginManifest = load_plugin_manifest(_MANIFEST)
    evidence_graph_enabled = True

    def validate_review_checkpoint(
        self, checkpoint: ReviewCheckpoint, collection_items: Sequence[Mapping[str, Any]],
        prior_checkpoints: Sequence[ReviewCheckpoint], packet: InvestigationPacket,
        check: CheckContract, context: PlatformContext,
    ) -> None:
        """Reject malformed or untraceable semantic findings before persistence."""
        del collection_items, check, context
        from .review import (
            validate_checklist_reviews, validate_cross_document_reviews,
            validate_review_decisions,
        )

        if checkpoint.collection_id == "checklist-dimensions":
            raw_checklist = checkpoint.payload.get("checklist_review")
            if not isinstance(raw_checklist, (tuple, list)) or not raw_checklist:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    "Each Spec checklist checkpoint requires checklist_review",
                )
            validate_checklist_reviews(
                raw_checklist, packet, expected_check_ids=checkpoint.item_ids,
            )
            if any(
                isinstance(item, Mapping) and "applicability" in item
                for item in raw_checklist
            ):
                from .review import _validate_strict_checklist_items
                _validate_strict_checklist_items(raw_checklist)
            return
        if checkpoint.collection_id in {"candidate-findings", "document-navigation"}:
            raw_decisions = checkpoint.payload.get("decisions")
            if not isinstance(raw_decisions, (tuple, list)) or not raw_decisions:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Each Spec finding checkpoint requires decisions",
                )
            prior_decisions: list[Mapping[str, Any]] = []
            for prior in prior_checkpoints:
                if prior.checkpoint_id == checkpoint.checkpoint_id:
                    continue
                values = prior.payload.get("decisions")
                if isinstance(values, (tuple, list)):
                    prior_decisions.extend(item for item in values if isinstance(item, Mapping))
            validate_review_decisions(
                raw_decisions, packet,
                expected_candidate_ids=checkpoint.item_ids,
                prior_decisions=prior_decisions,
            )
            # The generic protocol owns candidate coverage; Spec retains the
            # richer domain validation above.
            status_map = {
                "CONFIRMED": "confirmed", "SUPPRESSED": "suppressed",
                "MERGED": "merged", "UNVERIFIED": "needs_review",
            }
            validate_review_submission(
                {"decisions": [
                    {"candidate_ids": item.get("candidate_ids", ()),
                     "disposition": status_map.get(str(item.get("status")), "")}
                    for item in raw_decisions if isinstance(item, Mapping)
                ]},
                expected_item_ids=checkpoint.item_ids,
            )
            return
        if checkpoint.collection_id == "cross-document-relationships":
            raw_reviews = checkpoint.payload.get("cross_document_review")
            if not isinstance(raw_reviews, (tuple, list)) or not raw_reviews:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    "Each cross-document checkpoint requires cross_document_review",
                )
            prior_decisions: list[Mapping[str, Any]] = []
            for prior in prior_checkpoints:
                if prior.checkpoint_id == checkpoint.checkpoint_id:
                    continue
                prior_reviews = prior.payload.get("cross_document_review")
                if not isinstance(prior_reviews, (tuple, list)):
                    continue
                for prior_review in prior_reviews:
                    if isinstance(prior_review, Mapping):
                        decisions = prior_review.get("decisions")
                        if isinstance(decisions, (tuple, list)):
                            prior_decisions.extend(
                                item for item in decisions if isinstance(item, Mapping)
                            )
            validate_cross_document_reviews(
                raw_reviews, packet,
                expected_relationship_ids=checkpoint.item_ids,
                prior_decisions=prior_decisions,
            )
            return
        raise PlatformContractError(
            "SPEC_REVIEW_INVALID", "Spec review checkpoint uses an unsupported collection",
        )

    def discover(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]:
        del context
        if isinstance(scope, Mapping) and "anchor" in scope:
            cross_scope = _load_cross_document_scope(scope)
            anchor = cross_scope["documents"][0]
            anchor_path = Path(anchor["path"])
            state_digest = _document_state_digest(cross_scope["documents"])
            anchor_identity = _digest(str(anchor_path).encode())
            return (WorkItem(
                f"spec:{anchor_identity[:16]}", "spec_document", f"sha256:{anchor_identity}", state_digest,
                {
                    "path": str(anchor_path),
                    "profile": anchor.get("profile") or _POLICY["default_profile"],
                    "sourceDigest": anchor["source_digest"],
                    "scopeKind": "cross-spec",
                    "crossDocumentScope": cross_scope,
                },
            ),)
        entries = scope.get("files", []) if isinstance(scope, dict) else scope
        if isinstance(entries, (str, Path)):
            entries = [entries]
        items: list[WorkItem] = []
        for entry in entries or []:
            config = {"path": entry} if isinstance(entry, (str, Path)) else dict(entry)
            path = Path(config["path"]).expanduser().resolve()
            raw = path.read_bytes()
            path_identity = _digest(str(path).encode())
            items.append(WorkItem(
                f"spec:{path_identity[:16]}", "spec_document", f"sha256:{path_identity}", _digest(raw),
                {"path": str(path), "profile": config.get("profile", _POLICY["default_profile"]),
                 "sourceDigest": _digest(raw),
                 "reviewStrategy": config.get("reviewStrategy", "candidate")},
            ))
        return tuple(items)

    def inspect(self, work_items: Sequence[WorkItem], check: CheckContract,
                context: PlatformContext) -> Sequence[InvestigationPacket]:
        navigation_context = context
        packets: list[InvestigationPacket] = []
        for item in work_items:
            path = Path(item.metadata["path"])
            raw = path.read_bytes()
            source_digest = _digest(raw)
            cross_scope = item.metadata.get("crossDocumentScope")
            if isinstance(cross_scope, Mapping):
                current_documents: list[dict[str, Any]] = []
                for document in cross_scope.get("documents", ()):
                    if not isinstance(document, Mapping):
                        raise ValueError("Cross-document scope metadata is malformed")
                    document_path = Path(str(document.get("path")))
                    current_documents.append({
                        "document_id": str(document.get("document_id")),
                        "role": str(document.get("role")),
                        "path": str(document_path),
                        "source_digest": _digest(document_path.read_bytes()),
                    })
                if _document_state_digest(current_documents) != item.state_digest:
                    raise ValueError("A cross-document Spec changed after discovery")
            elif source_digest != item.state_digest:
                raise ValueError("Spec changed after discovery")
            text = raw.decode("utf-8-sig")
            profile = str(item.metadata.get("profile") or _POLICY["default_profile"])
            # The generic Markdown navigator supplies a complete, immutable
            # structure map for consumers. Domain rules below remain owned by
            # this plugin; navigation is only a reading aid and evidence map.
            navigation = _MARKDOWN_NAVIGATION.read_document(
                {"raw": raw, "path": str(path)}, navigation_context,
            )
            chapters, bodies = _chapters(text), _chapter_bodies(text)
            source_chunks = _source_chunks(
                text, path, source_digest,
                document_id=(cross_scope.get("anchor_document_id") if isinstance(cross_scope, Mapping) else None),
                document_role="anchor" if isinstance(cross_scope, Mapping) else None,
            )
            cross_document_evidence: list[dict[str, Any]] = []
            if isinstance(cross_scope, Mapping):
                for document in cross_scope.get("documents", ())[1:]:
                    document_path = Path(str(document["path"]))
                    related_raw = document_path.read_bytes()
                    source_chunks.extend(_source_chunks(
                        related_raw.decode("utf-8-sig"), document_path,
                        _digest(related_raw), document_id=str(document["document_id"]),
                        document_role="related",
                    ))
                cross_document_evidence = _cross_document_evidence(cross_scope, source_chunks)
            dimension_evidence = _dimension_evidence(source_chunks)
            document_context = _document_context(text, source_chunks, profile, cross_scope)
            source_facts = _source_fact_index(text, source_chunks)
            recognized_structure = "\n".join(
                f"{line_no}: {line.strip()}" for line_no, line in enumerate(text.splitlines(), 1)
                if re.match(r"^#{1,6}\s+", line)
            ) or "No Markdown headings were detected."
            candidates: list[dict[str, Any]] = []

            def add(cid: str, rule: str, sev: str, obj: str | None, line: int | None,
                    chapter: str | None, message: str, evidence: str | None,
                    impact: str, recommendation: str) -> None:
                existing = {str(candidate["candidateId"]) for candidate in candidates}
                stable_id, suffix = cid, 2
                while stable_id in existing:
                    stable_id = f"{cid}-{suffix}"
                    suffix += 1
                candidates.append(_candidate(stable_id, rule, sev, obj, line, chapter, message, evidence, impact, recommendation))

            if profile not in _AVAILABLE_PROFILES:
                add("profile-unknown", "PROFILE-001", "P1", None, 1, None,
                    f"Unknown profile: {profile}.", None, "The selected audit authority cannot be established.", f"Select one available profile: {', '.join(_AVAILABLE_PROFILES)}.")
                profile = "product-spec"
            if profile == _STRICT_STRUCTURE_PROFILE:
                for number, chapter in enumerate(_REQUIRED_CHAPTERS, 1):
                    locations = chapters.get(chapter, [])
                    if not locations:
                        add(f"chapter-missing-{number:02d}", "CHAPTER-001", "P1", None, None, chapter,
                            f"Missing required chapter {number}: {chapter}.", recognized_structure,
                            "The Spec cannot be reviewed against the default product contract.", f"Add chapter {number} ({chapter}) with meaningful content or an explicit not-applicable decision.")
                    elif len(locations) > 1:
                        add(f"chapter-duplicate-{number:02d}", "CHAPTER-005", "P1", chapter, locations[1], chapter,
                            f"Duplicate required chapter: {chapter}.", _line_excerpt(text, locations[1], 2),
                            "Multiple sources of truth can produce divergent implementation and test behavior.", "Merge the duplicate chapter and retain one authoritative section.")
                    elif not _meaningful(bodies.get(chapter, (0, ""))[1]):
                        add(f"chapter-empty-{number:02d}", "CHAPTER-003", "P1", chapter, locations[0], chapter,
                            f"Chapter {chapter} has no meaningful content.", _line_excerpt(text, locations[0], 2),
                            "Required behavior remains undefined for implementers and reviewers.", "Replace placeholders or an empty marker with an explicit decision.")
            for line_no, line in enumerate(text.splitlines(), 1):
                if _PLACEHOLDER.search(line):
                    add(f"placeholder-{line_no}", "PLACEHOLDER-001", "P2", None, line_no, None,
                        "The Spec contains an unfinished placeholder.", line.strip(),
                        "The requirement cannot be implemented or reviewed deterministically.", "Replace it with an explicit decision or a documented unresolved blocker.")
                if _VAGUE.search(line):
                    add(f"vague-{line_no}", "FR-006", "P2", None, line_no, None,
                        "The Spec uses vague or non-verifiable wording.", line.strip(),
                        "Different implementations or tests may treat the requirement as satisfied at different points.", "Define a measurable target, boundary, or observable result.")

            fr_ids = sorted({match.upper() for match in _FR.findall(text)})
            ac_ids = sorted({match.upper() for match in _AC.findall(text)})
            case_ids = sorted({match.upper() for match in _CASE.findall(text)})
            if not fr_ids:
                add("requirements-missing", "FR-001", "P1", None, bodies.get("功能需求清单", (None,))[0], "功能需求清单",
                    "No FR-NNN functional requirement was detected.", _line_excerpt(text, bodies.get("功能需求清单", (1,))[0]),
                    "There is no stable behavior unit to implement or trace.", "Add independently understandable FR-NNN behavior units.")
            if not ac_ids:
                add("acceptance-missing", "AC-001", "P1", fr_ids[0] if len(fr_ids) == 1 else None, bodies.get("功能需求清单", (None,))[0], "功能需求清单",
                    "No stable AC acceptance criterion was detected.", _line_excerpt(text, bodies.get("功能需求清单", (1,))[0]),
                    "Delivery cannot be judged against observable outcomes.", "Add atomic AC-* criteria linked to each critical requirement.")
            if ac_ids and not case_ids:
                add("cases-missing", "AC-004", "P1", fr_ids[0] if len(fr_ids) == 1 else None, bodies.get("功能需求清单", (None,))[0], "功能需求清单",
                    "Acceptance criteria have no executable CASE scenarios.", _line_excerpt(text, bodies.get("功能需求清单", (1,))[0]),
                    "Testers must invent inputs and expected outcomes.", "Map each critical AC to materially distinct CASE scenarios.")
            if not re.search(r"不包含|不做|Out of Scope|范围外|excluded|not included", text, re.I):
                add("scope-missing", "SCOPE-001", "P2", None, bodies.get("模块定义", (None,))[0], "模块定义",
                    "The module boundary does not explicitly state Out of Scope.", _line_excerpt(text, bodies.get("模块定义", (1,))[0]),
                    "Adjacent work may be included or excluded inconsistently.", "List capabilities explicitly excluded from this phase.")
            if not re.search(r"版本|修订|revision|change log", text, re.I):
                add("metadata-missing", "META-001", "P2", None, 1, None,
                    "Spec version or revision history was not detected.", _line_excerpt(text, 1, 8),
                    "Reviewers cannot establish which behavioral baseline was assessed.", "Record version, date, author, and a revision summary.")

            tables = _tables(text)
            # Content signals apply to layout-neutral product Specs. Exact
            # table and diagram expectations apply only to the explicit
            # strict structure profile. All scanner output remains candidate
            # evidence and never constitutes a final finding.
            preamble = text[: min((text.find("## ") if "## " in text else len(text)), len(text))]
            if profile != "speckit":
                if not any("元信息" in " ".join(headers) or "metadata" in " ".join(headers).lower() for _, headers, _ in tables) and not re.search(r"版本|业务 Owner|创建日期|version|owner|created date", preamble, re.I):
                    add("metadata-table-missing", "META-001", "P1", None, 1, None,
                        "The Spec has no identifiable baseline metadata.", _line_excerpt(text, 1, 8),
                        "Version, ownership, baseline, and blocker status cannot be traced.", "Record the current version, status, owner, baseline, and unresolved blockers in a uniquely locatable form.")
                state_body = bodies.get("状态模型", (None, ""))[1]
                if profile == _STRICT_STRUCTURE_PROFILE and state_body and "stateDiagram-v2" not in state_body:
                    add("state-diagram-missing", "STATE-001", "P2", "状态模型", bodies["状态模型"][0], "状态模型",
                        "The state model has no Mermaid stateDiagram-v2 definition.", _line_excerpt(text, bodies["状态模型"][0], 5),
                        "Allowed transitions and terminal behavior cannot be reviewed consistently.", "Add a complete stateDiagram-v2 with guards and outcomes.")
                if profile == _STRICT_STRUCTURE_PROFILE and state_body and not any(any("状态" in header or "state" in header.lower() for header in headers) and rows for _, headers, rows in tables):
                    add("state-definition-missing", "STATE-002", "P1", "状态模型", bodies["状态模型"][0], "状态模型",
                        "The state model has no structured state definition table.", _line_excerpt(text, bodies["状态模型"][0], 5),
                        "Implementers cannot determine state meaning, entry conditions, or legal operations.", "Add a state definition table with meaning, entry conditions, and operations.")
                dep_body = bodies.get("依赖与假设", (None, ""))[1]
                if profile == _STRICT_STRUCTURE_PROFILE and dep_body and not any(any(token in header.lower() for token in ("系统/模块", "依赖内容", "降级策略", "dependency") for header in headers) for _, headers, _ in tables):
                    add("dependency-table-missing", "DEP-001", "P2", "依赖与假设", bodies["依赖与假设"][0], "依赖与假设",
                        "The dependency chapter has no structured dependency table.", _line_excerpt(text, bodies["依赖与假设"][0], 5),
                        "External failure behavior and assumptions remain implicit.", "List each dependency, prerequisite, and unavailable-service fallback.")
                if bodies.get("成功标准", (None, ""))[1] and not re.search(r"\bSC[-_]\d{2,3}\b", bodies["成功标准"][1], re.I):
                    add("success-criteria-missing", "SC-001", "P1", "成功标准", bodies["成功标准"][0], "成功标准",
                        "The success criteria chapter has no stable SC identifier.", _line_excerpt(text, bodies["成功标准"][0], 5),
                        "Release outcomes cannot be measured or traced to evidence.", "Add measurable SC-NN criteria and their measurement method.")
                decision_body = bodies.get("关键决策记录", (None, ""))[1]
                if profile == _STRICT_STRUCTURE_PROFILE and decision_body and not any(any("决策ID" in header or "decision" in header.lower() for header in headers) for _, headers, _ in tables):
                    add("decision-table-missing", "DECISION-001", "P2", "关键决策记录", bodies["关键决策记录"][0], "关键决策记录",
                        "The decision chapter has no structured decision record table.", _line_excerpt(text, bodies["关键决策记录"][0], 5),
                        "Important choices and their ownership cannot be reconstructed.", "Record decision ID, question, conclusion, rationale/trade-offs, and date.")
            # Candidate terminology signal: uppercase domain abbreviations with
            # no glossary/term column. It intentionally stays P3 because the
            # authority requires semantic review before admission.
            acronym_tokens = {token for token in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", text) if token not in {"FR", "AC", "CASE", "NFR", "GEN", "HTTP", "HTTPS", "JSON", "API", "MUST", "NOT"}}
            has_term_definition_area = bool(re.search(
                r"(?im)^#{1,6}\s+.*(?:terms? and abbreviations|glossary|术语|缩写)", text,
            )) or any(any("术语" in header or "缩写" in header or "glossary" in header.lower() for header in headers) for _, headers, _ in tables)
            if acronym_tokens and not has_term_definition_area:
                first = min((text.count("\n", 0, text.find(token)) + 1 for token in acronym_tokens if token in text), default=1)
                add("glossary-missing", "TERM-001", "P3", None, first, None,
                    "Potential domain abbreviations are used without a glossary table.", _line_excerpt(text, first),
                    "Readers may assign different meanings to the same term.", "Define domain abbreviations and specialized terms once in a glossary.")
            field_rows = [(line, headers, row) for line, headers, rows in tables for row_line, row in rows for line in [row_line]
                          if any("字段名" in header or "field" in header.lower() for header in headers)]
            for line, headers, row in field_rows:
                values = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
                field = values.get("字段名") or values.get("Field") or values.get("field") or "field"
                required = ("业务含义", "类型", "必填", "取值范围", "校验规则")
                missing = [name for name in required if not _meaningful(values.get(name, ""))]
                for name in missing:
                    add(f"data-{re.sub(r'[^A-Za-z0-9]+', '-', field).strip('-').lower()}-{name}", "DATA-001", "P2", field, line, "数据字段定义",
                        f"Field {field} is missing {name}.", _line_excerpt(text, line),
                        "The business data contract is incomplete and implementations must guess valid input and error behavior.", f"Define {name} for field {field} according to its logical business type.")
            nfr_rows = [(line, headers, row) for line, headers, rows in tables for line, row in rows
                        if any("NFR" in header.upper() for header in headers)]
            has_nfr_decision = bool(re.search(
                r"\bNFR[-_][A-Z0-9-]+\b.*(?:ADOPTED|NOT_APPLICABLE|EXEMPTED|采用|不适用|豁免)",
                text,
                re.I,
            ))
            if not (nfr_rows or has_nfr_decision) and profile != "speckit":
                add("nfr-selection-missing", "NFR-001", "P1", None, bodies.get("非功能性需求选择", (None,))[0], "非功能性需求选择",
                    "No explicit NFR applicability decision was detected.", _line_excerpt(text, bodies.get("非功能性需求选择", (1,))[0]),
                    "Required quality controls, targets, and verification plans remain undecided.", "Record each applicable NFR as adopted, not applicable with reason, or exempted with human approval and expiry.")
            if re.search(r"权限|角色|数据范围|数据隔离|permission|role", text, re.I) and not re.search(r"无权限|权限不足|越权|数据范围|数据隔离|tenant|租户|permission denial|unauthorized|forbidden", text, re.I):
                add("permission-unclear", "PERM-001", "P2", None, None, "功能需求清单",
                    "Permission-related behavior is mentioned without a clear role, data-scope, or denial response.", None,
                    "Unauthorized access and isolation behavior cannot be tested consistently.", "Define roles, readable/writable operations, data scope, and no-permission response.")
            if re.search(r"关键决策|决策记录|Clarifications|澄清", text, re.I) and not re.search(r"理由|取舍|原因|背景|rationale|trade-off", text, re.I):
                add("decision-rationale-missing", "DECISION-003", "P3", None, bodies.get("关键决策记录", (None,))[0], "关键决策记录",
                    "Decision records do not preserve rationale or trade-offs.", _line_excerpt(text, bodies.get("关键决策记录", (1,))[0]),
                    "Later reviewers cannot reconstruct why the chosen behavior was adopted.", "Record the question, conclusion, rationale, trade-offs, date, and affected requirements.")
            if fr_ids and not re.search(r"正常|边界|异常|非法|权限|超时|重试|并发|幂等|恢复|normal|boundary|invalid|permission|timeout|retry", text, re.I):
                add("scenario-coverage-missing", "SCENE-001", "P2", fr_ids[0] if len(fr_ids) == 1 else None, None, "功能需求清单",
                    "No normal, boundary, failure, permission, concurrency, or recovery scenario signal was detected.", None,
                    "Material failure behavior may be left to implementation assumptions.", "Add risk-based scenarios with distinct preconditions, actions, and observable outcomes.")

            scanner_candidates = list(candidates)
            review_strategy = str(item.metadata.get("reviewStrategy") or "candidate")
            if review_strategy not in {"candidate", "navigation"}:
                review_strategy = "candidate"
            candidate_results = (
                _navigation_candidates(navigation["units"])
                if review_strategy == "navigation"
                else [_canonical_candidate(candidate) for candidate in scanner_candidates]
            )
            check_results = []
            for item_def in _CHECKLIST:
                prefixes = tuple(item_def.get("rulePrefixes", ()))
                related = [candidate for candidate in candidates if any(candidate["ruleId"] == prefix or candidate["ruleId"].startswith(prefix) for prefix in prefixes)]
                semantic_review = bool(item_def.get("semanticReviewRequired", True))
                note = "This dimension requires evidence-backed Agent or human semantic review." if semantic_review else None
                check_results.append({
                    "checkId": item_def["id"], "category": item_def["category"], "question": item_def["question"], "method": item_def["method"],
                    "status": "FINDING" if related else "UNVERIFIED" if semantic_review else "PASS",
                    "candidateIds": [candidate["candidateId"] for candidate in related],
                    "evidence": [candidate["evidence"] or candidate["message"] for candidate in related[:3]], "reviewNote": note,
                })
            mapping_ids_by_check: dict[str, list[str]] = {}
            for mapping in dimension_evidence:
                mapping_ids_by_check.setdefault(str(mapping["check_id"]), []).append(
                    str(mapping["mapping_id"]),
                )
            checklist_items = [{
                "check_id": result["checkId"],
                "batch": result["category"],
                "question": result["question"],
                "method": result["method"],
                "scanner_status": result["status"],
                "candidate_ids": result["candidateIds"],
                "source_mapping_ids": mapping_ids_by_check.get(result["checkId"], []),
            } for result in check_results]
            evidence_id = f"evidence:{item.work_item_id}:{source_digest[:16]}"
            evidence_manifest = {
                "manifest_version": "1.1.0",
                "policy": {"policy_id": _POLICY_ID, "policy_version": _POLICY_VERSION, "authority_path": "authority.md", "authority_version": _AUTHORITY_VERSION},
                "target": {"path": str(path), "version_or_commit": None},
                "scope": {"kind": "cross-spec" if isinstance(cross_scope, Mapping) else "single-file", "project_root": None, "code_verification": False, "external_materials": bool(cross_scope)},
                "selected_sources": [
                    {"kind": "skill-default", "source": "authority.md", "version": _AUTHORITY_VERSION, "owner": "spec-quality-audit maintainers", "applicable_dimensions": ["all"], "adoption_basis": "canonical authority"},
                    {"kind": "organization-baseline", "source": "quality-standard.md", "version": _POLICY_VERSION, "owner": "spec-quality-audit maintainers", "applicable_dimensions": ["CHK-01..CHK-18"], "adoption_basis": "reviewed semantic standard derived from the organization baseline"},
                ],
                "profiles": {"selected": profile, "overlays": ["adversarial"] if profile == "adversarial" else [], "available": list(_AVAILABLE_PROFILES)},
                "claim_verifications": [],
                "unavailable_evidence": ["Target version/commit was not established.", "Business correctness and human approval authenticity are not proven by deterministic inspection."] + ([] if isinstance(cross_scope, Mapping) else ["Existing-system compatibility and cross-spec conflicts were not verified in single-file scope."]),
                "checker_drift": ["The platform emits candidate evidence; semantic review and structured result presentation are Agent/platform responsibilities.", "Full lifecycle gates for speckit remain reviewer responsibilities."],
            }
            cross_document_packet = ({
                "schema_version": "1.0.0",
                "scope": cross_scope,
                "evidence": cross_document_evidence,
            } if isinstance(cross_scope, Mapping) else None)
            cross_document_review_items = ([{
                "relationship_id": relationship["relationship_id"],
                "from_document_id": relationship["from_document_id"],
                "to_document_id": relationship["to_document_id"],
                "kind": relationship["kind"],
                "relationship_evidence": relationship["evidence"],
                "resolution_owner": relationship["resolution_owner"],
                "selection_reason": relationship.get("selection_reason"),
                "evidence_item_ids": [
                    evidence_item["evidence_item_id"]
                    for evidence_item in cross_document_evidence
                    if evidence_item["relationship_id"] == relationship["relationship_id"]
                ],
            } for relationship in cross_scope.get("relationships", ())]
                if isinstance(cross_scope, Mapping) else [])
            payload = {
                "authorityVersion": _AUTHORITY_VERSION, "policyId": _POLICY_ID, "policyVersion": _POLICY_VERSION,
                "profile": profile, "sourceDigest": source_digest, "candidateOnly": True,
                "reviewStrategy": review_strategy,
                "candidates": scanner_candidates, "candidateFindings": candidate_results,
                "navigation": navigation,
                "sourceChunks": source_chunks, "dimensionEvidence": dimension_evidence,
                "documentContext": document_context,
                "sourceFacts": source_facts,
                "crossDocumentPacket": cross_document_packet,
                "crossDocumentReviewItems": cross_document_review_items,
                "checklistItems": checklist_items,
                "checklistResults": check_results, "checklist_results": [{
                    "check_id": result["checkId"], "category": result["category"], "question": result["question"], "method": result["method"],
                    "status": result["status"], "candidate_ids": result["candidateIds"], "evidence": result["evidence"], "review_note": result["reviewNote"],
                } for result in check_results],
                "evidenceManifest": evidence_manifest, "evidence_manifest": evidence_manifest,
                "readiness": {"status": "UNVERIFIED", "reason": "Scanner candidates require explicit semantic review; no final readiness is inferred."},
            }
            # Platform-owned projection used for generic coverage and exact
            # root-cause grouping.  The plugin candidate payload remains the
            # source of truth and is intentionally not replaced.
            candidate_graph = build_candidate_evidence_graph(
                candidate_results,
                work_item_id=item.work_item_id,
                check_id=check.check_id,
                check_version=check.version,
            )
            payload["candidateGraph"] = render_candidate_evidence_graph(candidate_graph)
            validate_candidate_evidence_graph_projection(payload["candidateGraph"])
            evidence = EvidenceRecord(evidence_id, item.work_item_id, check.check_id, check.version, "structured", item.identity, payload)
            dimensions = tuple(DimensionObservation(
                item_def["id"],
                (next((result["reviewNote"] for result in check_results if result["checkId"] == item_def["id"] and result["reviewNote"]), item_def["question"]),),
                (evidence_id,),
                "violated" if any(result["checkId"] == item_def["id"] and result["status"] == "FINDING" for result in check_results) else "unresolved" if any(result["checkId"] == item_def["id"] and result["status"] == "UNVERIFIED" for result in check_results) else "satisfied",
            ) for item_def in _CHECKLIST)
            packets.append(InvestigationPacket(
                item, check.check_id, check.version, dimensions, (evidence,), "not_required",
                metadata={
                    "path": str(path),
                    "profile": profile,
                    "reviewStrategy": review_strategy,
                    "candidateCount": len(candidate_results),
                    "scannerCandidateCount": len(scanner_candidates),
                    "sourceChunkCount": len(source_chunks),
                    "documentTypeProposal": document_context["documentType"],
                    "documentContextConfidence": document_context["classificationConfidence"],
                    "sourceFactCounts": {
                        key: len(value) for key, value in source_facts["identifiers"].items()
                    },
                    "dimensionEvidenceCount": len(dimension_evidence),
                    "evidenceManifest": evidence_manifest,
                    "evidenceCollections": [
                        {
                            "collectionId": "candidate-findings",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/candidateFindings",
                            "itemIdField": "candidate_id",
                            "groupBy": ["rule_id"],
                            "reviewRequired": review_strategy == "candidate",
                        },
                        {
                            "collectionId": "document-navigation",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/navigation/units",
                            "itemIdField": "unitId",
                            "groupBy": ["kind"],
                            "reviewRequired": review_strategy == "navigation",
                        },
                        *([{
                            "collectionId": "cross-document-relationships",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/crossDocumentReviewItems",
                            "itemIdField": "relationship_id",
                            "groupBy": ["kind"],
                            "reviewRequired": True,
                        }] if isinstance(cross_scope, Mapping) else []),
                        {
                            "collectionId": "checklist-dimensions",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/checklistItems",
                            "itemIdField": "check_id",
                            "groupBy": ["batch"],
                            "reviewRequired": True,
                        },
                        {
                            "collectionId": "dimension-evidence",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/dimensionEvidence",
                            "itemIdField": "mapping_id",
                            "groupBy": ["check_id"],
                            "reviewRequired": False,
                        },
                        {
                            "collectionId": "source-sections",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/sourceChunks",
                            "itemIdField": "source_chunk_id",
                            "groupBy": ["heading_level"],
                            "reviewRequired": False,
                        },
                        *([{
                            "collectionId": "cross-document-evidence",
                            "evidenceId": evidence_id,
                            "jsonPointer": "/crossDocumentPacket/evidence",
                            "itemIdField": "evidence_item_id",
                            "groupBy": ["relationship_id", "document_id"],
                            "reviewRequired": False,
                        }] if isinstance(cross_scope, Mapping) else []),
                    ],
                },
            ))
        return tuple(packets)

    def assemble_review_checkpoints(
        self, checkpoints: Sequence[ReviewCheckpoint], finalization: Mapping[str, Any],
        packet: InvestigationPacket, check: CheckContract, context: PlatformContext,
    ) -> Mapping[str, Any]:
        """Assemble paged Agent review records into the canonical Spec envelope."""
        del check, context
        readiness_context = finalization.get("readiness_context")
        if not isinstance(readiness_context, Mapping):
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", "Checkpoint finalization requires readiness_context",
            )
        reviewer_origin = finalization.get("reviewer_origin_decisions", ())
        if not isinstance(reviewer_origin, (tuple, list)) or any(
            not isinstance(item, Mapping) for item in reviewer_origin
        ):
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID", "reviewer_origin_decisions must be an array of objects",
            )
        if "checklist_review" in readiness_context:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                "Checkpoint finalization must not resend persisted checklist_review items",
            )
        decisions: list[Mapping[str, Any]] = []
        checklist_review: list[Mapping[str, Any]] = []
        cross_document_review: list[Mapping[str, Any]] = []
        for checkpoint in checkpoints:
            if checkpoint.collection_id == "checklist-dimensions":
                raw_checklist = checkpoint.payload.get("checklist_review")
                if not isinstance(raw_checklist, (tuple, list)) or not raw_checklist:
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        "Each Spec checklist checkpoint requires checklist_review",
                    )
                check_ids: list[str] = []
                for item in raw_checklist:
                    if not isinstance(item, Mapping):
                        raise PlatformContractError(
                            "SPEC_REVIEW_INVALID", "Checkpoint checklist reviews must be objects",
                        )
                    check_ids.append(str(item.get("check_id", "")))
                    checklist_review.append(dict(item))
                if tuple(check_ids) != checkpoint.item_ids:
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        "Checklist checkpoint payload must preserve its declared CHK order",
                    )
                continue
            if checkpoint.collection_id == "cross-document-relationships":
                raw_reviews = checkpoint.payload.get("cross_document_review")
                if not isinstance(raw_reviews, (tuple, list)) or not raw_reviews:
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        "Each cross-document checkpoint requires cross_document_review",
                    )
                relationship_ids: list[str] = []
                for item in raw_reviews:
                    if not isinstance(item, Mapping):
                        raise PlatformContractError(
                            "SPEC_REVIEW_INVALID",
                            "Cross-document checkpoint reviews must be objects",
                        )
                    relationship_ids.append(str(item.get("relationship_id", "")))
                    cross_document_review.append(dict(item))
                    raw_decisions = item.get("decisions")
                    if not isinstance(raw_decisions, (tuple, list)):
                        raise PlatformContractError(
                            "SPEC_REVIEW_INVALID",
                            "Cross-document checkpoint review decisions must be an array",
                        )
                    decisions.extend(
                        dict(decision) for decision in raw_decisions
                        if isinstance(decision, Mapping)
                    )
                if set(relationship_ids) != set(checkpoint.item_ids) or len(
                    relationship_ids
                ) != len(set(relationship_ids)):
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID",
                        "Cross-document checkpoint must cover its relationships exactly once",
                    )
                continue
            if checkpoint.collection_id not in {"candidate-findings", "document-navigation"}:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Spec review checkpoint uses an unsupported collection",
                )
            raw_decisions = checkpoint.payload.get("decisions")
            if not isinstance(raw_decisions, (tuple, list)) or not raw_decisions:
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Each Spec finding checkpoint requires decisions",
                )
            handled: list[str] = []
            for item in raw_decisions:
                if not isinstance(item, Mapping):
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID", "Checkpoint decisions must be objects",
                    )
                candidate_ids = item.get("candidate_ids")
                if not isinstance(candidate_ids, (tuple, list)):
                    raise PlatformContractError(
                        "SPEC_REVIEW_INVALID", "Checkpoint decisions require candidate_ids",
                    )
                handled.extend(candidate_ids)
                decisions.append(dict(item))
            if len(handled) != len(set(handled)) or set(handled) != set(checkpoint.item_ids):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID", "Checkpoint payload must cover its declared candidate IDs exactly once",
                )
        expected_checks = tuple(str(item["id"]) for item in _CHECKLIST)
        checklist_review.sort(key=lambda item: expected_checks.index(str(item.get("check_id"))))
        if tuple(item.get("check_id") for item in checklist_review) != expected_checks:
            raise PlatformContractError(
                "SPEC_REVIEW_INCOMPLETE",
                "Checkpointed review must cover CHK-01 through CHK-18 exactly once",
            )
        decisions.extend(dict(item) for item in reviewer_origin)
        if any(
            item.get("semantic_type") in {
                "ambiguity", "conflict", "contradiction", "drift", "unverified_dependency",
            }
            for item in reviewer_origin
        ):
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                "Cross-document findings must come from relationship review checkpoints",
            )
        cross_scope = packet.evidence[0].payload.get("crossDocumentPacket") if packet.evidence else None
        if isinstance(cross_scope, Mapping):
            relationship_order = [
                str(item["relationship_id"])
                for item in cross_scope.get("scope", {}).get("relationships", ())
            ]
            cross_document_review.sort(
                key=lambda item: relationship_order.index(str(item.get("relationship_id"))),
            )
            if [str(item.get("relationship_id")) for item in cross_document_review] != relationship_order:
                raise PlatformContractError(
                    "SPEC_REVIEW_INCOMPLETE",
                    "Checkpointed review must cover every declared cross-document relationship",
                )
        elif cross_document_review:
            raise PlatformContractError(
                "SPEC_REVIEW_INVALID",
                "Single-document review cannot contain cross-document relationship checkpoints",
            )
        assembled_context = dict(readiness_context)
        assembled_context["checklist_review"] = checklist_review
        # The current Spec policy is v1.3.  A checkpointed review is a
        # formal decision boundary, so silently assembling a legacy v1.2
        # envelope would let an Agent omit the source-bound semantic fields
        # and still publish a completed result.  Fail at the boundary with a
        # repairable contract error instead of downgrading the review.
        strict_review = bool(checklist_review) and all(
            isinstance(item, Mapping)
            and all(
                field in item
                for field in (
                    "applicability", "observation", "gap", "impact",
                    "recommendation", "owner", "next_action", "confidence",
                )
            )
            for item in checklist_review
        )
        if not strict_review:
            raise PlatformContractError(
                "SPEC_REVIEW_SCHEMA_OUTDATED",
                "Checkpointed Spec review must use review schema 1.3.0 and provide "
                "applicability, observation, gap, impact, recommendation, owner, "
                "next_action, and confidence for all 18 checklist dimensions",
            )
        review_envelope: dict[str, Any] = {
            "review_schema_version": "1.3.0" if strict_review else "1.2.0",
            "readiness_context": assembled_context,
            "decisions": decisions,
            "cross_document_review": cross_document_review,
        }
        raw_candidates = packet.evidence[0].payload.get("candidateFindings", ()) if packet.evidence else ()
        if isinstance(raw_candidates, (tuple, list)):
            candidate_graph = build_candidate_evidence_graph(
                raw_candidates,
                work_item_id=packet.work_item.work_item_id,
                check_id=packet.check_id,
                check_version=packet.check_version,
                decisions=decisions,
            )
            if not candidate_graph.coverage_complete:
                raise PlatformContractError(
                    "SPEC_REVIEW_INCOMPLETE",
                    "Finalized Spec review must dispose every candidate in the evidence graph",
                )
            review_envelope["candidate_graph"] = render_candidate_evidence_graph(candidate_graph)
            validate_candidate_evidence_graph_projection(review_envelope["candidate_graph"])
        if strict_review:
            raw_context = packet.evidence[0].payload.get("documentContext") if packet.evidence else None
            if not isinstance(raw_context, Mapping):
                raise PlatformContractError(
                    "SPEC_REVIEW_INVALID",
                    "Strict v1.3 review requires a documentContext in the investigation packet",
                )
            review_envelope["document_context"] = _review_document_context(raw_context)
        return {
            "review": review_envelope,
            "result_delivery": _actionable_result_delivery(
                decisions, checklist_review, packet,
            ),
        }

    def summarize(self, work_items: Sequence[WorkItem], investigations: Sequence[InvestigationPacket],
                  decisions: Sequence[Any], status: str) -> Mapping[str, Any]:
        """Return a concise, structured presentation of the reviewed result.

        The platform owns persistence and trace artifacts.  This plugin only
        projects its domain review into the normal interactive response; it
        does not create a second HTML report or another durable output format.
        """
        from .review import evaluate_review

        packet_by_id = {packet.work_item.work_item_id: packet for packet in investigations}
        candidate_count = 0
        unavailable_evidence: list[str] = []
        for packet in investigations:
            payload = packet.evidence[0].payload if packet.evidence else {}
            candidates = payload.get("candidateFindings", ()) if isinstance(payload, Mapping) else ()
            if isinstance(candidates, (list, tuple)):
                candidate_count += len(candidates)
            manifest = payload.get("evidence_manifest", {}) if isinstance(payload, Mapping) else {}
            if isinstance(manifest, Mapping):
                unavailable_evidence.extend(manifest.get("unavailable_evidence", ()))

        reviewed_reports: list[tuple[dict[str, Any], InvestigationPacket]] = []
        for decision in decisions:
            details = getattr(decision, "details", None)
            packet = packet_by_id.get(getattr(decision, "work_item_id", ""))
            if isinstance(details, Mapping) and details.get("review") and packet is not None:
                reviewed_reports.append((evaluate_review(decision, packet), packet))

        reviewed = bool(decisions) and len(reviewed_reports) == len(decisions)
        precedence = {"READY": 0, "REWORK": 1, "UNVERIFIED": 2, "ESCALATE": 3}
        readiness = {"status": "UNVERIFIED", "reason": "Deterministic scanner output is candidate evidence only; semantic reviewer confirmation is required."}
        if reviewed_reports:
            readiness = max(
                (report["readiness"] for report, _packet in reviewed_reports),
                key=lambda item: precedence[item["status"]],
            )

        status_counts = {name: 0 for name in ("CONFIRMED", "SUPPRESSED", "MERGED", "UNVERIFIED")}
        confirmed_findings: list[dict[str, Any]] = []
        checklist_counts = {name: 0 for name in ("PASS", "REWORK", "ESCALATE", "UNVERIFIED")}
        checklist_items: list[dict[str, Any]] = []
        remediations: list[dict[str, Any]] = []
        for report, packet in reviewed_reports:
            for finding in report["reviewed_findings"]:
                finding_status = finding["status"]
                status_counts[finding_status] += 1
                if finding_status == "CONFIRMED":
                    confirmed_findings.append({
                        key: finding.get(key) for key in (
                            "finding_id", "severity", "object_id", "dimension", "gap",
                            "impact", "recommendation", "closure_evidence", "evidence",
                            "semantic_type", "relationship_id", "document_ids",
                            "affected_elements", "resolution_owner", "next_action",
                            "evidence_refs",
                        )
                    })
            context = report["readiness_context"]
            delivery = _actionable_result_delivery(
                report["reviewed_findings"], context["checklist_review"], packet,
            )
            remediations.extend(delivery["remediations"])
            for item in context["checklist_review"]:
                checklist_counts[item["status"]] += 1
                checklist_items.append({
                    "checkId": item["check_id"], "status": item["status"],
                    "note": item["note"], "evidenceRefs": [
                        dict(ref) if isinstance(ref, Mapping) else ref
                        for ref in item.get("evidence_refs", ())
                    ],
                })
        handled_candidate_count = sum(
            report["review_summary"]["handled_candidate_count"]
            for report, _packet in reviewed_reports
        )

        work_item_summaries = []
        decision_by_id = {getattr(item, "work_item_id", ""): item for item in decisions}
        for item in work_items:
            payload = packet_by_id.get(item.work_item_id)
            evidence_payload = payload.evidence[0].payload if payload and payload.evidence else {}
            candidates = evidence_payload.get("candidateFindings", ()) if isinstance(evidence_payload, Mapping) else ()
            decision = decision_by_id.get(item.work_item_id)
            work_item_summaries.append({
                "workItemId": item.work_item_id,
                "path": item.metadata.get("path"),
                "candidateCount": len(candidates) if isinstance(candidates, (list, tuple)) else 0,
                "result": getattr(decision, "result", None),
                "reason": getattr(decision, "reason", None),
            })

        return {
            "phase": "REVIEWED" if reviewed else "CANDIDATE",
            "runStatus": status,
            "readiness": readiness,
            "review": {
                "candidateCount": candidate_count,
                "handledCandidateCount": handled_candidate_count,
                "pendingCandidateCount": max(candidate_count - handled_candidate_count, 0),
                "statusCounts": status_counts,
                "confirmedFindings": confirmed_findings,
                "remediations": remediations,
                "checklist": {"total": 18 * len(work_items), "statusCounts": checklist_counts, "items": checklist_items},
            },
            "policy": {"policyId": _POLICY_ID, "policyVersion": _POLICY_VERSION, "authorityVersion": _AUTHORITY_VERSION},
            "workItems": work_item_summaries,
            "unavailableEvidence": sorted(set(str(item) for item in unavailable_evidence)),
            "evidenceBoundary": "Business correctness, human approvals, existing-system compatibility, and unavailable external sources remain unverified unless directly verified within the declared scope.",
        }
