"""Product runtime assembly for a real URL (B10)."""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse

from .auth import LoginResult
from .browser_readonly import BrowserReadOnlyPageAdapter, PlaywrightBrowserBackend
from .browser_recovery import create_recoverable_browser_adapter_bundle
from .browser_session import BrowserProfile, BrowserSession
from .core import HostCore
from .errors import HostError
from .action_safety import ActionSafetyPolicy
from .evidence import EvidenceSanitizer
from .runtime_rules import RuleEvaluationEngine


class AnonymousBrowserLoginAdapter:
    """Navigate to a public URL; no credential value crosses the Core boundary."""

    def __init__(self, page, guard=None):
        self._page = page
        self._guard = guard

    def authenticate_anonymous(self, url):
        if self._guard is None:
            self._page.navigate(url)
        else:
            with self._guard.operation("bootstrap", lambda req: ActionSafetyPolicy().classify_request(req, self._guard.allowed_origin)):
                self._page.navigate(url)
                self._page.settle_readonly()
        return LoginResult("succeeded", current_page_state_id="page-bootstrap-001",
                           capabilities=("runtime", "dom", "interaction", "visual"))


class BrowserHostRuntime:
    """Own one real Chromium Session and the HostCore using its adapters."""

    def __init__(self, url: str, output_dir: str | Path, *, profile: BrowserProfile | None = None):
        parsed = urlparse(url)
        try:
            origin = BrowserReadOnlyPageAdapter._origin(parsed)
        except HostError as error:
            raise ValueError("url must be an http(s) URL without credentials") from error
        self.entry_url = url
        self.output_dir = str(Path(output_dir).expanduser().resolve())
        scan_id = f"scan-{uuid.uuid4().hex}"
        self.session = BrowserSession(scan_id, profile or BrowserProfile(), backend=PlaywrightBrowserBackend())
        self.session.open()
        try:
            self.bundle = create_recoverable_browser_adapter_bundle(self.session, allowed_origin=origin)
            self.core = HostCore(
                login_adapter=AnonymousBrowserLoginAdapter(self.bundle.page, self.bundle.network_guard),
                page_adapter=self.bundle.page, object_identity_adapter=self.bundle.identity,
                action_adapter=self.bundle.action, recovery_adapter=self.bundle.recovery,
                evidence_adapter=self.bundle.evidence,
                scan_id_factory=lambda: scan_id,
                entrypoint_adapter=self.bundle.entrypoint,
            )
        except Exception:
            self.session.close()
            raise
        self._sanitizer = EvidenceSanitizer()
        self._rule_engine = RuleEvaluationEngine()
        self._bootstrap_key = None

    def handle(self, request: dict) -> dict:
        if request.get("tool") == "start_audit":
            request_input = request.get("input", {})
            requested_url = request_input.get("url")
            if requested_url != self.entry_url:
                raise HostError("INVALID_REQUEST", "start_audit URL 与运行时绑定 URL 不一致")
            if request_input.get("authMode") != "anonymous":
                raise HostError("INVALID_REQUEST", "真实 URL 运行时当前只接受 anonymous 模式")
            requested_output = request_input.get("outputDir")
            if not isinstance(requested_output, str) or str(Path(requested_output).expanduser().resolve()) != self.output_dir:
                raise HostError("INVALID_REQUEST", "start_audit outputDir 与运行时绑定目录不一致")
            if request_input.get("browserProfile") != "default":
                raise HostError("INVALID_REQUEST", "start_audit browserProfile 与运行时配置不一致")
            key = request.get("idempotencyKey")
            if self._bootstrap_key is not None and key != self._bootstrap_key:
                raise HostError("RUN_CONFLICT", "一个 BrowserHostRuntime 只允许一个 Scan")
        response = self.core.handle(request)
        if request.get("tool") == "start_audit":
            self._bootstrap_key = request.get("idempotencyKey")
        if request.get("tool") == "start_audit" and response.get("status") == "ok":
            if response["result"]["scanId"] != self.session.scan_id:
                self.session.close()
                raise HostError("INTERNAL_FAILURE", "Browser Session 与 Scan 绑定失败")
        if request.get("tool") == "complete_audit" and response.get("result", {}).get("scanStatus") in {"completed", "partial", "failed"}:
            self.session.close()
        return response

    def close(self):
        try:
            self.core.close()
        finally:
            self.session.close()

    def probe(self, url: str) -> dict:
        start = self.handle({
            "protocolVersion": "1.0", "requestId": "runtime-start", "agentTurnId": "runtime-turn-1",
            "tool": "start_audit", "idempotencyKey": "runtime-start",
            "input": {"url": url, "ruleRegistryVersion": "1.0.0", "outputDir": self.output_dir,
                      "browserProfile": "default", "authMode": "anonymous"},
        })
        if start.get("status") != "ok":
            return start
        scan = start["result"]
        page = self.handle({
            "protocolVersion": "1.0", "requestId": "runtime-page", "scanId": scan["scanId"], "runId": scan["runId"],
            "agentTurnId": "runtime-turn-2", "tool": "inspect_page", "idempotencyKey": "runtime-page",
            "expectedRunRevision": scan["runRevision"],
            "input": {"pageStateId": scan["currentPageStateId"], "include": ["route", "visibleText", "objects", "safeEntrypoints", "networkSummary"]},
        })
        if page.get("status") != "ok":
            return page
        result = {"scanId": scan["scanId"], "runId": scan["runId"], "runRevision": page["runRevision"],
                  "pageStateId": page["result"]["pageStateId"], "candidateRefs": page["result"]["candidateRefs"],
                  "entrypointRefs": page["result"]["entrypointRefs"], "route": page["result"].get("route"),
                  "title": page["result"].get("title"), "activeTab": page["result"].get("activeTab"),
                  "structureSummary": page["result"].get("structureSummary", {}),
                  "networkSummary": page["result"].get("networkSummary", {}), "pages": []}
        visited_tabs = set()
        current_page = page
        current_revision = page["runRevision"]
        for _ in range(16):
            active_tab = current_page["result"].get("activeTab")
            if active_tab:
                visited_tabs.add(active_tab)
            candidates = current_page["result"]["candidateRefs"]
            page_result = {"pageStateId": current_page["result"]["pageStateId"], "route": current_page["result"].get("route"),
                           "title": current_page["result"].get("title"), "activeTab": active_tab,
                           "candidateRefs": candidates, "entrypointRefs": current_page["result"]["entrypointRefs"],
                           "structureSummary": current_page["result"].get("structureSummary", {}),
                           "networkSummary": current_page["result"].get("networkSummary", {}),
                           "visibleTextPreview": self._sanitizer.sanitize(current_page["result"].get("visibleText", ""))[:2000]}
            if candidates:
                suffix = len(result["pages"])
                verified = self.handle({
                "protocolVersion": "1.0", "requestId": f"runtime-object-{suffix}", "scanId": scan["scanId"], "runId": scan["runId"],
                "agentTurnId": "runtime-turn-3", "tool": "inspect_object", "idempotencyKey": f"runtime-object-{suffix}",
                    "expectedRunRevision": current_revision, "input": {"candidateId": candidates[0]},
                })
                page_result["objectVerification"] = verified.get("result", verified)
                if suffix == 0:
                    result["objectVerification"] = page_result["objectVerification"]
                if verified.get("status") == "ok" and verified.get("result", {}).get("objectId"):
                    evidence = self.handle({
                    "protocolVersion": "1.0", "requestId": f"runtime-evidence-{suffix}", "scanId": scan["scanId"], "runId": scan["runId"],
                    "agentTurnId": "runtime-turn-4", "tool": "capture_evidence", "idempotencyKey": f"runtime-evidence-{suffix}",
                    "expectedRunRevision": verified["runRevision"],
                        "input": {"pageStateId": current_page["result"]["pageStateId"], "objectId": verified["result"]["objectId"], "includeRawVisual": True},
                    })
                    page_result["evidence"] = evidence.get("result", evidence)
                    if suffix == 0:
                        result["evidence"] = page_result["evidence"]
                    current_revision = evidence["runRevision"]
            result["pages"].append(page_result)
            entries = current_page["result"].get("entrypoints", [])
            tab_entries = [entry for entry in entries if entry.get("kind") == "tab"
                           and entry.get("label") not in visited_tabs and entry.get("status") != "processed"]
            if not tab_entries:
                break
            next_entry = tab_entries[0]
            explored = self.handle({
                "protocolVersion": "1.0", "requestId": f"runtime-tab-{len(result['pages'])}", "scanId": scan["scanId"], "runId": scan["runId"],
                "agentTurnId": "runtime-turn-tabs", "tool": "explore_entrypoint", "idempotencyKey": f"runtime-tab-{len(result['pages'])}",
                "expectedRunRevision": current_revision, "input": {"pageStateId": current_page["result"]["pageStateId"], "entrypointId": next_entry["entrypointId"]},
            })
            if explored.get("status") != "ok":
                result["explorationError"] = explored.get("error", explored)
                break
            current_page = explored
            current_revision = explored["runRevision"]
            scan = {**scan, "runRevision": explored["runRevision"], "currentPageStateId": explored["result"]["pageStateId"]}
        evidence_refs = [item["evidence"]["evidenceId"] for item in result["pages"] if item.get("evidence", {}).get("evidenceId")]
        result["runRevision"] = current_revision
        result["summary"] = {
            "visitedPageStates": len(result["pages"]),
            "tabsDiscovered": max((item.get("structureSummary", {}).get("tabs", 0) for item in result["pages"]), default=0),
            "ruleCandidateCount": sum(len(item.get("candidateRefs", [])) for item in result["pages"]),
            "evidenceCount": len(evidence_refs),
            "pagesWithVisibleErrors": sum(1 for item in result["pages"] if item.get("structureSummary", {}).get("errorCount", 0) > 0),
            "observedWrites": sum(item.get("networkSummary", {}).get("observedWrites", 0) for item in result["pages"]),
            "unknownRequests": sum(item.get("networkSummary", {}).get("unknownRequests", 0) for item in result["pages"]),
        }
        return {"protocolVersion": "1.0", "requestId": "runtime-probe", "status": "ok", "result": result,
                "evidenceRefs": evidence_refs, "diagnosticRefs": []}

    @staticmethod
    def _request(scan: dict, tool: str, key: str, revision: int, input_data: dict) -> dict:
        return {
            "protocolVersion": "1.0", "requestId": f"runtime-{key}",
            "scanId": scan["scanId"], "runId": scan["runId"],
            "agentTurnId": f"runtime-{tool}", "tool": tool,
            "idempotencyKey": key, "expectedRunRevision": revision, "input": input_data,
        }

    @staticmethod
    def _rule_ref(rule: dict) -> dict:
        return {"ruleId": rule["ruleId"], "version": rule["version"]}

    def audit(self, url: str) -> dict:
        """Run a generic, conservative URL audit through the complete lifecycle.

        The URL is only a runtime entrypoint. All object selection, action
        safety, evidence, recovery and ledger writes go through HostCore.
        """
        start = self.handle({
            "protocolVersion": "1.0", "requestId": "runtime-audit-start",
            "agentTurnId": "runtime-audit-bootstrap", "tool": "start_audit",
            "idempotencyKey": "runtime-audit-start",
            "input": {"url": url, "ruleRegistryVersion": self.core.rule_registry_version,
                      "outputDir": self.output_dir, "browserProfile": "default",
                      "authMode": "anonymous"},
        })
        if start.get("status") != "ok":
            return start
        scan = dict(start["result"])
        revision = scan["runRevision"]
        pages = []
        processed_objects = []
        assessment_records = []
        all_entrypoints = {}
        object_count = 0
        errors = []
        visited_labels = set()
        current = self.handle(self._request(scan, "inspect_page", "runtime-audit-page-0",
                                             revision, {"pageStateId": scan["currentPageStateId"],
                                                        "include": ["route", "visibleText", "objects", "safeEntrypoints", "networkSummary"]}))
        if current.get("status") != "ok":
            return current
        revision = current["runRevision"]
        page_count = 0
        while page_count < 16:
            page_result = current["result"]
            all_entrypoints.update({item["entrypointId"]: {**item, "pageStateRef": page_result["pageStateId"]}
                                    for item in page_result.get("entrypoints", [])})
            page_id = page_result["pageStateId"]
            active_tab = page_result.get("activeTab")
            if active_tab:
                visited_labels.add(active_tab)
            page_record = {
                "pageStateId": page_id, "route": page_result.get("route"),
                "activeTab": active_tab, "candidateRefs": list(page_result.get("candidateRefs", [])),
                "entrypointRefs": list(page_result.get("entrypointRefs", [])),
                "decisions": [], "processedCandidateRefs": [],
            }
            for candidate_id in page_result.get("candidateRefs", []):
                verified = self.handle(self._request(scan, "inspect_object",
                    f"runtime-audit-object-{page_count}-{len(page_record['decisions'])}",
                    revision, {"candidateId": candidate_id}))
                if verified.get("status") != "ok" or verified.get("result", {}).get("rebindStatus") != "matched":
                    errors.append(verified.get("error", {"code": "OBJECT_NOT_VERIFIED"}))
                    continue
                verified_result = verified["result"]
                object_id = verified_result["objectId"]
                object_count += 1
                rules = verified_result.get("potentialRules", [])
                decisions_before = len(page_record["decisions"])
                for rule_index, rule in enumerate(rules):
                    rule_contract = next((
                        item for item in scan.get("frozenRules", [])
                        if self._rule_ref(item) == self._rule_ref(rule)
                    ), None)
                    if not rule_contract or not rule_contract.get("coverageDimensions"):
                        errors.append({"code": "RULE_CONTRACT_UNAVAILABLE",
                                       "message": "冻结规则缺少最低覆盖维度"})
                        continue
                    case = self.handle(self._request(scan, "begin_case",
                        f"runtime-audit-case-{page_count}-{object_count}-{rule_index}",
                        revision, {"objectId": object_id, "rule": self._rule_ref(rule),
                                   "kind": "observation",
                                   "purpose": "读取运行态对象事实并验证恢复屏障",
                                   "plannedCoverageDimensions": list(rule_contract["coverageDimensions"])}))
                    if case.get("status") != "ok":
                        errors.append(case.get("error", {"code": "CASE_BEGIN_FAILED"}))
                        continue
                    case_result = case["result"]
                    revision = case_result["runRevision"]
                    action = self.handle(self._request(scan, "perform_action",
                        f"runtime-audit-action-{page_count}-{object_count}-{rule_index}", revision,
                        {"pageStateId": page_id, "caseId": case_result["caseId"], "objectId": object_id,
                         "type": "focus", "intent": "观察目标对象", "parameters": {}}))
                    if action.get("status") != "ok":
                        errors.append(action.get("error", {"code": "ACTION_FAILED"}))
                        revision = action.get("runRevision", revision)
                        cleanup = self.handle(self._request(scan, "restore_case",
                            f"runtime-audit-cleanup-action-{page_count}-{object_count}-{rule_index}", revision,
                            {"caseId": case_result["caseId"], "pageStateId": page_id, "objectId": object_id,
                             "fallback": "refresh_and_replay_safe_entrypoints"}))
                        revision = cleanup.get("runRevision", revision)
                        if cleanup.get("status") != "ok":
                            errors.append(cleanup.get("error", {"code": "RESTORE_FAILED"}))
                        continue
                    revision = action["runRevision"]
                    evidence_response = self.handle(self._request(scan, "capture_evidence",
                        f"runtime-audit-evidence-{page_count}-{object_count}-{rule_index}", revision,
                        {"pageStateId": page_id, "objectId": object_id,
                         "caseId": case_result["caseId"], "includeRawVisual": False}))
                    if evidence_response.get("status") != "ok":
                        errors.append(evidence_response.get("error", {"code": "EVIDENCE_FAILED"}))
                        revision = evidence_response.get("runRevision", revision)
                        cleanup = self.handle(self._request(scan, "restore_case",
                            f"runtime-audit-cleanup-evidence-{page_count}-{object_count}-{rule_index}", revision,
                            {"caseId": case_result["caseId"], "pageStateId": page_id, "objectId": object_id,
                             "fallback": "refresh_and_replay_safe_entrypoints"}))
                        revision = cleanup.get("runRevision", revision)
                        if cleanup.get("status") != "ok":
                            errors.append(cleanup.get("error", {"code": "RESTORE_FAILED"}))
                        continue
                    revision = evidence_response["runRevision"]
                    evidence_id = evidence_response["result"]["evidenceId"]
                    evaluation = self._rule_engine.evaluate(rule, evidence_response["result"].get("evidence", {}))
                    result, reason, blocker = evaluation.result, evaluation.reason, evaluation.blocker
                    visual_response = self.handle(self._request(scan, "capture_evidence",
                        f"runtime-audit-visual-{page_count}-{object_count}-{rule_index}", revision,
                        {"pageStateId": page_id, "objectId": object_id,
                         "caseId": case_result["caseId"], "includeRawVisual": True}))
                    visual_result = visual_response.get("result", {})
                    if visual_response.get("status") == "ok":
                        revision = visual_response["runRevision"]
                    else:
                        errors.append(visual_response.get("error", {"code": "RAW_VISUAL_FAILED"}))
                    restored = self.handle(self._request(scan, "restore_case",
                        f"runtime-audit-restore-{page_count}-{object_count}-{rule_index}", revision,
                        {"caseId": case_result["caseId"], "pageStateId": page_id, "objectId": object_id,
                         "fallback": "refresh_and_replay_safe_entrypoints"}))
                    if restored.get("status") != "ok":
                        errors.append(restored.get("error", {"code": "RESTORE_FAILED"}))
                        continue
                    revision = restored["runRevision"]
                    dimensions = list(rule_contract["coverageDimensions"])
                    statuses = {dimension: "satisfied" for dimension in dimensions}
                    if result == "issue_found" and dimensions:
                        statuses[dimensions[-1]] = "violated"
                    elif result == "needs_review" and dimensions:
                        statuses[dimensions[-1]] = "blocked" if blocker else "unresolved"
                    finding_response = self.handle(self._request(scan, "record_findings",
                        f"runtime-audit-findings-{page_count}-{object_count}-{rule_index}", revision,
                        {"objectId":object_id, "rule":self._rule_ref(rule),
                         "findings":[{"dimension":dimension, "status":status, "reasonText":reason,
                                      "evidenceRefs":[evidence_id], "caseRefs":[case_result["caseId"]]}
                                     for dimension, status in statuses.items()]}))
                    if finding_response.get("status") != "ok":
                        errors.append(finding_response.get("error", {"code":"FINDING_RECORD_FAILED"}))
                        continue
                    revision = finding_response["runRevision"]
                    prepare_input = {"objectId": object_id, "rule": self._rule_ref(rule),
                                 "result": result, "reasonText": reason,
                                 "findingRefs":finding_response["result"]["findingRefs"],
                                 "evidenceRefs": [evidence_id], "caseRefs": [case_result["caseId"]]}
                    if blocker:
                        prepare_input["blocker"] = blocker
                    prepared = self.handle(self._request(scan, "prepare_decision",
                        f"runtime-audit-prepare-{page_count}-{object_count}-{rule_index}", revision, prepare_input))
                    if prepared.get("status") != "ok":
                        errors.append(prepared.get("error", {"code": "DECISION_PREPARE_FAILED"}))
                        continue
                    revision = prepared["runRevision"]
                    committed = self.handle(self._request(scan, "commit_decision",
                        f"runtime-audit-commit-{page_count}-{object_count}-{rule_index}", revision,
                        {"pendingDecisionId": prepared["result"]["pendingDecisionId"]}))
                    if committed.get("status") != "ok":
                        errors.append(committed.get("error", {"code": "DECISION_COMMIT_FAILED"}))
                        continue
                    revision = committed["runRevision"]
                    decision_record = {"objectId": object_id, "rule": self._rule_ref(rule),
                                   "result": result, "evidenceId": evidence_id,
                                   "assessmentId": committed["result"].get("assessmentId"),
                                   "coverageComplete": result in {"scanned_no_issue", "issue_found"}}
                    if visual_result.get("evidenceId"):
                        decision_record["visualEvidenceId"] = visual_result["evidenceId"]
                    if visual_result.get("screenshotRef"):
                        decision_record["rawVisualRef"] = visual_result["screenshotRef"]
                    page_record["decisions"].append(decision_record)
                    assessment_records.append(decision_record)
                if rules and len(page_record["decisions"]) - decisions_before == len(rules):
                    page_record["processedCandidateRefs"].append(candidate_id)
                    processed_objects.append(object_id)
            pages.append(page_record)
            page_count += 1
            tabs = [entry for entry in page_result.get("entrypoints", []) if entry.get("kind") == "tab"
                    and entry.get("label") not in visited_labels and entry.get("status") != "processed"]
            if not tabs:
                break
            next_entry = tabs[0]
            explored = self.handle(self._request(scan, "explore_entrypoint",
                f"runtime-audit-tab-{page_count}", revision,
                {"pageStateId": page_id, "entrypointId": next_entry["entrypointId"]}))
            if explored.get("status") != "ok":
                errors.append(explored.get("error", {"code": "TAB_EXPLORE_FAILED"}))
                break
            revision = explored["runRevision"]
            scan["currentPageStateId"] = explored["result"]["pageStateId"]
            current = explored
        all_entries = list(all_entrypoints.values())
        # A discovered tab is considered processed once its label was visited;
        # this avoids treating the same tab choices rendered in each SPA state
        # as separate unhandled business work.
        visited_pages = {item["pageStateId"] for item in pages
                         if len(item["processedCandidateRefs"]) == len(item["candidateRefs"])}
        processed_entries = [item["entrypointId"] for item in all_entries
                             if item.get("kind") == "safe_action" and item.get("pageStateRef") in visited_pages]
        processed_entries += [item["entrypointId"] for item in all_entries
                              if item.get("kind") == "tab" and item.get("label") in visited_labels]
        processed_entries = sorted(set(processed_entries))
        unprocessed_entries = sorted({item["entrypointId"] for item in all_entries} - set(processed_entries))
        summaries = []
        for rule in scan.get("frozenRules", []):
            ref = self._rule_ref(rule)
            found = [item for item in assessment_records if item.get("rule") == ref]
            counts = {name: sum(1 for item in found if item.get("result") == name)
                      for name in ("issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise")}
            summaries.append({"rule": ref, "assessmentCount": len(found), "resultCounts": counts,
                              "coverageComplete": bool(found) and all(item.get("coverageComplete") for item in found)})
        completion = self.handle(self._request(scan, "complete_audit", "runtime-audit-complete",
            revision, {"visitedPageStateRefs": [item["pageStateId"] for item in pages],
                       "processedObjectRefs": sorted(set(processed_objects)),
                       "processedEntrypointRefs": processed_entries, "skippedEntrypoints": [],
                       "ruleSummaries": summaries, "unprocessedEntrypointRefs": unprocessed_entries,
                       "completionReason": "通用规则驱动运行器完成页面探索、对象 Case、Evidence、恢复、判定和收束"}))
        return {"protocolVersion": "1.0", "requestId": "runtime-audit", "status": completion.get("status", "failed"),
                "result": {"start": start.get("result"), "pages": pages,
                           "completion": completion.get("result", completion.get("error")),
                           "errors": errors, "objectCount": object_count,
                           "assessmentCount": len(assessment_records)}}
