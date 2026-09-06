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
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

from jsonschema import Draft202012Validator

from .core import HostCore, TOOL_KINDS
from .errors import HostError
from .plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport
from .plugin_store_registry import default_store_root, store_backed_plugin_registry
from .resources import default_schema_root
from assayer_platform import (
    PlatformRunner, PlatformContractError, PluginRegistry, InteractivePluginController,
)
from assayer_platform import installed_plugin_registry
from assayer_platform.plugin_lifecycle import package_checksum


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
            "PLATFORM_PERSISTENCE_FAILED", "PLUGIN_COMMIT_UNAVAILABLE",
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


class PlatformMcpToolTransport:
    """Generic plugin execution MCP facade for non-interactive Checks.

    This transport exposes no browser vocabulary and accepts only business
    scope. PlatformRunner owns plugin selection, Run identity, persistence,
    publication, and all internal protocol fields. Interactive domain plugins
    continue to use their explicit compatibility facade until they provide a
    domain-neutral interactive adapter.
    """

    TOOL = "run_plugin"
    LIST_TOOL = "list_plugins"

    def __init__(self, output_root: str | Path = "./assayer-output",
                 *, plugin_registry: PluginRegistry | None = None):
        self._registry = plugin_registry or installed_plugin_registry()
        self._runner = PlatformRunner(self._registry, output_root)

    def list_tools(self) -> list[dict]:
        return [{
            "name": self.LIST_TOOL,
            "description": "List registered plugins, Checks, capabilities, execution modes, and business scope schemas.",
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "properties": {},
            },
        }, {
            "name": self.TOOL,
            "description": (
                "Run one registered non-interactive plugin Check. Supply only "
                "pluginId, checkId, optional checkVersion, and business scope; "
                "Assayer owns Run IDs, protocol fields, output paths, and traces."
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "required": ["pluginId", "checkId", "scope"],
                "properties": {
                    "pluginId": {"type": "string", "minLength": 1},
                    "checkId": {"type": "string", "minLength": 1},
                    "checkVersion": {"type": "string", "minLength": 1},
                    "scope": {},
                },
            },
        }]

    def call_tool(self, name: str, arguments: object) -> dict:
        if name not in {self.TOOL, self.LIST_TOOL}:
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", f"{name} arguments must be an object")
        schema = next(item["inputSchema"] for item in self.list_tools() if item["name"] == name)
        validation_error = next(Draft202012Validator(schema).iter_errors(arguments), None)
        if validation_error is not None:
            raise HostError("INVALID_REQUEST", f"{name} arguments do not satisfy the plugin schema")
        if name == self.LIST_TOOL:
            payload = {"plugins": [{
                "pluginId": item.manifest.plugin_id,
                "version": item.manifest.version,
                "platformApiVersion": item.manifest.platform_api_version,
                "domains": list(item.manifest.domains),
                "subjectKinds": list(item.manifest.subject_kinds),
                "checks": [{"checkId": check.check_id, "version": check.version} for check in item.manifest.checks],
                "capabilities": sorted(item.capabilities),
                "executionModes": sorted(item.execution_modes),
                "scopeSchema": dict(item.scope_schema),
            } for item in sorted(self._registry.list(), key=lambda value: value.manifest.plugin_id)]}
            return {"structuredContent": {"status": "ok", "result": payload},
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}],
                    "isError": False}
        try:
            result = self._runner.run(
                plugin_id=arguments["pluginId"], check_id=arguments["checkId"],
                check_version=arguments.get("checkVersion"), scope=arguments["scope"],
            )
        except PlatformContractError as error:
            raise HostError(error.code, error.message) from error
        payload = {
            "runId": result.run_id,
            "pluginId": result.ledger.run.plugin_id if result.ledger else arguments["pluginId"],
            "check": {
                "checkId": result.ledger.run.check_id if result.ledger else arguments["checkId"],
                "version": result.ledger.run.check_version if result.ledger else arguments.get("checkVersion"),
            },
            "status": result.status,
            "decisions": [{"workItemId": item.work_item_id, "result": item.result} for item in result.decisions],
            "failures": [{"workItemId": item.work_item_id, "code": item.code, "message": item.message} for item in result.failures],
            "artifacts": [artifact.location for artifact in (result.ledger.artifacts if result.ledger else ())],
        }
        return {
            "structuredContent": {"status": "ok" if result.status != "failed" else "failed", "result": payload},
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}],
            "isError": result.status == "failed",
        }


