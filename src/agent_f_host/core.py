from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, RefResolver

from .auth import CredentialVault, LoginAdapter, LoginCoordinator, UnavailableLoginAdapter
from .errors import HostError
from .page import ReadOnlyPageAdapter, UnavailablePageAdapter
from .object_identity import ObjectIdentityAdapter, UnavailableObjectIdentityAdapter
from .action_safety import (ActionExecution, ActionSafetyPolicy,
                            SafeActionAdapter, UnavailableActionAdapter)
from .recovery import RECOVERY_DIMENSIONS, RecoveryAdapter, RecoveryAttempt, RecoveryCheck, UnavailableRecoveryAdapter
from .evidence import EvidenceAdapter, EvidenceSanitizer, UnavailableEvidenceAdapter
from .reporting import DerivedReportBuilder
from .store import SQLiteStore


TOOL_KINDS = {
    "start_audit": "bootstrap", "inspect_page": "read", "inspect_object": "read",
    "begin_case": "lifecycle", "perform_action": "browser_action", "restore_case": "recovery",
    "inspect_source": "read", "capture_evidence": "read", "prepare_decision": "decision_preparation",
    "commit_decision": "decision_commit", "complete_audit": "lifecycle",
}


class HostCore:
    """Protocol boundary with durable Scan/Operation state and fail-closed adapters."""

    def __init__(self, schema_root: str | Path | None = None, *, store: SQLiteStore | None = None,
                 credential_vault: CredentialVault | None = None, login_adapter: LoginAdapter | None = None,
                 page_adapter: ReadOnlyPageAdapter | None = None, object_identity_adapter: ObjectIdentityAdapter | None = None,
                 action_adapter: SafeActionAdapter | None = None, action_policy: ActionSafetyPolicy | None = None,
                 recovery_adapter: RecoveryAdapter | None = None, evidence_adapter: EvidenceAdapter | None = None,
                 evidence_sanitizer: EvidenceSanitizer | None = None, report_builder: DerivedReportBuilder | None = None,
                 login_coordinator: LoginCoordinator | None = None):
        root = Path(schema_root or Path(__file__).resolve().parents[2] / "schemas")
        schemas = {}
        for path in root.rglob("*.schema.json"):
            data = json.loads(path.read_text())
            schemas[data["$id"]] = data
            schemas[path.name] = data
        envelope = schemas["envelope.schema.json"]
        self._envelope_validator = Draft202012Validator(envelope, resolver=RefResolver(envelope["$id"], envelope, store=schemas))
        contracts = schemas["tool-contracts.schema.json"]
        def contract_validator(name):
            wrapper = {"$schema":"https://json-schema.org/draft/2020-12/schema", "$ref":f"{contracts['$id']}#/$defs/{name}"}
            return Draft202012Validator(wrapper, resolver=RefResolver.from_schema(wrapper, store=schemas))
        self._contract_validator = contract_validator
        def entity_validator(name):
            schema = schemas[name]
            return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))
        self._page_state_validator = entity_validator("page-state.schema.json")
        self._entrypoint_validator = entity_validator("entrypoint.schema.json")
        self._candidate_validator = entity_validator("page-candidate.schema.json")
        self._audit_object_validator = entity_validator("audit-object.schema.json")
        self._object_verification_validator = entity_validator("object-verification.schema.json")
        self._case_validator = entity_validator("reverse-case.schema.json")
        self._action_attempt_validator = entity_validator("action-attempt.schema.json")
        self._request_observation_validator = entity_validator("request-observation.schema.json")
        self._evidence_validator = entity_validator("evidence.schema.json")
        self._screenshot_validator = entity_validator("screenshot.schema.json")
        self._pending_decision_validator = entity_validator("pending-decision.schema.json")
        self._assessment_validator = entity_validator("rule-assessment.schema.json")
        self._issue_validator = entity_validator("issue.schema.json")
        self._scan_run_validator = entity_validator("scan-run.schema.json")
        self._ledger_validator = entity_validator("audit-ledger.schema.json")
        self._derived_output_validators = {"issues.json": entity_validator("derived-issues.schema.json"), "page-element-judgement.json": entity_validator("page-element-judgement.schema.json"), "run-diagnostics.json": entity_validator("run-diagnostics.schema.json")}
        self._store = store or SQLiteStore()
        self._credential_vault = credential_vault or CredentialVault()
        self._login_adapter = login_adapter or UnavailableLoginAdapter()
        self._login_coordinator = login_coordinator or LoginCoordinator()
        self._page_adapter = page_adapter or UnavailablePageAdapter()
        self._object_identity_adapter = object_identity_adapter or UnavailableObjectIdentityAdapter()
        self._action_adapter = action_adapter or UnavailableActionAdapter()
        self._action_policy = action_policy or ActionSafetyPolicy()
        self._recovery_adapter = recovery_adapter or UnavailableRecoveryAdapter()
        self._evidence_adapter = evidence_adapter or UnavailableEvidenceAdapter()
        self._evidence_sanitizer = evidence_sanitizer or EvidenceSanitizer()
        self._report_builder = report_builder or DerivedReportBuilder()
        registry_path = root.parent / "rules" / "registry.json"
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {"registryVersion":"0.0.0","digest":"0"*64}
        self._rule_registry = registry
        self._rule_registry_version = registry["registryVersion"]
        self._rule_registry_digest = registry["digest"]
        self._rules_by_kind = {}
        self._rules = {}
        for rule in registry.get("rules", []):
            if rule.get("status") != "enabled":
                continue
            self._rules[(rule["ruleId"], rule["version"])] = rule
            for kind in rule["objectKinds"]:
                self._rules_by_kind.setdefault(kind, []).append({"ruleId":rule["ruleId"],"version":rule["version"]})
        self._recover_interrupted_operations()

    @property
    def credential_vault(self) -> CredentialVault:
        return self._credential_vault

    def _recover_interrupted_operations(self):
        interrupted = self._store.get_interrupted_operations()
        for row in interrupted:
            scan = self._scan_from_row(self._store.get_scan(row["scan_id"]))
            case = self._store.get_case(row.get("case_ref")) if row.get("case_ref") else None
            scan["runRevision"] += 1
            scan["status"] = "failed"
            row.update({"status": "result_unknown", "error_code": "REQUEST_RESULT_UNKNOWN", "error_message": "Host 在 Operation 执行期间重启，无法证明动作或恢复是否发送/完成"})
            if case:
                case.update({"status": "invalidated", "endedAt": self._now(), "restoreReason": {"code": "HOST_RESTART_INTERRUPTED", "message": "Host 重启中断了动作或恢复"}})
            with self._store.transaction():
                self._store.update_scan(scan)
                self._store.update_operation(row)
                if case:
                    self._store.update_case(case)

    def handle(self, request: dict) -> dict:
        self._validate_envelope(request)
        tool = request["tool"]
        self._validate_tool_input(tool, request["input"])
        if tool == "start_audit":
            return self._start_audit(request)
        scan = self._require_scan(request)
        if tool == "get_operation":
            return self._get_operation(request, scan)
        existing = self._store.get_by_idempotency(scan["scanId"], request["idempotencyKey"])
        if existing:
            self._assert_same_digest(existing, self._request_digest(request), "幂等键对应不同请求摘要")
            if existing["status"] == "running":
                if tool == "inspect_page": return self._inspect_page(request, scan, existing)
                if tool == "inspect_object": return self._inspect_object(request, scan, existing)
                if tool == "begin_case": return self._begin_case(request, scan, existing)
                if tool == "capture_evidence": return self._capture_evidence(request, scan, existing)
                if tool == "prepare_decision": return self._prepare_decision(request, scan, existing)
                if tool == "commit_decision": return self._commit_decision(request, scan, existing)
                if tool == "complete_audit": return self._complete_audit(request, scan, existing)
            return self._operation_response(request, scan, existing)
        if scan["status"] in {"completed", "partial", "failed"} and (tool != "complete_audit" or scan["status"] == "completed"):
            raise HostError("RUN_TERMINAL", "Scan 已进入终态")
        implemented = tool in {"inspect_page", "inspect_object", "begin_case", "perform_action", "restore_case", "capture_evidence", "prepare_decision", "commit_decision", "complete_audit"}
        operation, repeated = self._accept_operation(request, scan, implemented=implemented)
        if repeated:
            if operation["status"] == "running" and tool == "inspect_page":
                return self._inspect_page(request, scan, operation)
            if operation["status"] == "running" and tool == "inspect_object":
                return self._inspect_object(request, scan, operation)
            if operation["status"] == "running" and tool == "begin_case":
                return self._begin_case(request, scan, operation)
            if operation["status"] == "running" and tool == "capture_evidence":
                return self._capture_evidence(request, scan, operation)
            if operation["status"] == "running" and tool == "prepare_decision":
                return self._prepare_decision(request, scan, operation)
            if operation["status"] == "running" and tool == "commit_decision":
                return self._commit_decision(request, scan, operation)
            if operation["status"] == "running" and tool == "complete_audit":
                return self._complete_audit(request, scan, operation)
            return self._operation_response(request, scan, operation)
        if tool == "inspect_page":
            return self._inspect_page(request, scan, operation)
        if tool == "inspect_object":
            return self._inspect_object(request, scan, operation)
        if tool == "begin_case":
            return self._begin_case(request, scan, operation)
        if tool == "perform_action":
            return self._perform_action(request, scan, operation)
        if tool == "restore_case":
            return self._restore_case(request, scan, operation)
        if tool == "capture_evidence":
            return self._capture_evidence(request, scan, operation)
        if tool == "prepare_decision":
            return self._prepare_decision(request, scan, operation)
        if tool == "commit_decision":
            return self._commit_decision(request, scan, operation)
        if tool == "complete_audit":
            return self._complete_audit(request, scan, operation)
        error = HostError("INTERNAL_FAILURE", f"工具 {tool} 尚未接入 Host 适配器")
        return self._response(request, scan, "rejected", error=error, operation=operation)

    def _start_audit(self, request: dict) -> dict:
        if request["input"]["ruleRegistryVersion"] != self._rule_registry_version:
            raise HostError("INVALID_REQUEST", "请求的规则注册表版本与 Host 当前版本不一致", next_step="refresh_rule_registry")
        digest = self._request_digest(request)
        with self._store.transaction():
            existing = self._store.get_bootstrap(request["idempotencyKey"])
            if existing:
                self._assert_same_digest(existing, digest, "Bootstrap 幂等键对应不同请求摘要")
                scan = self._scan_from_row(self._store.get_scan(existing["scan_id"]))
                return self._operation_response(request, scan, existing)
            scan = {
                "scanId": self._new_id("scan"), "runId": self._new_id("run"), "runRevision": 0,
                "status": "authenticating", "loginStatus": "pending", "createdAt": self._now(),
                "ruleRegistryDigest": self._rule_registry_digest, "currentPageStateId": None, "capabilitiesJson": "[]",
                "outputDir": request["input"]["outputDir"], "entryUrl": request["input"]["url"],
            }
            operation = self._new_operation(request, scan["scanId"], "bootstrap", digest, "running", 0)
            self._store.insert_scan(scan)
            self._store.insert_operation(operation)
            self._store.insert_bootstrap_key(request["idempotencyKey"], operation["operationId"], digest)

        secret = self._credential_vault.consume(request["input"]["credentialHandle"])
        if secret is None:
            return self._finish_bootstrap_failure(request, scan, operation, "CREDENTIAL_CHANNEL_FAILED", "凭据句柄不存在、已过期或已消费")
        outcome = self._login_coordinator.authenticate(request["input"]["url"], secret, self._login_adapter)
        if outcome.status != "succeeded":
            return self._finish_bootstrap_failure(request, scan, operation, outcome.error_code or "LOGIN_FAILED", outcome.reason or "登录失败")
        login = outcome.result

        scan.update({"runRevision": 1, "status": "exploring", "loginStatus": "succeeded",
                     "currentPageStateId": login.current_page_state_id,
                     "capabilitiesJson": json.dumps(list(login.capabilities), separators=(",", ":"))})
        operation["status"] = "succeeded"
        with self._store.transaction():
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=self._start_result(scan), operation=operation)

    def _finish_bootstrap_failure(self, request, scan, operation, code, message):
        scan.update({"runRevision": 1, "status": "failed", "loginStatus": "failed"})
        operation.update({"status": "failed_known", "errorCode": code, "errorMessage": message})
        with self._store.transaction():
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        return self._response(request, scan, "failed", error=HostError(code, message), operation=operation)

    def _start_result(self, scan: dict) -> dict:
        result = {"scanId":scan["scanId"],"runId":scan["runId"],"loginStatus":scan["loginStatus"],
                  "ruleRegistryDigest":scan["ruleRegistryDigest"],"capabilities":json.loads(scan["capabilitiesJson"]),
                  "runRevision":scan["runRevision"]}
        if scan.get("currentPageStateId"):
            result["currentPageStateId"] = scan["currentPageStateId"]
        return result

    def _get_operation(self, request: dict, scan: dict) -> dict:
        op = self._store.get_operation(request["input"]["operationId"])
        if not op or op["scan_id"] != scan["scanId"]:
            raise HostError("UNKNOWN_REFERENCE", "Operation 不存在于当前 Scan")
        result = {"operationId":op["operation_id"],"status":op["status"],"requestDigest":op["request_digest"]}
        if op.get("result_json"):
            result["result"] = json.loads(op["result_json"])
        return self._response(request, scan, "ok", result=result)

    def _inspect_page(self, request: dict, scan: dict, operation: dict) -> dict:
        page_state_id = request["input"]["pageStateId"]
        if page_state_id != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PageState 不是当前 Scan 的活动页面状态")
        stored = self._store.get_page_state(page_state_id)
        if stored:
            entrypoints = self._store.get_entrypoints(page_state_id)
            candidates = self._store.get_candidates(page_state_id)
            inspection = self._store.get_page_inspection(page_state_id) or {}
        else:
            try:
                observed = self._page_adapter.observe(page_state_id)
                stored, inspection, entrypoints, candidates = self._materialize_page(scan, page_state_id, observed)
                self._validate_entity(self._page_state_validator, stored, "PageState")
                for item in entrypoints:
                    self._validate_entity(self._entrypoint_validator, item, "Entrypoint")
                for item in candidates:
                    self._validate_entity(self._candidate_validator, item, "PageCandidate")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            except Exception:
                return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "只读页面适配器执行失败")
            with self._store.transaction():
                self._store.insert_page_inspection(stored, inspection, entrypoints, candidates)
        result = self._inspect_page_result(request, scan, operation, stored, inspection, entrypoints, candidates)
        operation.update({"status":"succeeded","resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _materialize_page(self, scan, page_state_id, observed):
        revision = scan["runRevision"]
        entrypoints = []
        embedded = []
        for index, item in enumerate(observed.entrypoints):
            entrypoint_id = self._stable_id("entrypoint", page_state_id, str(index), item.kind, item.label)
            reason = {"code":item.reason_code,"message":item.reason_message}
            entrypoints.append({"entrypointId":entrypoint_id,"scanId":scan["scanId"],"pageStateRef":page_state_id,
                                "kind":item.kind,"label":item.label,"status":item.status,"discoveredAtRevision":revision,"reason":reason})
            embedded.append({"entrypointId":entrypoint_id,"label":item.label,"intent":item.intent,"status":item.status,"reason":reason})
        candidates = []
        for item in observed.candidates:
            rules = self._rules_by_kind.get(item.kind, [])
            if not rules:
                continue
            candidates.append({"candidateId":self._stable_id("candidate", page_state_id, item.locator_material),
                               "scanId":scan["scanId"],"pageStateRef":page_state_id,"kind":item.kind,"label":item.label,
                               "role":item.role,"locatorDigest":self._digest(item.locator_material),"potentialRules":rules,
                               "observedAtRevision":revision})
        page = {"pageStateId":page_state_id,"scanId":scan["scanId"],"url":observed.url,"origin":observed.origin,
                "route":observed.route,"title":observed.title,"stateKind":observed.state_kind,"observedAtRevision":revision,
                "observedAt":self._now(),"domDigest":self._digest(observed.dom_material),
                "identity":{"algorithmVersion":"1.0.0","materialDigest":self._digest(observed.identity_material)},
                "safeEntrypoints":embedded,"objectRefs":[]}
        inspection = {"visibleText":observed.visible_text,"networkSummary":observed.network_summary or {}}
        return page, inspection, entrypoints, candidates

    def _inspect_page_result(self, request, scan, operation, page, inspection, entrypoints, candidates):
        include = set(request["input"]["include"])
        result = {"operationId":operation.get("operation_id") or operation["operationId"],"runRevision":scan["runRevision"],
                  "pageStateId":page["pageStateId"],"candidateRefs":[x["candidateId"] for x in candidates],
                  "entrypointRefs":[x["entrypointId"] for x in entrypoints]}
        if "route" in include:
            result.update({"route":page.get("route", ""),"title":page.get("title", "")})
        if "visibleText" in include:
            result["visibleText"] = inspection.get("visibleText", "")
        if "networkSummary" in include:
            result["networkSummary"] = inspection.get("networkSummary", {})
        return result

    def _inspect_object(self, request: dict, scan: dict, operation: dict) -> dict:
        source_kind = "candidate" if "candidateId" in request["input"] else "audit_object"
        source_ref = request["input"].get("candidateId") or request["input"].get("objectId")
        source = self._store.get_candidate(source_ref) if source_kind == "candidate" else self._store.get_audit_object(source_ref)
        if not source or source["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Candidate 或 AuditObject 不存在于当前 Scan")
        if source["pageStateRef"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "对象来源页面不是当前活动 PageState")
        page = self._store.get_page_state(source["pageStateRef"])
        if not page:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "对象来源 PageState 不存在")
        try:
            outcome = self._object_identity_adapter.verify_candidate(source, page) if source_kind == "candidate" else self._object_identity_adapter.rebind_object(source, page)
            self._validate_identity_outcome(outcome)
            verification, audit_object, replacement = self._materialize_object_verification(scan, source_kind, source, outcome, operation)
            self._validate_entity(self._object_verification_validator, verification, "ObjectVerification")
            if audit_object:
                self._validate_entity(self._audit_object_validator, audit_object, "AuditObject")
            if replacement:
                self._validate_entity(self._candidate_validator, replacement, "PageCandidate")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "对象身份适配器执行失败")
        result = {"operationId":operation.get("operation_id") or operation["operationId"],"runRevision":scan["runRevision"],
                  "verificationRef":verification["verificationId"],"rebindStatus":verification["outcome"],
                  "candidateCount":verification["candidateCount"],"matchedDimensions":verification["matchedDimensions"],
                  "changedDimensions":verification["changedDimensions"],"evidenceRefs":[]}
        if verification.get("objectRef"):
            result["objectId"] = verification["objectRef"]
        if replacement:
            result["replacementCandidateRef"] = replacement["candidateId"]
        operation.update({"status":"succeeded","resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            if replacement:
                self._store.insert_candidate(replacement)
            self._store.insert_object_verification(verification, audit_object)
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _begin_case(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        obj = self._store.get_audit_object(data["objectId"])
        if not obj or obj["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "AuditObject 不存在于当前 Scan")
        if obj["pageStateRef"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "对象不是当前活动页面中的已验证对象")
        if obj.get("status") not in {"eligible", "investigating"} or obj.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "对象当前状态不允许创建 Case")
        rule = data["rule"]
        if rule not in obj.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "规则不属于该对象的冻结规则集合")
        if (rule["ruleId"], rule["version"]) not in self._rules:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "规则版本不在 Host 注册表中")
        required = set(self._rules[(rule["ruleId"], rule["version"])].get("requiredCapabilities", []))
        capabilities = set(json.loads(scan["capabilitiesJson"]))
        if not required.issubset(capabilities):
            return self._finish_operation_failure(request, scan, operation, "CAPABILITY_MISSING", "当前 Scan 能力不足以执行该规则")
        active = self._store.get_active_case(scan["scanId"], obj["objectId"], rule)
        if active:
            return self._finish_operation_failure(request, scan, operation, "CASE_ALREADY_ACTIVE", "同一对象同一规则已有执行中的 Case")
        case = {"caseId": self._new_id("case"), "scanId": scan["scanId"], "objectRef": obj["objectId"],
                "rule": rule, "kind": data["kind"], "purpose": data["purpose"], "beginRevision": scan["runRevision"] + 1,
                "plannedAt": self._now(), "plannedCoverageDimensions": data["plannedCoverageDimensions"],
                "syntheticInputs": data.get("syntheticInputs", []), "status": "planned", "operationRefs": [operation.get("operation_id") or operation["operationId"]],
                "actions": [], "beforePageStateRef": scan["currentPageStateId"],
                "recovery": {"policy": "targeted_then_refresh", "baselinePageStateRef": scan["currentPageStateId"], "finalStatus": "not_started", "attempts": []}}
        try:
            self._validate_entity(self._case_validator, case, "ReverseCase")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        capture_baseline = getattr(self._recovery_adapter, "capture_baseline", None)
        if callable(capture_baseline):
            try:
                capture_baseline(case, obj, self._store.get_page_state(scan["currentPageStateId"]))
            except Exception:
                return self._finish_operation_failure(request, scan, operation, "RECOVERY_BASELINE_UNAVAILABLE", "无法建立 Case 恢复基线")
        scan["runRevision"] += 1
        obj["status"] = "investigating"
        operation["caseRef"] = case["caseId"]
        result = {"caseId": case["caseId"], "baselinePageStateRef": case["beforePageStateRef"], "status": "planned", "runRevision": scan["runRevision"]}
        operation.update({"status": "succeeded", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        inserted = False
        with self._store.transaction():
            inserted = self._store.insert_case(case)
            if inserted:
                self._store.update_audit_object(obj)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        if not inserted:
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            operation.pop("caseRef", None)
            operation.pop("resultJson", None)
            return self._finish_operation_failure(request, scan, operation, "CASE_ALREADY_ACTIVE", "同一对象同一规则已有执行中的 Case")
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _perform_action(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        case = self._store.get_case(data["caseId"])
        obj = self._store.get_audit_object(data["objectId"])
        if not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != data["objectId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case 不存在于当前 Scan 或未绑定目标对象")
        if case.get("status") not in {"planned", "executing", "safety_check", "evidence_captured", "decision_prepared"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Case 当前状态不允许动作")
        if not obj or obj["scanId"] != scan["scanId"] or obj.get("status") not in {"eligible", "investigating"} or obj.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "目标对象不是当前 Scan 中已验证对象")
        if data["pageStateId"] != scan.get("currentPageStateId") or obj["pageStateRef"] != data["pageStateId"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "动作页面不是当前活动 PageState")
        decision = self._action_policy.action_decision(data["type"], data["intent"], data["parameters"])
        action_id = self._new_id("action")
        action = {"actionId": action_id, "scanId": scan["scanId"], "caseId": case["caseId"], "operationId": operation.get("operation_id") or operation["operationId"],
                  "type": data["type"], "targetObjectRef": obj["objectId"], "intent": data["intent"], "parameters": self._sanitized_parameters(data["parameters"]),
                  "atRunRevision": scan["runRevision"], "safetyOutcome": "blocked" if decision.outcome == "blocked" else "not_attempted"}
        if decision.outcome == "blocked":
            action["blockReason"] = {"code": decision.code, "message": decision.reason}
            try:
                self._validate_entity(self._action_attempt_validator, action, "ActionAttempt")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            operation.update({"status": "failed_known", "errorCode": decision.code, "errorMessage": decision.reason, "resultJson": json.dumps({"runRevision": scan["runRevision"], "beforePageStateRef": data["pageStateId"], "afterPageStateRef": data["pageStateId"], "resultStatus": "rejected", "actionId": action_id, "safetyOutcome": "blocked"}, ensure_ascii=False, separators=(",", ":"))})
            case["actions"].append(self._case_action(action, data["pageStateId"]))
            case["operationRefs"].append(action["operationId"])
            self._validate_entity(self._case_validator, case, "ReverseCase")
            with self._store.transaction():
                self._store.update_case(case)
                self._store.insert_action_attempt(action); self._store.update_operation(operation)
            return self._response(request, scan, "rejected", error=HostError(decision.code, decision.reason), operation=operation)
        page = self._store.get_page_state(data["pageStateId"])
        if not page:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PageState 不存在")
        try:
            action_for_adapter = dict(data)
            action_for_adapter["operationId"] = operation.get("operation_id") or operation["operationId"]
            execution = self._action_adapter.execute(action_for_adapter, obj, page, lambda req: self._action_policy.classify_request(req, page.get("origin", "")))
        except Exception:
            execution = ActionExecution(status="result_unknown", diagnostic="动作适配器执行异常")
        if execution.status not in {"succeeded", "request_blocked", "result_unknown", "persistent_write_observed", "unavailable"}:
            execution = ActionExecution(status="result_unknown", requests=execution.requests, diagnostic="动作适配器返回未知状态")
        requests = []
        request_decisions = []
        for idx, req in enumerate(execution.requests):
            request_decision = self._action_policy.classify_request(req, page.get("origin", ""))
            request_decisions.append(request_decision)
            parsed = urlparse(req.url)
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else parsed.path
            requests.append({"observationId": self._stable_id("request", operation.get("operation_id") or operation["operationId"], str(idx)), "scanId": scan["scanId"], "operationId": operation.get("operation_id") or operation["operationId"],
                              "method": req.method.upper(), "url": safe_url, "transport": req.transport, "sent": req.sent,
                              "outcome": request_decision.outcome, "code": request_decision.code, "reason": request_decision.reason})
        if execution.status == "succeeded":
            if any(item.outcome == "already_sent" for item in request_decisions):
                execution = ActionExecution(status="persistent_write_observed", requests=execution.requests, diagnostic="请求已在拦截器接管前发送")
            elif any(item.outcome == "unknown" for item in request_decisions):
                execution = ActionExecution(status="result_unknown", requests=execution.requests, diagnostic="请求无法证明未发送")
            elif any(item.outcome == "blocked" for item in request_decisions):
                execution = ActionExecution(status="request_blocked", requests=execution.requests, diagnostic="请求在发送前被 Host 阻断")
        action["requestObservationRefs"] = [x["observationId"] for x in requests]
        if execution.status == "unavailable":
            return self._finish_operation_failure(request, scan, operation, "ACTION_ADAPTER_UNAVAILABLE", execution.diagnostic or "浏览器动作适配器未配置")
        if execution.status == "persistent_write_observed":
            scan["runRevision"] += 1; scan["status"] = "failed"
        elif execution.status == "succeeded":
            scan["runRevision"] += 1
        elif execution.status == "result_unknown":
            scan["runRevision"] += 1
        elif execution.status == "request_blocked" and execution.local_state_changed:
            scan["runRevision"] += 1
        action["atRunRevision"] = scan["runRevision"]
        result_status = "succeeded" if execution.status == "succeeded" else ("result_unknown" if execution.status in {"result_unknown", "persistent_write_observed"} else "rejected")
        after = data["pageStateId"]
        action["safetyOutcome"] = "allowed" if execution.status == "succeeded" else "blocked"
        if execution.status != "succeeded":
            action["blockReason"] = {"code": "REQUEST_RESULT_UNKNOWN" if result_status == "result_unknown" else "REQUEST_BLOCKED", "message": execution.diagnostic or "动作未完成"}
        try:
            self._validate_entity(self._action_attempt_validator, action, "ActionAttempt")
            for item in requests:
                self._validate_entity(self._request_observation_validator, item, "RequestObservation")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        operation_status = "succeeded" if result_status == "succeeded" else ("failed_known" if execution.status == "persistent_write_observed" else ("result_unknown" if result_status == "result_unknown" else "failed_known"))
        operation.update({"status": operation_status, "errorCode": None if result_status == "succeeded" else action["blockReason"]["code"], "errorMessage": None if result_status == "succeeded" else action["blockReason"]["message"]})
        if execution.status == "persistent_write_observed":
            operation.update({"errorCode": "PERSISTENT_WRITE_OBSERVED", "errorMessage": "观察到潜在持久化写请求已离开浏览器"})
        result = {"runRevision": scan["runRevision"], "beforePageStateRef": data["pageStateId"], "afterPageStateRef": after, "resultStatus": result_status, "actionId": action_id, "safetyOutcome": action["safetyOutcome"], "requestObservationRefs": [x["observationId"] for x in requests]}
        operation["resultJson"] = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        case["operationRefs"].append(action["operationId"])
        case["actions"].append(self._case_action(action, data["pageStateId"]))
        case["startedAt"] = case.get("startedAt") or self._now()
        case["afterPageStateRef"] = after
        if execution.status == "succeeded":
            case["status"] = "executing"
        elif execution.status in {"request_blocked", "result_unknown"}:
            case["status"] = "restoring"
        elif execution.status == "persistent_write_observed":
            case.update({"status": "invalidated", "endedAt": self._now(),
                         "restoreReason": {"code": "PERSISTENT_WRITE_OBSERVED", "message": "潜在持久化写请求已离开浏览器"}})
        self._validate_entity(self._case_validator, case, "ReverseCase")
        with self._store.transaction():
            self._store.update_case(case)
            for item in requests: self._store.insert_request_observation(item)
            self._store.insert_action_attempt(action)
            if execution.status == "succeeded":
                self._store.update_scan(scan)
            elif execution.status == "result_unknown":
                self._store.update_scan(scan)
            elif execution.status == "persistent_write_observed":
                self._store.update_scan(scan)
            elif execution.status == "request_blocked" and execution.local_state_changed:
                self._store.update_scan(scan)
            self._store.update_operation(operation)
        if execution.status == "persistent_write_observed":
            return self._response(request, scan, "failed", error=HostError("PERSISTENT_WRITE_OBSERVED", "观察到潜在持久化写请求已离开浏览器"), operation=operation)
        if result_status == "result_unknown":
            return self._response(request, scan, "rejected", error=HostError("REQUEST_RESULT_UNKNOWN", operation["errorMessage"]), operation=operation)
        if result_status == "rejected":
            return self._response(request, scan, "rejected", error=HostError(operation["errorCode"], operation["errorMessage"]), operation=operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _restore_case(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        case = self._store.get_case(data["caseId"])
        target = self._store.get_audit_object(data["objectId"])
        if not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != data["objectId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case 不存在于当前 Scan 或未绑定目标对象")
        if case.get("status") in {"completed", "restore_failed", "invalidated"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Case 已经收束，不能重复恢复")
        if data["pageStateId"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "恢复输入不是当前活动 PageState")
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "恢复目标对象不存在")
        case["status"] = "restoring"
        case["startedAt"] = case.get("startedAt") or self._now()
        page = self._store.get_page_state(data["pageStateId"])
        attempts = []
        try:
            adapter_case = dict(case)
            adapter_case["_hostOperationId"] = operation.get("operation_id") or operation["operationId"]
            first = self._recovery_adapter.restore(adapter_case, target, page, "targeted_inverse")
            self._validate_recovery_attempt(first, "targeted_inverse")
        except Exception:
            first = self._unknown_recovery_attempt("targeted_inverse", "恢复适配器执行失败")
        attempts.append(self._recovery_attempt_dict(first))
        final = first
        if first.outcome != "restored" or not self._all_checks_match(first):
            try:
                adapter_case = dict(case)
                adapter_case["_hostOperationId"] = operation.get("operation_id") or operation["operationId"]
                second = self._recovery_adapter.restore(adapter_case, target, page, "refresh_replay")
                self._validate_recovery_attempt(second, "refresh_replay")
            except Exception:
                second = self._unknown_recovery_attempt("refresh_replay", "刷新重放适配器执行失败")
            attempts.append(self._recovery_attempt_dict(second))
            final = second
        clean = final.outcome == "restored" and self._all_checks_match(final)
        if clean:
            final_status, case_status = "restored", "completed"
            target["status"] = "eligible"
        else:
            final_status, case_status = ("uncertain" if final.outcome == "uncertain" else "failed"), "restore_failed"
            case["restoreReason"] = {"code": "CASE_NOT_RESTORED", "message": final.reason or "恢复检查未全部通过"}
            target.update({"status": "blocked", "blockedReason": {"code": "CASE_NOT_RESTORED", "message": "Case 未越过恢复屏障"}})
            if self._has_pollution_uncertainty(final):
                scan["status"] = "failed"
            elif scan["status"] not in {"failed", "completed"}:
                scan["status"] = "partial"
        scan["runRevision"] += 1
        case.update({"status": case_status, "endedAt": self._now(), "afterPageStateRef": case.get("beforePageStateRef"),
                     "recovery": {"policy": "targeted_then_refresh", "baselinePageStateRef": case.get("beforePageStateRef"), "finalStatus": final_status, "attempts": attempts}})
        if clean:
            case.pop("restoreReason", None)
        try:
            self._validate_entity(self._case_validator, case, "ReverseCase")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        result = {"caseId": case["caseId"], "finalStatus": final_status, "runRevision": scan["runRevision"]}
        operation.update({"caseRef": case["caseId"], "status": "succeeded" if clean else "failed_known", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        if not clean:
            operation.update({"errorCode": "CASE_NOT_RESTORED", "errorMessage": "Case 未越过恢复屏障"})
        with self._store.transaction():
            self._store.update_case(case)
            self._store.update_audit_object(target)
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        if clean:
            return self._response(request, scan, "ok", result=result, operation=operation)
        return self._response(request, scan, "failed" if scan["status"] == "failed" else "rejected", result=result,
                              error=HostError("CASE_NOT_RESTORED", "Case 未越过恢复屏障"), operation=operation)

    def _capture_evidence(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        page = self._store.get_page_state(data["pageStateId"])
        target = self._store.get_audit_object(data["objectId"])
        case = self._store.get_case(data["caseId"]) if data.get("caseId") else None
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "证据目标不存在于当前 Scan")
        if data["pageStateId"] != scan.get("currentPageStateId") or not page or page["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "证据页面不是当前活动 PageState")
        if target["pageStateRef"] != data["pageStateId"] or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "证据目标不是当前页面中已唯一验证的对象")
        if target.get("status") not in {"eligible", "investigating"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "对象状态不允许采集正式证据")
        if data.get("caseId") and (not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != target["objectId"]):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case 不存在或未绑定证据目标")
        if case and case.get("status") not in {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Case 当前状态不允许采集证据")
        try:
            captured = self._evidence_adapter.capture(page, target, case, data.get("includeRawVisual", False))
            payload = self._evidence_sanitizer.sanitize(captured.payload)
            if data.get("includeRawVisual", False) and captured.kind != "runtime_visual":
                raise ValueError("Raw Visual 必须生成 runtime_visual Evidence")
            if not data.get("includeRawVisual", False) and captured.kind == "runtime_visual":
                raise ValueError("runtime_visual Evidence 必须显式请求 Raw Visual")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        except ValueError as error:
            return self._finish_operation_failure(request, scan, operation, "SANITIZATION_FAILED", str(error))
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "证据适配器执行失败")
        revision = scan["runRevision"] + 1
        captured_at = self._now()
        operation_id = operation.get("operation_id") or operation["operationId"]
        evidence_id = self._stable_id("evidence", operation_id)
        screenshot = None
        screenshot_path = None
        if data.get("includeRawVisual", False):
            screenshot, screenshot_path = self._materialize_raw_visual(scan, page, target, case, captured, operation_id, captured_at, revision)
        evidence = {"evidenceId": evidence_id, "scanId": scan["scanId"], "pageStateRef": page["pageStateId"], "objectRef": target["objectId"],
                    "kind": captured.kind, "capturedAt": captured_at, "capturedAtRevision": revision,
                    "collectorVersion": captured.collector_version, "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
                    "normalizationAlgorithmVersion": "1.0.0", "sanitized": True,
                    "payload": {"payloadType": captured.payload_type, "content": payload}}
        if case:
            evidence["caseRef"] = case["caseId"]
        if captured.source_binding is not None:
            evidence["sourceBinding"] = self._evidence_sanitizer.sanitize(captured.source_binding)
        if screenshot:
            evidence["screenshotRefs"] = [screenshot["screenshotId"]]
        digest_material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
        evidence["integrityDigest"] = self._digest(digest_material)
        try:
            self._validate_entity(self._evidence_validator, evidence, "Evidence")
            if screenshot:
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        scan["runRevision"] = revision
        if case:
            case["status"] = "evidence_captured"
            case.setdefault("evidenceRefs", []).append(evidence_id)
            case["operationRefs"].append(operation_id)
            self._validate_entity(self._case_validator, case, "ReverseCase")
        result = {"evidenceId": evidence_id, "runRevision": revision}
        if screenshot:
            result["screenshotRef"] = screenshot["screenshotId"]
        operation.update({"caseRef": case["caseId"] if case else operation.get("caseRef"), "status": "succeeded",
                          "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        created_file = False
        try:
            if screenshot and screenshot["status"] == "captured":
                created_file = self._write_screenshot(scan["outputDir"], screenshot["path"], captured.raw_visual.image_bytes, screenshot["digest"])
            with self._store.transaction():
                self._store.insert_evidence(evidence, screenshot)
                if case:
                    self._store.update_case(case)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            if created_file and screenshot_path and screenshot_path.exists():
                screenshot_path.unlink()
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "证据或截图持久化失败")
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=[evidence_id])

    def _materialize_raw_visual(self, scan, page, target, case, captured, operation_id, captured_at, revision):
        raw = captured.raw_visual
        screenshot_id = self._stable_id("screenshot", operation_id)
        common = {"screenshotId": screenshot_id, "scanId": scan["scanId"], "pageStateRef": page["pageStateId"], "objectRef": target["objectId"],
                  "kind": "raw_visual", "capturedAt": captured_at, "capturedAtRevision": revision,
                  "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
                  "sanitizationStatus": getattr(raw, "sanitization_status", "sanitized") if raw else "failed"}
        if case:
            common["caseRef"] = case["caseId"]
        target_box = target.get("location", {}).get("boundingBox")
        source_box = (raw.source_bounding_box or raw.bounding_box) if raw else None
        bbox_valid = bool(raw and raw.bounding_box and source_box and target_box and all(source_box.get(key) == target_box.get(key) for key in ("x", "y", "width", "height"))
                          and raw.bounding_box["x"] + raw.bounding_box["width"] <= raw.width
                          and raw.bounding_box["y"] + raw.bounding_box["height"] <= raw.height)
        header_valid = bool(raw and ((raw.image_type == "png" and raw.image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                                     or (raw.image_type == "jpeg" and raw.image_bytes.startswith(b"\xff\xd8\xff"))
                                     or (raw.image_type == "webp" and raw.image_bytes.startswith(b"RIFF") and raw.image_bytes[8:12] == b"WEBP")))
        sanitization_status = getattr(raw, "sanitization_status", "sanitized") if raw else "failed"
        status_consistent = sanitization_status in {"sanitized", "not_performed"} and raw is not None and raw.sanitized == (sanitization_status == "sanitized")
        if raw and raw.status == "captured" and status_consistent and raw.image_bytes and raw.image_type in {"png", "jpeg", "webp"} and raw.width > 0 and raw.height > 0 and bbox_valid and header_valid and raw.annotation:
            extension = "jpg" if raw.image_type == "jpeg" else raw.image_type
            relative = f"screenshots/{screenshot_id}.{extension}"
            common.update({"status": "captured", "path": relative, "digest": hashlib.sha256(raw.image_bytes).hexdigest(), "imageType": raw.image_type,
                           "width": raw.width, "height": raw.height, "problemBoundingBox": raw.bounding_box,
                           "sourceBoundingBox": source_box,
                           "annotation": self._evidence_sanitizer.sanitize(raw.annotation)})
            return common, Path(scan["outputDir"]) / relative
        status = raw.status if raw and raw.status in {"not_located", "ambiguous", "rejected"} else "rejected"
        reason = raw.reason if raw and raw.reason else ("截图脱敏状态无效" if raw and not status_consistent else "截图格式、尺寸或对象定位校验失败")
        common.update({"status": status, "failureReason": {"code": "SANITIZATION_FAILED" if raw and not status_consistent else "SCREENSHOT_NOT_CAPTURED", "message": reason}})
        return common, None

    def _prepare_decision(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        target = self._store.get_audit_object(data["objectId"])
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "判定对象不存在于当前 Scan")
        if target.get("pageStateRef") != scan.get("currentPageStateId") or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "判定对象不是当前页面中已唯一验证的对象")
        if target.get("status") not in {"eligible", "investigating"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "对象状态不允许准备判定")
        rule_ref = data["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not rule or rule_ref not in target.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "规则不属于对象的冻结规则集合")

        evidence = []
        for evidence_ref in data["evidenceRefs"]:
            item = self._store.get_evidence(evidence_ref)
            if not item or item.get("scanId") != scan["scanId"] or item.get("objectRef") != target["objectId"] or item.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence 不存在或未绑定当前 Scan、页面和对象")
            if item.get("caseRef") and item["caseRef"] not in data["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence 的 Case 未包含在当前判定中")
            evidence.append(item)

        cases = []
        covered_dimensions = set()
        for case_ref in data["caseRefs"]:
            case = self._store.get_case(case_ref)
            if not case or case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"] or case.get("rule") != rule_ref:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case 不存在或未绑定当前对象与规则")
            if case.get("status") != "completed" or case.get("recovery", {}).get("finalStatus") != "restored":
                return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "引用 Case 尚未完成恢复屏障")
            cases.append(case)
            covered_dimensions.update(case.get("plannedCoverageDimensions", []))

        required_dimensions = list(rule.get("coverageDimensions", []))
        covered = [dimension for dimension in required_dimensions if dimension in covered_dimensions]
        coverage = {"requiredDimensions": required_dimensions, "coveredDimensions": covered,
                    "complete": set(required_dimensions).issubset(covered_dimensions)}
        if data["result"] == "scanned_no_issue" and not coverage["complete"]:
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INCOMPLETE", "无问题结论未满足规则最低覆盖契约")
        if data["result"] == "issue_found" and not evidence:
            return self._finish_operation_failure(request, scan, operation, "EVIDENCE_INSUFFICIENT", "问题结论至少需要一条有效 Evidence")
        if data.get("severity") and data["severity"] not in rule.get("allowedSeverities", []):
            return self._finish_operation_failure(request, scan, operation, "INVALID_REQUEST", "问题严重度不在规则允许范围内")

        operation_id = operation.get("operation_id") or operation["operationId"]
        pending_id = self._stable_id("pending", operation_id)
        revision = scan["runRevision"] + 1
        screenshot = None
        screenshot_bytes = None
        screenshot_path = None
        if data["result"] == "issue_found":
            raw_ref = data.get("rawVisualRef")
            raw = self._store.get_screenshot(raw_ref) if raw_ref else None
            evidence_links_raw = any(raw_ref in item.get("screenshotRefs", []) for item in evidence)
            if not raw or raw.get("kind") != "raw_visual" or raw.get("status") != "captured" or not evidence_links_raw:
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "问题结论必须引用 Evidence 中已捕获的 Raw Visual")
            if raw.get("scanId") != scan["scanId"] or raw.get("objectRef") != target["objectId"] or raw.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Raw Visual 未绑定当前 Scan、页面和对象")
            if raw.get("caseRef") and raw["caseRef"] not in data["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Raw Visual 的 Case 未包含在当前判定中")
            if raw.get("sanitizationStatus") != "sanitized":
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_SANITIZATION_REQUIRED", "正式 issue_found 需要已完成图片脱敏的 IssueScreenshot；当前截图未执行自动像素脱敏")
            try:
                screenshot_bytes = self._read_screenshot(scan["outputDir"], raw)
                screenshot = self._materialize_issue_screenshot(scan, raw, operation_id, revision)
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
                screenshot_path = Path(scan["outputDir"]) / screenshot["path"]
            except (OSError, ValueError, HostError) as error:
                message = error.message if isinstance(error, HostError) else str(error)
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", message)

        applicable = data["result"] != "not_applicable"
        pending = {"pendingDecisionId": pending_id, "scanId": scan["scanId"], "preparationOperationRef": operation_id,
                   "objectRef": target["objectId"], "rule": rule_ref, "applicable": applicable, "result": data["result"],
                   "coverage": coverage, "evidenceRefs": list(data["evidenceRefs"]), "caseRefs": list(data["caseRefs"]),
                   "reasonText": self._evidence_sanitizer.sanitize(data["reasonText"]), "status": "pending",
                   "preparedAt": self._now(), "preparedAtRevision": revision}
        if data["result"] == "needs_review":
            pending["blocker"] = self._evidence_sanitizer.sanitize(data["blocker"])
        if screenshot:
            pending.update({"rawVisualRef": data["rawVisualRef"], "screenshotRef": screenshot["screenshotId"]})
            for field in ("severity", "title", "message", "impact", "recommendation"):
                pending[field] = self._evidence_sanitizer.sanitize(data[field])
        try:
            self._validate_entity(self._pending_decision_validator, pending, "PendingDecision")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        result = {"pendingDecisionId": pending_id, "runRevision": revision}
        if screenshot:
            result["screenshotRef"] = screenshot["screenshotId"]
        operation.update({"status": "succeeded", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        scan["runRevision"] = revision
        created_file = False
        try:
            if screenshot:
                created_file = self._write_screenshot(scan["outputDir"], screenshot["path"], screenshot_bytes, screenshot["digest"])
            with self._store.transaction():
                if screenshot:
                    self._store.insert_screenshot(screenshot)
                self._store.insert_pending_decision(pending)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            if created_file and screenshot_path and screenshot_path.exists():
                screenshot_path.unlink()
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "PendingDecision 或问题截图持久化失败")
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=data["evidenceRefs"])

    def _commit_decision(self, request: dict, scan: dict, operation: dict) -> dict:
        pending = self._store.get_pending_decision(request["input"]["pendingDecisionId"])
        if not pending or pending.get("scanId") != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PendingDecision 不存在于当前 Scan")
        if pending.get("status") != "pending":
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "PendingDecision 已提交或失效")
        if scan.get("ruleRegistryDigest") != self._rule_registry_digest or pending.get("preparedAtRevision", scan["runRevision"]) > scan["runRevision"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "规则注册表或 PendingDecision revision 已失效")
        try:
            self._validate_entity(self._pending_decision_validator, pending, "PendingDecision")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        preparation = self._store.get_operation(pending["preparationOperationRef"])
        if not preparation or preparation.get("scan_id") != scan["scanId"] or preparation.get("tool") != "prepare_decision" or preparation.get("status") != "succeeded":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PendingDecision 的准备 Operation 引用失效")
        target = self._store.get_audit_object(pending["objectRef"])
        rule_ref = pending["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not target or target.get("scanId") != scan["scanId"] or target.get("pageStateRef") != scan.get("currentPageStateId") or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "提交时对象身份或页面状态已失效")
        if target.get("status") not in {"eligible", "investigating"} or not rule or rule_ref not in target.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "提交时对象或规则不再有效")
        try:
            self._validate_entity(self._audit_object_validator, target, "AuditObject")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        if self._store.get_assessment_for_object_rule(scan["scanId"], target["objectId"], rule_ref):
            return self._finish_operation_failure(request, scan, operation, "ALREADY_COMMITTED", "同一对象和规则已经存在正式判定")
        covered_dimensions = set()
        for case_ref in pending["caseRefs"]:
            case = self._store.get_case(case_ref)
            if not case or case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"] or case.get("rule") != rule_ref:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "提交时 Case 引用不闭合")
            if case.get("status") != "completed" or case.get("recovery", {}).get("finalStatus") != "restored":
                pending["status"] = "invalidated"
                with self._store.transaction():
                    self._store.update_pending_decision(pending)
                return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "提交时 Case 未越过恢复屏障")
            try:
                self._validate_entity(self._case_validator, case, "ReverseCase")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            covered_dimensions.update(case.get("plannedCoverageDimensions", []))
        required_dimensions = list(rule.get("coverageDimensions", []))
        coverage = {"requiredDimensions": required_dimensions, "coveredDimensions": [d for d in required_dimensions if d in covered_dimensions], "complete": set(required_dimensions).issubset(covered_dimensions)}
        if coverage != pending.get("coverage"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "提交时覆盖证明已变化")
        if pending["result"] == "scanned_no_issue" and not coverage["complete"]:
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INCOMPLETE", "提交时覆盖不足")
        evidence_refs = list(pending["evidenceRefs"])
        for evidence_ref in evidence_refs:
            evidence = self._store.get_evidence(evidence_ref)
            if not evidence or evidence.get("scanId") != scan["scanId"] or evidence.get("objectRef") != target["objectId"] or evidence.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "提交时 Evidence 引用失效")
            if evidence.get("caseRef") and evidence["caseRef"] not in pending["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "提交时 Evidence 的 Case 引用不闭合")
            digest_material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
            if evidence.get("integrityDigest") != self._digest(digest_material):
                return self._finish_operation_failure(request, scan, operation, "EVIDENCE_INTEGRITY_FAILED", "提交时 Evidence 完整性校验失败")
            try:
                self._validate_entity(self._evidence_validator, evidence, "Evidence")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        assessment_id = self._new_id("assessment")
        revision = scan["runRevision"] + 1
        assessment = {"assessmentId": assessment_id, "scanId": scan["scanId"], "preparationOperationRef": pending["preparationOperationRef"], "commitOperationRef": operation.get("operation_id") or operation["operationId"], "objectRef": target["objectId"], "rule": rule_ref, "applicable": pending["applicable"], "result": pending["result"], "coverage": coverage, "evidenceRefs": evidence_refs, "caseRefs": list(pending["caseRefs"]), "reasonText": pending["reasonText"], "conclusionValidity": "valid", "decidedAt": self._now(), "committedAtRevision": revision}
        if pending.get("screenshotRef"):
            screenshot = self._store.get_screenshot(pending["screenshotRef"])
            raw = self._store.get_screenshot(pending.get("rawVisualRef"))
            if not screenshot or screenshot.get("kind") != "issue" or screenshot.get("status") != "captured" or screenshot.get("scanId") != scan["scanId"] or screenshot.get("objectRef") != target["objectId"] or screenshot.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "提交时问题截图无效")
            if not raw or raw.get("kind") != "raw_visual" or raw.get("status") != "captured" or screenshot.get("rawVisualRef") != raw.get("screenshotId") or screenshot.get("digest") != raw.get("digest"):
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "问题截图与 Raw Visual 来源链失效")
            if screenshot.get("caseRef") and screenshot["caseRef"] not in pending["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "问题截图的 Case 引用不闭合")
            try:
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
                self._read_screenshot(scan["outputDir"], screenshot)
            except (OSError, ValueError, HostError) as error:
                message = error.message if isinstance(error, HostError) else str(error)
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", message)
            assessment["screenshotRef"] = pending["screenshotRef"]
        for field in ("severity", "title", "impact", "recommendation"):
            if field in pending:
                assessment[field] = pending[field]
        if pending.get("blocker"):
            assessment["blocker"] = pending["blocker"]
        try:
            self._validate_entity(self._assessment_validator, assessment, "RuleAssessment")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        issue = None
        if pending["result"] == "issue_found":
            issue = {"issueId": self._new_id("issue"), "scanId": scan["scanId"], "assessmentRef": assessment_id, "objectRef": target["objectId"], "rule": rule_ref, "result": "issue_found", "evidenceRefs": evidence_refs, "screenshotRef": pending["screenshotRef"], "severity": pending["severity"], "title": pending["title"], "message": pending["message"], "impact": pending["impact"], "recommendation": pending["recommendation"], "conclusionValidity": "valid", "createdAt": self._now(), "createdAtRevision": revision}
            try:
                self._validate_entity(self._issue_validator, issue, "Issue")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        target.setdefault("assessmentRefs", []).append(assessment_id)
        target["status"] = "decided" if len(target["assessmentRefs"]) >= len(target.get("potentialRules", [])) else "eligible"
        self._validate_entity(self._audit_object_validator, target, "AuditObject")
        pending["status"] = "committed"
        operation_result = {"assessmentId": assessment_id, "runRevision": revision}
        if issue:
            operation_result["issueId"] = issue["issueId"]
        operation.update({"status": "succeeded", "resultJson": json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))})
        scan["runRevision"] = revision
        try:
            with self._store.transaction():
                self._store.insert_assessment(assessment)
                if issue:
                    self._store.insert_issue(issue)
                self._store.update_audit_object(target)
                self._store.update_pending_decision(pending)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            operation.pop("resultJson", None)
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "正式判定原子持久化失败")
        return self._response(request, scan, "ok", result=operation_result, operation=operation, evidence_refs=evidence_refs)

    def _complete_audit(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        if (Path(scan["outputDir"]) / "audit-ledger.json").exists():
            return self._finish_operation_failure(request, scan, operation, "ALREADY_COMPLETED", "当前 Scan 已生成审计账本")
        if scan.get("loginStatus") != "succeeded":
            return self._finish_operation_failure(request, scan, operation, "LOGIN_FAILED", "登录未成功，不能结束审计")
        if scan.get("ruleRegistryDigest") != self._rule_registry_digest:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "扫描冻结规则摘要与 Host 当前注册表不一致")
        if any(item.get("status") == "pending" for item in self._store.list_entities("pending_decisions", scan["scanId"])):
            return self._finish_operation_failure(request, scan, operation, "DECISION_PENDING", "仍有未提交的 PendingDecision")
        operation_id = operation.get("operation_id") or operation["operationId"]
        if any(item["operation_id"] != operation_id and item["status"] == "running" for item in self._store.list_operations(scan["scanId"])):
            return self._finish_operation_failure(request, scan, operation, "OPERATION_IN_PROGRESS", "仍有未收束的 Operation")
        cases = self._store.list_entities("reverse_cases", scan["scanId"])
        if any(item.get("status") in {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared", "restoring"} for item in cases):
            return self._finish_operation_failure(request, scan, operation, "CASE_ACTIVE", "仍有未收束的 Case")
        page_states = self._store.list_entities("page_states", scan["scanId"]); entrypoints = self._store.list_entities("entrypoints", scan["scanId"])
        objects = self._store.list_entities("audit_objects", scan["scanId"]); assessments = self._store.list_entities("assessments", scan["scanId"]); issues = self._store.list_entities("issues", scan["scanId"])
        scan_id = scan["scanId"]
        if any(item.get("scanId") != scan_id for item in page_states + entrypoints + objects + assessments + issues):
            return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "账本包含跨 Scan 实体")
        page_ids = {item["pageStateId"] for item in page_states}; object_ids = {item["objectId"] for item in objects}; entry_ids = {item["entrypointId"] for item in entrypoints}
        if len(data["visitedPageStateRefs"]) != len(set(data["visitedPageStateRefs"])) or not set(data["visitedPageStateRefs"]).issubset(page_ids):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "visitedPageStateRefs 引用不闭合")
        if len(data["processedObjectRefs"]) != len(set(data["processedObjectRefs"])) or not set(data["processedObjectRefs"]).issubset(object_ids):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "processedObjectRefs 引用不闭合")
        processed = set(data["processedEntrypointRefs"]); skipped = {item["entrypointId"] for item in data["skippedEntrypoints"]}; unprocessed = set(data["unprocessedEntrypointRefs"])
        if len(skipped) != len(data["skippedEntrypoints"]) or processed | skipped | unprocessed != entry_ids or not processed.isdisjoint(skipped | unprocessed) or not skipped.isdisjoint(unprocessed):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "入口 processed/skipped/unprocessed 未闭合")
        if any(not item.get("reason", {}).get("message") for item in data["skippedEntrypoints"]):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "跳过入口缺少原因")
        by_rule = {}
        for assessment in assessments:
            try:
                self._validate_entity(self._assessment_validator, assessment, "RuleAssessment")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            by_rule.setdefault((assessment["rule"]["ruleId"], assessment["rule"]["version"]), []).append(assessment)
        assessment_ids = {item["assessmentId"] for item in assessments}
        issues_by_assessment = {}
        for issue in issues:
            try:
                self._validate_entity(self._issue_validator, issue, "Issue")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            if issue["assessmentRef"] not in assessment_ids:
                return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Issue 未绑定当前 Scan 的 Assessment")
            issues_by_assessment.setdefault(issue["assessmentRef"], []).append(issue)
        if any((assessment["result"] == "issue_found" and len(issues_by_assessment.get(assessment["assessmentId"], [])) != 1) or (assessment["result"] != "issue_found" and assessment["assessmentId"] in issues_by_assessment) for assessment in assessments):
            return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Issue 与 issue_found Assessment 不是一对一关系")
        summaries = []
        summary_keys = [(item["rule"]["ruleId"], item["rule"]["version"]) for item in data["ruleSummaries"]]
        if len(summary_keys) != len(set(summary_keys)) or set(summary_keys) != set(self._rules):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "规则摘要必须恰好覆盖冻结启用规则")
        for summary in data["ruleSummaries"]:
            rule_ref = summary["rule"]; rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
            if not rule:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "规则摘要引用未知规则")
            found = by_rule.get((rule_ref["ruleId"], rule_ref["version"]), [])
            counts = {result: sum(1 for item in found if item["result"] == result) for result in ("issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise")}
            provided_counts = summary.get("resultCounts", {})
            if summary["assessmentCount"] != len(found) or any(provided_counts.get(key, 0) != value for key, value in counts.items()):
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "规则摘要计数与正式 Assessment 不一致")
            complete = bool(found) and all(item["coverage"].get("complete") for item in found)
            if summary["coverageComplete"] != complete:
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "规则摘要覆盖状态不一致")
            summaries.append({"rule": rule_ref, "assessmentCount": len(found), "resultCounts": {k: v for k, v in counts.items() if v}, "coverageComplete": complete})
        incomplete_objects = object_ids - set(data["processedObjectRefs"])
        incomplete_pages = page_ids - set(data["visitedPageStateRefs"])
        for object_ref in data["processedObjectRefs"]:
            target = next(item for item in objects if item["objectId"] == object_ref)
            if target.get("status") != "decided" or len(target.get("assessmentRefs", [])) < len(target.get("potentialRules", [])):
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "processedObjectRefs 包含尚未完成全部规则判定的对象")
        incomplete_rules = any(not item["coverageComplete"] for item in summaries)
        status = "failed" if scan["status"] == "failed" else ("partial" if unprocessed or incomplete_objects or incomplete_pages or incomplete_rules else "completed")
        proof = {"visitedPageStateRefs": list(data["visitedPageStateRefs"]), "processedObjectRefs": list(data["processedObjectRefs"]), "processedEntrypointRefs": list(data["processedEntrypointRefs"]), "skippedEntrypoints": list(data["skippedEntrypoints"]), "ruleSummaries": summaries, "unprocessedEntrypointRefs": list(data["unprocessedEntrypointRefs"]), "completionReason": data["completionReason"]}
        terminal = {"code": "COVERAGE_COMPLETE" if status == "completed" else ("SCAN_FAILED" if status == "failed" else "COVERAGE_PARTIAL"), "message": data["completionReason"]}
        revision = scan["runRevision"] + 1
        scan.update({"status": status, "runRevision": revision})
        operation_result = {"scanStatus": status, "conclusionsValid": status != "failed", "runRevision": revision}
        operation.update({"status": "succeeded", "resultJson": json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_scan(scan); self._store.update_operation(operation)
        try:
            artifact_paths = self._export_ledger(scan, proof, summaries, terminal, status)
            operation_result.update({"ledgerPath": "audit-ledger.json", "artifactPaths": artifact_paths})
            operation["resultJson"] = json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))
            with self._store.transaction():
                self._store.update_operation(operation)
        except Exception as error:
            scan["status"] = "failed"; operation.update({"status": "failed_known", "errorCode": "LEDGER_EXPORT_FAILED", "errorMessage": str(error)}); operation.pop("resultJson", None)
            with self._store.transaction(): self._store.update_scan(scan); self._store.update_operation(operation)
            return self._response(request, scan, "failed", error=HostError("LEDGER_EXPORT_FAILED", str(error)), operation=operation)
        return self._response(request, scan, "ok", result=operation_result, operation=operation)

    def _export_ledger(self, scan: dict, proof: dict, summaries: list[dict], terminal: dict, status: str) -> list[str]:
        ended_at = self._now(); entry_url = scan.get("entryUrl") or "https://unknown.invalid"; parsed = urlparse(entry_url)
        scan_entity = {"scanId": scan["scanId"], "runId": scan["runId"], "protocolVersion": "1.0", "skillVersion": "1.0.0", "runRevision": scan["runRevision"], "algorithms": {"pageIdentity": "1.0.0", "objectIdentity": "1.0.0", "recoveryPolicy": "1.0.0", "sanitizationPolicy": "1.0.0", "normalization": "1.0.0"}, "status": status, "auditMode": "runtime_only", "entryUrl": entry_url, "allowedOrigins": [f"{parsed.scheme}://{parsed.netloc}"], "startedAt": scan["createdAt"], "endedAt": ended_at, "loginStatus": scan["loginStatus"], "ruleRegistryDigest": scan["ruleRegistryDigest"], "frozenRules": [{"ruleId": r["ruleId"], "version": r["version"]} for r in self._rule_registry.get("rules", []) if r.get("status") == "enabled"], "capabilities": json.loads(scan["capabilitiesJson"]), "coverageProof": proof, "terminalReason": terminal, "conclusionsValid": status != "failed"}
        self._validate_entity(self._scan_run_validator, scan_entity, "ScanRun")
        operations = []
        for row in self._store.list_operations(scan["scanId"]):
            item = {"operationId": row["operation_id"], "scanId": row["scan_id"], "requestId": row["request_id"], "tool": row["tool"], "operationKind": row["operation_kind"], "idempotencyKey": row["idempotency_key"], "requestDigest": row["request_digest"], "status": row["status"], "acceptedAt": scan["createdAt"], "acceptedAtRevision": row["accepted_at_revision"]}
            if row.get("case_ref"): item["caseRef"] = row["case_ref"]
            if row["status"] in {"succeeded", "rejected", "failed_known", "result_unknown"}: item["endedAt"] = ended_at
            if row["status"] in {"rejected", "failed_known", "result_unknown"}: item["reason"] = {"code": row.get("error_code") or "OPERATION_FAILED", "message": row.get("error_message") or "Operation 未产生成功结果"}
            operations.append(item)
        entrypoints = self._store.list_entities("entrypoints", scan["scanId"]); processed = set(proof["processedEntrypointRefs"]); unprocessed = set(proof["unprocessedEntrypointRefs"]); skipped = {item["entrypointId"]: item["reason"] for item in proof["skippedEntrypoints"]}
        for entrypoint in entrypoints:
            ref = entrypoint["entrypointId"]
            if ref in processed: entrypoint["status"] = "processed"
            elif ref in skipped: entrypoint.update({"status": "skipped", "reason": skipped[ref]})
            elif ref in unprocessed: entrypoint["status"] = "unprocessed"
        objects = self._store.list_entities("audit_objects", scan["scanId"]); refs_by_page = {}
        for target in objects: refs_by_page.setdefault(target["pageStateRef"], []).append(target["objectId"])
        pages = self._store.list_entities("page_states", scan["scanId"])
        for page in pages:
            page["objectRefs"] = sorted(refs_by_page.get(page["pageStateId"], []))
            for embedded in page.get("safeEntrypoints", []):
                ref = embedded["entrypointId"]
                if ref in processed: embedded["status"] = "processed"
                elif ref in skipped: embedded.update({"status": "skipped", "reason": skipped[ref]})
                elif ref in unprocessed: embedded["status"] = "unprocessed"
        ledger = {"schemaVersion": "1.0.0", "createdAt": ended_at, "scan": scan_entity, "ruleRegistry": self._rule_registry, "pageStates": pages, "entrypoints": entrypoints, "objects": objects, "operations": operations, "assessments": self._store.list_entities("assessments", scan["scanId"]), "cases": self._store.list_entities("reverse_cases", scan["scanId"]), "evidence": self._store.list_entities("evidence", scan["scanId"]), "screenshots": self._store.list_entities("screenshots", scan["scanId"]), "issues": self._store.list_entities("issues", scan["scanId"])}
        self._validate_entity(self._ledger_validator, ledger, "AuditLedger")
        artifacts = {"audit-ledger.json": (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
        derived = self._report_builder.render(ledger)
        for name, validator in self._derived_output_validators.items():
            if name not in derived: raise ValueError(f"缺少派生 JSON 产物: {name}")
            self._validate_entity(validator, json.loads(derived[name]), name)
        artifacts.update(derived)
        self._publish_artifacts(scan["outputDir"], artifacts)
        return sorted(artifacts)

    @staticmethod
    def _publish_artifacts(output_dir: str, artifacts: dict[str, bytes]) -> None:
        root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
        targets = {}
        for relative, content in artifacts.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path.name != relative:
                raise ValueError("派生产物路径无效")
            target = root / path; targets[target] = content
            if target.exists() and target.read_bytes() != content:
                raise ValueError(f"派生产物内容冲突: {relative}")
        created = []
        try:
            for target, content in targets.items():
                if target.exists(): continue
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}-", dir=root)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
                    os.link(temporary, target); created.append(target)
                finally:
                    temporary.unlink(missing_ok=True)
        except Exception:
            for target in created: target.unlink(missing_ok=True)
            raise

    def _materialize_issue_screenshot(self, scan: dict, raw: dict, operation_id: str, revision: int) -> dict:
        screenshot_id = self._stable_id("screenshot", operation_id, "issue")
        extension = "jpg" if raw["imageType"] == "jpeg" else raw["imageType"]
        issue = {key: raw[key] for key in ("scanId", "pageStateRef", "objectRef", "imageType", "width", "height", "problemBoundingBox", "sourceBoundingBox", "annotation", "sanitizationPolicyVersion", "sanitizationStatus")}
        if raw.get("caseRef"):
            issue["caseRef"] = raw["caseRef"]
        issue.update({"screenshotId": screenshot_id, "kind": "issue", "status": "captured", "rawVisualRef": raw["screenshotId"],
                      "path": f"screenshots/{screenshot_id}.{extension}", "digest": raw["digest"],
                      "capturedAt": self._now(), "capturedAtRevision": revision})
        return issue

    @staticmethod
    def _read_screenshot(output_dir: str, screenshot: dict) -> bytes:
        root = Path(output_dir).resolve()
        relative = Path(screenshot["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("截图路径越出 outputDir")
        path = (root / relative).resolve()
        if path == root or root not in path.parents or not path.is_file():
            raise ValueError("Raw Visual 文件不存在于 outputDir")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != screenshot["digest"]:
            raise ValueError("Raw Visual 文件完整性校验失败")
        return content

    @staticmethod
    def _write_screenshot(output_dir: str, relative_path: str, image_bytes: bytes, expected_digest: str):
        if not output_dir:
            raise ValueError("Scan 未配置 outputDir")
        target = Path(output_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest:
                raise ValueError("同名截图文件内容冲突")
            return False
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(image_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    @staticmethod
    def _validate_recovery_attempt(attempt: RecoveryAttempt, method: str):
        if attempt.method != method or attempt.outcome not in {"restored", "uncertain", "failed"}:
            raise ValueError("恢复适配器返回未知状态")
        if not attempt.checks:
            raise ValueError("恢复结果缺少检查项")
        if any(check.dimension not in {"url_route", "page_layer", "active_tab", "overlay_state", "control_state", "object_identity", "pending_requests", "write_request", "local_visual"} or check.outcome not in {"match", "mismatch", "unknown"} for check in attempt.checks):
            raise ValueError("恢复检查项无效")
        dimensions = [check.dimension for check in attempt.checks]
        if len(dimensions) != len(set(dimensions)) or not set(RECOVERY_DIMENSIONS[:-1]).issubset(dimensions):
            raise ValueError("恢复结果缺少必检维度或包含重复维度")
        outcomes = {check.outcome for check in attempt.checks}
        if attempt.outcome == "restored" and outcomes != {"match"}:
            raise ValueError("restored 不能包含 unknown 或 mismatch")
        if attempt.outcome == "uncertain" and ("unknown" not in outcomes or "mismatch" in outcomes):
            raise ValueError("uncertain 必须包含 unknown 且不能包含 mismatch")
        if attempt.outcome == "failed" and not attempt.reason:
            raise ValueError("failed 必须携带原因")

    @staticmethod
    def _all_checks_match(attempt: RecoveryAttempt) -> bool:
        return bool(attempt.checks) and all(check.outcome == "match" for check in attempt.checks)

    @staticmethod
    def _has_unknown(attempt: RecoveryAttempt) -> bool:
        return any(check.outcome == "unknown" for check in attempt.checks)

    @staticmethod
    def _has_pollution_uncertainty(attempt: RecoveryAttempt) -> bool:
        return any(check.dimension in {"pending_requests", "write_request"} and check.outcome != "match" for check in attempt.checks)

    @staticmethod
    def _recovery_attempt_dict(attempt: RecoveryAttempt) -> dict:
        item = {"method": attempt.method, "outcome": attempt.outcome,
                "checks": [{"dimension": check.dimension, "outcome": check.outcome, "expected": check.expected, "observed": check.observed} for check in attempt.checks]}
        if attempt.reason:
            item["reason"] = {"code": "RECOVERY_FAILED", "message": attempt.reason}
        return item

    @staticmethod
    def _unknown_recovery_attempt(method: str, reason: str) -> RecoveryAttempt:
        return RecoveryAttempt(method, "failed", tuple(RecoveryCheck(dimension, "unknown") for dimension in RECOVERY_DIMENSIONS), reason)

    def _materialize_object_verification(self, scan, source_kind, source, outcome, operation):
        object_id = source.get("objectId") if source_kind == "audit_object" else None
        audit_object = None
        replacement = None
        if outcome.status == "matched":
            match = outcome.match
            object_id = object_id or self._stable_id("object", scan["scanId"], source["candidateId"])
            audit_object = {"objectId":object_id,"scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],
                            "kind":source["kind"],"status":"eligible","runtimeObserved":True,
                            "identity":{"hostLocatorId":match.host_locator_id,"fingerprint":self._digest(match.identity_material),
                                        "algorithmVersion":"1.0.0","role":match.role,"accessibleName":match.accessible_name,
                                        "visibleText":match.visible_text},"rebindStatus":"matched","lastVerifiedAtRevision":scan["runRevision"],
                            "location":{"boundingBox":{"x":match.x,"y":match.y,"width":match.width,"height":match.height},
                                        "visible":True,"viewportWidth":match.viewport_width,"viewportHeight":match.viewport_height},
                            "potentialRules":source["potentialRules"],"assessmentRefs":[],"observedAt":self._now()}
        elif source_kind == "audit_object":
            audit_object = dict(source)
            audit_object.update({"status":"blocked","rebindStatus":outcome.status,
                                 "blockedReason":{"code":f"OBJECT_{outcome.status.upper()}","message":"对象无法可靠重新绑定。"},
                                 "lastVerifiedAtRevision":scan["runRevision"]})
        if outcome.status == "changed":
            match = outcome.match
            replacement = {"candidateId":self._stable_id("candidate", source["pageStateRef"], match.identity_material),
                           "scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],"kind":source["kind"],
                           "label":match.accessible_name or source.get("label", source["kind"]),"role":match.role,
                           "locatorDigest":self._digest(match.identity_material),"potentialRules":source["potentialRules"],
                           "observedAtRevision":scan["runRevision"]}
        verification = {"verificationId":self._stable_id("verification", operation.get("operation_id") or operation["operationId"]),
                        "scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],"sourceKind":source_kind,
                        "sourceRef":source.get("candidateId") or source.get("objectId"),"outcome":outcome.status,
                        "candidateCount":outcome.candidate_count,"matchedDimensions":list(outcome.matched_dimensions),
                        "excludedReasons":list(outcome.excluded_reasons),"changedDimensions":list(outcome.changed_dimensions),
                        "observedAt":self._now(),"observedAtRevision":scan["runRevision"]}
        if object_id and outcome.status == "matched":
            verification["objectRef"] = object_id
        return verification, audit_object, replacement

    @classmethod
    def _sanitized_parameters(cls, value):
        if isinstance(value, dict):
            return {str(key): cls._sanitized_parameters(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._sanitized_parameters(item) for item in value]
        if isinstance(value, str):
            return {"valueClass": "redacted_string", "length": len(value)}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return {"valueClass": "redacted_unknown"}

    @staticmethod
    def _case_action(action: dict, before_page_state_id: str) -> dict:
        item = {key: action[key] for key in ("actionId", "type", "targetObjectRef", "intent", "parameters", "safetyOutcome", "atRunRevision")}
        if action["safetyOutcome"] == "allowed":
            recovery_mode = "inverse" if action["type"] in {"expand", "collapse"} else ("noop" if action["type"] in {"focus", "scroll"} else "refresh_only")
            item.update({"beforeStateEvidenceRefs": [before_page_state_id], "recoveryMode": recovery_mode})
            if recovery_mode == "inverse":
                inverse_type = {"expand": "collapse", "collapse": "expand"}[action["type"]]
                item["inverseAction"] = {"type": inverse_type, "targetObjectRef": action.get("targetObjectRef"), "parameters": {}}
        if action.get("blockReason"):
            item["blockReason"] = action["blockReason"]
        if action.get("requestObservationRefs"):
            item["requestObservationRefs"] = list(action["requestObservationRefs"])
        return item

    @staticmethod
    def _validate_identity_outcome(outcome):
        if outcome.status not in {"matched", "not_found", "ambiguous", "changed"}:
            raise HostError("INTERNAL_FAILURE", "对象身份适配器返回未知状态")
        if outcome.status == "matched" and (outcome.candidate_count != 1 or not outcome.match or not outcome.matched_dimensions):
            raise HostError("INTERNAL_FAILURE", "matched 必须恰好一个候选、完整匹配材料和匹配维度")
        if outcome.status == "not_found" and (outcome.candidate_count != 0 or outcome.match):
            raise HostError("INTERNAL_FAILURE", "not_found 必须为零候选且不得携带匹配对象")
        if outcome.status == "ambiguous" and (outcome.candidate_count < 2 or outcome.match):
            raise HostError("INTERNAL_FAILURE", "ambiguous 必须至少两个候选且不得选择对象")
        if outcome.status == "changed" and (outcome.candidate_count < 1 or not outcome.match or not outcome.changed_dimensions):
            raise HostError("INTERNAL_FAILURE", "changed 必须携带相关对象和变化维度")

    def _finish_operation_failure(self, request, scan, operation, code, message):
        operation.update({"status":"failed_known","errorCode":code,"errorMessage":message})
        with self._store.transaction():
            self._store.update_operation(operation)
        return self._response(request, scan, "rejected", error=HostError(code, message), operation=operation)

    def _accept_operation(self, request: dict, scan: dict, *, implemented: bool = False) -> tuple[dict, bool]:
        digest = self._request_digest(request)
        with self._store.transaction():
            existing = self._store.get_by_idempotency(scan["scanId"], request["idempotencyKey"])
            if existing:
                self._assert_same_digest(existing, digest, "幂等键对应不同请求摘要")
                return existing, True
            current_scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            self._check_revision(request, current_scan)
            operation = self._new_operation(request, scan["scanId"], TOOL_KINDS[request["tool"]], digest, "running" if implemented else "rejected", current_scan["runRevision"])
            if not implemented:
                operation.update({"errorCode":"INTERNAL_FAILURE","errorMessage":f"工具 {request['tool']} 尚未接入 Host 适配器"})
            self._store.insert_operation(operation)
        return operation, False

    def _operation_response(self, request: dict, scan: dict, operation: dict) -> dict:
        status = operation["status"]
        tool = operation.get("tool")
        if status in {"running", "succeeded"} and tool == "start_audit":
            return self._response(request, scan, "ok", result=self._start_result(scan), operation=operation)
        if status == "succeeded" and operation.get("result_json"):
            saved = json.loads(operation["result_json"])
            evidence_refs = [saved["evidenceId"]] if tool == "capture_evidence" and saved.get("evidenceId") else []
            if tool == "prepare_decision" and saved.get("pendingDecisionId"):
                pending = self._store.get_pending_decision(saved["pendingDecisionId"])
                evidence_refs = list(pending.get("evidenceRefs", [])) if pending else []
            if tool == "commit_decision" and saved.get("assessmentId"):
                assessment = self._store.get_assessment(saved["assessmentId"])
                evidence_refs = list(assessment.get("evidenceRefs", [])) if assessment else []
            return self._response(request, scan, "ok", result=saved, operation=operation, evidence_refs=evidence_refs)
        code = operation.get("error_code") or operation.get("errorCode") or ("OPERATION_IN_PROGRESS" if status == "running" else "OPERATION_RESULT_UNKNOWN")
        message = operation.get("error_message") or operation.get("errorMessage") or "Operation 尚未产生确定结果"
        saved_result = operation.get("result_json") or operation.get("resultJson")
        return self._response(request, scan, "failed" if scan["status"] == "failed" else "rejected",
                              result=json.loads(saved_result) if saved_result else None,
                              error=HostError(code, message), operation=operation)

    def _require_scan(self, request: dict) -> dict:
        row = self._store.get_scan_by_run(request["scanId"], request["runId"])
        if not row:
            raise HostError("UNKNOWN_REFERENCE", "Scan 或 Run 不存在")
        return self._scan_from_row(row)

    @staticmethod
    def _scan_from_row(row: dict) -> dict:
        return {"scanId":row["scan_id"],"runId":row["run_id"],"runRevision":row["run_revision"],
                "status":row["status"],"loginStatus":row["login_status"],"createdAt":row["created_at"],
                "ruleRegistryDigest":row["rule_registry_digest"],"currentPageStateId":row["current_page_state_id"],
                "capabilitiesJson":row["capabilities_json"],"outputDir":row["output_dir"],"entryUrl":row["entry_url"]}

    def _check_revision(self, request: dict, scan: dict) -> None:
        if request["expectedRunRevision"] != scan["runRevision"]:
            raise HostError("STALE_STATE", "expectedRunRevision 已过期", retryable=True, next_step="inspect_current_state")

    @staticmethod
    def _assert_same_digest(operation: dict, digest: str, message: str) -> None:
        if (operation.get("request_digest") or operation.get("requestDigest")) != digest:
            raise HostError("IDEMPOTENCY_CONFLICT", message)

    def _validate_envelope(self, request: dict) -> None:
        errors = sorted(self._envelope_validator.iter_errors(request), key=lambda e: list(e.path))
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_request")
        if request["tool"] not in TOOL_KINDS and request["tool"] != "get_operation":
            raise HostError("UNKNOWN_TOOL", f"未知工具: {request['tool']}")

    def _validate_tool_input(self, tool: str, value: dict) -> None:
        name = {"start_audit":"startAuditInput","inspect_page":"inspectPageInput","inspect_object":"inspectObjectInput",
                "begin_case":"beginCaseInput","perform_action":"performActionInput","restore_case":"restoreCaseInput",
                "inspect_source":"inspectSourceInput","capture_evidence":"captureEvidenceInput","prepare_decision":"prepareDecisionInput",
                "commit_decision":"commitDecisionInput","get_operation":"getOperationInput","complete_audit":"completeAuditInput"}[tool]
        errors = sorted(self._contract_validator(name).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_tool_input")

    def _validate_tool_output(self, tool: str, value: dict) -> None:
        name = {"start_audit":"startAuditOutput","inspect_page":"inspectPageOutput","inspect_object":"inspectObjectOutput",
                "begin_case":"beginCaseOutput","perform_action":"performActionOutput","restore_case":"restoreCaseOutput",
                "inspect_source":"inspectSourceOutput","capture_evidence":"captureEvidenceOutput","prepare_decision":"prepareDecisionOutput",
                "commit_decision":"commitDecisionOutput","get_operation":"getOperationOutput","complete_audit":"completeAuditOutput"}[tool]
        errors = sorted(self._contract_validator(name).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INTERNAL_FAILURE", f"Host 工具输出不符合契约: {errors[0].message}")

    @staticmethod
    def _validate_entity(validator, value, name):
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INTERNAL_FAILURE", f"Host 生成的 {name} 不符合 Schema: {errors[0].message}")

    @staticmethod
    def _new_operation(request, scan_id, kind, digest, status, revision):
        operation = {"operationId":HostCore._new_id("operation"),"scanId":scan_id,"requestId":request["requestId"],
                "tool":request["tool"],"operationKind":kind,"idempotencyKey":request["idempotencyKey"],
                "requestDigest":digest,"status":status,"acceptedAtRevision":revision}
        if request.get("input", {}).get("caseId"):
            operation["caseRef"] = request["input"]["caseId"]
        return operation

    @staticmethod
    def _digest(value) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _request_digest(self, request: dict) -> str:
        return self._digest({"tool":request["tool"],"input":request["input"]})

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def _stable_id(self, prefix: str, *parts: str) -> str:
        return f"{prefix}-{self._digest(list(parts))[:24]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _response(self, request, scan, status, *, result=None, error=None, operation=None, evidence_refs=None, diagnostic_refs=None):
        response = {"protocolVersion":request["protocolVersion"],"requestId":request["requestId"],"scanId":scan["scanId"],"runId":scan["runId"],"runRevision":scan["runRevision"],"status":status,"evidenceRefs":list(evidence_refs or []),"diagnosticRefs":list(diagnostic_refs or [])}
        if result is not None:
            if operation is not None:
                result.setdefault("operationId", operation.get("operation_id") or operation.get("operationId"))
            self._validate_tool_output(request["tool"], result)
            response["result"] = result
        if error is not None:
            response["error"] = error.as_dict()
        return response

    def close(self) -> None:
        try:
            self._credential_vault.clear_all()
        finally:
            self._store.close()
