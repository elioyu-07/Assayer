"""Transport-only JSON and MCP-shaped adapters for the Host Core (B08).

These adapters deliberately do not create IDs, inspect browsers, interpret
rules, or persist state.  They only frame a complete protocol envelope and
delegate it to one ``HostCore`` instance.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import logging
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

from jsonschema import Draft202012Validator

from .core import HostCore, TOOL_KINDS
from .errors import HostError
from .resources import default_rules_root, default_schema_root


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_TOOLS = tuple(TOOL_KINDS) + ("get_operation",)
_LOG = logging.getLogger(__name__)


_TOOL_INPUT_DEFS = {
    "start_audit": "startAuditInput",
    "get_rule_contract": "getRuleContractInput",
    "get_audit_progress": "getAuditProgressInput",
    "inspect_page": "inspectPageInput",
    "explore_entrypoint": "exploreEntrypointInput",
    "inspect_object": "inspectObjectInput",
    "begin_case": "beginCaseInput",
    "perform_action": "performActionInput",
    "restore_case": "restoreCaseInput",
    "inspect_source": "inspectSourceInput",
    "observe_page": "observePageInput",
    "capture_evidence": "captureEvidenceInput",
    "record_findings": "recordFindingsInput",
    "prepare_decision": "prepareDecisionInput",
    "commit_decision": "commitDecisionInput",
    "get_operation": "getOperationInput",
    "complete_audit": "completeAuditInput",
}


def _protocol_tool_schemas() -> dict[str, dict]:
    """Build MCP input schemas from the checked-in protocol contracts.

    FastMCP normally derives a schema from the Python function signature.  The
    transport deliberately accepts one ``request`` dictionary, so that default
    would be only ``{"type": "object"}`` and leaves a model guessing required
    lifecycle fields.  Keep the protocol contracts as the single source of
    truth and inline their small shared references for MCP clients that do not
    resolve external JSON Schema URLs.
    """
    root = default_schema_root()
    contracts = json.loads((root / "protocol" / "tool-contracts.schema.json").read_text())
    common = json.loads((root / "common.schema.json").read_text())
    envelope = json.loads((root / "protocol" / "envelope.schema.json").read_text())

    def resolve(value: object, seen: frozenset[str] = frozenset()) -> object:
        if isinstance(value, list):
            return [resolve(item, seen) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str):
            if reference.startswith("#/$defs/"):
                key = reference.rsplit("/", 1)[-1]
                if key in seen:
                    return {"type": "object"}
                target = contracts.get("$defs", {}).get(key)
                if target is not None:
                    return resolve(target, seen | {key})
                target = envelope.get("$defs", {}).get(key)
                if target is not None:
                    return resolve(target, seen | {key})
                target = common.get("$defs", {}).get(key)
                if target is not None:
                    return resolve(target, seen | {f"common:{key}"})
            if reference.startswith("../common.schema.json#/$defs/"):
                key = reference.rsplit("/", 1)[-1]
                target = common.get("$defs", {}).get(key)
                if target is not None:
                    return resolve(target, seen | {f"common:{key}"})
            # No input contract currently relies on an unresolved reference.
            # Preserve it rather than silently broadening an unknown schema.
            return deepcopy(value)
        return {key: resolve(item, seen) for key, item in value.items()}

    def field(name: str) -> dict:
        return deepcopy(envelope["$defs"][name])

    schemas: dict[str, dict] = {}
    for tool, definition in _TOOL_INPUT_DEFS.items():
        input_schema = resolve(contracts["$defs"][definition])
        if tool == "start_audit":
            # The dynamic MCP Runtime is the product-facing boundary. Keep
            # HostCore/JSON able to exercise credential fixtures, but make the
            # CLI contract impossible to guess: the field is authMode (not
            # mode), and the Router-owned values are fixed.
            input_schema["required"] = list(dict.fromkeys(
                [*input_schema.get("required", []), "authMode"]
            ))
            input_schema.setdefault("properties", {})["authMode"] = {"const": "anonymous"}
            input_schema["properties"]["outputDir"] = {"const": "auto"}
            input_schema["properties"]["browserProfile"] = {"const": "default"}
        request_properties = {
            "protocolVersion": field("protocolVersion"),
            "requestId": field("id"),
            "agentTurnId": field("id"),
            "decisionReason": {"type": "string", "minLength": 1, "maxLength": 480},
            "modelTelemetry": field("modelTelemetry"),
            "tool": {"const": tool},
            "idempotencyKey": field("nonEmpty"),
            "input": input_schema,
        }
        # MCP is the model-facing boundary: require a short public purpose so
        # real CLI runs produce an accountable decision trace. The lower-level
        # JSON/Harness protocol remains backward compatible for existing tools.
        required = [
            "protocolVersion", "requestId", "agentTurnId", "decisionReason",
            "tool", "idempotencyKey", "input",
        ]
        if tool != "start_audit":
            request_properties.update({"scanId": field("id"), "runId": field("id"),
                                        "expectedRunRevision": {"type": "integer", "minimum": 0}})
            required.insert(2, "scanId")
            required.insert(3, "runId")
            required.insert(6, "expectedRunRevision")
        schemas[tool] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["request"],
            "properties": {
                "request": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": required,
                    "properties": request_properties,
                }
            },
            "description": "Complete Assayer protocol envelope in arguments.request; input follows the tool contract.",
        }
    return schemas


class JsonLineTransport:
    """Invoke HostCore using one JSON request/response per input line."""

    def __init__(self, core: HostCore):
        self.core = core

    @staticmethod
    def _safe_request_id(value: object) -> str:
        return value if isinstance(value, str) and _ID.fullmatch(value) else "transport-error"

    def invoke(self, request: object) -> dict:
        request_id = self._safe_request_id(request.get("requestId") if isinstance(request, dict) else None)
        try:
            if not isinstance(request, dict):
                raise HostError("INVALID_REQUEST", "JSON request must be an object")
            return self.core.handle(request)
        except HostError as error:
            return self._error_response(request if isinstance(request, dict) else {}, request_id, error)
        except Exception:
            # Do not expose parser/adapter stack traces or request contents over
            # an untrusted transport boundary.
            _LOG.exception("unhandled Host transport failure requestId=%s", request_id)
            return self._error_response(request if isinstance(request, dict) else {}, request_id,
                                        HostError("INTERNAL_FAILURE", "Host encountered an internal failure while processing the request"))

    def _error_response(self, request: dict, request_id: str, error: HostError) -> dict:
        terminal_error_codes = {
            "INTERNAL_FAILURE", "CREDENTIAL_CHANNEL_FAILED", "AGENT_CONTROL_BUDGET_EXCEEDED",
            "AGENT_LEASE_EXPIRED", "AGENT_RUNTIME_EXITED", "LEDGER_EXPORT_FAILED",
        }
        status = "failed" if error.code in terminal_error_codes else "rejected"
        scan_id = request.get("scanId")
        run_id = request.get("runId")
        if isinstance(scan_id, str) and isinstance(run_id, str) and _ID.fullmatch(scan_id) and _ID.fullmatch(run_id):
            revision = 0
            try:
                state_reader = getattr(self.core, "protocol_state", None)
                if callable(state_reader):
                    try:
                        state = state_reader(scan_id, run_id)
                    except TypeError:
                        state = state_reader()
                    if state and state.get("scanId") == scan_id and state.get("runId") == run_id:
                        revision = int(state.get("runRevision", 0))
                else:
                    scan = self.core._store.get_scan(scan_id)
                    if scan and scan.get("run_id") == run_id:
                        revision = int(scan.get("run_revision", 0))
            except Exception:
                revision = 0
            return {"protocolVersion": request.get("protocolVersion", "1.0"), "requestId": request_id,
                    "scanId": scan_id, "runId": run_id, "runRevision": max(0, revision),
                    "status": status, "error": error.as_dict(),
                    "evidenceRefs": [], "diagnosticRefs": []}
        return {"protocolVersion": request.get("protocolVersion", "1.0"), "requestId": request_id,
                "status": status, "error": error.as_dict(),
                "evidenceRefs": [], "diagnosticRefs": []}

    def serve(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> None:
        """Serve newline-delimited JSON until EOF; malformed lines stay isolated."""
        for line in input_stream:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                response = self._error_response({}, "transport-error", HostError("INVALID_REQUEST", "JSON request format is invalid"))
            else:
                response = self.invoke(request)
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()


class McpToolTransport:
    """MCP-shaped tool listing/call adapter over the same JSON envelope.

    ``arguments`` must contain the complete Assayer protocol request.  This
    keeps IDs and lifecycle fields caller-owned and prevents MCP from becoming
    a second business-logic implementation.
    """

    def __init__(self, core: HostCore):
        self._json = JsonLineTransport(core)
        # FastMCP invokes synchronous tools from its asyncio event loop.  The
        # real browser adapter intentionally uses Playwright's sync API, whose
        # greenlet is thread-affine.  Keep every request (including shutdown)
        # on one dedicated worker so a Scan's browser is never crossed between
        # the event-loop thread and another executor thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assayer-mcp")
        self._closed = False
        self._schemas = _protocol_tool_schemas()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        heartbeat = getattr(core, "heartbeat", None)
        interval = getattr(core, "heartbeat_interval_seconds", None)
        if callable(heartbeat) and isinstance(interval, (int, float)) and interval > 0:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(heartbeat, float(interval)),
                name="assayer-mcp-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def list_tools(self) -> list[dict]:
        return [{"name": name,
                 "description": (
                     f"Assayer Host {name} (transport adapter only)."
                     " arguments.request must be a complete protocol envelope."
                 ),
                 "inputSchema": deepcopy(self._schemas[name])} for name in _TOOLS]

    def call_tool(self, name: str, arguments: object) -> dict:
        if name not in _TOOLS:
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict) or arguments.get("tool") != name:
            raise HostError("INVALID_REQUEST", "MCP arguments must be a complete protocol envelope for the selected tool")
        if self._closed:
            raise HostError("RUNTIME_CLOSED", "MCP Runtime is closed")
        response = self._executor.submit(self._json.invoke, arguments).result()
        content = [{"type": "text", "text": json.dumps(response, ensure_ascii=False, separators=(",", ":"))}]
        if name == "observe_page" and response.get("status") == "ok":
            result = response.get("result", {})
            screenshot_ref = result.get("screenshotRef")
            visual = result.get("observation", {}).get("visual", {})
            reader = getattr(self._json.core, "read_screenshot", None)
            if screenshot_ref and visual.get("status") == "captured" and callable(reader):
                image, mime_type = self._executor.submit(
                    reader, response["scanId"], response["runId"], screenshot_ref
                ).result()
                content.append({"type": "image", "data": base64.b64encode(image).decode("ascii"), "mimeType": mime_type})
        return {"structuredContent": response,
                "content": content,
                "isError": response.get("status") != "ok"}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=2.0)
        # RuntimeRouter.close() must run on the same worker as Playwright.
        try:
            self._executor.submit(self._json.core.close).result()
        finally:
            self._executor.shutdown(wait=True)

    def _heartbeat_loop(self, heartbeat, interval: float) -> None:
        while not self._heartbeat_stop.wait(interval):
            try:
                self._executor.submit(heartbeat).result()
            except Exception:
                # EOF/close owns the terminal failure path.  A heartbeat must
                # never create a second shutdown path or expose internals.
                if self._heartbeat_stop.is_set():
                    return


class ProductMcpToolTransport(McpToolTransport):
    """Product-facing MCP facade that owns protocol framing and Scan state.

    The lower-level :class:`McpToolTransport` remains available for JSON/Harness
    compatibility and protocol tests.  The MCP server shipped to users uses
    this facade instead: the model supplies only a tool's business input and
    the facade generates every envelope/lifecycle field internally.
    """

    _PUBLIC_REASON = "Continue the Assayer audit with the requested operation."

    def __init__(self, core: HostCore):
        super().__init__(core)
        self._public_schemas = self._build_public_schemas()
        registry = json.loads((default_rules_root() / "registry.json").read_text())
        self._rule_registry_version = registry["registryVersion"]
        self._public_prefix = f"facade-{uuid.uuid4().hex[:12]}"
        self._public_turn = 0
        self._public_active: dict[str, object] | None = None
        self._public_pending_operation: str | None = None

    def _build_public_schemas(self) -> dict[str, dict]:
        schemas: dict[str, dict] = {}
        for name, schema in self._schemas.items():
            input_schema = deepcopy(schema["properties"]["request"]["properties"]["input"])
            if name == "start_audit":
                # Product defaults are internal.  The user/model only supplies
                # the target URL; the facade obtains the frozen registry
                # version from HostCore and injects anonymous Runtime values.
                input_schema = {
                    "type": "object", "additionalProperties": False,
                    "required": ["url"],
                    "properties": {"url": deepcopy(input_schema["properties"]["url"])},
                }
            elif name == "get_operation":
                # The facade records the exact unknown Operation internally;
                # the model only chooses the recovery action, never an ID.
                input_schema = {
                    "type": "object", "additionalProperties": False,
                    "properties": {},
                }
            elif name == "prepare_decision":
                # The preferred product path is atomic: the model supplies
                # dimension Findings together with the decision, and the
                # facade performs record -> prepare -> commit without giving
                # page navigation a chance to stale the object.  Keep the
                # legacy findingRefs shape accepted for old integrations.
                record_schema = deepcopy(self._schemas["record_findings"]["properties"]["request"]["properties"]["input"])
                input_schema = deepcopy(input_schema)
                input_schema.setdefault("properties", {})["findings"] = deepcopy(record_schema["properties"]["findings"])
                input_schema["required"] = [key for key in input_schema.get("required", []) if key != "findingRefs"]
                input_schema["anyOf"] = [{"required": ["findings"]}, {"required": ["findingRefs"]}]
            elif name == "complete_audit":
                input_schema = {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "completionReason": deepcopy(input_schema["properties"]["completionReason"]),
                    },
                }
            schemas[name] = input_schema
        return schemas

    def list_tools(self) -> list[dict]:
        tools = []
        for name in _TOOLS:
            schema = deepcopy(self._public_schemas[name])
            schema.setdefault("properties", {})["decisionReason"] = {
                "type": "string", "minLength": 1, "maxLength": 480,
            }
            tools.append({
                "name": name,
                "description": (
                    f"Assayer {name} (product entrypoint). Pass business parameters only; protocol version, request ID, "
                    "Scan/Run, revision, output paths, and runtime configuration are maintained internally by Assayer."
                    + ("For decision preparation, findings may be supplied once and the Facade atomically records, prepares, and commits them." if name == "prepare_decision" else "")
                ),
                "inputSchema": schema,
            })
        return tools

    def call_tool(self, name: str, arguments: object) -> dict:
        if name not in _TOOLS:
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", "MCP arguments must be a business-parameter object")
        public_schema = deepcopy(self._public_schemas[name])
        public_schema.setdefault("properties", {})["decisionReason"] = {
            "type": "string", "minLength": 1, "maxLength": 480,
        }
        validation_error = next(Draft202012Validator(public_schema).iter_errors(arguments), None)
        if validation_error is not None:
            raise HostError("INVALID_REQUEST", "MCP tool business parameters do not satisfy the product schema")
        reason = arguments.get("decisionReason", f"Run the Assayer {name} operation for the current audit.")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 480:
                raise HostError("INVALID_REQUEST", "decisionReason must be a 1-480 character public action explanation")
        input_data = {key: value for key, value in arguments.items() if key != "decisionReason"}
        if name == "start_audit":
            if self._public_active is not None:
                raise HostError("SCAN_IN_PROGRESS", "An audit is already running; complete or stop it first")
            url = input_data.get("url")
            if not isinstance(url, str) or not url:
                raise HostError("INVALID_REQUEST", "start_audit requires an HTTP(S) URL")
            input_data = {
                "url": url,
                "ruleRegistryVersion": self._rule_registry_version,
                "outputDir": "auto",
                "browserProfile": "default",
                "authMode": "anonymous",
            }
        else:
            if self._public_active is None:
                raise HostError("UNKNOWN_REFERENCE", "There is no audit to continue")
            if self._public_pending_operation is not None:
                if name != "get_operation" or input_data:
                    raise HostError(
                        "REQUEST_RESULT_UNKNOWN",
                        "The previous operation result is unknown; query the specified Assayer operation first",
                        next_step="call_get_operation",
                    )
                input_data = {"operationId": self._public_pending_operation}
            elif name == "get_operation":
                raise HostError("UNKNOWN_REFERENCE", "There is no operation with an unknown result to query")
        if name == "complete_audit":
            # Do not let the model copy a potentially stale progress snapshot
            # into the coverage proof.  The Runtime builds the exact closure
            # from its durable ledger on the owning worker thread.
            reason_text = input_data.get("completionReason")
            builder = getattr(self._json.core, "build_completion_input", None)
            if not callable(builder):
                raise HostError("INTERNAL_FAILURE", "The runtime does not support automatic coverage completion")
            input_data = self._executor.submit(
                builder, self._public_active["scanId"], self._public_active["runId"], reason_text
            ).result()
        if name == "prepare_decision" and "findings" in input_data:
            return self._atomic_prepare_decision(input_data, reason)
        envelope = self._next_envelope(name, input_data, reason)
        result = super().call_tool(name, envelope)
        response = result.get("structuredContent")
        if not isinstance(response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid response")
        self._update_public_state(name, response)
        public_response = {
            key: deepcopy(value) for key, value in response.items()
            if key not in {"protocolVersion", "requestId", "scanId", "runId", "runRevision"}
        }
        if isinstance(public_response.get("result"), dict):
            for key in ("scanId", "runId", "runRevision", "operationId"):
                public_response["result"].pop(key, None)
        public_content = [{"type": "text", "text": json.dumps(public_response, ensure_ascii=False, separators=(",", ":"))}]
        for block in result.get("content", [])[1:]:
            public_content.append(block)
        return {
            "structuredContent": public_response,
            "content": public_content,
            "isError": result.get("isError", False),
        }

    def _atomic_prepare_decision(self, input_data: dict, reason: str) -> dict:
        """Close a decision while the verified object remains on its page."""
        findings = input_data.pop("findings")
        record_input = {
            "objectId": input_data["objectId"],
            "rule": input_data["rule"],
            "findings": findings,
        }
        record_envelope = self._next_envelope("record_findings", record_input, reason)
        recorded = super().call_tool("record_findings", record_envelope)
        record_response = recorded.get("structuredContent")
        if not isinstance(record_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid Finding response")
        self._update_public_state("record_findings", record_response)
        if record_response.get("status") != "ok":
            return self._publicize(recorded, record_response)
        finding_refs = record_response.get("result", {}).get("findingRefs")
        if not isinstance(finding_refs, list) or not finding_refs:
            raise HostError("INTERNAL_FAILURE", "Host did not return a Finding reference")
        prepare_input = {key: value for key, value in input_data.items() if key != "findingRefs"}
        prepare_input["findingRefs"] = finding_refs
        prepare_envelope = self._next_envelope("prepare_decision", prepare_input, reason)
        prepared = super().call_tool("prepare_decision", prepare_envelope)
        prepare_response = prepared.get("structuredContent")
        if not isinstance(prepare_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid PendingDecision response")
        self._update_public_state("prepare_decision", prepare_response)
        if prepare_response.get("status") != "ok":
            return self._publicize(prepared, prepare_response)
        pending_id = prepare_response.get("result", {}).get("pendingDecisionId")
        if not isinstance(pending_id, str):
            raise HostError("INTERNAL_FAILURE", "Host did not return a PendingDecision reference")
        commit_envelope = self._next_envelope("commit_decision", {"pendingDecisionId": pending_id}, reason)
        committed = super().call_tool("commit_decision", commit_envelope)
        commit_response = committed.get("structuredContent")
        if not isinstance(commit_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid Assessment response")
        self._update_public_state("commit_decision", commit_response)
        return self._publicize(committed, commit_response)

    def _publicize(self, result: dict, response: dict) -> dict:
        public_response = {
            key: deepcopy(value) for key, value in response.items()
            if key not in {"protocolVersion", "requestId", "scanId", "runId", "runRevision"}
        }
        if isinstance(public_response.get("result"), dict):
            for key in ("scanId", "runId", "runRevision", "operationId"):
                public_response["result"].pop(key, None)
        public_content = [{"type": "text", "text": json.dumps(public_response, ensure_ascii=False, separators=(",", ":"))}]
        for block in result.get("content", [])[1:]:
            public_content.append(block)
        return {"structuredContent": public_response, "content": public_content,
                "isError": result.get("isError", False)}

    def _next_envelope(self, name: str, input_data: dict, reason: str) -> dict:
        self._public_turn += 1
        turn = self._public_turn
        request_id = f"{self._public_prefix}-request-{turn:04d}"
        if name == "start_audit":
            digest = hashlib.sha256(json.dumps(input_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
            return {
                "protocolVersion": "1.0", "requestId": request_id,
                "agentTurnId": f"{self._public_prefix}-turn-{turn:04d}",
                "decisionReason": reason, "tool": name,
                "idempotencyKey": f"{self._public_prefix}-bootstrap-{digest}",
                "input": input_data,
            }
        active = self._public_active
        assert active is not None
        signature = hashlib.sha256(json.dumps({"tool": name, "input": input_data}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return {
            "protocolVersion": "1.0", "requestId": request_id,
            "scanId": active["scanId"], "runId": active["runId"],
            "agentTurnId": f"{self._public_prefix}-turn-{turn:04d}",
            "decisionReason": reason, "tool": name,
            "idempotencyKey": f"{self._public_prefix}-{turn:04d}-{signature}",
            "expectedRunRevision": active["runRevision"], "input": input_data,
        }

    def _update_public_state(self, name: str, response: dict) -> None:
        if self._public_active is None and name == "start_audit" and response.get("status") == "ok":
            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("scanId"), str) and isinstance(result.get("runId"), str):
                self._public_active = {
                    "scanId": result["scanId"], "runId": result["runId"],
                    "runRevision": response.get("runRevision", result.get("runRevision", 0)),
                }
                return
        if self._public_active is not None and isinstance(response.get("runRevision"), int):
            self._public_active["runRevision"] = response["runRevision"]
        result = response.get("result")
        error = response.get("error")
        result_unknown = (
            isinstance(error, dict)
            and error.get("code") in {"REQUEST_RESULT_UNKNOWN", "OPERATION_RESULT_UNKNOWN"}
        ) or (isinstance(result, dict) and result.get("resultStatus") == "result_unknown")
        if result_unknown and isinstance(result, dict) and isinstance(result.get("operationId"), str):
            self._public_pending_operation = result["operationId"]
        elif name == "get_operation" and isinstance(result, dict) and result.get("status") != "result_unknown":
            self._public_pending_operation = None
        terminal = isinstance(result, dict) and result.get("scanStatus") in {"completed", "partial", "failed"}
        if response.get("status") == "failed" or terminal:
            self._public_active = None
            self._public_pending_operation = None


def _load_fast_mcp():
    """Load FastMCP after resolving its deferred Settings annotations."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.server import Settings
    except ImportError as error:
        raise RuntimeError("MCP SDK is not installed; install assayer[mcp]") from error
    # MCP 1.27 defines Settings under postponed annotations.  Rebuilding the
    # model before its first instance prevents pydantic-settings from treating
    # the lifespan field as an unresolved forward reference.
    Settings.model_rebuild()
    return FastMCP


