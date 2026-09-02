"""Canonical Spec-quality candidate scanner.

The bundled ``authority.md`` is the normative entry point.  This module is an
implementation projection only: all deterministic observations are candidates
and remain unresolved until an Agent/reviewer submits an explicit review.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from ...contract import (
    CheckContract, DimensionObservation, EvidenceRecord, InvestigationPacket,
    PlatformContext, PluginManifest, WorkItem,
)
from ...registry import load_plugin_manifest


_ROOT = Path(__file__).parent
_MANIFEST = _ROOT / "manifest.json"
_POLICY = json.loads((_ROOT / "policy.json").read_text(encoding="utf-8"))
_CHECKLIST = json.loads((_ROOT / "checklist.json").read_text(encoding="utf-8"))
_POLICY_ID = str(_POLICY["policy_id"])
_POLICY_VERSION = str(_POLICY["policy_version"])
_AUTHORITY_VERSION = str(_POLICY["authority"]["version"])
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
_PLACEHOLDER = re.compile(r"(?i)\b(?:TODO|TBD|YYYY-MM-DD)\b|待填写|待补充|示例内容")
_FR = re.compile(r"\bFR[-_]\d{3}[A-Za-z]?\b", re.I)
_AC = re.compile(r"\bAC[-_]?[A-Z0-9-]+\b", re.I)
_CASE = re.compile(r"\bCASE[-_]?[A-Z0-9-]+\b", re.I)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _candidate(cid: str, rule: str, severity: str, object_id: str | None, line: int | None,
              chapter: str | None, message: str, evidence: str | None,
              impact: str, recommendation: str) -> dict[str, Any]:
    # camelCase fields are retained only for the existing platform evidence
    # compatibility view; candidateFindings below is the canonical projection.
    return {
        "candidateId": cid, "ruleId": rule, "suggestedSeverity": severity,
        "objectId": object_id, "line": line, "chapter": chapter,
        "message": message, "evidence": evidence, "impact": impact,
        "recommendation": recommendation, "policyId": _POLICY_ID,
        "policyVersion": _POLICY_VERSION, "confidence": None,
    }


def _canonical_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": item["candidateId"], "rule_id": item["ruleId"],
        "suggested_severity": item["suggestedSeverity"], "object_id": item["objectId"],
        "line": item["line"], "chapter": item["chapter"], "message": item["message"],
        "evidence": item["evidence"], "impact": item["impact"],
        "recommendation": item["recommendation"], "policy_id": item["policyId"],
        "policy_version": item["policyVersion"], "confidence": item["confidence"],
    }


class SpecQualityPlugin:
    """Discover and inspect Markdown Specs under the canonical authority."""

    manifest: PluginManifest = load_plugin_manifest(_MANIFEST)

    def discover(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]:
        del context
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
                 "sourceDigest": _digest(raw)},
            ))
        return tuple(items)

    def inspect(self, work_items: Sequence[WorkItem], check: CheckContract,
                context: PlatformContext) -> Sequence[InvestigationPacket]:
        del context
        packets: list[InvestigationPacket] = []
        for item in work_items:
            path = Path(item.metadata["path"])
            raw = path.read_bytes()
            source_digest = _digest(raw)
            if source_digest != item.state_digest:
                raise ValueError("Spec changed after discovery")
            text = raw.decode("utf-8-sig")
            profile = str(item.metadata.get("profile") or _POLICY["default_profile"])
            chapters, bodies = _chapters(text), _chapter_bodies(text)
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

            if profile not in {"product-spec", "speckit", "adversarial"}:
                add("profile-unknown", "PROFILE-001", "P1", None, 1, None,
                    f"Unknown profile: {profile}.", None, "The selected structural authority cannot be established.", "Select product-spec, speckit, or adversarial explicitly.")
                profile = "product-spec"
            if profile != "speckit":
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
            # Structure-specific checks delegated by authority.md to the
            # bundled product template. These are still candidate evidence,
            # never final findings.
            preamble = text[: min((text.find("## ") if "## " in text else len(text)), len(text))]
            if profile != "speckit":
                if not any("元信息" in " ".join(headers) or "metadata" in " ".join(headers).lower() for _, headers, _ in tables) and not re.search(r"版本|业务 Owner|创建日期", preamble, re.I):
                    add("metadata-table-missing", "META-001", "P1", None, 1, None,
                        "The Spec has no structured metadata record.", _line_excerpt(text, 1, 8),
                        "Version, ownership, baseline, and blocker status cannot be traced.", "Add the template metadata table and fill every required field.")
                state_body = bodies.get("状态模型", (None, ""))[1]
                if state_body and "stateDiagram-v2" not in state_body:
                    add("state-diagram-missing", "STATE-001", "P2", "状态模型", bodies["状态模型"][0], "状态模型",
                        "The state model has no Mermaid stateDiagram-v2 definition.", _line_excerpt(text, bodies["状态模型"][0], 5),
                        "Allowed transitions and terminal behavior cannot be reviewed consistently.", "Add a complete stateDiagram-v2 with guards and outcomes.")
                if state_body and not any("状态" in header and len(rows) > 0 for _, headers, rows in tables):
                    add("state-definition-missing", "STATE-002", "P1", "状态模型", bodies["状态模型"][0], "状态模型",
                        "The state model has no structured state definition table.", _line_excerpt(text, bodies["状态模型"][0], 5),
                        "Implementers cannot determine state meaning, entry conditions, or legal operations.", "Add a state definition table with meaning, entry conditions, and operations.")
                dep_body = bodies.get("依赖与假设", (None, ""))[1]
                if dep_body and not any(any(token in header for token in ("系统/模块", "依赖内容", "降级策略", "dependency")) for _, headers, _ in tables):
                    add("dependency-table-missing", "DEP-001", "P2", "依赖与假设", bodies["依赖与假设"][0], "依赖与假设",
                        "The dependency chapter has no structured dependency table.", _line_excerpt(text, bodies["依赖与假设"][0], 5),
                        "External failure behavior and assumptions remain implicit.", "List each dependency, prerequisite, and unavailable-service fallback.")
                if bodies.get("成功标准", (None, ""))[1] and not re.search(r"\bSC[-_]\d{2,3}\b", bodies["成功标准"][1], re.I):
                    add("success-criteria-missing", "SC-001", "P1", "成功标准", bodies["成功标准"][0], "成功标准",
                        "The success criteria chapter has no stable SC identifier.", _line_excerpt(text, bodies["成功标准"][0], 5),
                        "Release outcomes cannot be measured or traced to evidence.", "Add measurable SC-NN criteria and their measurement method.")
                decision_body = bodies.get("关键决策记录", (None, ""))[1]
                if decision_body and not any("决策ID" in header or "decision" in header.lower() for _, headers, _ in tables):
                    add("decision-table-missing", "DECISION-001", "P2", "关键决策记录", bodies["关键决策记录"][0], "关键决策记录",
                        "The decision chapter has no structured decision record table.", _line_excerpt(text, bodies["关键决策记录"][0], 5),
                        "Important choices and their ownership cannot be reconstructed.", "Record decision ID, question, conclusion, rationale/trade-offs, and date.")
            # Candidate terminology signal: uppercase domain abbreviations with
            # no glossary/term column. It intentionally stays P3 because the
            # authority requires semantic review before admission.
            acronym_tokens = {token for token in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", text) if token not in {"FR", "AC", "CASE", "NFR", "HTTP", "HTTPS", "JSON", "API"}}
            if acronym_tokens and not any("术语" in header or "缩写" in header or "glossary" in header.lower() for _, headers, _ in tables):
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
            if not nfr_rows and profile != "speckit":
                add("nfr-selection-missing", "NFR-001", "P1", None, bodies.get("非功能性需求选择", (None,))[0], "非功能性需求选择",
                    "No structured NFR selection records were detected.", _line_excerpt(text, bodies.get("非功能性需求选择", (1,))[0]),
                    "Required quality controls, targets, and verification plans remain undecided.", "Record each applicable NFR as adopted, not applicable with reason, or exempted with human approval and expiry.")
            if re.search(r"权限|角色|数据范围|数据隔离|permission|role", text, re.I) and not re.search(r"无权限|权限不足|越权|数据范围|数据隔离|tenant|租户", text, re.I):
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

            candidate_results = [_canonical_candidate(candidate) for candidate in candidates]
            check_results = []
            for item_def in _CHECKLIST:
                prefixes = tuple(item_def.get("rulePrefixes", ()))
                related = [candidate for candidate in candidates if any(candidate["ruleId"] == prefix or candidate["ruleId"].startswith(prefix) for prefix in prefixes)]
                manual = bool(item_def.get("manual"))
                partial = bool(item_def.get("partial"))
                note = "This dimension requires Agent or human semantic review." if manual or partial else None
                check_results.append({
                    "checkId": item_def["id"], "category": item_def["category"], "question": item_def["question"], "method": item_def["method"],
                    "status": "FINDING" if related else "UNVERIFIED" if manual or partial else "PASS",
                    "candidateIds": [candidate["candidateId"] for candidate in related],
                    "evidence": [candidate["evidence"] or candidate["message"] for candidate in related[:3]], "reviewNote": note,
                })
            evidence_id = f"evidence:{item.work_item_id}:{source_digest[:16]}"
            evidence_manifest = {
                "manifest_version": "1.1.0",
                "policy": {"policy_id": _POLICY_ID, "policy_version": _POLICY_VERSION, "authority_path": "authority.md", "authority_version": _AUTHORITY_VERSION},
                "target": {"path": str(path), "version_or_commit": None},
                "scope": {"kind": "single-file", "project_root": None, "code_verification": False, "external_materials": False},
                "selected_sources": [
                    {"kind": "skill-default", "source": "authority.md", "version": _AUTHORITY_VERSION, "owner": "spec-quality-audit maintainers", "applicable_dimensions": ["all"], "adoption_basis": "canonical authority"},
                    {"kind": "organization-baseline", "source": "self-check-checklist.md", "version": _POLICY_VERSION, "owner": "spec-quality-audit maintainers", "applicable_dimensions": ["CHK-01..CHK-18"], "adoption_basis": "adopted organization baseline"},
                ],
                "profiles": {"selected": profile, "overlays": ["adversarial"] if profile == "adversarial" else [], "available": ["product-spec", "speckit", "adversarial"]},
                "claim_verifications": [],
                "unavailable_evidence": ["Target version/commit was not established.", "Business correctness and human approval authenticity are not proven by deterministic inspection.", "Existing-system compatibility and cross-spec conflicts were not verified in single-file scope."],
                "checker_drift": ["The platform emits candidate evidence; semantic review and structured result presentation are Agent/platform responsibilities.", "Full lifecycle gates for speckit remain reviewer responsibilities."],
            }
            payload = {
                "authorityVersion": _AUTHORITY_VERSION, "policyId": _POLICY_ID, "policyVersion": _POLICY_VERSION,
                "profile": profile, "sourceDigest": source_digest, "candidateOnly": True,
                "candidates": candidates, "candidateFindings": candidate_results,
                "checklistResults": check_results, "checklist_results": [{
                    "check_id": result["checkId"], "category": result["category"], "question": result["question"], "method": result["method"],
                    "status": result["status"], "candidate_ids": result["candidateIds"], "evidence": result["evidence"], "review_note": result["reviewNote"],
                } for result in check_results],
                "evidenceManifest": evidence_manifest, "evidence_manifest": evidence_manifest,
                "readiness": {"status": "UNVERIFIED", "reason": "Scanner candidates require explicit semantic review; no final readiness is inferred."},
            }
            evidence = EvidenceRecord(evidence_id, item.work_item_id, check.check_id, check.version, "structured", item.identity, payload)
            dimensions = tuple(DimensionObservation(
                item_def["id"],
                (next((result["reviewNote"] for result in check_results if result["checkId"] == item_def["id"] and result["reviewNote"]), item_def["question"]),),
                (evidence_id,),
                "violated" if any(result["checkId"] == item_def["id"] and result["status"] == "FINDING" for result in check_results) else "unresolved" if any(result["checkId"] == item_def["id"] and result["status"] == "UNVERIFIED" for result in check_results) else "satisfied",
            ) for item_def in _CHECKLIST)
            packets.append(InvestigationPacket(item, check.check_id, check.version, dimensions, (evidence,), "not_required", metadata={"path": str(path), "profile": profile, "candidateCount": len(candidates), "evidenceManifest": evidence_manifest}))
        return tuple(packets)

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

        reviewed_reports: list[dict[str, Any]] = []
        for decision in decisions:
            details = getattr(decision, "details", None)
            packet = packet_by_id.get(getattr(decision, "work_item_id", ""))
            if isinstance(details, Mapping) and details.get("review") and packet is not None:
                reviewed_reports.append(evaluate_review(decision, packet))

        reviewed = bool(decisions) and len(reviewed_reports) == len(decisions)
        precedence = {"READY": 0, "REWORK": 1, "UNVERIFIED": 2, "ESCALATE": 3}
        readiness = {"status": "UNVERIFIED", "reason": "Deterministic scanner output is candidate evidence only; semantic reviewer confirmation is required."}
        if reviewed_reports:
            readiness = max((report["readiness"] for report in reviewed_reports), key=lambda item: precedence[item["status"]])

        status_counts = {name: 0 for name in ("CONFIRMED", "SUPPRESSED", "MERGED", "UNVERIFIED")}
        confirmed_findings: list[dict[str, Any]] = []
        checklist_counts = {name: 0 for name in ("PASS", "REWORK", "ESCALATE", "UNVERIFIED")}
        checklist_items: list[dict[str, Any]] = []
        for report in reviewed_reports:
            for finding in report["reviewed_findings"]:
                finding_status = finding["status"]
                status_counts[finding_status] += 1
                if finding_status == "CONFIRMED":
                    confirmed_findings.append({
                        key: finding.get(key) for key in (
                            "finding_id", "severity", "object_id", "dimension", "gap",
                            "impact", "recommendation", "closure_evidence", "evidence",
                        )
                    })
            context = report["readiness_context"]
            for item in context["checklist_review"]:
                checklist_counts[item["status"]] += 1
                checklist_items.append({"checkId": item["check_id"], "status": item["status"], "note": item["note"]})
        handled_candidate_count = sum(
            report["review_summary"]["handled_candidate_count"] for report in reviewed_reports
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
                "checklist": {"total": 18 * len(work_items), "statusCounts": checklist_counts, "items": checklist_items},
            },
            "policy": {"policyId": _POLICY_ID, "policyVersion": _POLICY_VERSION, "authorityVersion": _AUTHORITY_VERSION},
            "workItems": work_item_summaries,
            "unavailableEvidence": sorted(set(str(item) for item in unavailable_evidence)),
            "evidenceBoundary": "Business correctness, human approvals, existing-system compatibility, and unavailable external sources remain unverified unless directly verified within the declared scope.",
        }
