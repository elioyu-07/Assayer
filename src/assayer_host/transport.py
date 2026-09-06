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
import os
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
from .lifecycle import InstallationStatusBuilder
from .lifecycle_product import LifecycleProductController, LifecycleProductToolTransport
from .plugin_store_registry import store_backed_plugin_registry
from .resources import default_rules_root, default_schema_root
from assayer_platform import (
    DecisionProposal, DimensionObservation, EvidenceRecord, PlatformRunner,
    Finding, InteractivePlatformSession, InvestigationPacket, PlatformContext,
    PlatformContractError, PluginRegistry, WorkItem, InteractivePluginController,
    StagedResultDocument,
)
from assayer_platform.builtin_plugins import installed_plugin_registry


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_TOOLS = tuple(TOOL_KINDS) + ("get_operation",)
_PUBLIC_COMPOSITE_TOOLS = ("discover_scope", "investigate_object")
_PUBLIC_TOOLS = tuple(
    name for name in _TOOLS if name not in {"get_rule_contract", "record_findings", "commit_decision"}
) + _PUBLIC_COMPOSITE_TOOLS
# The user-facing plugin lifecycle is deliberately narrow.  The standalone
# InteractivePlatformMcpToolTransport retains every primitive for conformance,
# compatibility, and diagnostics, while normal Codex sessions get the
# Host-driven path that cannot strand a Run between bookkeeping operations.
_PRODUCT_INTERACTIVE_TOOLS = (
    "start_plugin_run", "resume_plugin_run", "advance_plugin_run", "get_plugin_result",
    "expand_evidence_collection", "recover_work_item", "get_plugin_progress",
)
_INSTALLATION_STATUS_TOOL = "get_installation_status"
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

    These names describe the platform lifecycle rather than a browser.  The
    frontend ``start_audit``/``discover_scope``/``investigate_object`` tools
    remain compatibility aliases in :class:`FrontendProductMcpToolTransport`.
    A product adapter supplies a runtime resolver when a selected plugin needs
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
                 capabilities_resolver: Any = None):
        self._controller = InteractivePluginController(
            plugin_registry or installed_plugin_registry(), output_root,
            runtime_resolver=runtime_resolver,
            capabilities_resolver=capabilities_resolver,
        )
        self._active_run_id: str | None = self._controller.active_run_id
        self._terminal_run_id: str | None = self._controller.terminal_run_id

    def list_tools(self) -> list[dict]:
        descriptions = {
            "start_plugin_run": "Start a domain-neutral interactive plugin Run using only plugin, Check, and business scope.",
            "resume_plugin_run": "Explicitly resume one durable plugin Run by the Run ID returned when it started; Host startup never resumes a Run implicitly.",
            "advance_plugin_run": "Drive Host-owned discovery, inspection, checkpoint persistence, decision assembly, and eligible closeout until semantic input is required or the Run is terminal.",
            "get_plugin_result": "Read one bounded page from a terminal result; the complete result remains durably stored.",
            "discover_work_items": "Discover logical WorkItems for an active plugin Run.",
            "inspect_work_items": "Inspect selected WorkItems and return summary-first InvestigationPackets; set includeEvidence=true only when full payloads are required.",
            "expand_investigation": "Expand selected immutable Evidence for an inspected WorkItem when the indexed payload is required in full.",
            "expand_evidence_collection": "Read one bounded page from a plugin-declared immutable Evidence collection, with stable item IDs and optional mechanical groups; this never advances or mutates the Run.",
            "checkpoint_review": "Durably checkpoint semantic-review progress for stable items in a declared Evidence collection.",
            "submit_decisions": "Submit semantic DecisionProposals; the platform validates and commits them.",
            "recover_work_item": "Record plugin/runtime recovery for one WorkItem.",
            "get_plugin_progress": "Return progress for an active plugin Run.",
            "finish_plugin_run": "Finish an interactive plugin Run with completed, partial, or failed status.",
        }
        return [{"name": name, "description": descriptions[name], "inputSchema": deepcopy(schema)}
                for name, schema in self._SCHEMAS.items()]

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