class InteractivePlatformMcpToolTransport:
    """Domain-neutral MCP facade for Agent-driven plugin Runs.

    These names describe the platform lifecycle rather than a browser.  A
    product adapter supplies a runtime resolver when a selected plugin needs
    an external runtime; this transport never starts a browser implicitly.
    """

    _SCHEMAS = {
        "start_plugin_run": {
            "type": "object", "additionalProperties": False,
            "required": ["pluginId", "checkId", "scope"],
            "properties": {
                "pluginId": {"type": "string", "minLength": 1},
                "checkId": {"type": "string", "minLength": 1},
                "checkVersion": {"type": "string", "minLength": 1},
                "scope": {},
            },
        },
        "resume_plugin_run": {
            "type": "object", "additionalProperties": False,
            "required": ["runId"],
            "properties": {
                "runId": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{2,127}$"},
            },
        },
        "advance_plugin_run": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "pageSize": {"type": "integer", "minimum": 1, "maximum": 100},
                "reviewCheckpoint": {
                    "type": "object", "additionalProperties": False,
                    "required": ["workItemId", "collectionId", "itemIds", "payload"],
                    "properties": {
                        "workItemId": {"type": "string", "minLength": 1},
                        "collectionId": {"type": "string", "minLength": 1},
                        "itemIds": {
                            "type": "array", "minItems": 1, "maxItems": 100, "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "payload": {"type": "object"},
                        "supersedesCheckpointId": {"type": "string", "minLength": 1},
                    },
                },
                "decision": {
                    "type": "object", "additionalProperties": False,
                    "required": ["workItemId", "result", "findings", "reason"],
                    "properties": {
                        "workItemId": {"type": "string", "minLength": 1},
                        "result": {"enum": ["issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise"]},
                        "reason": {"type": "string", "minLength": 1},
                        "findings": {
                            "type": "array", "minItems": 1,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["dimension", "status", "reason"],
                                "properties": {
                                    "dimension": {"type": "string", "minLength": 1},
                                    "status": {"enum": ["satisfied", "violated", "unresolved", "blocked", "conflicted"]},
                                    "reason": {"type": "string", "minLength": 1},
                                },
                            },
                        },
                        "details": {"type": "object"},
                        "reviewCheckpointIds": {
                            "type": "array", "minItems": 1, "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "finalization": {"type": "object"},
                    },
                },
                "closeout": {
                    "type": "object", "additionalProperties": False,
                    "required": ["status"],
                    "properties": {
                        "status": {"enum": ["partial", "failed"]},
                        "failures": {
                            "type": "array", "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["code", "message"],
                                "properties": {
                                    "workItemId": {"type": "string", "minLength": 1},
                                    "code": {"type": "string", "minLength": 1},
                                    "message": {"type": "string", "minLength": 1},
                                },
                            },
                        },
                    },
                },
            },
        },
        "get_plugin_result": {
            "type": "object", "additionalProperties": False,
            "required": ["sectionId"],
            "properties": {
                "sectionId": {"type": "string", "minLength": 1},
                "cursor": {"type": "string", "minLength": 1},
                "pageSize": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        "discover_work_items": {
            "type": "object", "additionalProperties": False,
            "properties": {},
        },
        "inspect_work_items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "workItemIds": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "cursor": {"type": "string", "minLength": 1},
                "pageSize": {"type": "integer", "minimum": 1},
                "includeEvidence": {"type": "boolean"},
            },
        },
        "expand_investigation": {
            "type": "object", "additionalProperties": False,
            "required": ["workItemId"],
            "properties": {
                "workItemId": {"type": "string", "minLength": 1},
                "evidenceIds": {"type": "array", "items": {"type": "string", "minLength": 1}},
            },
        },
        "expand_evidence_collection": {
            "type": "object", "additionalProperties": False,
            "required": ["workItemId", "collectionId"],
            "properties": {
                "workItemId": {"type": "string", "minLength": 1},
                "collectionId": {"type": "string", "minLength": 1},
                "cursor": {"type": "string", "minLength": 1},
                "pageSize": {"type": "integer", "minimum": 1},
                "groupKey": {"type": "string", "minLength": 1},
            },
        },
        "checkpoint_review": {
            "type": "object", "additionalProperties": False,
            "required": ["workItemId", "collectionId", "itemIds", "payload"],
            "properties": {
                "workItemId": {"type": "string", "minLength": 1},
                "collectionId": {"type": "string", "minLength": 1},
                "itemIds": {
                    "type": "array", "minItems": 1, "maxItems": 100, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "payload": {"type": "object"},
                "supersedesCheckpointId": {"type": "string", "minLength": 1},
            },
        },
        "submit_decisions": {
            "type": "object", "additionalProperties": False,
            "required": ["decisions"], "properties": {
                "decisions": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["workItemId", "result", "findings", "reason"],
                        "properties": {
                            "workItemId": {"type": "string", "minLength": 1},
                            "result": {"enum": ["issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise"]},
                            "reason": {"type": "string", "minLength": 1},
                            "findings": {
                                "type": "array", "minItems": 1,
                                "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["dimension", "status", "reason"],
                                    "properties": {
                                        "dimension": {"type": "string", "minLength": 1},
                                        "status": {"enum": ["satisfied", "violated", "unresolved", "blocked", "conflicted"]},
                                        "reason": {"type": "string", "minLength": 1},
                                    },
                                },
                            },
                            "details": {"type": "object"},
                            "reviewCheckpointIds": {
                                "type": "array", "minItems": 1, "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "finalization": {"type": "object"},
                        },
                    },
                },
            },
        },
        "recover_work_item": {
            "type": "object", "additionalProperties": False,
            "required": ["workItemId"], "properties": {
                "workItemId": {"type": "string", "minLength": 1},
                "payload": {"type": "object"},
            },
        },
        "get_plugin_progress": {
            "type": "object", "additionalProperties": False,
            "properties": {},
        },
        "finish_plugin_run": {
            "type": "object", "additionalProperties": False,
            "required": ["status"], "properties": {
                "status": {"enum": ["completed", "partial", "failed"]},
                "failures": {
                    "type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["code", "message"],
                        "properties": {
                            "workItemId": {"type": "string", "minLength": 1},
                            "code": {"type": "string", "minLength": 1},
                            "message": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    }

    def __init__(self, output_root: str | Path = "./assayer-output",
                 *, plugin_registry: PluginRegistry | None = None,
                 runtime_resolver: Any = None,
                 capabilities_resolver: Any = None,
                 store_root: str | None = None):
        self._store_root = str(store_root) if store_root else None
        self._store_digest = self._compute_store_digest()
        self._controller = InteractivePluginController(
            plugin_registry or installed_plugin_registry(), output_root,
            runtime_resolver=runtime_resolver,
            capabilities_resolver=capabilities_resolver,
        )
        self._active_run_id: str | None = self._controller.active_run_id
        self._terminal_run_id: str | None = self._controller.terminal_run_id

    def _compute_store_digest(self) -> str | None:
        """Digest the durable store so a change can be detected without polling.

        The digest covers the ``index.json`` content *and* the content hash of
        every installed plugin's active package.  ``index.json`` alone signals
        lifecycle changes (install/upgrade/rollback), but a tampered package on
        disk leaves it untouched; hashing the package content makes an in-place
        modification invalidate the digest too, so ``start_plugin_run`` refuses
        to keep running a modified plugin package.  Returns None when no store
        root is configured or no index exists yet.
        """
        if self._store_root is None:
            return None
        store_path = Path(self._store_root).expanduser().resolve()
        index_path = store_path / "index.json"
        if not index_path.is_file():
            return None
        hasher = hashlib.sha256()
        hasher.update(index_path.read_bytes())
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return hasher.hexdigest()
        plugins = index.get("plugins", {})
        if isinstance(plugins, dict):
            for plugin_id in sorted(plugins):
                entry = plugins[plugin_id]
                if not isinstance(entry, dict):
                    continue
                history = entry.get("history")
                active = history[-1] if isinstance(history, list) and history else None
                versions = entry.get("versions")
                record = versions.get(active) if isinstance(versions, dict) and active is not None else None
                if not isinstance(record, dict):
                    continue
                package_root = store_path / record.get("packageRoot", "")
                if record.get("checksum") and package_root.is_dir():
                    hasher.update(plugin_id.encode("utf-8"))
                    hasher.update(b"\0")
                    hasher.update(package_checksum(package_root).encode("utf-8"))
                    hasher.update(b"\0")
        return hasher.hexdigest()

    def _refresh_registry(self) -> None:
        """Reload the store-backed registry when the store changed and no Run is active.

        The controller keeps its run and terminal state in place; only the
        plugin selection source is swapped, so a freshly installed plugin becomes
        selectable in the same MCP process without a restart. Callers ensure no
        active Run exists before invoking this.
        """
        if self._store_root is None:
            return
        digest = self._compute_store_digest()
        if digest == self._store_digest:
            return
        self._controller.registry = store_backed_plugin_registry(self._store_root)
        self._store_digest = digest

    def list_tools(self) -> list[dict]:
        descriptions = {
            "start_plugin_run": "Start a domain-neutral interactive plugin Run using only plugin, Check, and business scope. The response reports the resolved plugin identity (pluginId, version, platformApiVersion); confirm that version before submitting checkpoint payloads.",
            "resume_plugin_run": "Explicitly resume one durable plugin Run by the Run ID returned when it started; Host startup never resumes a Run implicitly.",
            "advance_plugin_run": "Drive Host-owned discovery, inspection, checkpoint persistence, decision assembly, and eligible closeout until semantic input is required or the Run is terminal.",
            "get_plugin_result": "Read one bounded page from a terminal result; the complete result remains durably stored.",
            "discover_work_items": "Discover logical WorkItems for an active plugin Run.",
            "inspect_work_items": "Inspect selected WorkItems and return summary-first InvestigationPackets; set includeEvidence=true only when full payloads are required.",
            "expand_investigation": "Expand selected immutable Evidence for an inspected WorkItem when the indexed payload is required in full.",
            "expand_evidence_collection": "Read one bounded page from a plugin-declared immutable Evidence collection, with stable item IDs and optional mechanical groups; this never advances or mutates the Run.",
            "checkpoint_review": "Durably checkpoint semantic-review progress for stable items in a declared Evidence collection. First expand the Evidence collection that exposes the frozen source sections (its itemIdField is source_chunk_id) to read the sourceChunks; then build each evidence_refs entry by copying five fields verbatim from one matching sourceChunks entry: source_chunk_id, start_line, end_line, source_digest, document_path. Do NOT reference an EvidenceRecord evidenceId here: evidence_refs must point at sourceChunks, and evidenceId belongs to a different identifier namespace.",
            "submit_decisions": "Submit the platform DecisionProposal skeleton (workItemId, result, findings, reason) and, for a review, its finalization and reviewCheckpointIds. Submit domain-level findings page-by-page through checkpoint_review instead; the plugin assembles the canonical envelope from those checkpoints.",
            "recover_work_item": "Record plugin/runtime recovery for one WorkItem.",
            "get_plugin_progress": "Return progress for an active plugin Run, including the resolved plugin identity (pluginId, version).",
            "finish_plugin_run": "Finish an interactive plugin Run with completed, partial, or failed status.",
        }
        contract = self._review_payload_contract_text()
        if contract:
            descriptions["checkpoint_review"] += " Checkpoint payload contract: " + contract
            descriptions["advance_plugin_run"] += " Review checkpoint payload contract: " + contract
        tools = []
        for name, schema in self._SCHEMAS.items():
            tool_schema = deepcopy(schema)
            if contract:
                if name == "checkpoint_review":
                    tool_schema["properties"]["payload"]["description"] = contract
                elif name == "advance_plugin_run":
                    tool_schema["properties"]["reviewCheckpoint"]["properties"]["payload"]["description"] = contract
            tools.append({"name": name, "description": descriptions[name], "inputSchema": tool_schema})
        return tools

    def _review_payload_contract_text(self) -> str:
        """Collect every registered plugin's checkpoint payload contract.

        The platform treats review checkpoints as opaque plugin semantics, so
        the Agent never saw the accepted ``payload`` shape and guessed wrong.
        Publishing each plugin's declared ``review_payload_schema`` onto the
        ``checkpoint_review`` tool restores that contract to the Agent without
        making the platform enforce plugin-specific payload validation.
        """
        parts = []
        for registration in self._controller.registry.list():
            schema = registration.review_payload_schema
            if not schema:
                continue
            parts.append(
                f"{registration.manifest.plugin_id} ({registration.manifest.version}): "
                f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
            )
        return "\n".join(parts)

    def call_tool(self, name: str, arguments: object) -> dict:
        if name not in self._SCHEMAS:
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", f"{name} arguments must be an object")
        error = next(Draft202012Validator(self._SCHEMAS[name]).iter_errors(arguments), None)
        if error is not None:
            raise HostError("INVALID_REQUEST", f"{name} arguments do not satisfy the platform schema")
        try:
            if name == "start_plugin_run":
                if self._controller.resume_error is not None:
                    raise self._controller.resume_error
                if self._active_run_id is not None:
                    raise PlatformContractError("RUN_CONFLICT", "An interactive plugin Run is already active")
                self._refresh_registry()
                result = self._controller.start(
                    plugin_id=arguments["pluginId"], check_id=arguments["checkId"],
                    check_version=arguments.get("checkVersion"), scope=arguments["scope"],
                )
                self._active_run_id = result["runId"]
                self._terminal_run_id = None
            elif name == "resume_plugin_run":
                if self._active_run_id is not None:
                    raise PlatformContractError("RUN_CONFLICT", "An interactive plugin Run is already active")
                result = self._controller.resume(arguments["runId"])
                if result.get("status") in {"completed", "partial", "failed"}:
                    self._terminal_run_id = result["runId"]
                else:
                    self._active_run_id = result["runId"]
                    self._terminal_run_id = None
            elif name == "get_plugin_result":
                result = self._controller.get_result(
                    self._terminal_run(), arguments["sectionId"],
                    cursor=arguments.get("cursor"), page_size=arguments.get("pageSize"),
                )
            elif name == "advance_plugin_run":
                if self._active_run_id is None and self._terminal_run_id is not None:
                    if any(key in arguments for key in ("reviewCheckpoint", "decision", "closeout")):
                        raise PlatformContractError(
                            "RUN_TERMINAL", "The latest plugin Run is terminal and cannot accept more semantic input",
                        )
                    result = self._controller.terminal_status(self._terminal_run_id)
                else:
                    result = self._controller.advance(
                        self._active_run(), review_checkpoint=arguments.get("reviewCheckpoint"),
                        decision=arguments.get("decision"), closeout=arguments.get("closeout"),
                        page_size=arguments.get("pageSize"),
                    )
            elif name == "discover_work_items":
                result = self._controller.discover(self._active_run())
            elif name == "inspect_work_items":
                result = self._controller.inspect(
                    self._active_run(), arguments.get("workItemIds"),
                    cursor=arguments.get("cursor"), page_size=arguments.get("pageSize"),
                    include_evidence=arguments.get("includeEvidence", False),
                )
            elif name == "expand_investigation":
                result = self._controller.expand_investigation(
                    self._active_run(), arguments["workItemId"], arguments.get("evidenceIds"),
                )
            elif name == "expand_evidence_collection":
                result = self._controller.expand_evidence_collection(
                    self._active_run(), arguments["workItemId"], arguments["collectionId"],
                    cursor=arguments.get("cursor"), page_size=arguments.get("pageSize"),
                    group_key=arguments.get("groupKey"),
                )
            elif name == "checkpoint_review":
                result = self._controller.checkpoint_review(
                    self._active_run(), arguments["workItemId"], arguments["collectionId"],
                    arguments["itemIds"], arguments["payload"],
                    arguments.get("supersedesCheckpointId"),
                )
            elif name == "submit_decisions":
                result = self._controller.submit_decisions(self._active_run(), arguments["decisions"])
            elif name == "recover_work_item":
                result = self._controller.recover(self._active_run(), arguments["workItemId"], arguments.get("payload"))
            elif name == "get_plugin_progress":
                result = self._controller.progress(self._active_run())
            else:
                result = self._controller.finish(self._active_run(), arguments["status"], arguments.get("failures", ()))
                self._terminal_run_id = result["runId"]
                self._active_run_id = None
            if name == "advance_plugin_run" and result.get("status") in {"completed", "partial", "failed"}:
                self._terminal_run_id = result["runId"]
                self._active_run_id = None
        except PlatformContractError as exc:
            # Keep rejected requests visible in the same durable platform
            # diary. A telemetry write can never replace the original error.
            if self._active_run_id is not None:
                try:
                    self._controller.record_rejection(
                        self._active_run_id, exc.code, message=exc.message,
                    )
                except Exception as telemetry_error:
                    # Rejection telemetry is best-effort and must not mask
                    # the original protocol error, but the failure remains
                    # observable for host diagnostics and resilience scans.
                    _LOG.debug("Unable to persist host rejection telemetry: %s", telemetry_error)
            raise HostError(exc.code, exc.message) from exc
        failed = result.get("status") == "failed"
        return {
            "structuredContent": {"status": "failed" if failed else "ok", "result": result},
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)}],
            "isError": failed,
        }

    def close(self) -> None:
        self._controller.close()
        self._active_run_id = None

    def _active_run(self) -> str:
        if self._active_run_id is None:
            if self._controller.resume_error is not None:
                raise self._controller.resume_error
            if self._controller.terminal_error is not None:
                raise self._controller.terminal_error
            raise PlatformContractError("RUN_NOT_STARTED", "No interactive plugin Run is active")
        return self._active_run_id

    def _terminal_run(self) -> str:
        if self._terminal_run_id is None:
            if self._controller.terminal_error is not None:
                raise self._controller.terminal_error
            raise PlatformContractError("RESULT_NOT_AVAILABLE", "No terminal plugin result is available")
        return self._terminal_run_id


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


