from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from .auth import CredentialVault, LoginAdapter, UnavailableLoginAdapter
from .errors import HostError
from .page import DeterministicPageAdapter, ReadOnlyPageAdapter
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
                 page_adapter: ReadOnlyPageAdapter | None = None):
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
        self._store = store or SQLiteStore()
        self._credential_vault = credential_vault or CredentialVault()
        self._login_adapter = login_adapter or UnavailableLoginAdapter()
        self._page_adapter = page_adapter or DeterministicPageAdapter()
        registry_path = root.parent / "rules" / "registry.json"
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {"registryVersion":"0.0.0","digest":"0"*64}
        self._rule_registry_version = registry["registryVersion"]
        self._rule_registry_digest = registry["digest"]
        self._rules_by_kind = {}
        for rule in registry.get("rules", []):
            if rule.get("status") != "enabled":
                continue
            for kind in rule["objectKinds"]:
                self._rules_by_kind.setdefault(kind, []).append({"ruleId":rule["ruleId"],"version":rule["version"]})

    @property
    def credential_vault(self) -> CredentialVault:
        return self._credential_vault

    def handle(self, request: dict) -> dict:
        self._validate_envelope(request)
        tool = request["tool"]
        self._validate_tool_input(tool, request["input"])
        if tool == "start_audit":
            return self._start_audit(request)
        scan = self._require_scan(request)
        if tool == "get_operation":
            return self._get_operation(request, scan)
        operation, repeated = self._accept_operation(request, scan, implemented=tool == "inspect_page")
        if repeated:
            return self._operation_response(request, scan, operation)
        if tool == "inspect_page":
            return self._inspect_page(request, scan, operation)
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
            }
            operation = self._new_operation(request, scan["scanId"], "bootstrap", digest, "running", 0)
            self._store.insert_scan(scan)
            self._store.insert_operation(operation)
            self._store.insert_bootstrap_key(request["idempotencyKey"], operation["operationId"], digest)

        secret = self._credential_vault.consume(request["input"]["credentialHandle"])
        if secret is None:
            return self._finish_bootstrap_failure(request, scan, operation, "CREDENTIAL_CHANNEL_FAILED", "凭据句柄不存在、已过期或已消费")
        try:
            login = self._login_adapter.authenticate(request["input"]["url"], secret)
        except Exception:
            return self._finish_bootstrap_failure(request, scan, operation, "INTERNAL_FAILURE", "登录适配器执行失败")
        finally:
            del secret
        if login.status != "succeeded":
            return self._finish_bootstrap_failure(request, scan, operation, "LOGIN_FAILED", login.reason or "登录失败")

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
        return self._response(request, scan, "ok", result={"operationId":op["operation_id"],"status":op["status"],"requestDigest":op["request_digest"]})

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
            except HostError:
                raise
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
            return self._response(request, scan, "ok", result=json.loads(operation["result_json"]), operation=operation)
        code = operation.get("error_code") or operation.get("errorCode") or "OPERATION_RESULT_UNKNOWN"
        message = operation.get("error_message") or operation.get("errorMessage") or "Operation 尚未产生确定结果"
        return self._response(request, scan, "failed" if scan["status"] == "failed" else "rejected", error=HostError(code, message), operation=operation)

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
                "capabilitiesJson":row["capabilities_json"]}

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
        return {"operationId":HostCore._new_id("operation"),"scanId":scan_id,"requestId":request["requestId"],
                "tool":request["tool"],"operationKind":kind,"idempotencyKey":request["idempotencyKey"],
                "requestDigest":digest,"status":status,"acceptedAtRevision":revision}

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

    def _response(self, request, scan, status, *, result=None, error=None, operation=None):
        response = {"protocolVersion":request["protocolVersion"],"requestId":request["requestId"],"scanId":scan["scanId"],"runId":scan["runId"],"runRevision":scan["runRevision"],"status":status,"evidenceRefs":[],"diagnosticRefs":[]}
        if result is not None:
            if operation is not None:
                result.setdefault("operationId", operation.get("operation_id") or operation.get("operationId"))
            self._validate_tool_output(request["tool"], result)
            response["result"] = result
        if error is not None:
            response["error"] = error.as_dict()
        return response

    def close(self) -> None:
        self._store.close()