class FrontendProductMcpToolTransport(McpToolTransport):
    """Frontend compatibility MCP facade that owns protocol framing and Scan state.

    The lower-level :class:`McpToolTransport` remains available for JSON/Harness
    compatibility and protocol tests.  The MCP server shipped to users uses
    this facade instead: the model supplies only a tool's business input and
    the facade generates every envelope/lifecycle field internally.
    """

    _PUBLIC_REASON = "Continue the Assayer audit with the requested operation."

    def __init__(self, core: HostCore, *, plugin_registry: PluginRegistry | None = None,
                 plugin_id: str = "assayer.frontend-audit",
                 output_root: str | Path = "./assayer-output",
                 lifecycle_controller: LifecycleProductController | None = None):
        super().__init__(core)
        self._public_schemas = self._build_public_schemas()
        self._installation_status = InstallationStatusBuilder(output_root)
        self._installation_status_schema = json.loads(
            (default_schema_root() / "installation-status.schema.json").read_text()
        )
        self._lifecycle_tools = (
            LifecycleProductToolTransport(lifecycle_controller)
            if lifecycle_controller is not None else None
        )
        self._public_progress_validator = Draft202012Validator(
            json.loads((default_schema_root() / "public-progress.schema.json").read_text())
        )
        registry = json.loads((default_rules_root() / "registry.json").read_text())
        self._rule_registry_version = registry["registryVersion"]
        self._public_rules = {
            (item["ruleId"], item["version"]): item
            for item in registry.get("rules", []) if item.get("status") == "enabled"
        }
        rules_root = default_rules_root()

        def resolve_rule_document(document_name: object) -> Path:
            """Resolve a registry document below the bundled rules directory.

            Registry references are repository-relative (for example,
            ``rules/FUA-10-v1.1.md``), while ``rules_root`` already points at
            that directory.  Normalize the optional leading directory and
            reject absolute paths or traversal outside the resource root.
            """
            if not isinstance(document_name, str) or not document_name:
                raise RuntimeError("Enabled rule has an invalid document path")
            document = Path(document_name)
            if document.is_absolute():
                raise RuntimeError("Enabled rule document must be relative")
            if document.parts and document.parts[0] == rules_root.name:
                document = Path(*document.parts[1:])
            candidate = (rules_root / document).resolve()
            try:
                candidate.relative_to(rules_root.resolve())
            except ValueError as exc:
                raise RuntimeError("Enabled rule document escapes the rules directory") from exc
            return candidate

        self._public_rule_contracts = {
            key: {
                "rule": {"ruleId": value["ruleId"], "version": value["version"]},
                "content": resolve_rule_document(value.get("document")).read_text(encoding="utf-8"),
                "contentDigest": value["contentDigest"],
                "registryDigest": registry["digest"],
            }
            for key, value in self._public_rules.items()
        }
        # Plugin identity is selected through the platform registry.  The
        # frontend runtime remains a compatibility adapter, but the transport
        # no longer knows how to import or discover its manifest directly.
        self._plugin_registry = plugin_registry or installed_plugin_registry()
        try:
            self._plugin_registration = self._plugin_registry.select(plugin_id=plugin_id)
        except PlatformContractError as error:
            raise RuntimeError(f"No frontend audit plugin is registered: {error.message}") from error
        self._platform_tools = PlatformMcpToolTransport(
            output_root, plugin_registry=self._plugin_registry,
        )
        # Domain-neutral interactive plugins (for example Spec quality) share
        # the same product MCP connection. Their session state is independent
        # from the frontend compatibility Scan state.
        self._interactive_tools = InteractivePlatformMcpToolTransport(
            output_root, plugin_registry=self._plugin_registry,
        )
        self._platform_session = InteractivePlatformSession(self._plugin_registration.manifest)
        try:
            self._platform_committer = self._plugin_registration.create_committer(self)
        except PlatformContractError as error:
            raise RuntimeError(f"Frontend plugin commit contract is unavailable: {error.message}") from error
        self._platform_run = None
        self._public_prefix = f"facade-{uuid.uuid4().hex[:12]}"
        self._public_turn = 0
        self._public_envelope_sequence = 0
        self._public_call_depth = 0
        self._public_active_turn_id: str | None = None
        self._public_active: dict[str, object] | None = None
        self._public_pending_operation: str | None = None
        self._frontend_result_document: StagedResultDocument | None = None
        self._frontend_result_status: str | None = None
        self._reset_public_progress()

    def _reset_public_progress(self) -> None:
        """Start public counters from zero for each independent Scan."""
        self._public_investigations: dict[str, dict] = {}
        self._public_candidate_objects: dict[str, str] = {}
        self._public_progress = {
            "pagesVisited": 0,
            "objectsDiscovered": 0,
            "objectsVerified": 0,
            "decisionsCommitted": 0,
            "entrypointsProcessed": 0,
            "entrypointsRemaining": 0,
        }
        self._public_progress_seen = {
            "pages": set(), "candidates": set(), "objects": set(), "assessments": set(),
        }

    def close(self) -> None:
        self._interactive_tools.close()
        super().close()

    def _retire_public_run_state(self) -> None:
        """Release per-Run semantic caches before the next Scan starts."""
        self._platform_run = None
        self._public_investigations.clear()
        self._public_candidate_objects.clear()
        self._public_pending_operation = None

    def _begin_platform_tracking(self, scope: dict, response: dict) -> None:
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        run_id = result.get("runId")
        scan_id = result.get("scanId")
        if not isinstance(run_id, str) or not isinstance(scan_id, str):
            return
        source = getattr(self._json.core, "platform_ledger_store", None)
        if callable(source):
            # A real RuntimeRouter must expose the store for the Scan that it
            # just created.  Do not silently downgrade a production run to an
            # untracked run when that lookup fails.
            store = self._executor.submit(source, scan_id, run_id).result()
        else:
            store = source
        if store is None:
            return
        check = self._platform_session.manifest.checks[0]
        capabilities = set(result.get("capabilities", ()))
        aliases = {
            "dom": "structured_read",
            "visual": "visual_read",
            "interaction": "safe_interaction",
            "network": "network_observation",
            "source": "source_lookup",
        }
        capabilities.update(
            normalized for native, normalized in aliases.items() if native in capabilities
        )
        context = PlatformContext(run_id, frozenset(capabilities))
        self._platform_run = self._platform_session.begin(
            context, {"url": scope.get("url")}, check.check_id, check.version, store,
        )

    def _platform_work_item(self, *, candidate_id: str | None = None,
                            object_id: str | None = None, result: dict | None = None) -> WorkItem:
        """Resolve one stable platform WorkItem across candidate/object views."""
        if self._platform_run is None:
            raise HostError("INTERNAL_FAILURE", "Platform tracking is not active")
        for item in self._platform_run.work_items.values():
            metadata = item.metadata
            if candidate_id and metadata.get("candidateId") == candidate_id:
                return item
            if object_id and metadata.get("objectId") == object_id:
                return item
            if object_id and item.identity == object_id:
                return item
        identity = candidate_id or object_id
        if not isinstance(identity, str) or not identity:
            raise HostError("INTERNAL_FAILURE", "Platform tracking could not identify the work item")
        page_state_id = (result or {}).get("pageStateId")
        state_digest = hashlib.sha256(json.dumps({
            "pageStateId": page_state_id, "identity": identity,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return WorkItem(
            f"frontend:{identity}", "frontend_object", identity, state_digest,
            {"candidateId": candidate_id, "objectId": object_id,
             "pageStateId": page_state_id, "runtime_state_digest": state_digest},
        )

    def _track_discovery(self, plan: dict) -> None:
        if self._platform_run is None:
            return
        items = []
        page = plan.get("currentPage") if isinstance(plan.get("currentPage"), dict) else {}
        page_state_id = page.get("pageStateId")
        for candidate in plan.get("candidates", ()):
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("candidateId")
            if not isinstance(candidate_id, str) or candidate.get("status") != "pending":
                continue
            potential_rules = candidate.get("potentialRules", ())
            if not any(
                isinstance(rule, dict)
                and rule.get("ruleId") == self._platform_run.check.check_id
                and rule.get("version") == self._platform_run.check.version
                for rule in potential_rules
            ):
                continue
            items.append(self._platform_work_item(
                candidate_id=candidate_id,
                object_id=candidate.get("objectId") if isinstance(candidate.get("objectId"), str) else None,
                result={"pageStateId": page_state_id},
            ))
        try:
            self._platform_run.record_discovery(tuple(items))
        except PlatformContractError as error:
            raise HostError(error.code, error.message) from error

    def _track_investigation(self, candidate_id: str | None, result: dict) -> None:
        if self._platform_run is None:
            return
        identity = candidate_id or result.get("objectId")
        if not isinstance(identity, str):
            raise HostError("INTERNAL_FAILURE", "Platform tracking could not identify the investigated object")
        recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
        if result.get("readyForDecision") is not True or recovery.get("finalStatus") != "restored":
            # A successful Host response without verified recovery is not a
            # platform InvestigationPacket.  The Host response remains
            # available for diagnosis, but cannot enter the decision ledger.
            return
        item = self._platform_work_item(
            candidate_id=candidate_id,
            object_id=result.get("objectId") if isinstance(result.get("objectId"), str) else None,
            result=result,
        )
        work_id = item.work_item_id
        if item.work_item_id not in self._platform_run.work_items:
            self._platform_run.record_discovery((item,))
        legacy_refs = tuple(ref for ref in result.get("evidenceRefs", ()) if isinstance(ref, str))
        evidence = tuple(EvidenceRecord(
            f"platform:{work_id}:{kind}", work_id, self._platform_run.check.check_id,
            self._platform_run.check.version, kind, item.identity,
            {"legacyEvidenceRefs": legacy_refs},
        ) for kind in ("runtime_visual", "runtime_dom"))
        refs = tuple(record.evidence_id for record in evidence)
        packet = InvestigationPacket(
            item, self._platform_run.check.check_id, self._platform_run.check.version,
            tuple(DimensionObservation(
                dimension, ("Aligned runtime evidence is available for Agent review.",),
                refs, "unresolved",
            ) for dimension in self._platform_run.check.dimensions),
            evidence, "restored", result.get("caseId"),
            {"objectId": result.get("objectId"), "rawVisualRef": result.get("rawVisualRef")},
        )
        try:
            self._platform_run.record_investigation(packet)
        except PlatformContractError as error:
            raise HostError(error.code, error.message) from error

    def _track_commit(self, input_data: dict, findings: list[dict], result: dict) -> None:
        if self._platform_run is None:
            return
        object_id = input_data.get("objectId")
        work_id = next((work_item_id for work_item_id, packet in self._platform_run.investigations.items()
                        if packet.metadata.get("objectId") == object_id), None)
        if work_id is None:
            # Preserve the lower-level public lifecycle as a compatibility
            # path.  Its Host-owned evidence/case refs are wrapped as the two
            # required platform evidence kinds at commit time, so even that
            # path remains represented in the generic ledger.
            item = self._platform_work_item(object_id=object_id, result=input_data)
            if item.work_item_id not in self._platform_run.work_items:
                self._platform_run.record_discovery((item,))
            legacy_refs = tuple(ref for ref in input_data.get("evidenceRefs", ()) if isinstance(ref, str))
            evidence = tuple(EvidenceRecord(
                f"platform:{item.work_item_id}:legacy:{kind}", item.work_item_id,
                self._platform_run.check.check_id, self._platform_run.check.version,
                kind, item.identity, {"legacyEvidenceRefs": legacy_refs},
            ) for kind in ("runtime_visual", "runtime_dom"))
            refs = tuple(record.evidence_id for record in evidence)
            packet = InvestigationPacket(
                item, self._platform_run.check.check_id, self._platform_run.check.version,
                tuple(DimensionObservation(
                    dimension, ("Host-owned legacy evidence is bound to this decision.",), refs, "unresolved",
                ) for dimension in self._platform_run.check.dimensions), evidence, "restored",
                input_data.get("caseRefs", ())[0] if input_data.get("caseRefs") else None,
                {"objectId": object_id, "legacy": True},
            )
            self._platform_run.record_investigation(packet)
            work_id = item.work_item_id
        proposal = DecisionProposal(
            work_id, self._platform_run.check.check_id, self._platform_run.check.version,
            input_data["result"], tuple(Finding(
                finding["dimension"], finding["status"], finding["reasonText"],
            ) for finding in findings), input_data["reasonText"], {
                key: input_data[key] for key in (
                    "blocker", "rawVisualRef", "severity", "title", "message",
                    "impact", "recommendation",
                ) if key in input_data
            },
        )
        committer = self._platform_committer
        receipt_factory = getattr(committer, "receipt_for_host_result", None)
        if not callable(receipt_factory):
            raise HostError("PLUGIN_COMMIT_UNAVAILABLE", "Frontend plugin does not expose a Platform commit receipt seam")
        receipt = receipt_factory(
            proposal, self._platform_run.investigations[work_id],
            self._platform_run.check, self._platform_run.context, result,
        )
        try:
            self._platform_run.record_commit(proposal, receipt)
        except PlatformContractError as error:
            raise HostError(error.code, error.message) from error
        except Exception as error:
            # Without this durable Platform checkpoint the Host Assessment is
            # not publishable as a formal result.
            raise HostError(
                "PLATFORM_PERSISTENCE_FAILED",
                "Platform decision receipt could not be durably persisted; the Assessment is not publishable",
            ) from error

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
                # page navigation a chance to stale the object. The product
                # path requires Findings; legacy findingRefs remain available
                # only through the lower-level protocol transport.
                record_schema = deepcopy(self._schemas["record_findings"]["properties"]["request"]["properties"]["input"])
                input_schema = deepcopy(input_schema)
                input_schema.setdefault("properties", {})["findings"] = deepcopy(record_schema["properties"]["findings"])
                input_schema["required"] = [key for key in input_schema.get("required", []) if key != "findingRefs"]
                input_schema["required"].append("findings")
            elif name == "complete_audit":
                input_schema = {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "completionReason": deepcopy(input_schema["properties"]["completionReason"]),
                    },
                }
            schemas[name] = input_schema
        schemas["investigate_object"] = {
            "type": "object", "additionalProperties": False,
            "$defs": {
                "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{2,127}$"},
                "objectId": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{2,127}$"},
                "pageStateId": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{2,127}$"},
            },
            "required": ["pageStateId"],
            "properties": {
                "objectId": {"$ref": "#/$defs/objectId"},
                "candidateId": {"$ref": "#/$defs/id"},
                "pageStateId": {"$ref": "#/$defs/pageStateId"},
                "rule": {
                    "type": "object", "additionalProperties": False,
                    "required": ["ruleId", "version"],
                    "properties": {
                        "ruleId": {"type": "string", "pattern": "^[A-Z][A-Z0-9_-]{1,63}$"},
                        "version": {"type": "string", "pattern": "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$"},
                    },
                },
                "kind": {"enum": ["negative", "boundary", "invalid", "exception", "happy_prerequisite", "observation"]},
                "purpose": {"type": "string", "minLength": 1},
                "plannedCoverageDimensions": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
            },
            "oneOf": [{"required": ["objectId"]}, {"required": ["candidateId"]}],
        }
        schemas["discover_scope"] = {
            "type": "object", "additionalProperties": False,
            "properties": {},
        }
        return schemas

    def list_tools(self) -> list[dict]:
        interactive = {
            item["name"]: item for item in self._interactive_tools.list_tools()
        }
        generic = self._platform_tools.list_tools() + [
            interactive[name] for name in _PRODUCT_INTERACTIVE_TOOLS
        ]
        tools = [{
            "name": _INSTALLATION_STATUS_TOOL,
            "description": (
                "Report read-only Assayer installation, version, runtime-integrity, and safe feedback "
                "diagnostics. This tool does not require an active Run and never starts a browser."
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "maxRecentRuns": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}
                },
            },
        }]
        for name in _PUBLIC_TOOLS:
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
                    + ("Builds the next bounded page-discovery plan, deduplicates logical entrypoints, and advances through safe empty pages without requiring a model turn for each one." if name == "discover_scope" else "")
                    + ("Verifies the object, loads its frozen rule, opens an observation Case, captures aligned visual and DOM evidence, restores the page, and returns decision-ready references in one bounded call." if name == "investigate_object" else "")
                ),
                "inputSchema": schema,
            })
        lifecycle = self._lifecycle_tools.list_tools() if self._lifecycle_tools is not None else []
        return generic + lifecycle + tools

    def call_tool(self, name: str, arguments: object) -> dict:
        """Correlate all nested Host work to one product-level Agent call."""
        if name == _INSTALLATION_STATUS_TOOL:
            return self._installation_status_result(arguments)
        if self._lifecycle_tools is not None and name in {
            "plan_plugin_change", "execute_plugin_change",
        }:
            return self._lifecycle_tools.call_tool(name, arguments)
        if name in {self._platform_tools.TOOL, self._platform_tools.LIST_TOOL}:
            return self._platform_tools.call_tool(name, arguments)
        if name == "get_plugin_result" and self._frontend_result_document is not None:
            return self._frontend_result_page(arguments)
        if name in _PRODUCT_INTERACTIVE_TOOLS:
            if name == "start_plugin_run":
                self._frontend_result_document = None
                self._frontend_result_status = None
            return self._interactive_tools.call_tool(name, arguments)
        if name in {item["name"] for item in self._interactive_tools.list_tools()}:
            raise HostError(
                "DIAGNOSTIC_TOOL_ONLY",
                f"Tool {name} is available only through the diagnostic interactive transport; "
                "normal product Runs must use advance_plugin_run",
            )
        outer = self._public_call_depth == 0
        if outer:
            self._public_turn += 1
            self._public_active_turn_id = (
                f"{self._public_prefix}-turn-{self._public_turn:04d}:{name}"
            )
        self._public_call_depth += 1
        try:
            return self._call_tool(name, arguments)
        finally:
            self._public_call_depth -= 1
            if outer:
                self._public_active_turn_id = None

    def _installation_status_result(self, arguments: object) -> dict:
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", "Installation status arguments must be an object")
        schema = next(
            item["inputSchema"] for item in self.list_tools()
            if item["name"] == _INSTALLATION_STATUS_TOOL
        )
        if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
            raise HostError("INVALID_REQUEST", "Installation status arguments do not satisfy the product schema")
        status = self._installation_status.build(max_recent_runs=arguments.get("maxRecentRuns", 5))
        if next(Draft202012Validator(self._installation_status_schema).iter_errors(status), None) is not None:
            raise HostError("INTERNAL_FAILURE", "Installation status does not satisfy its product schema")
        public = {"status": "ok", "result": status}
        return {
            "structuredContent": public,
            "content": [{"type": "text", "text": json.dumps(public, ensure_ascii=False, separators=(",", ":"))}],
            "isError": False,
        }

    def _call_tool(self, name: str, arguments: object) -> dict:
        if name not in _PUBLIC_TOOLS:
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
        if name == "investigate_object":
            if self._public_active is None:
                raise HostError("UNKNOWN_REFERENCE", "There is no audit to continue")
            if self._public_pending_operation is not None:
                raise HostError(
                    "REQUEST_RESULT_UNKNOWN",
                    "The previous operation result is unknown; query that operation before continuing",
                    next_step="call_get_operation",
                )
            investigated = self._investigate_object(input_data, reason)
            structured = investigated.get("structuredContent")
            if isinstance(structured, dict) and structured.get("status") == "ok":
                result = structured.get("result")
                if isinstance(result, dict):
                    try:
                        self._track_investigation(input_data.get("candidateId"), result)
                    except PlatformContractError as error:
                        raise HostError(error.code, error.message) from error
            return investigated
        if name == "discover_scope":
            if self._public_active is None:
                raise HostError("UNKNOWN_REFERENCE", "There is no audit to continue")
            if self._public_pending_operation is not None:
                raise HostError(
                    "REQUEST_RESULT_UNKNOWN",
                    "The previous operation result is unknown; query that operation before continuing",
                    next_step="call_get_operation",
                )
            discovered = self._discover_scope(reason)
            structured = discovered.get("structuredContent")
            if isinstance(structured, dict) and structured.get("status") == "ok":
                result = structured.get("result")
                if isinstance(result, dict):
                    self._track_discovery(result)
            return discovered
        if name == "start_audit":
            if self._public_active is not None:
                raise HostError("SCAN_IN_PROGRESS", "An audit is already running; complete or stop it first")
            url = input_data.get("url")
            if not isinstance(url, str) or not url:
                raise HostError("INVALID_REQUEST", "start_audit requires an HTTP(S) URL")
            self._retire_public_run_state()
            self._frontend_result_document = None
            self._frontend_result_status = None
            self._reset_public_progress()
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
        completion_input = None
        if name == "complete_audit":
            # Do not let the model copy a potentially stale progress snapshot
            # into the coverage proof.  The Runtime builds the exact closure
            # from its durable ledger on the owning worker thread.
            reason_text = input_data.get("completionReason")
            builder = getattr(self._json.core, "build_completion_input", None)
            if not callable(builder):
                raise HostError("INTERNAL_FAILURE", "The runtime does not support automatic coverage completion")
            completion_input = self._executor.submit(
                builder, self._public_active["scanId"], self._public_active["runId"], reason_text
            ).result()
            input_data = completion_input
        if name == "prepare_decision" and "findings" in input_data:
            decided = self._atomic_prepare_decision(input_data, reason)
            structured = decided.get("structuredContent")
            if isinstance(structured, dict) and structured.get("status") == "ok":
                result = structured.get("result")
                if isinstance(result, dict) and isinstance(result.get("assessmentId"), str):
                    already_recorded = self._platform_run is not None and any(
                        receipt.metadata.get("hostAssessmentId") == result["assessmentId"]
                        for receipt in self._platform_run.receipts.values()
                    )
                    if not already_recorded:
                        try:
                            self._track_commit(input_data, input_data["findings"], result)
                        except PlatformContractError as error:
                            raise HostError(error.code, error.message) from error
            return decided
        envelope = self._next_envelope(name, input_data, reason)
        result = super().call_tool(name, envelope)
        response = result.get("structuredContent")
        if not isinstance(response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid response")
        self._update_public_state(name, response)
        # Bootstrap the generic platform Run before any later interactive
        # operation can be accepted. A real Host must be trackable; fake
        # cores used by protocol tests simply have no platform store and stay
        # on the legacy compatibility path. Public state is retained if the
        # tracking store itself fails, so the already-started Host Scan is not
        # orphaned and can still be diagnosed or closed.
        if name == "start_audit" and response.get("status") == "ok":
            self._begin_platform_tracking(input_data, response)
        terminal_status = (
            response.get("result", {}).get("scanStatus")
            if isinstance(response.get("result"), dict) else None
        )
        if name == "complete_audit" and response.get("status") == "failed":
            terminal_status = "failed"
        platform_terminal_artifacts: tuple[str, ...] = ()
        if name == "complete_audit" and terminal_status in {"completed", "partial", "failed"}:
            if self._platform_run is not None:
                try:
                    platform_result = self._platform_run.finish(terminal_status)
                    if platform_result.ledger is not None:
                        platform_terminal_artifacts = (
                            "platform-ledger.json", "platform-events.jsonl", "platform-run.log",
                            "platform-performance-bill.json", "platform-performance-bill.md",
                            "canonical-result.json",
                        )
                except PlatformContractError as error:
                    raise HostError(error.code, error.message) from error
        if platform_terminal_artifacts and isinstance(response.get("result"), dict):
            existing = response["result"].get("artifactPaths")
            paths = list(existing) if isinstance(existing, list) else []
            response["result"]["artifactPaths"] = list(dict.fromkeys(paths + list(platform_terminal_artifacts)))
        public_response = {
            key: deepcopy(value) for key, value in response.items()
            if key not in {"protocolVersion", "requestId", "scanId", "runId", "runRevision"}
        }
        if isinstance(public_response.get("result"), dict):
            for key in ("scanId", "runId", "runRevision", "operationId"):
                public_response["result"].pop(key, None)
        if name == "complete_audit":
            complete_summary = self._result_summary(completion_input, public_response)
            self._frontend_result_document = StagedResultDocument({"summary": complete_summary})
            self._frontend_result_status = terminal_status
            public_response["summary"] = self._frontend_result_document.overview["summary"]
            public_response["resultDelivery"] = self._frontend_result_document.descriptor()
            artifact_paths = public_response.get("result", {}).get("artifactPaths", ())
            if "canonical-result.json" in artifact_paths:
                public_response["canonicalResult"] = "canonical-result.json"
        public_response["progress"] = self._progress_block(name, public_response)
        public_content = [{"type": "text", "text": json.dumps(public_response, ensure_ascii=False, separators=(",", ":"))}]
        for block in result.get("content", [])[1:]:
            public_content.append(block)
        if name == "complete_audit" and terminal_status in {"completed", "partial", "failed"}:
            self._retire_public_run_state()
        return {
            "structuredContent": public_response,
            "content": public_content,
            "isError": result.get("isError", False),
        }

    def _frontend_result_page(self, arguments: object) -> dict:
        """Page the latest compatibility-frontend result through the generic contract."""
        schema = InteractivePlatformMcpToolTransport._SCHEMAS["get_plugin_result"]
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", "get_plugin_result arguments must be an object")
        error = next(Draft202012Validator(schema).iter_errors(arguments), None)
        if error is not None:
            raise HostError("INVALID_REQUEST", "get_plugin_result arguments do not satisfy the platform schema")
        document = self._frontend_result_document
        if document is None:
            raise HostError("RESULT_NOT_AVAILABLE", "No terminal plugin result is available")
        try:
            page = document.page(
                arguments["sectionId"], cursor=arguments.get("cursor"),
                page_size=arguments.get("pageSize"),
            )
        except PlatformContractError as exc:
            raise HostError(exc.code, exc.message) from exc
        payload = {
            **page,
            "deltaOnly": True,
            "sourceDigest": document.digest,
            "delivery": document.descriptor(),
            "terminalStatus": self._frontend_result_status,
        }
        public = {"status": "ok", "result": payload}
        return {
            "structuredContent": public,
            "content": [{"type": "text", "text": json.dumps(public, ensure_ascii=False, separators=(",", ":"))}],
            "isError": False,
        }

    def _investigate_object(self, input_data: dict, reason: str) -> dict:
        """Run the bounded, read-only evidence lifecycle in one facade turn.

        The model still chooses the object and frozen rule. HostCore remains
        the authority for identity, evidence, recovery, and all state changes.
        """
        object_id = input_data.get("objectId")
        candidate_id = input_data.get("candidateId")
        rule = input_data.get("rule")
        page = input_data["pageStateId"]
        case_kind = input_data.get("kind", "observation")
        purpose = input_data.get("purpose", "Collect the evidence required by the frozen rule.")
        dimensions = input_data.get("plannedCoverageDimensions")
        cached_object_id = object_id or self._public_candidate_objects.get(candidate_id)
        cached = self._public_investigations.get(cached_object_id) if cached_object_id else None
        if (
            cached
            and not cached.get("decided")
            and cached.get("pageStateId") == page
            and cached.get("kind") == case_kind
            and (rule is None or cached.get("rule") == rule)
            and (dimensions is None or cached.get("plannedCoverageDimensions") == dimensions)
        ):
            return deepcopy(cached["response"])
        common = {
            "objectId": object_id, "rule": rule, "kind": case_kind,
            "purpose": purpose, "plannedCoverageDimensions": dimensions,
        }
        steps = []

        visual_blocks: list[dict] = []

        def wrap(structured: dict) -> dict:
            """Return the normal MCP envelope for composite early exits."""
            return {
                "structuredContent": structured,
                "content": [{"type": "text", "text": json.dumps(
                    structured, ensure_ascii=False, separators=(",", ":")
                )}],
                "isError": False,
            }

        def call(tool: str, payload: dict) -> dict:
            result = self.call_tool(tool, {**payload, "decisionReason": reason})
            steps.append(tool)
            visual_blocks.extend(
                block for block in result.get("content", [])
                if isinstance(block, dict) and block.get("type") == "image"
            )
            structured = result.get("structuredContent")
            if not isinstance(structured, dict):
                raise HostError("INTERNAL_FAILURE", f"Assayer returned an invalid {tool} response")
            if structured.get("status") not in {None, "ok"} and structured.get("error"):
                return structured
            return structured

        def restore_after_failure() -> dict:
            """Attempt recovery without masking the original operation error."""
            try:
                return call("restore_case", {
                    "caseId": case_id, "pageStateId": page, "objectId": object_id,
                    "fallback": "refresh_and_replay_safe_entrypoints",
                })
            except HostError as error:
                return {
                    "status": "failed",
                    "error": {"code": error.code, "message": str(error)},
                }

        inspected = call("inspect_object", {"objectId": object_id} if object_id else {"candidateId": candidate_id})
        if inspected.get("status") != "ok" and inspected.get("error"):
            return wrap(inspected)
        verified_object_id = inspected.get("result", {}).get("objectId")
        if not isinstance(verified_object_id, str):
            return wrap(inspected)
        object_id = verified_object_id
        if rule is None:
            potential_rules = inspected.get("result", {}).get("potentialRules", [])
            if len(potential_rules) != 1:
                inspected["result"] = {
                    "objectId": object_id,
                    "potentialRules": potential_rules,
                    "readyForDecision": False,
                }
                inspected["progress"] = self._progress_block("inspect_object", inspected)
                return {
                    "structuredContent": inspected,
                    "content": [{"type": "text", "text": json.dumps(inspected, ensure_ascii=False, separators=(",", ":"))}],
                    "isError": False,
                }
            rule = potential_rules[0]
        if not dimensions:
            contract = self._public_rules.get((rule["ruleId"], rule["version"]), {})
            dimensions = list(contract.get("coverageDimensions", []))
        if (rule.get("ruleId"), rule.get("version")) not in self._public_rules:
            raise HostError("UNKNOWN_REFERENCE", "Rule is not enabled in the current frozen registry")
        if any(
            check.check_id == rule.get("ruleId") and check.version == rule.get("version")
            for check in self._platform_session.manifest.checks
        ):
            try:
                self._platform_session.check(rule.get("ruleId", ""), rule.get("version", ""))
            except PlatformContractError as error:
                raise HostError(error.code, error.message) from error
        if rule not in inspected.get("result", {}).get("potentialRules", []):
            raise HostError("UNKNOWN_REFERENCE", "Rule does not belong to the verified object")
        common["objectId"] = object_id
        common["rule"] = rule
        common["plannedCoverageDimensions"] = dimensions
        contract = self._public_rule_contracts.get((rule["ruleId"], rule["version"]))
        case = call("begin_case", common)
        if case.get("status") != "ok" and case.get("error"):
            return wrap(case)
        case_id = case.get("result", {}).get("caseId")
        if not isinstance(case_id, str):
            raise HostError("INTERNAL_FAILURE", "Assayer did not return a Case reference")
        observed = call("observe_page", {
            "pageStateId": page, "objectId": object_id, "caseId": case_id,
        })
        if observed.get("status") != "ok" and observed.get("error"):
            recovery = restore_after_failure()
            observed["recovery"] = recovery.get("result", recovery.get("error"))
            return wrap(observed)
        evidence = call("capture_evidence", {
            "pageStateId": page, "objectId": object_id, "caseId": case_id,
            "includeRawVisual": False,
        })
        if evidence.get("status") != "ok" and evidence.get("error"):
            recovery = restore_after_failure()
            evidence["recovery"] = recovery.get("result", recovery.get("error"))
            return wrap(evidence)
        evidence_refs = []
        for result in (observed, evidence):
            ref = result.get("result", {}).get("evidenceId") if isinstance(result.get("result"), dict) else None
            if isinstance(ref, str):
                evidence_refs.append(ref)
        restored = call("restore_case", {
            "caseId": case_id, "pageStateId": page, "objectId": object_id,
            "fallback": "refresh_and_replay_safe_entrypoints",
        })
        if restored.get("status") != "ok" and restored.get("error"):
            return wrap(restored)
        result = {
            "objectId": object_id,
            "rule": rule,
            "ruleContract": contract,
            "pageStateId": page,
            "caseId": case_id,
            "evidenceRefs": evidence_refs,
            "observation": observed.get("result", {}).get("observation"),
            "evidence": evidence.get("result", {}).get("evidence"),
            "recovery": restored.get("result", {}),
            "steps": steps,
            "readyForDecision": restored.get("result", {}).get("finalStatus") == "restored",
        }
        raw_visual_ref = observed.get("result", {}).get("screenshotRef")
        if isinstance(raw_visual_ref, str):
            # The observation already captured this image. Exposing its
            # immutable reference lets a domain adapter bind a later issue
            # proposal to the Host-owned screenshot without taking another
            # screenshot or moving image bytes through the platform kernel.
            result["rawVisualRef"] = raw_visual_ref
        public = {
            "status": "ok", "result": result,
            "progress": self._progress_block("observe_page", {"status": "ok", "result": result}),
        }
        response = {
            "structuredContent": public,
            "content": [{"type": "text", "text": json.dumps(public, ensure_ascii=False, separators=(",", ":"))}, *visual_blocks],
            "isError": False,
        }
        self._public_investigations[object_id] = {
            "pageStateId": page,
            "rule": deepcopy(rule),
            "kind": case_kind,
            "purpose": purpose,
            "plannedCoverageDimensions": deepcopy(dimensions),
            "evidenceRefs": deepcopy(evidence_refs),
            "caseRefs": [case_id],
            "response": deepcopy(response),
            "decided": False,
        }
        if isinstance(candidate_id, str):
            self._public_candidate_objects[candidate_id] = object_id
        return response

    def _discover_scope(self, reason: str) -> dict:
        """Discover a bounded batch while keeping mechanical navigation in Host.

        The Facade may inspect the current page and advance through a small
        number of safe, candidate-free tab pages in one public call. It stops
        as soon as an undecided object is available, so no active investigation
        is navigated away from. Budgets are intentionally Host-owned.
        """
        builder = getattr(self._json.core, "build_discovery_plan", None)
        if not callable(builder):
            raise HostError("INTERNAL_FAILURE", "The runtime does not support discovery planning")
        steps: list[str] = []
        max_internal_pages = 4

        def wrap(structured: dict, images: list[dict] | None = None) -> dict:
            content = [{"type": "text", "text": json.dumps(
                structured, ensure_ascii=False, separators=(",", ":")
            )}]
            content.extend(images or [])
            return {"structuredContent": structured, "content": content, "isError": False}

        def plan() -> dict:
            return self._executor.submit(
                builder,
                self._public_active["scanId"], self._public_active["runId"],
                max_pages=32, max_logical_entrypoints=128,
            ).result()

        for _ in range(max_internal_pages + 1):
            current_page = self._public_active.get("currentPageStateId")
            if not isinstance(current_page, str):
                raise HostError("INTERNAL_FAILURE", "The active Scan has no current PageState")
            try:
                current_plan = plan()
            except HostError as error:
                if error.code != "UNKNOWN_REFERENCE":
                    raise
                inspected = self.call_tool("inspect_page", {
                    "pageStateId": current_page,
                    "include": ["route", "objects", "safeEntrypoints"],
                    "decisionReason": reason,
                })
                inspected_structured = inspected.get("structuredContent")
                if not isinstance(inspected_structured, dict):
                    raise HostError("INTERNAL_FAILURE", "Assayer returned an invalid discovery response")
                if inspected_structured.get("status") != "ok":
                    return inspected
                steps.append("inspect_page")
                current_plan = plan()
            pending = [item for item in current_plan.get("candidates", []) if item.get("status") == "pending"]
            if pending:
                current_plan["nextAction"] = {
                    "type": "investigate_objects",
                    "candidateRefs": [item["candidateId"] for item in pending],
                }
                break
            next_action = current_plan.get("nextAction") if isinstance(current_plan.get("nextAction"), dict) else {}
            if next_action.get("type") != "explore_entrypoint" or len(steps) >= max_internal_pages:
                break
            explored = self.call_tool("explore_entrypoint", {
                "pageStateId": current_page,
                "entrypointId": next_action["entrypointId"],
                "decisionReason": reason,
            })
            explored_structured = explored.get("structuredContent")
            if not isinstance(explored_structured, dict):
                raise HostError("INTERNAL_FAILURE", "Assayer returned an invalid entrypoint response")
            if explored_structured.get("status") != "ok":
                return explored
            steps.append("explore_entrypoint")
        else:
            current_plan = plan()
        current_plan["steps"] = steps
        current_plan["currentPageStateId"] = current_plan.get("currentPage", {}).get("pageStateId")
        current_plan["status"] = "ok"
        public = {"status": "ok", "result": current_plan}
        public["progress"] = self._progress_block("discover_scope", public)
        return wrap(public)

    @staticmethod
    def _public_summary_text(value: object) -> str:
        text = " ".join(str(value or "").split())
        return re.sub(
            r"(?i)(password|passwd|token|secret|cookie|authorization)\s*[=:]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            text,
        )

    @classmethod
    def _result_summary(cls, completion_input: dict | None, response: dict) -> dict:
        """Summarize terminal coverage and outcomes without exposing IDs."""
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        status = result.get("scanStatus")
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        if status not in {"completed", "partial", "failed"}:
            status = "failed" if response.get("status") == "failed" else "partial"
        if not isinstance(completion_input, dict):
            reason = cls._public_summary_text(
                error.get("message", "The audit did not produce a terminal coverage summary.")
            )
            return {
                "status": status,
                "conclusionsValid": bool(result.get("conclusionsValid", False)),
                "message": "The audit cannot support formal conclusions.",
                "reason": reason,
                "coverage": {
                    "pagesVisited": 0,
                    "objectsProcessed": 0,
                    "entrypointsProcessed": 0,
                    "entrypointsSkipped": 0,
                    "entrypointsRemaining": 0,
                },
                "outcomes": {
                    "issues": 0,
                    "needsReview": 0,
                    "needsReviewDetails": [],
                    "rules": [],
                },
                "uncoveredScope": {
                    "entrypointsRemaining": 0,
                    "incompleteRules": [],
                    "needsReview": [],
                },
                "nextStep": "Review diagnostics and retry with the same URL.",
            }
        rule_summaries = []
        issue_count = 0
        needs_review_count = 0
        needs_review_details = []
        for item in completion_input.get("ruleSummaries", []):
            if not isinstance(item, dict):
                continue
            counts = item.get("resultCounts") if isinstance(item.get("resultCounts"), dict) else {}
            issue_count += int(counts.get("issue_found", 0) or 0)
            needs_review_count += int(counts.get("needs_review", 0) or 0)
            rule = item.get("rule") if isinstance(item.get("rule"), dict) else {}
            rule_summaries.append({
                "rule": f"{rule.get('ruleId', 'unknown')}@{rule.get('version', 'unknown')}",
                "assessmentCount": int(item.get("assessmentCount", 0) or 0),
                "resultCounts": {str(key): int(value) for key, value in counts.items()},
                "coverageComplete": bool(item.get("coverageComplete")),
            })
            if counts.get("needs_review", 0) and isinstance(item.get("reason"), str):
                needs_review_details.append({
                    "rule": f"{rule.get('ruleId', 'unknown')}@{rule.get('version', 'unknown')}",
                    "detail": cls._public_summary_text(item["reason"]),
                })
        unprocessed = completion_input.get("unprocessedEntrypointRefs")
        skipped = completion_input.get("skippedEntrypoints")
        processed = completion_input.get("processedEntrypointRefs")
        incomplete_rules = [
            item["rule"] for item in rule_summaries if not item["coverageComplete"]
        ]
        if status == "failed":
            reason = cls._public_summary_text(
                error.get("message") or completion_input.get("completionReason") or "The Run failed before formal conclusions could be published."
            )
            next_step = "Review diagnostics, fix the reported cause, and start a new audit with the same URL."
        elif status == "partial":
            reason = cls._public_summary_text(
                completion_input.get("completionReason") or "The declared scope remains incomplete."
            )
            if needs_review_details:
                next_step = "Resolve the listed needs-review blockers and uncovered scope, then start a new audit if complete coverage is required."
            else:
                next_step = "Review the uncovered scope, then start a new audit if complete coverage is required."
        else:
            reason = cls._public_summary_text(
                completion_input.get("completionReason") or "The declared scope reached its coverage requirements."
            )
            next_step = (
                "Address the published issues, then start a new audit to verify the remediation."
                if issue_count else
                "No remediation is required for the rules and scope checked."
            )
        return {
            "status": status,
            "conclusionsValid": bool(result.get("conclusionsValid", status != "failed")),
            "message": {
                "completed": "The declared audit scope was completed.",
                "partial": "The audit produced valid results for part of the declared scope.",
                "failed": "The audit cannot support formal conclusions.",
            }[status],
            "reason": reason,
            "coverage": {
                "pagesVisited": len(completion_input.get("visitedPageStateRefs", [])),
                "objectsProcessed": len(completion_input.get("processedObjectRefs", [])),
                "entrypointsProcessed": len(processed or []),
                "entrypointsSkipped": len(skipped or []),
                "entrypointsRemaining": len(unprocessed or []),
            },
            "outcomes": {
                "issues": issue_count,
                "needsReview": needs_review_count,
                "needsReviewDetails": needs_review_details,
                "rules": rule_summaries,
            },
            "uncoveredScope": {
                "entrypointsRemaining": len(unprocessed or []),
                "incompleteRules": incomplete_rules,
                "needsReview": needs_review_details,
            },
            "nextStep": next_step,
        }

    def _atomic_prepare_decision(self, input_data: dict, reason: str) -> dict:
        """Close a decision while the verified object remains on its page."""
        input_data = deepcopy(input_data)
        findings = input_data.pop("findings")
        rule = input_data.get("rule", {})
        if any(
            check.check_id == rule.get("ruleId") and check.version == rule.get("version")
            for check in self._platform_session.manifest.checks
        ):
            try:
                self._platform_session.validate_decision(rule, input_data.get("result", ""), findings)
            except PlatformContractError as error:
                raise HostError(error.code, error.message) from error
        investigation = self._public_investigations.get(input_data["objectId"])
        if investigation and not investigation.get("decided"):
            if input_data.get("rule") != investigation["rule"]:
                raise HostError(
                    "UNKNOWN_REFERENCE",
                    "The decision rule does not match the current investigation context",
                )
            evidence_refs = deepcopy(investigation["evidenceRefs"])
            case_refs = deepcopy(investigation["caseRefs"])
            input_data["evidenceRefs"] = evidence_refs
            input_data["caseRefs"] = case_refs
            for finding in findings:
                finding["evidenceRefs"] = deepcopy(evidence_refs)
                finding["caseRefs"] = deepcopy(case_refs)
        # When the generic Run already has an InvestigationPacket, let the
        # registered plugin own the proposal-to-Host-command mapping and
        # receipt creation. The Host callback below remains only the narrow
        # browser-domain atomic persistence seam.
        if self._platform_run is not None:
            work_id = next((work_item_id for work_item_id, packet in self._platform_run.investigations.items()
                            if packet.metadata.get("objectId") == input_data.get("objectId")), None)
            if work_id is not None and self._platform_committer is not None:
                proposal = DecisionProposal(
                    work_id, self._platform_run.check.check_id, self._platform_run.check.version,
                    input_data["result"], tuple(Finding(
                        finding["dimension"], finding["status"], finding["reasonText"],
                    ) for finding in findings), input_data["reasonText"], {
                        key: input_data[key] for key in (
                            "blocker", "rawVisualRef", "severity", "title", "message",
                            "impact", "recommendation",
                        ) if key in input_data
                    },
                )
                commit_atomic = getattr(self._platform_committer, "commit_atomic", None)
                if callable(commit_atomic):
                    try:
                        receipt, committed = commit_atomic(
                            proposal, self._platform_run.investigations[work_id],
                            self._platform_run.check, self._platform_run.context,
                            lambda arguments: self._host_atomic_commit(arguments, reason),
                        )
                        if receipt is None:
                            committed_structured = committed.get("structuredContent")
                            return self._publicize(committed, committed_structured or {})
                        self._platform_run.record_commit(proposal, receipt)
                    except PlatformContractError as error:
                        raise HostError(error.code, error.message) from error
                    except HostError:
                        raise
                    except Exception as error:
                        raise HostError(
                            "PLATFORM_PERSISTENCE_FAILED",
                            "Platform decision receipt could not be durably persisted; the Assessment is not publishable",
                        ) from error
                    committed_structured = committed.get("structuredContent")
                    if isinstance(committed_structured, dict) and committed_structured.get("status") == "ok":
                        self._update_public_state("commit_decision", committed_structured)
                        if investigation:
                            investigation["decided"] = True
                    return self._publicize(committed, committed_structured or {})
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
        if commit_response.get("status") == "ok" and investigation:
            investigation["decided"] = True
        return self._publicize(committed, commit_response)

    def _host_atomic_commit(self, input_data: Mapping[str, Any], reason: str) -> dict:
        """Execute the existing browser-domain Finding/prepare/commit sequence."""
        record_input = {
            "objectId": input_data["objectId"],
            "rule": input_data["rule"],
            "findings": deepcopy(input_data["findings"]),
        }
        record_envelope = self._next_envelope("record_findings", record_input, reason)
        recorded = super().call_tool("record_findings", record_envelope)
        record_response = recorded.get("structuredContent")
        if not isinstance(record_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid Finding response")
        self._update_public_state("record_findings", record_response)
        if record_response.get("status") != "ok":
            return recorded
        finding_refs = record_response.get("result", {}).get("findingRefs")
        if not isinstance(finding_refs, list) or not finding_refs:
            raise HostError("INTERNAL_FAILURE", "Host did not return a Finding reference")
        prepare_input = {
            key: deepcopy(value) for key, value in input_data.items()
            if key not in {"findingRefs", "findings"}
        }
        prepare_input["findingRefs"] = finding_refs
        prepared = super().call_tool(
            "prepare_decision", self._next_envelope("prepare_decision", prepare_input, reason),
        )
        prepare_response = prepared.get("structuredContent")
        if not isinstance(prepare_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid PendingDecision response")
        self._update_public_state("prepare_decision", prepare_response)
        if prepare_response.get("status") != "ok":
            return prepared
        pending_id = prepare_response.get("result", {}).get("pendingDecisionId")
        if not isinstance(pending_id, str):
            raise HostError("INTERNAL_FAILURE", "Host did not return a PendingDecision reference")
        committed = super().call_tool(
            "commit_decision", self._next_envelope("commit_decision", {"pendingDecisionId": pending_id}, reason),
        )
        commit_response = committed.get("structuredContent")
        if not isinstance(commit_response, dict):
            raise HostError("INTERNAL_FAILURE", "Host returned an invalid Assessment response")
        self._update_public_state("commit_decision", commit_response)
        return committed

    def _publicize(self, result: dict, response: dict) -> dict:
        public_response = {
            key: deepcopy(value) for key, value in response.items()
            if key not in {"protocolVersion", "requestId", "scanId", "runId", "runRevision"}
        }
        if isinstance(public_response.get("result"), dict):
            for key in ("scanId", "runId", "runRevision", "operationId"):
                public_response["result"].pop(key, None)
        public_response["progress"] = self._progress_block("prepare_decision", public_response)
        public_content = [{"type": "text", "text": json.dumps(public_response, ensure_ascii=False, separators=(",", ":"))}]
        for block in result.get("content", [])[1:]:
            public_content.append(block)
        return {"structuredContent": public_response, "content": public_content,
                "isError": result.get("isError", False)}

    def _progress_block(self, tool: str, response: dict) -> dict:
        """Return a stable, user-readable progress summary without internal IDs."""
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        status = result.get("scanStatus")
        if status not in {"completed", "partial", "failed"}:
            status = {"failed": "failed", "rejected": "blocked"}.get(response.get("status"), "running")
        phase, message, next_step = {
            "start_audit": ("starting", "Audit started; connecting to the target browser.", "Inspect the current page."),
            "discover_scope": ("discovering", "Batching page discovery and removing duplicate logical entrypoints.", "Investigate the returned objects or continue with the next safe page."),
            "get_rule_contract": ("planning", "Loading the frozen rule contract for this audit.", "Inspect the current page or verified object."),
            "get_audit_progress": ("planning", "Rebuilt progress from the durable audit ledger.", "Continue with the next unfinished page or object."),
            "inspect_page": ("discovering", "Inspecting the current page and discovering safe entrypoints and objects.", "Inspect a discovered object or explore a safe entrypoint."),
            "explore_entrypoint": ("discovering", "Exploring a safe page entrypoint and recording the resulting page state.", "Inspect the discovered page or object."),
            "inspect_object": ("inspecting", "Verifying an audit object and its applicable rules.", "Start a bounded Case for the object."),
            "begin_case": ("investigating", "Starting a bounded evidence-gathering Case.", "Perform the planned safe observation."),
            "perform_action": ("acting", "Executing a controlled, read-only investigation action.", "Capture evidence and restore the Case."),
            "capture_evidence": ("collecting_evidence", "Capturing Host-verified evidence for the current Case.", "Restore the Case before deciding."),
            "observe_page": ("collecting_evidence", "Capturing aligned page and visual evidence for the current object.", "Use the evidence to complete the Finding."),
            "restore_case": ("recovering", "Restoring the page and verifying the original object context.", "Prepare the decision after recovery succeeds."),
            "record_findings": ("deciding", "Recording structured Findings for the frozen rule dimensions.", "Prepare the decision."),
            "prepare_decision": ("deciding", "Validating evidence, coverage, and the proposed rule decision.", "Commit the validated decision."),
            "commit_decision": ("deciding", "Committing the evidence-backed rule decision.", "Continue with the next object or page."),
            "get_operation": ("reconciling", "Reconciling an operation whose result was previously uncertain.", "Resume from the reconciled result."),
            "complete_audit": ("finalizing", "Checking coverage and publishing the audit artifacts.", "Review the audit summary and diagnostics."),
        }.get(tool, ("running", "Assayer is processing the audit.", "Continue the audit."))
        if status == "completed":
            phase, message, next_step = "completed", "Audit completed successfully.", "Review the audit summary and findings."
        elif status == "partial":
            phase, message, next_step = "completed", "Audit completed with unfinished or blocked scope.", "Review uncovered scope and retry if needed."
        elif status == "failed":
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            detail = error.get("message") if isinstance(error.get("message"), str) else "The audit cannot support a formal conclusion."
            phase, message, next_step = "failed", detail, "Fix the reported cause and retry with the same URL."
        elif status == "blocked":
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            detail = error.get("message") if isinstance(error.get("message"), str) else "The requested audit step was blocked."
            code = error.get("code") if isinstance(error.get("code"), str) else ""
            next_step = {
                "STALE_STATE": "Read durable audit progress, then continue from the current page state.",
                "AUDIT_PROGRESS_REQUIRED": "Read durable audit progress before retrying completion.",
                "CASE_ACTIVE": "Restore the active Case before changing pages or finalizing.",
                "DECISION_REQUIRED": "Finish the current object decision before changing pages.",
                "REQUEST_RESULT_UNKNOWN": "Reconcile the previous operation before issuing another action.",
                "OPERATION_RESULT_UNKNOWN": "Reconcile the previous operation before issuing another action.",
                "INVALID_REQUEST": "Correct the business input described by the error, then retry once.",
            }.get(code, "Follow the reported recovery guidance before continuing.")
            message = detail
        self._update_progress_counts(tool, result)
        if tool == "get_audit_progress" and status == "running":
            if result.get("pendingDecisionRefs"):
                phase, message, next_step = "deciding", "A prepared decision still needs to be completed.", "Complete the pending decision before changing pages."
            elif result.get("activeCases"):
                phase, message, next_step = "investigating", "An evidence-gathering Case is still active.", "Finish evidence collection and restore the active Case."
            elif self._public_progress["entrypointsRemaining"]:
                phase, message, next_step = "discovering", "The audit has reachable entrypoints left to process.", "Explore the next safe entrypoint."
            elif self._public_progress["objectsVerified"] > self._public_progress["decisionsCommitted"]:
                phase, message, next_step = "deciding", "A verified object still needs a rule decision.", "Complete the next unfinished object decision."
            else:
                phase, message, next_step = "finalizing", "Durable progress has no active Case or known unfinished object.", "Validate coverage and finalize the audit."
        progress = {
            "phase": phase,
            "status": status,
            "message": message[:512],
            "nextStep": next_step,
            "counts": deepcopy(self._public_progress),
            "terminal": status in {"completed", "partial", "failed"},
        }
        validation_error = next(self._public_progress_validator.iter_errors(progress), None)
        if validation_error is not None:
            raise HostError("INTERNAL_FAILURE", "Assayer produced an invalid public progress response")
        return progress

    def _update_progress_counts(self, tool: str, result: dict) -> None:
        if isinstance(result.get("visitedPageStateRefs"), list):
            self._public_progress_seen["pages"].update(result["visitedPageStateRefs"])
        for key in ("currentPageStateId", "pageStateId"):
            if isinstance(result.get(key), str):
                self._public_progress_seen["pages"].add(result[key])
        if isinstance(result.get("candidateRefs"), list):
            self._public_progress_seen["candidates"].update(result["candidateRefs"])
        if isinstance(result.get("candidates"), list):
            self._public_progress_seen["candidates"].update(
                item["candidateId"] for item in result["candidates"]
                if isinstance(item, dict) and isinstance(item.get("candidateId"), str)
            )
        if isinstance(result.get("objects"), list):
            for item in result["objects"]:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("objectId"), str):
                    self._public_progress_seen["objects"].add(item["objectId"])
                if isinstance(item.get("assessmentRefs"), list):
                    self._public_progress_seen["assessments"].update(item["assessmentRefs"])
        if isinstance(result.get("objectId"), str):
            self._public_progress_seen["objects"].add(result["objectId"])
        if isinstance(result.get("assessmentId"), str):
            self._public_progress_seen["assessments"].add(result["assessmentId"])
        self._public_progress["pagesVisited"] = len(self._public_progress_seen["pages"])
        self._public_progress["objectsDiscovered"] = len(self._public_progress_seen["candidates"])
        self._public_progress["objectsVerified"] = len(self._public_progress_seen["objects"])
        self._public_progress["decisionsCommitted"] = len(self._public_progress_seen["assessments"])
        entrypoints = result.get("entrypoints")
        if isinstance(entrypoints, dict):
            self._public_progress["entrypointsProcessed"] = len(entrypoints.get("processed", []))
            self._public_progress["entrypointsRemaining"] = len(entrypoints.get("unprocessed", []))
        counts = result.get("entrypointCounts")
        if isinstance(counts, dict):
            self._public_progress["entrypointsProcessed"] = int(counts.get("processed", 0) or 0)
            self._public_progress["entrypointsRemaining"] = int(counts.get("unprocessed", 0) or 0)

    def _next_envelope(self, name: str, input_data: dict, reason: str) -> dict:
        self._public_envelope_sequence += 1
        sequence = self._public_envelope_sequence
        request_id = f"{self._public_prefix}-request-{sequence:04d}"
        agent_turn_id = self._public_active_turn_id or (
            f"{self._public_prefix}-turn-{sequence:04d}:{name}"
        )
        if name == "start_audit":
            digest = hashlib.sha256(json.dumps(input_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
            return {
                "protocolVersion": "1.0", "requestId": request_id,
                "agentTurnId": agent_turn_id,
                "decisionReason": reason, "tool": name,
                # A second audit of the same URL is a new Scan, not an
                # idempotent replay of the prior terminal bootstrap.
                "idempotencyKey": f"{self._public_prefix}-bootstrap-{sequence:04d}-{digest}",
                "input": input_data,
            }
        active = self._public_active
        assert active is not None
        signature = hashlib.sha256(json.dumps({"tool": name, "input": input_data}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return {
            "protocolVersion": "1.0", "requestId": request_id,
            "scanId": active["scanId"], "runId": active["runId"],
            "agentTurnId": agent_turn_id,
            "decisionReason": reason, "tool": name,
            "idempotencyKey": f"{self._public_prefix}-{sequence:04d}-{signature}",
            "expectedRunRevision": active["runRevision"], "input": input_data,
        }

    def _update_public_state(self, name: str, response: dict) -> None:
        if self._public_active is None and name == "start_audit" and response.get("status") == "ok":
            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("scanId"), str) and isinstance(result.get("runId"), str):
                self._public_active = {
                    "scanId": result["scanId"], "runId": result["runId"],
                    "runRevision": response.get("runRevision", result.get("runRevision", 0)),
                    "currentPageStateId": result.get("currentPageStateId"),
                }
                return
        if self._public_active is not None and isinstance(response.get("runRevision"), int):
            self._public_active["runRevision"] = response["runRevision"]
        result = response.get("result")
        if self._public_active is not None and isinstance(result, dict):
            page_ref = result.get("currentPageStateId") or result.get("pageStateId")
            if isinstance(page_ref, str):
                self._public_active["currentPageStateId"] = page_ref
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


def create_interactive_mcp_server(
    *, output_root: str | Path = "./assayer-output",
    plugin_registry: PluginRegistry | None = None,
    runtime_resolver: Any = None,
    capabilities_resolver: Any = None,
):
    """Create a browser-independent MCP server for interactive plugins.

    Runtime adapters are intentionally injected by the embedding product.  A
    plain server created here can run non-browser interactive fixtures and will
    never start Chromium as a side effect of tool discovery.
    """
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer Interactive Plugins")
    adapter = InteractivePlatformMcpToolTransport(
        output_root, plugin_registry=plugin_registry,
        runtime_resolver=runtime_resolver,
        capabilities_resolver=capabilities_resolver,
    )
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
                key, inspect.Parameter.KEYWORD_ONLY, annotation=Any,
                default=inspect.Parameter.empty if key in required else None,
            ) for key in ordered
        ])
        return invoke

    for item in adapter.list_tools():
        name = item["name"]
        server.tool(name=name, description=item["description"])(make_invoke(name, item["inputSchema"]))
        registered = server._tool_manager.get_tool(name)
        if registered is not None:
            registered.parameters = deepcopy(item["inputSchema"])
    return server


def create_product_mcp_server(
    core: HostCore,
    *,
    output_root: str | Path = "./assayer-output",
    lifecycle_controller: LifecycleProductController | None = None,
    plugin_registry: PluginRegistry | None = None,
):
    """Create the generic plugin MCP server with frontend compatibility tools."""
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer")
    adapter = FrontendProductMcpToolTransport(
        core, output_root=output_root, lifecycle_controller=lifecycle_controller,
        plugin_registry=plugin_registry,
    )
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
        annotations = None
        if isinstance(item.get("annotations"), dict):
            from mcp.types import ToolAnnotations
            annotations = ToolAnnotations(**item["annotations"])
        server.tool(
            name=name, description=item["description"], annotations=annotations,
        )(make_invoke(name, item["inputSchema"]))
        registered = server._tool_manager.get_tool(name)
        if registered is not None:
            registered.parameters = deepcopy(item["inputSchema"])
    return server


# Existing callers and the shipped frontend Skill retain the historical name;
# new code should use the explicit compatibility name above.
ProductMcpToolTransport = FrontendProductMcpToolTransport


def mcp_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assayer plugin MCP stdio server")
    parser.add_argument("--output-root", default="./assayer-output", help="Fixed parent directory for all Scan output directories")
    parser.add_argument("--store", default=os.environ.get("ASSAYER_STORE", "./.assayer/plugins"),
                        help="Plugin installation store directory (defaults to $ASSAYER_STORE)")
    parser.add_argument("--max-runtimes", type=int, default=4)
    parser.add_argument("--lease-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    from .runtime_router import RuntimeRouter
    core = RuntimeRouter(args.output_root, max_runtimes=args.max_runtimes,
                         lease_timeout_seconds=args.lease_timeout)
    server = create_product_mcp_server(
        core, output_root=args.output_root,
        plugin_registry=store_backed_plugin_registry(args.store),
    )
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