def create_mcp_server(core: HostCore):
    """Create an optional official-SDK stdio server without making MCP required."""
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer")
    adapter = McpToolTransport(core)
    # Keep the transport available to mcp_main for thread-affine shutdown.
    server._assayer_transport = adapter

    def make_invoke(tool_name: str):
        def invoke(request: dict) -> dict:
            return adapter.call_tool(tool_name, request)

        invoke.__name__ = f"assayer_{tool_name}"
        return invoke

    for item in adapter.list_tools():
        name = item["name"]
        server.tool(name=name, description=item["description"])(make_invoke(name))
        # FastMCP derives parameters from ``request: dict`` above.  Replace
        # that opaque generated schema with the checked-in protocol contract;
        # invocation still receives the same complete request dictionary.
        registered = server._tool_manager.get_tool(name)
        if registered is not None:
            registered.parameters = deepcopy(item["inputSchema"])
    return server


def create_product_mcp_server(core: HostCore):
    """Create the user-facing MCP server with an internal protocol facade."""
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer")
    adapter = ProductMcpToolTransport(core)
    server._assayer_transport = adapter

    def make_invoke(tool_name: str, input_schema: dict):
        def invoke(**kwargs: Any) -> dict:
            arguments = {key: value for key, value in kwargs.items() if value is not None}
            return adapter.call_tool(tool_name, arguments)
        invoke.__name__ = f"assayer_{tool_name}"
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))
        ordered = [key for key in properties if key in required]
        ordered.extend(key for key in properties if key not in required)
        invoke.__signature__ = inspect.Signature(parameters=[
            inspect.Parameter(
                key,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Any,
                default=inspect.Parameter.empty if key in required else None,
            )
            for key in ordered
        ])
        return invoke

    for item in adapter.list_tools():
        name = item["name"]
        server.tool(name=name, description=item["description"])(make_invoke(name, item["inputSchema"]))
        registered = server._tool_manager.get_tool(name)
        if registered is not None:
            registered.parameters = deepcopy(item["inputSchema"])
    return server


