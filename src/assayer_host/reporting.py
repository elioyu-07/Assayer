from __future__ import annotations

import hashlib
import json
import re


class DerivedReportBuilder:
    """Pure deterministic views over a validated AuditLedger."""

    VERSION = "1.0.0"

    @staticmethod
    def _json(value: dict) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _text(value) -> str:
        return " ".join(str(value or "").replace("<", "‹").replace(">", "›").split())

    @classmethod
    def _diary_text(cls, value) -> str:
        text = cls._text(value)
        return re.sub(
            r"(?i)(password|passwd|token|secret|cookie|authorization)\s*[=:]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            text,
        )

    def render(self, ledger: dict, platform_artifacts: tuple[str, ...] = ()) -> dict[str, bytes]:
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
        attributions = self._attributions(
            failed_operations, scan.get("terminalReason"), scan.get("status"),
        )
        diagnostics = {"schemaVersion": self.VERSION, "sourceLedger": "audit-ledger.json", "sourceLedgerDigest": ledger_digest, "scanId": scan["scanId"], "runId": scan["runId"], "scanStatus": scan["status"], "conclusionsValid": effective_valid, "terminalReason": scan["terminalReason"], "coverageProof": scan["coverageProof"], "assessmentResultCounts": result_counts, "failedOperations": failed_operations, "assessmentTimelines": timelines, "attributions": attributions}

        proof = scan["coverageProof"]
        remaining_refs = set(proof.get("unprocessedEntrypointRefs", []))
        skipped_reasons = {
            item.get("entrypointId"): item.get("reason", {})
            for item in proof.get("skippedEntrypoints", [])
            if isinstance(item, dict)
        }
        entrypoints = {item["entrypointId"]: item for item in ledger["entrypoints"]}
        unfinished = [entrypoints[ref] for ref in sorted(remaining_refs) if ref in entrypoints]
        skipped_items = [
            (entrypoints.get(ref, {"entrypointId": ref}), reason)
            for ref, reason in skipped_reasons.items()
            if ref not in remaining_refs
        ]
        review_items = []
        decision_items = []
        for assessment in assessments:
            target = objects.get(assessment.get("objectRef"), {})
            page = pages.get(target.get("pageStateRef"), {})
            decision_items.append({
                "result": assessment.get("result", "unknown"),
                "rule": assessment.get("rule", {}),
                "location": page.get("route") or page.get("title") or page.get("url") or "Unknown page",
                "objectKind": target.get("kind", "unknown object"),
                "reason": assessment.get("reasonText") or "No decision explanation was recorded.",
                "validity": (
                    "valid" if effective_valid and assessment.get("conclusionValidity") == "valid"
                    else "invalidated"
                ),
            })
            if assessment.get("result") != "needs_review":
                continue
            blocker = assessment.get("blocker") if isinstance(assessment.get("blocker"), dict) else {}
            review_items.append({
                "rule": assessment.get("rule", {}),
                "location": page.get("route") or page.get("title") or page.get("url") or "Unknown page",
                "objectKind": target.get("kind", "unknown object"),
                "message": blocker.get("message") or assessment.get("reasonText") or "A required fact remains unresolved.",
                "unresolvedDimensions": assessment.get("coverage", {}).get("unresolvedDimensions", []),
                "evidenceCount": len(assessment.get("evidenceRefs", [])),
                "caseCount": len(assessment.get("caseRefs", [])),
            })
        next_step = self._result_next_step(scan["status"], len(issue_items), review_items, unfinished)
        summary = [
            "# Assayer Audit Result", "", "## Outcome", "",
            f"- Status: **{scan['status']}**",
            f"- Formal conclusions: **{'valid' if effective_valid else 'invalid'}**",
            f"- Reason: {self._diary_text(scan['terminalReason']['message'])}",
            f"- Next step: {next_step}",
            "", "## Recorded Decision Counts", "",
            (
                "These counts are formal results."
                if effective_valid else
                "These counts are diagnostic only because the Run's conclusions are invalid."
            ), "",
            f"- Issues found: {result_counts['issue_found']}",
            f"- No issue: {result_counts['scanned_no_issue']}",
            f"- Not applicable: {result_counts['not_applicable']}",
            f"- Needs review: {result_counts['needs_review']}",
            f"- Noise: {result_counts['noise']}",
        ]
        if platform_artifacts:
            summary.extend(["", "## Platform Trace", ""])
            summary.extend(f"- {name}" for name in platform_artifacts)
        summary.extend(["", "## Decisions"])
        if not decision_items:
            summary.extend(["", "No object-level decision was completed."])
        for index, item in enumerate(decision_items, 1):
            rule = item["rule"]
            summary.extend([
                "", f"### {index}. {self._text(item['result'])} — {self._text(item['objectKind'])} on {self._text(item['location'])}", "",
                f"- Rule: {self._text(rule.get('ruleId', 'unknown'))}@{self._text(rule.get('version', 'unknown'))}",
                f"- Conclusion validity: {self._text(item['validity'])}",
                f"- Why: {self._diary_text(item['reason'])}",
            ])
        summary.extend(["", "## Issues"])
        if not issue_items:
            no_issue_message = {
                "completed": "No issue was found in the completed decisions.",
                "partial": "No issue was found in the valid completed decisions; unfinished scope remains below.",
                "failed": "No issue conclusion is publishable because this Run failed.",
            }.get(scan["status"], "No valid issue is publishable from this Run.")
            summary.extend(["", no_issue_message])
        for index, issue in enumerate(issue_items, 1):
            summary.extend(["", f"### {index}. {self._text(issue['title'])}", "", f"- Severity: {issue['severity']}", f"- Rule: {issue['rule']['ruleId']}@{issue['rule']['version']}", f"- Page: {self._text(issue['page']['route'] or issue['page']['url'])}", f"- Issue: {self._text(issue['message'])}", f"- Impact: {self._text(issue['impact'])}", f"- Recommendation: {self._text(issue['recommendation'])}", f"- Screenshot: {issue['screenshotPath']}"])
        summary.extend(["", "## Needs Review"])
        if not review_items:
            summary.extend(["", "No item requires manual review."])
        for index, item in enumerate(review_items, 1):
            rule = item["rule"]
            dimensions = ", ".join(item["unresolvedDimensions"]) or "not specified"
            summary.extend([
                "", f"### {index}. {self._text(item['objectKind'])} on {self._text(item['location'])}", "",
                f"- Rule: {self._text(rule.get('ruleId', 'unknown'))}@{self._text(rule.get('version', 'unknown'))}",
                f"- Missing fact or blocker: {self._diary_text(item['message'])}",
                f"- Unresolved dimensions: {self._text(dimensions)}",
                f"- Checks completed: {item['evidenceCount']} evidence item(s) across {item['caseCount']} restored Case(s)",
                "- Resolution: Provide or enable the missing fact named above, then start a new audit for a formal decision.",
            ])
        summary.extend(["", "## Coverage", ""])
        summary.extend([
            f"- Pages visited: {len(proof.get('visitedPageStateRefs', []))}",
            f"- Objects decided: {len(proof.get('processedObjectRefs', []))}",
            f"- Entrypoints processed: {len(proof.get('processedEntrypointRefs', []))}",
            f"- Entrypoints skipped: {len(proof.get('skippedEntrypoints', []))}",
            f"- Entrypoints remaining: {len(proof.get('unprocessedEntrypointRefs', []))}",
            "", "### Pages", "",
        ])
        visited_refs = set(proof.get("visitedPageStateRefs", []))
        visited_pages = [page for ref, page in sorted(pages.items()) if ref in visited_refs]
        if visited_pages:
            summary.extend(
                f"- {self._text(page.get('route') or page.get('url') or page.get('title') or 'Unknown page')}"
                + (f" — {self._text(page['title'])}" if page.get("title") else "")
                for page in visited_pages
            )
        else:
            summary.append("- No page state was reached.")
        summary.extend(["", "### Rules", ""])
        for item in proof["ruleSummaries"]:
            summary.append(f"- {item['rule']['ruleId']}@{item['rule']['version']}: {item['assessmentCount']} assessments, {'complete' if item['coverageComplete'] else 'incomplete'} coverage")
        summary.extend(["", "## Unfinished Scope"])
        if not unfinished and not skipped_items and all(item.get("coverageComplete") for item in proof["ruleSummaries"]):
            summary.extend(["", "No unfinished or explicitly skipped scope remains."])
        for item in unfinished:
            reason = item.get("reason", {}) if isinstance(item.get("reason"), dict) else {}
            detail = reason.get("message") or "The entrypoint was not processed before the audit ended."
            summary.extend(["", f"- Remaining: {self._text(item.get('label') or item.get('kind') or item['entrypointId'])} — {self._diary_text(detail)}"])
        for item, reason in skipped_items:
            detail = reason.get("message") if isinstance(reason, dict) else None
            summary.extend(["", f"- Skipped: {self._text(item.get('label') or item.get('kind') or item['entrypointId'])} — {self._diary_text(detail or 'The entrypoint was explicitly excluded.')}"])
        for item in proof["ruleSummaries"]:
            if not item.get("coverageComplete"):
                summary.extend(["", f"- Incomplete rule: {item['rule']['ruleId']}@{item['rule']['version']}"])
        summary.extend([
            "", "## Technical Trace", "",
            f"- Scan: {scan['scanId']}",
            f"- Ledger digest: {ledger_digest}",
        ])
        summary.append("")
        diagnostics_md = ["# Assayer Run Diagnostics", "", f"- Terminal status: {scan['status']}", f"- Reason: {self._text(scan['terminalReason']['message'])}", f"- Unprocessed entrypoints: {len(scan['coverageProof'].get('unprocessedEntrypointRefs', []))}", f"- Failed/rejected operations: {len(failed_operations)}", f"- Assessment timeline entries: {len(timelines)}"]
        if platform_artifacts:
            diagnostics_md.extend(["", "## Platform Trace", ""])
            diagnostics_md.extend(f"- {name}" for name in platform_artifacts)
        diagnostics_md.extend(["", "## Failure Attribution", ""])
        diagnostics_md.extend([f"- {item['layer']} ({item['confidence']}): {self._text(item['reason'])}; recommendation: {self._text(item['recommendation'])}" for item in attributions])
        diagnostics_md.append("")
        log_lines = self._render_run_diary(scan, ledger["operations"], result_counts)
        return {"issues.json": self._json(issues_view), "page-element-judgement.json": self._json(judgement), "run-diagnostics.json": self._json(diagnostics), "audit-summary.md": ("\n".join(summary)).encode("utf-8"), "run-diagnostics.md": ("\n".join(diagnostics_md)).encode("utf-8"), "audit.log": ("\n".join(log_lines) + "\n").encode("utf-8")}

    @classmethod
    def _render_run_diary(cls, scan: dict, operations: list[dict], result_counts: dict[str, int]) -> list[str]:
        """Render a compact, human-readable timeline without exposing secrets."""
        started = scan.get("startedAt", "unknown")
        ended = scan.get("endedAt", "unknown")
        status = scan.get("status", "unknown")
        valid = "yes" if scan.get("conclusionsValid") else "no"
        lines = [
            "Assayer Run Diary",
            "=================",
            f"Scan: {cls._text(scan.get('scanId'))}",
            f"Run: {cls._text(scan.get('runId'))}",
            f"Status: {cls._text(status)} (conclusions valid: {valid})",
            f"Started: {cls._text(started)}",
            f"Ended: {cls._text(ended)}",
            f"Decisions: {sum(result_counts.values())} "
            f"(issues: {result_counts.get('issue_found', 0)}, "
            f"no issue: {result_counts.get('scanned_no_issue', 0)}, "
            f"needs review: {result_counts.get('needs_review', 0)})",
            "",
            "Timeline",
            "--------",
        ]
        ordered = sorted(
            operations,
            key=lambda value: (value.get("acceptedAtRevision", 0), value.get("operationId", "")),
        )
        groups: list[tuple[str, list[dict]]] = []
        positions: dict[str, int] = {}
        for index, item in enumerate(ordered):
            turn_id = item.get("agentTurnId")
            key = turn_id if isinstance(turn_id, str) and turn_id else f"operation:{index}"
            if key not in positions:
                positions[key] = len(groups)
                groups.append((key, []))
            groups[positions[key]][1].append(item)
        for group_key, items in groups:
            first = items[0]
            timestamp = cls._text(first.get("acceptedAt", "unknown"))
            public_tool_match = re.search(r":([a-z][a-z0-9_]{1,63})$", group_key)
            public_tool = public_tool_match.group(1) if public_tool_match else cls._text(first.get("tool", "unknown"))
            states = {item.get("status") for item in items}
            if "result_unknown" in states:
                state = "UNKNOWN"
            elif states.intersection({"rejected", "failed_known"}):
                state = "FAILED"
            elif states == {"succeeded"}:
                state = "SUCCEEDED"
            else:
                state = "RUNNING"
            duration = sum(int(item.get("durationMs", 0)) for item in items if isinstance(item.get("durationMs"), (int, float)))
            unit = "step" if len(items) == 1 else "steps"
            trace = f"turn={cls._text(group_key)}" if not group_key.startswith("operation:") else "Host-only operation"
            lines.append(f"{timestamp} | {state:<9} | {public_tool} ({len(items)} Host {unit}, {duration} ms) [{trace}]")
            rationale = first.get("decisionReason")
            if isinstance(rationale, str) and rationale.strip():
                lines.append(f"  Goal: {cls._diary_text(rationale)}")
            else:
                lines.append("  Goal: No public Agent rationale was supplied.")
            for item in items:
                operation_id = cls._text(item.get("operationId"))
                tool = cls._text(item.get("tool", "unknown"))
                item_state = cls._text(item.get("status", "unknown")).upper()
                item_duration = item.get("durationMs")
                elapsed = f", {int(item_duration)} ms" if isinstance(item_duration, (int, float)) else ""
                lines.append(f"  - {item_state}: {tool}{elapsed} — {cls._operation_diary_detail(item)} [{operation_id}]")
        proof = scan.get("coverageProof") if isinstance(scan.get("coverageProof"), dict) else {}
        lines.extend([
            "",
            "Coverage",
            "--------",
            f"Pages visited: {len(proof.get('visitedPageStateRefs', []))}",
            f"Objects decided: {len(proof.get('processedObjectRefs', []))}",
            f"Entrypoints: {len(proof.get('processedEntrypointRefs', []))} processed, "
            f"{len(proof.get('skippedEntrypoints', []))} skipped, "
            f"{len(proof.get('unprocessedEntrypointRefs', []))} remaining",
            "",
            f"Terminal state: {cls._text(status)} — {cls._diary_text(scan.get('terminalReason', {}).get('message'))}",
            "Next: Review audit-summary.md and run-diagnostics.md.",
        ])
        return lines

    @classmethod
    def _operation_diary_detail(cls, operation: dict) -> str:
        status = operation.get("status")
        if status in {"rejected", "failed_known", "result_unknown"}:
            reason = operation.get("reason") or {}
            code = cls._text(reason.get("code", "OPERATION_FAILED"))
            message = cls._diary_text(reason.get("message", "The operation did not complete."))
            return f"Blocked or failed: {code} — {message}"
        descriptions = {
            "start_audit": "Started the audit and bound the target runtime.",
            "inspect_page": "Observed the current page and discovered safe entrypoints.",
            "discover_scope": "Advanced bounded discovery and removed duplicate logical entrypoints.",
            "explore_entrypoint": "Explored a safe entrypoint and recorded the resulting page state.",
            "inspect_object": "Verified an audit object and its applicable rules.",
            "begin_case": "Started a bounded evidence-gathering Case.",
            "perform_action": "Executed a controlled investigation action.",
            "observe_page": "Captured aligned page and visual evidence.",
            "capture_evidence": "Captured Host-verified evidence.",
            "restore_case": "Restored the original page and object context.",
            "record_findings": "Recorded Findings for the frozen rule dimensions.",
            "prepare_decision": "Validated evidence, coverage, and the proposed decision.",
            "commit_decision": "Committed the evidence-backed decision.",
            "get_audit_progress": "Rebuilt progress from the durable audit ledger.",
            "complete_audit": "Validated coverage and published audit artifacts.",
        }
        action = descriptions.get(str(operation.get("tool")), "Completed the requested audit operation.")
        return action

    @staticmethod
    def _result_next_step(status: str, issue_count: int, review_items: list[dict], unfinished: list[dict]) -> str:
        if status == "failed":
            return "Fix the failure described above, then start a new audit; do not use conclusions from this Run."
        if status == "partial":
            if review_items:
                return "Resolve the needs-review blockers and remaining scope below, then start a new audit if complete coverage is required."
            if unfinished:
                return "Review the remaining entrypoints below and start a new audit if complete coverage is required."
            return "Review the incomplete coverage below and start a new audit if complete coverage is required."
        if issue_count:
            return "Address the published issues, then run the audit again to verify the remediation."
        return "No remediation is required for the rules and scope checked."

    @staticmethod
    def _attributions(
        failed_operations: list[dict], terminal_reason: dict | None = None,
        scan_status: str | None = None,
    ) -> list[dict]:
        mapping = {"BROWSER_SESSION_FAILED": "environment", "CREDENTIAL_CHANNEL_FAILED": "environment", "ACTION_ADAPTER_UNAVAILABLE": "browser_adapter", "REQUEST_RESULT_UNKNOWN": "browser_adapter", "STALE_STATE": "host_contract", "INVALID_REQUEST": "host_contract", "LEDGER_EXPORT_FAILED": "host_contract", "DECISION_PENDING": "agent_strategy", "CASE_ACTIVE": "agent_strategy", "COVERAGE_INVALID": "agent_strategy", "AUDIT_PROGRESS_REQUIRED": "agent_strategy", "AGENT_CONTROL_BUDGET_EXCEEDED": "agent_strategy", "RULE_CONTRACT_UNAVAILABLE": "rule_contract", "RULE_CONTRACT_INTEGRITY_FAILED": "rule_contract", "AGENT_LEASE_EXPIRED": "transport_runtime", "AGENT_RUNTIME_EXITED": "transport_runtime", "PERSISTENT_WRITE_OBSERVED": "target_application"}
        recommendations = {"environment": "Check dependencies, browser processes, permissions, and the credential channel", "browser_adapter": "Reproduce the page interaction and fix browser adaptation or result confirmation", "host_contract": "Check protocol input, state versions, and Host transaction/export gates", "agent_strategy": "Resume from durable progress and close Cases, PendingDecisions, and coverage declarations", "rule_contract": "Validate the frozen rule document, summary, and version registry", "transport_runtime": "Check MCP/Router lifecycle, lease renewal, and process exit causes", "target_application": "Confirm whether the target page triggered persistence or other unexpected behavior", "unattributed": "Retain operation and event references for further reproduction"}
        if not failed_operations:
            reason = terminal_reason if isinstance(terminal_reason, dict) else {}
            code = reason.get("code", "")
            if scan_status == "failed" and code:
                layer = mapping.get(code, "unattributed")
                return [{
                    "layer": layer,
                    "confidence": "medium" if layer != "unattributed" else "low",
                    "reason": f"Run ended with {code}: {reason.get('message') or 'No further detail was recorded'}",
                    "recommendation": recommendations[layer],
                }]
            if scan_status == "partial":
                return [{
                    "layer": "unattributed", "confidence": "low",
                    "reason": f"No Host operation failed; the Run is partial because {reason.get('message') or 'coverage is incomplete'}",
                    "recommendation": "Review the disclosed unfinished scope and retry only if complete coverage is required",
                }]
            return [{"layer": "unattributed", "confidence": "low", "reason": "No failed operation or terminal fault was recorded", "recommendation": "No runtime recovery action is required"}]
        result = []
        for operation in failed_operations:
            code = (operation.get("reason") or {}).get("code", "")
            layer = mapping.get(code, "unattributed")
            item = {"layer": layer, "confidence": "medium" if layer != "unattributed" else "low", "reason": f"Operation {operation['operationId']} returned {code or 'unknown error'}", "recommendation": recommendations[layer], "operationRefs": [operation["operationId"]]}
            result.append(item)
        return result
