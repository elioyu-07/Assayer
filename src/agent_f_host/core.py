from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from .errors import HostError


TOOL_KINDS = {
    "start_audit": "bootstrap",
    "inspect_page": "read",
    "inspect_object": "read",
    "begin_case": "lifecycle",
    "perform_action": "browser_action",
    "restore_case": "recovery",
    "inspect_source": "read",
    "capture_evidence": "read",
    "prepare_decision": "decision_preparation",
    "commit_decision": "decision_commit",
    "complete_audit": "lifecycle",
}


@dataclass(frozen=True)
class Operation:
    operation_id: str
    scan_id: str
    request_id: str
    tool: str
    operation_kind: str
    idempotency_key: str
    request_digest: str
    status: str
    accepted_at_revision: int


class HostCore:
    """In-memory protocol/operation core used by the first vertical slice.

    Browser and persistence adapters are intentionally absent. The core still
    enforces envelope shape, tool input shape, session identity, optimistic
    concurrency and idempotency before an adapter can be called.
    """

    def __init__(self, schema_root: str | Path | None = None):
        root = Path(schema_root or Path(__file__).resolve().parents[2] / "schemas")
        self._schemas = {}
        for path in root.rglob("*.schema.json"):
            data = json.loads(path.read_text())
            self._schemas[data["$id"]] = data
            self._schemas[path.name] = data
        envelope = self._schemas["envelope.schema.json"]
        store = dict(self._schemas)
        self._envelope_validator = Draft202012Validator(
            envelope, resolver=RefResolver(envelope["$id"], envelope, store=store)
        )
        contracts = self._schemas["tool-contracts.schema.json"]
        self._contracts = contracts["$defs"]
        self._contract_validator = lambda name: Draft202012Validator(
            contracts["$defs"][name], resolver=RefResolver(contracts["$id"], contracts, store=store)
        )
        self._scans: dict[str, dict] = {}
        self._operations: dict[str, Operation] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}

    def handle(self, request: dict) -> dict:
        self._validate_envelope(request)
        tool = request["tool"]
        if tool == "get_operation":
            self._validate_tool_input(tool, request["input"])
            return self._get_operation(request)
        self._validate_tool_input(tool, request["input"])
        if tool == "start_audit":
            return self._start_audit(request)
        scan = self._require_scan(request)
        operation = self._accept_operation(request, scan)
        # The adapter boundary is explicit: no browser side effect is attempted here.
        return self._response(request, scan, "rejected", error=HostError("INTERNAL_FAILURE", f"工具 {tool} 尚未接入 Host 适配器"), operation=operation)

    def _validate_envelope(self, request: dict) -> None:
        errors = sorted(self._envelope_validator.iter_errors(request), key=lambda e: e.path)
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_request")
        if request["tool"] not in TOOL_KINDS and request["tool"] != "get_operation":
            raise HostError("UNKNOWN_TOOL", f"未知工具: {request['tool']}")

    def _validate_tool_input(self, tool: str, value: dict) -> None:
        name = {
            "start_audit": "startAuditInput", "inspect_page": "inspectPageInput", "inspect_object": "inspectObjectInput",
            "begin_case": "beginCaseInput", "perform_action": "performActionInput", "restore_case": "restoreCaseInput",
            "inspect_source": "inspectSourceInput", "capture_evidence": "captureEvidenceInput", "prepare_decision": "prepareDecisionInput",
            "commit_decision": "commitDecisionInput", "get_operation": "getOperationInput", "complete_audit": "completeAuditInput",
        }[tool]
        errors = sorted(self._contract_validator(name).iter_errors(value), key=lambda e: e.path)
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_tool_input")

    def _start_audit(self, request: dict) -> dict:
        digest = self._request_digest(request)
        key = ("bootstrap", request["idempotencyKey"])
        existing = self._idempotency.get(key)
        if existing:
            op_id, old_digest = existing
            if old_digest != digest:
                raise HostError("IDEMPOTENCY_CONFLICT", "Bootstrap 幂等键对应不同请求摘要")
            scan = self._scans[self._operations[op_id].scan_id]
            return self._response(request, scan, "ok", result=self._start_result(scan), operation=self._operations[op_id])
        scan_id, run_id = self._new_id("scan"), self._new_id("run")
        scan = {"scanId": scan_id, "runId": run_id, "runRevision": 0, "status": "authenticating", "loginStatus": "pending"}
        self._scans[scan_id] = scan
        operation = self._record_operation(request, scan_id, "bootstrap", digest, "running")
        self._idempotency[key] = (operation.operation_id, digest)
        return self._response(request, scan, "ok", result=self._start_result(scan), operation=operation)

    def _start_result(self, scan: dict) -> dict:
        return {"scanId": scan["scanId"], "runId": scan["runId"], "loginStatus": scan["loginStatus"], "runRevision": scan["runRevision"]}

    def _get_operation(self, request: dict) -> dict:
        op = self._operations.get(request["input"]["operationId"])
        if not op:
            raise HostError("UNKNOWN_REFERENCE", "Operation 不存在")
        scan = self._scans[op.scan_id]
        return self._response(request, scan, "ok", result={"operationId": op.operation_id, "status": op.status, "requestDigest": op.request_digest})

    def _require_scan(self, request: dict) -> dict:
        scan = self._scans.get(request["scanId"])
        if not scan or scan["runId"] != request["runId"]:
            raise HostError("UNKNOWN_REFERENCE", "Scan 或 Run 不存在")
        return scan

    def _check_revision(self, request: dict, scan: dict) -> None:
        if request["expectedRunRevision"] != scan["runRevision"]:
            raise HostError("STALE_STATE", "expectedRunRevision 已过期", retryable=True, next_step="inspect_current_state")

    def _accept_operation(self, request: dict, scan: dict) -> Operation:
        digest = self._request_digest(request)
        key = (scan["scanId"], request["idempotencyKey"])
        existing = self._idempotency.get(key)
        if existing:
            op_id, old_digest = existing
            if old_digest != digest:
                raise HostError("IDEMPOTENCY_CONFLICT", "幂等键对应不同请求摘要")
            return self._operations[op_id]
        self._check_revision(request, scan)
        operation = self._record_operation(request, scan["scanId"], TOOL_KINDS[request["tool"]], digest, "rejected")
        self._idempotency[key] = (operation.operation_id, digest)
        return operation

    def _record_operation(self, request: dict, scan_id: str, kind: str, digest: str, status: str) -> Operation:
        op = Operation(self._new_id("operation"), scan_id, request["requestId"], request["tool"], kind, request["idempotencyKey"], digest, status, self._scans[scan_id]["runRevision"])
        self._operations[op.operation_id] = op
        return op

    @staticmethod
    def _digest(value: dict) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def _request_digest(self, request: dict) -> str:
        return self._digest({"tool": request["tool"], "input": request["input"]})

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def _response(self, request: dict, scan: dict, status: str, *, result=None, error=None, operation=None) -> dict:
        response = {"protocolVersion": request["protocolVersion"], "requestId": request["requestId"], "scanId": scan["scanId"], "runId": scan["runId"], "runRevision": scan["runRevision"], "status": status, "evidenceRefs": [], "diagnosticRefs": []}
        if result is not None: response["result"] = result
        if error is not None: response["error"] = error.as_dict()
        if operation is not None and result is not None and isinstance(result, dict): result.setdefault("operationId", operation.operation_id)
        return response