def mcp_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assayer dynamic real-browser MCP stdio server")
    parser.add_argument("--output-root", default="./assayer-output", help="Fixed parent directory for all Scan output directories")
    parser.add_argument("--max-runtimes", type=int, default=4)
    parser.add_argument("--lease-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    from .runtime_router import RuntimeRouter
    core = RuntimeRouter(args.output_root, max_runtimes=args.max_runtimes,
                         lease_timeout_seconds=args.lease_timeout)
    server = create_product_mcp_server(core)
    try:
        server.run(transport="stdio")
    finally:
        transport = getattr(server, "_assayer_transport", None)
        if transport is not None:
            transport.close()
        else:
            core.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assayer dynamic JSON-lines Runtime Router")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdio", action="store_true", help="Read JSON requests from stdin and write responses to stdout")
    mode.add_argument("--mcp", action="store_true", help="Start the stdio MCP server")
    parser.add_argument("--output-root", default="./assayer-output", help="Fixed parent directory for all Scan output directories")
    parser.add_argument("--max-runtimes", type=int, default=4)
    parser.add_argument("--lease-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    from .runtime_router import RuntimeRouter
    core = RuntimeRouter(args.output_root, max_runtimes=args.max_runtimes,
                         lease_timeout_seconds=args.lease_timeout)
    try:
        if args.mcp:
            create_mcp_server(core).run(transport="stdio")
        else:
            JsonLineTransport(core).serve()
    finally:
        core.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
