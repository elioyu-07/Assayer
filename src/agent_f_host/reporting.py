from __future__ import annotations

import hashlib
import json


class DerivedReportBuilder:
    """Pure deterministic views over a validated AuditLedger."""

    VERSION = "1.0.0"

    @staticmethod
    def _json(value: dict) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _text(value) -> str:
        return " ".join(str(value or "").replace("<", "‹").replace(">", "›").split())

    def render(self, ledger: dict) -> dict[str, bytes]:
        scan = ledger["scan"]
        ledger_digest = hashlib.sha256(json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        pages = {item["pageStateId"]: item for item in ledger["pageStates"]}
        objects = {item["objectId"]: item for item in ledger["objects"]}
        screenshots = {item["screenshotId"]: item for item in ledger["screenshots"]}
        assessments = sorted(ledger["assessments"], key=lambda item: item["assessmentId"])
        assessment_validity = {item["assessmentId"]: item.get("conclusionValidity") for item in assessments}
        effective_valid = bool(scan["conclusionsValid"])

        issue_items = []
        invalidated_refs = []
        for issue in sorted(ledger["issues"], key=lambda item: item["issueId"]):
            valid = effective_valid and issue.get("conclusionValidity") == "valid" and assessment_validity.get(issue["assessmentRef"]) == "valid"
            if not valid:
                invalidated_refs.append(issue["issueId"])
                continue
            target = objects[issue["objectRef"]]
            page = pages[target["pageStateRef"]]
            screenshot = screenshots[issue["screenshotRef"]]
            issue_items.append({key: issue[key] for key in ("issueId", "assessmentRef", "objectRef", "rule", "severity", "title", "message", "impact", "recommendation", "evidenceRefs", "screenshotRef")} | {
                "page": {"pageStateId": page["pageStateId"], "url": page["url"], "route": page.get("route", ""), "title": page.get("title", "")},
                "screenshotPath": screenshot.get("path"), "effectiveConclusionValidity": "valid",
            })
        issues_view = {"schemaVersion": self.VERSION, "sourceLedger": "audit-ledger.json", "sourceLedgerDigest": ledger_digest, "scanId": scan["scanId"], "runId": scan["runId"], "scanStatus": scan["status"], "conclusionsValid": effective_valid, "ruleRegistryDigest": scan["ruleRegistryDigest"], "issues": issue_items, "invalidatedIssueRefs": invalidated_refs}

        by_object = {}
        for assessment in assessments:
            by_object.setdefault(assessment["objectRef"], []).append({key: assessment[key] for key in ("assessmentId", "rule", "applicable", "result", "coverage", "evidenceRefs", "caseRefs", "screenshotRef", "severity", "title", "impact", "recommendation", "reasonText", "blocker", "conclusionValidity", "decidedAt") if key in assessment})
        page_items = []
        for page in sorted(pages.values(), key=lambda item: item["pageStateId"]):
            page_objects = []
            for object_ref in sorted(page.get("objectRefs", [])):
                target = objects[object_ref]
                page_objects.append({"objectId": object_ref, "kind": target["kind"], "status": target["status"], "potentialRules": target["potentialRules"], "assessments": by_object.get(object_ref, [])})
            page_items.append({"pageStateId": page["pageStateId"], "url": page["url"], "route": page.get("route", ""), "title": page.get("title", ""), "stateKind": page["stateKind"], "objects": page_objects})
        judgement = {"schemaVersion": self.VERSION, "sourceLedger": "audit-ledger.json", "sourceLedgerDigest": ledger_digest, "scanId": scan["scanId"], "pages": page_items}

        result_counts = {name: 0 for name in ("issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise")}
        for assessment in assessments:
            result_counts[assessment["result"]] += 1
        failed_operations = [{"operationId": item["operationId"], "tool": item["tool"], "status": item["status"], "reason": item.get("reason")} for item in ledger["operations"] if item["status"] in {"rejected", "failed_known", "result_unknown"}]
        diagnostics = {"schemaVersion": self.VERSION, "sourceLedger": "audit-ledger.json", "sourceLedgerDigest": ledger_digest, "scanId": scan["scanId"], "runId": scan["runId"], "scanStatus": scan["status"], "conclusionsValid": effective_valid, "terminalReason": scan["terminalReason"], "coverageProof": scan["coverageProof"], "assessmentResultCounts": result_counts, "failedOperations": failed_operations}

        summary = ["# agent-f 审计摘要", "", f"- Scan：{scan['scanId']}", f"- 账本摘要：{ledger_digest}", f"- 状态：{scan['status']}", f"- 结论有效：{'是' if effective_valid else '否'}", f"- 正式问题：{len(issue_items)}", f"- 待复核：{result_counts['needs_review']}", "", "## 问题"]
        if not issue_items:
            summary.extend(["", "本次没有可发布的有效问题。"])
        for index, issue in enumerate(issue_items, 1):
            summary.extend(["", f"### {index}. {self._text(issue['title'])}", "", f"- 严重度：{issue['severity']}", f"- 规则：{issue['rule']['ruleId']}@{issue['rule']['version']}", f"- 页面：{self._text(issue['page']['route'] or issue['page']['url'])}", f"- 问题：{self._text(issue['message'])}", f"- 影响：{self._text(issue['impact'])}", f"- 建议：{self._text(issue['recommendation'])}", f"- 截图：{issue['screenshotPath']}"])
        summary.extend(["", "## 覆盖", ""])
        for item in scan["coverageProof"]["ruleSummaries"]:
            summary.append(f"- {item['rule']['ruleId']}@{item['rule']['version']}：{item['assessmentCount']} 个判定，覆盖{'完整' if item['coverageComplete'] else '不完整'}")
        summary.append("")
        diagnostics_md = ["# agent-f 运行诊断", "", f"- 终态：{scan['status']}", f"- 原因：{self._text(scan['terminalReason']['message'])}", f"- 未处理入口：{len(scan['coverageProof'].get('unprocessedEntrypointRefs', []))}", f"- 失败/拒绝 Operation：{len(failed_operations)}", ""]
        log_lines = [f"{item['acceptedAt']} {item['status']} {item['tool']} {item['operationId']}" + (f" {item['reason']['code']}" if item.get("reason") else "") for item in sorted(ledger["operations"], key=lambda value: (value["acceptedAtRevision"], value["operationId"]))]
        return {"issues.json": self._json(issues_view), "page-element-judgement.json": self._json(judgement), "run-diagnostics.json": self._json(diagnostics), "audit-summary.md": ("\n".join(summary)).encode("utf-8"), "run-diagnostics.md": ("\n".join(diagnostics_md)).encode("utf-8"), "audit.log": ("\n".join(log_lines) + "\n").encode("utf-8")}
