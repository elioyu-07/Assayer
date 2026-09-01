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
            by_object.setdefault(assessment["objectRef"], []).append({key: assessment[key] for key in ("assessmentId", "rule", "applicable", "result", "coverage", "findingRefs", "evidenceRefs", "caseRefs", "screenshotRef", "severity", "title", "impact", "recommendation", "reasonText", "blocker", "conclusionValidity", "decidedAt") if key in assessment})
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
        timelines = [{key: item[key] for key in ("assessmentId", "result", "decidedAt", "preparationOperationRef", "commitOperationRef", "findingRefs", "evidenceRefs", "caseRefs")} for item in assessments]
        attributions = self._attributions(failed_operations)
        diagnostics = {"schemaVersion": self.VERSION, "sourceLedger": "audit-ledger.json", "sourceLedgerDigest": ledger_digest, "scanId": scan["scanId"], "runId": scan["runId"], "scanStatus": scan["status"], "conclusionsValid": effective_valid, "terminalReason": scan["terminalReason"], "coverageProof": scan["coverageProof"], "assessmentResultCounts": result_counts, "failedOperations": failed_operations, "assessmentTimelines": timelines, "attributions": attributions}

        summary = ["# Assayer Audit Summary", "", f"- Scan: {scan['scanId']}", f"- Ledger digest: {ledger_digest}", f"- Status: {scan['status']}", f"- Conclusions valid: {'yes' if effective_valid else 'no'}", f"- Published issues: {len(issue_items)}", f"- Needs review: {result_counts['needs_review']}", "", "## Issues"]
        if not issue_items:
            summary.extend(["", "No valid issues are publishable from this run."])
        for index, issue in enumerate(issue_items, 1):
            summary.extend(["", f"### {index}. {self._text(issue['title'])}", "", f"- Severity: {issue['severity']}", f"- Rule: {issue['rule']['ruleId']}@{issue['rule']['version']}", f"- Page: {self._text(issue['page']['route'] or issue['page']['url'])}", f"- Issue: {self._text(issue['message'])}", f"- Impact: {self._text(issue['impact'])}", f"- Recommendation: {self._text(issue['recommendation'])}", f"- Screenshot: {issue['screenshotPath']}"])
        summary.extend(["", "## Coverage", ""])
        for item in scan["coverageProof"]["ruleSummaries"]:
            summary.append(f"- {item['rule']['ruleId']}@{item['rule']['version']}: {item['assessmentCount']} assessments, {'complete' if item['coverageComplete'] else 'incomplete'} coverage")
        summary.append("")
        diagnostics_md = ["# Assayer Run Diagnostics", "", f"- Terminal status: {scan['status']}", f"- Reason: {self._text(scan['terminalReason']['message'])}", f"- Unprocessed entrypoints: {len(scan['coverageProof'].get('unprocessedEntrypointRefs', []))}", f"- Failed/rejected operations: {len(failed_operations)}", f"- Assessment timeline entries: {len(timelines)}", "", "## Failure Attribution", ""]
        diagnostics_md.extend([f"- {item['layer']} ({item['confidence']}): {self._text(item['reason'])}; recommendation: {self._text(item['recommendation'])}" for item in attributions])
        diagnostics_md.append("")
        log_lines = [f"{item['acceptedAt']} {item['status']} {item['tool']} {item['operationId']}" + (f" {item['reason']['code']}" if item.get("reason") else "") for item in sorted(ledger["operations"], key=lambda value: (value["acceptedAtRevision"], value["operationId"]))]
        return {"issues.json": self._json(issues_view), "page-element-judgement.json": self._json(judgement), "run-diagnostics.json": self._json(diagnostics), "audit-summary.md": ("\n".join(summary)).encode("utf-8"), "run-diagnostics.md": ("\n".join(diagnostics_md)).encode("utf-8"), "audit.log": ("\n".join(log_lines) + "\n").encode("utf-8")}

    @staticmethod
    def _attributions(failed_operations: list[dict]) -> list[dict]:
        if not failed_operations:
            return [{"layer": "unattributed", "confidence": "low", "reason": "No operation failed; no attributable fault was observed", "recommendation": "No runtime fix is indicated by this run; continue checking the product rule against coverage and assessment results"}]
        result = []
        mapping = {"BROWSER_SESSION_FAILED": "environment", "CREDENTIAL_CHANNEL_FAILED": "environment", "ACTION_ADAPTER_UNAVAILABLE": "browser_adapter", "REQUEST_RESULT_UNKNOWN": "browser_adapter", "STALE_STATE": "host_contract", "INVALID_REQUEST": "host_contract", "LEDGER_EXPORT_FAILED": "host_contract", "DECISION_PENDING": "agent_strategy", "CASE_ACTIVE": "agent_strategy", "COVERAGE_INVALID": "agent_strategy", "AUDIT_PROGRESS_REQUIRED": "agent_strategy", "AGENT_CONTROL_BUDGET_EXCEEDED": "agent_strategy", "RULE_CONTRACT_UNAVAILABLE": "rule_contract", "RULE_CONTRACT_INTEGRITY_FAILED": "rule_contract", "AGENT_LEASE_EXPIRED": "transport_runtime", "AGENT_RUNTIME_EXITED": "transport_runtime", "PERSISTENT_WRITE_OBSERVED": "target_application"}
        recommendations = {"environment": "Check dependencies, browser processes, permissions, and the credential channel", "browser_adapter": "Reproduce the page interaction and fix browser adaptation or result confirmation", "host_contract": "Check protocol input, state versions, and Host transaction/export gates", "agent_strategy": "Resume from durable progress and close Cases, PendingDecisions, and coverage declarations", "rule_contract": "Validate the frozen rule document, summary, and version registry", "transport_runtime": "Check MCP/Router lifecycle, lease renewal, and process exit causes", "target_application": "Confirm whether the target page triggered persistence or other unexpected behavior", "unattributed": "Retain operation and event references for further reproduction"}
        for operation in failed_operations:
            code = (operation.get("reason") or {}).get("code", "")
            layer = mapping.get(code, "unattributed")
            item = {"layer": layer, "confidence": "medium" if layer != "unattributed" else "low", "reason": f"Operation {operation['operationId']} returned {code or 'unknown error'}", "recommendation": recommendations[layer], "operationRefs": [operation["operationId"]]}
            result.append(item)
        return result