def create_interactive_mcp_server(
    *, output_root: str | Path = "./assayer-output",
    plugin_registry: PluginRegistry | None = None,
    runtime_resolver: Any = None,
    capabilities_resolver: Any = None,
    store_root: str | None = None,
):
    """Create a browser-independent MCP server for interactive plugins and lifecycle.

    Runtime adapters are intentionally injected by the embedding product.  A
    plain server created here can run non-browser interactive fixtures and will
    never start Chromium as a side effect of tool discovery.  Plugin lifecycle
    management tools (install/upgrade/uninstall/list/info/downgrade/rollback)
    are registered alongside the interactive Run tools so a single ``assayer-mcp``
    process serves the full natural-language plugin journey.
    """
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer Interactive Plugins")
    adapter = InteractivePlatformMcpToolTransport(
        output_root, plugin_registry=plugin_registry,
        runtime_resolver=runtime_resolver,
        capabilities_resolver=capabilities_resolver,
        store_root=store_root,
    )
    server._assayer_transport = adapter

    def make_invoke(transport, tool_name: str, input_schema: dict):
        def invoke(**kwargs: Any) -> dict:
            arguments = {key: value for key, value in kwargs.items() if value is not None}
            return transport.call_tool(tool_name, arguments)

        invoke.__name__ = f"assayer_{tool_name}"
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))
        ordered = [key for key in properties if key in required]
        ordered.extend(key for key in properties if key not in required)
        invoke.__signature__ = inspect.Signature(parameters=[
            inspect.Parameter(
                key, inspect.Parameter.KEYWORD_ONLY, annotation=Any,
                default=inspect.Parameter.empty if key in required else None,
            ) for key in ordered
        ])
        return invoke

    def register(transport):
        for item in transport.list_tools():
            name = item["name"]
            server.tool(name=name, description=item["description"])(
                make_invoke(transport, name, item["inputSchema"])
            )
            registered = server._tool_manager.get_tool(name)
            if registered is not None:
                registered.parameters = deepcopy(item["inputSchema"])

    register(adapter)
    lifecycle = PluginLifecycleMcpToolTransport(
        store_root or str(default_store_root()),
        active_run_guard=lambda: adapter._active_run_id is not None,
    )
    server._assayer_lifecycle_transport = lifecycle
    register(lifecycle)
    return server



def mcp_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assayer plugin MCP stdio server")
    parser.add_argument("--output-root", default="./assayer-output", help="Fixed parent directory for all Scan output directories")
    parser.add_argument("--store", default=str(default_store_root()),
                        help="Plugin installation store directory (defaults to $ASSAYER_STORE)")
    args = parser.parse_args(argv)
    server = create_interactive_mcp_server(
        output_root=args.output_root,
        plugin_registry=store_backed_plugin_registry(args.store),
        store_root=args.store,
    )
    try:
        server.run(transport="stdio")
    finally:
        transport = getattr(server, "_assayer_transport", None)
        if transport is not None:
            transport.close()
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
