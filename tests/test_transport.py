import asyncio
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from assayer_host import (CredentialVault, DeterministicEvidenceAdapter, DeterministicLoginAdapter,
                          DeterministicObjectIdentityAdapter,
                          DeterministicPageAdapter, DeterministicRecoveryAdapter, HostCore, HostError,
                          JsonLineTransport, LoginSecret, McpToolTransport,
                          PlatformMcpToolTransport,
                          ProductMcpToolTransport, create_mcp_server,
                          create_product_mcp_server)
from assayer_host.page import EntrypointExecution, EntrypointObservation, PageObservation


VALID_RESPONSE = {
    "protocolVersion": "1.0", "requestId": "req-001", "scanId": "scan-001", "runId": "run-001",
    "runRevision": 1, "status": "ok", "result": {"operationId": "operation-001", "runRevision": 1},
    "evidenceRefs": [], "diagnosticRefs": [],
}


class RecordingCore:
    def __init__(self, response=None, error=None):
        self.response = response or VALID_RESPONSE
        self.error = error
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


class ThreadRecordingCore(RecordingCore):
    def __init__(self):
        super().__init__()
        self.thread_ids = []

    def handle(self, request):
        self.thread_ids.append(threading.get_ident())
        return super().handle(request)

    def close(self):
        self.thread_ids.append(threading.get_ident())


class HeartbeatRecordingCore(ThreadRecordingCore):
    heartbeat_interval_seconds = 0.01

    def __init__(self):
        super().__init__()
        self.heartbeat_seen = threading.Event()

    def heartbeat(self):
        self.thread_ids.append(threading.get_ident())
        self.heartbeat_seen.set()
        return 1


class VisualRecordingCore(RecordingCore):
    def __init__(self):
        super().__init__({
            "protocolVersion": "1.0", "requestId": "req-001", "scanId": "scan-001", "runId": "run-001",
            "runRevision": 2, "status": "ok", "result": {
                "operationId": "operation-001", "runRevision": 2, "evidenceId": "evidence-001",
                "screenshotRef": "screenshot-001", "observation": {"visual": {"status": "captured"}}
            }, "evidenceRefs": ["evidence-001"], "diagnosticRefs": []
        })

    def read_screenshot(self, scan_id, run_id, screenshot_ref):
        self.read_args = (scan_id, run_id, screenshot_ref)
        return b"\x89PNG\r\n\x1a\nvisual", "image/png"

    def close(self):
        pass


class ProductRecordingCore(RecordingCore):
    def __init__(self, terminal_status="completed", conclusions_valid=True, completion_payload=None):
        super().__init__()
        self.revision = 0
        self.terminal_status = terminal_status
        self.conclusions_valid = conclusions_valid
        self.completion_payload = completion_payload

    def handle(self, request):
        self.requests.append(request)
        self.revision += 1
        if request["tool"] == "start_audit":
            result = {
                "operationId": "operation-start", "runRevision": self.revision,
                "scanId": "scan-product", "runId": "run-product",
                "loginStatus": "succeeded", "ruleRegistryDigest": "a" * 64,
                "capabilities": ["runtime"], "frozenRules": [],
            }
        elif request["tool"] == "complete_audit":
            result = {
                "operationId": "operation-complete", "runRevision": self.revision,
                "scanStatus": self.terminal_status, "conclusionsValid": self.conclusions_valid,
            }
        elif request["tool"] == "record_findings":
            result = {"operationId": "operation-record", "runRevision": self.revision,
                      "findingRefs": ["finding-product"]}
        elif request["tool"] == "prepare_decision":
            result = {"operationId": "operation-prepare", "runRevision": self.revision,
                      "pendingDecisionId": "pending-product"}
        elif request["tool"] == "commit_decision":
            result = {"operationId": "operation-commit", "runRevision": self.revision,
                      "assessmentId": "assessment-product", "result": "scanned_no_issue"}
        else:
            result = {"operationId": f"operation-{self.revision}", "runRevision": self.revision}
        return {
            "protocolVersion": "1.0", "requestId": request["requestId"],
            "scanId": "scan-product", "runId": "run-product",
            "runRevision": self.revision, "status": "ok", "result": result,
            "evidenceRefs": [], "diagnosticRefs": [],
        }

    def close(self):
        pass

    def build_completion_input(self, scan_id, run_id, completion_reason=None):
        if self.completion_payload is not None:
            return self.completion_payload
        return {
            "visitedPageStateRefs": [], "processedObjectRefs": [],
            "processedEntrypointRefs": [], "skippedEntrypoints": [],
            "ruleSummaries": [], "unprocessedEntrypointRefs": [],
            "completionReason": completion_reason or "auto",
        }


class ProductUnknownCore(ProductRecordingCore):
    def handle(self, request):
        if request["tool"] == "perform_action":
            self.requests.append(request)
            self.revision += 1
            return {
                "protocolVersion": "1.0", "requestId": request["requestId"],
                "scanId": "scan-product", "runId": "run-product",
                "runRevision": self.revision, "status": "ok",
                "result": {
                    "operationId": "operation-unknown", "runRevision": self.revision,
                    "resultStatus": "result_unknown",
                },
                "evidenceRefs": [], "diagnosticRefs": [],
            }
        if request["tool"] == "get_operation":
            self.requests.append(request)
            self.revision += 1
            return {
                "protocolVersion": "1.0", "requestId": request["requestId"],
                "scanId": "scan-product", "runId": "run-product",
                "runRevision": self.revision, "status": "ok",
                "result": {
                    "operationId": "operation-unknown", "status": "succeeded",
                    "requestDigest": "b" * 64,
                },
                "evidenceRefs": [], "diagnosticRefs": [],
            }
        return super().handle(request)


class ProductBlockedCore(ProductRecordingCore):
    def handle(self, request):
        if request["tool"] != "start_audit":
            self.requests.append(request)
            self.revision += 1
            return {
                "protocolVersion": "1.0", "requestId": request["requestId"],
                "scanId": "scan-product", "runId": "run-product",
                "runRevision": self.revision, "status": "rejected",
                "error": {"code": "STALE_STATE", "message": "The current page object is stale"},
                "evidenceRefs": [], "diagnosticRefs": [],
            }
        return super().handle(request)


class ProductInvestigateCore(ProductRecordingCore):
    """Small deterministic core for the bounded investigate_object facade path."""

    def handle(self, request):
        tool = request["tool"]
        self.requests.append(request)
        self.revision += 1
        result = {"operationId": f"operation-{tool}", "runRevision": self.revision}
        if tool == "start_audit":
            result.update({
                "scanId": "scan-product", "runId": "run-product",
                "loginStatus": "succeeded", "ruleRegistryDigest": "a" * 64,
                "capabilities": ["runtime", "dom"], "frozenRules": [],
            })
        elif tool == "inspect_object":
            result.update({
                "objectId": "object-product", "candidateId": "candidate-product",
                "kind": "filter_region",
                "potentialRules": [{"ruleId": "FUA-10", "version": "1.1.0"}],
            })
        elif tool == "begin_case":
            result.update({"caseId": "case-product", "status": "active"})
        elif tool == "observe_page":
            if getattr(self, "fail_observe", False):
                return {
                    "protocolVersion": "1.0", "requestId": request["requestId"],
                    "scanId": "scan-product", "runId": "run-product",
                    "runRevision": self.revision, "status": "rejected",
                    "error": {"code": "CAPABILITY_MISSING", "message": "Visual observation is unavailable"},
                    "evidenceRefs": [], "diagnosticRefs": [],
                }
            result.update({
                "evidenceId": "evidence-observation",
                "screenshotRef": "screenshot-observation",
                "observation": {"visual": {"status": "captured"}},
            })
        elif tool == "capture_evidence":
            result.update({
                "evidenceId": "evidence-structured",
                "evidence": {"dom": {"status": "captured"}},
            })
        elif tool == "restore_case":
            result.update({"finalStatus": "restored"})
        elif tool == "record_findings":
            result.update({"findingRefs": ["finding-product"]})
        elif tool == "prepare_decision":
            result.update({"pendingDecisionId": "pending-product"})
        elif tool == "commit_decision":
            result.update({"assessmentId": "assessment-product", "result": "scanned_no_issue"})
        return {
            "protocolVersion": "1.0", "requestId": request["requestId"],
            "scanId": "scan-product", "runId": "run-product",
            "runRevision": self.revision, "status": "ok", "result": result,
            "evidenceRefs": [], "diagnosticRefs": [],
        }


class ProductSwitchingPageAdapter:
    active_tab = "Overview"

    def observe(self, page_state_id):
        return PageObservation(
            url="https://test.example.com/app", origin="https://test.example.com", route="/app",
            title="App", state_kind="tab", dom_material=f"<main>{self.active_tab}</main>",
            identity_material=f"/app|tab|{self.active_tab}", visible_text=self.active_tab,
            active_tab=self.active_tab,
            entrypoints=(
                EntrypointObservation(
                    "tab", "Overview", "switch_tab",
                    status="processed" if self.active_tab == "Overview" else "unprocessed",
                    host_locator_id="tab-browser-0",
                ),
                EntrypointObservation(
                    "tab", "Details", "switch_tab",
                    status="processed" if self.active_tab == "Details" else "unprocessed",
                    host_locator_id="tab-browser-1",
                ),
            ),
        )


class ProductSwitchingEntrypointAdapter:
    def __init__(self, page):
        self.page = page

    def explore(self, entrypoint, page_state, operation_id):
        self.page.active_tab = entrypoint["label"]
        return EntrypointExecution("succeeded", page_changed=True)


class TransportTest(unittest.TestCase):
    def test_json_transport_delegates_complete_envelope_without_mutation(self):
        core = RecordingCore()
        request = {"protocolVersion": "1.0", "requestId": "req-001", "tool": "inspect_page", "input": {"pageStateId": "page-001"}}
        self.assertIs(JsonLineTransport(core).invoke(request), VALID_RESPONSE)
        self.assertEqual(core.requests, [request])

    def test_json_lines_isolates_malformed_line_and_continues(self):
        core = RecordingCore()
        output = io.StringIO()
        JsonLineTransport(core).serve(io.StringIO("not-json\n{}\n"), output)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(lines[1], VALID_RESPONSE)

    def test_transport_error_does_not_echo_secret_or_stack(self):
        core = RecordingCore(error=HostError("INVALID_REQUEST", "invalid request"))
        request = {"protocolVersion": "1.0", "requestId": "req-001", "tool": "start_audit", "input": {"credentialHandle": "secret-handle"}}
        response = JsonLineTransport(core).invoke(request)
        self.assertEqual(response["status"], "rejected")
        self.assertNotIn("secret-handle", json.dumps(response))
        self.assertNotIn("Traceback", json.dumps(response))

    def test_agent_control_budget_is_reported_as_terminal_failure(self):
        core = RecordingCore(error=HostError(
            "AGENT_CONTROL_BUDGET_EXCEEDED", "Agent control budget exhausted"
        ))
        request = {
            "protocolVersion": "1.0", "requestId": "req-budget", "scanId": "scan-001",
            "runId": "run-001", "tool": "complete_audit", "input": {},
        }
        response = JsonLineTransport(core).invoke(request)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["requiredNextStep"], "stop")

    def test_mcp_lists_protocol_tools_and_returns_same_structured_response(self):
        core = RecordingCore()
        adapter = McpToolTransport(core)
        names = {item["name"] for item in adapter.list_tools()}
        self.assertIn("start_audit", names)
        self.assertTrue({"get_rule_contract", "get_audit_progress", "record_findings", "get_operation"}.issubset(names))
        start_schema = next(item["inputSchema"] for item in adapter.list_tools() if item["name"] == "start_audit")
        start_request = start_schema["properties"]["request"]
        self.assertEqual(start_request["properties"]["tool"], {"const": "start_audit"})
        self.assertIn("decisionReason", start_request["required"])
        self.assertIn("authMode", start_request["properties"]["input"]["required"])
        self.assertEqual(start_request["properties"]["input"]["properties"]["authMode"], {"const": "anonymous"})
        self.assertEqual(start_request["properties"]["input"]["properties"]["outputDir"], {"const": "auto"})
        self.assertEqual(start_request["properties"]["input"]["properties"]["browserProfile"], {"const": "default"})
        self.assertEqual(start_request["properties"]["input"]["required"],
                         ["url", "ruleRegistryVersion", "outputDir", "browserProfile", "authMode"])
        request = {"protocolVersion": "1.0", "requestId": "req-001", "tool": "inspect_page", "input": {}}
        result = adapter.call_tool("inspect_page", request)
        self.assertEqual(result["structuredContent"], VALID_RESPONSE)
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), VALID_RESPONSE)

    def test_mcp_schema_describes_complete_audit_required_fields(self):
        schema = next(item["inputSchema"] for item in McpToolTransport(RecordingCore()).list_tools()
                      if item["name"] == "complete_audit")
        request = schema["properties"]["request"]
        self.assertEqual(request["properties"]["tool"], {"const": "complete_audit"})
        self.assertIn("decisionReason", request["required"])
        self.assertEqual(request["properties"]["input"]["required"], [
            "visitedPageStateRefs", "processedObjectRefs", "processedEntrypointRefs",
            "skippedEntrypoints", "ruleSummaries", "unprocessedEntrypointRefs", "completionReason",
        ])
        summary = request["properties"]["input"]["properties"]["ruleSummaries"]["items"]
        self.assertEqual(summary["required"], ["rule", "assessmentCount", "resultCounts", "coverageComplete"])
        reason = request["properties"]["input"]["properties"]["skippedEntrypoints"]["items"]["properties"]["reason"]
        self.assertEqual(reason["required"], ["code", "message"])

    def test_mcp_cannot_change_tool_name_or_invent_partial_envelope(self):
        adapter = McpToolTransport(RecordingCore())
        with self.assertRaises(HostError) as mismatch:
            adapter.call_tool("inspect_page", {"tool": "start_audit"})
        self.assertEqual(mismatch.exception.code, "INVALID_REQUEST")
        with self.assertRaises(HostError) as unknown:
            adapter.call_tool("run arbitrary script", {})
        self.assertEqual(unknown.exception.code, "UNKNOWN_TOOL")

    def test_product_mcp_exposes_only_business_inputs(self):
        adapter = ProductMcpToolTransport(ProductRecordingCore())
        tools = adapter.list_tools()
        self.assertEqual(len(tools), 25)
        forbidden = {
            "protocolVersion", "requestId", "agentTurnId", "idempotencyKey",
            "scanId", "runId", "expectedRunRevision", "ruleRegistryVersion",
            "outputDir", "browserProfile", "authMode", "credentialHandle", "operationId",
        }
        for tool in tools:
            serialized = json.dumps(tool["inputSchema"])
            properties = json.loads(serialized).get("properties", {})
            self.assertTrue(forbidden.isdisjoint(properties))
            self.assertNotIn('"request"', serialized)
        start = next(tool for tool in tools if tool["name"] == "start_audit")
        self.assertEqual(set(start["inputSchema"]["properties"]), {"url", "decisionReason"})
        self.assertEqual(start["inputSchema"]["required"], ["url"])
        operation = next(tool for tool in tools if tool["name"] == "get_operation")
        self.assertEqual(set(operation["inputSchema"]["properties"]), {"decisionReason"})
        complete = next(tool for tool in tools if tool["name"] == "complete_audit")
        self.assertEqual(set(complete["inputSchema"]["properties"]), {"completionReason", "decisionReason"})
        self.assertEqual(complete["inputSchema"].get("required", []), [])
        self.assertIn("investigate_object", {tool["name"] for tool in tools})
        self.assertIn("discover_scope", {tool["name"] for tool in tools})
        self.assertIn("start_plugin_run", {tool["name"] for tool in tools})
        self.assertIn("submit_decisions", {tool["name"] for tool in tools})
        discover = next(tool for tool in tools if tool["name"] == "discover_scope")
        self.assertEqual(set(discover["inputSchema"]["properties"]), {"decisionReason"})
        investigate = next(tool for tool in tools if tool["name"] == "investigate_object")
        self.assertEqual(investigate["inputSchema"]["required"], ["pageStateId"])
        self.assertEqual(investigate["inputSchema"]["oneOf"], [{"required": ["objectId"]}, {"required": ["candidateId"]}])
        self.assertNotIn("record_findings", {tool["name"] for tool in tools})
        self.assertNotIn("commit_decision", {tool["name"] for tool in tools})
        self.assertNotIn("get_rule_contract", {tool["name"] for tool in tools})

    def test_generic_platform_mcp_runs_registered_configuration_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            transport = PlatformMcpToolTransport(Path(directory) / "output")
            tools = transport.list_tools()
            self.assertEqual([item["name"] for item in tools], ["list_plugins", "run_plugin"])
            catalog = transport.call_tool("list_plugins", {})["structuredContent"]["result"]["plugins"]
            config = next(item for item in catalog if item["pluginId"] == "assayer.config-quality")
            self.assertEqual(config["scopeSchema"]["required"], ["files"])
            result = transport.call_tool("run_plugin", {
                "pluginId": "assayer.config-quality", "checkId": "CFG-001",
                "scope": {"files": [{
                    "path": str(source), "requiredKeys": ["enabled"],
                    "expectedTypes": {"enabled": "boolean"},
                }]},
            })
            self.assertEqual(result["structuredContent"]["status"], "ok")
            self.assertEqual(result["structuredContent"]["result"]["status"], "completed")
            self.assertEqual(result["structuredContent"]["result"]["decisions"][0]["result"], "scanned_no_issue")

    def test_generic_platform_mcp_rejects_interactive_plugin(self):
        transport = PlatformMcpToolTransport(tempfile.mkdtemp())
        with self.assertRaises(HostError) as error:
            transport.call_tool("run_plugin", {
                "pluginId": "assayer.frontend-audit", "checkId": "FUA-10", "scope": {},
            })
        self.assertEqual(error.exception.code, "PLUGIN_EXECUTION_MODE_UNSUPPORTED")

    def test_product_mcp_internally_frames_start_and_session_requests(self):
        core = ProductRecordingCore()
        adapter = ProductMcpToolTransport(core)
        started = adapter.call_tool("start_audit", {"url": "https://test.example.com"})
        bootstrap = core.requests[0]
        self.assertEqual(bootstrap["protocolVersion"], "1.0")
        self.assertEqual(bootstrap["tool"], "start_audit")
        self.assertEqual(bootstrap["input"], {
            "url": "https://test.example.com", "ruleRegistryVersion": "1.0.0",
            "outputDir": "auto", "browserProfile": "default", "authMode": "anonymous",
        })
        self.assertNotIn("protocolVersion", started["structuredContent"])
        self.assertNotIn("scanId", started["structuredContent"]["result"])
        self.assertNotIn("operationId", started["structuredContent"]["result"])
        adapter.call_tool("get_audit_progress", {
            "decisionReason": "Read durable audit progress.",
        })
        session = core.requests[1]
        self.assertEqual(session["scanId"], "scan-product")
        self.assertEqual(session["runId"], "run-product")
        self.assertEqual(session["expectedRunRevision"], 1)
        self.assertEqual(session["input"], {})
        adapter.close()

    def test_product_mcp_returns_user_readable_progress_without_internal_ids(self):
        adapter = ProductMcpToolTransport(ProductRecordingCore())
        started = adapter.call_tool("start_audit", {"url": "https://test.example.com"})["structuredContent"]
        self.assertEqual(started["progress"]["phase"], "starting")
        self.assertEqual(started["progress"]["status"], "running")
        self.assertEqual(started["progress"]["nextStep"], "Inspect the current page.")
        self.assertNotIn("scanId", json.dumps(started["progress"]))
        inspected = adapter.call_tool("inspect_page", {
            "pageStateId": "page-product", "include": ["objects"],
        })["structuredContent"]
        self.assertEqual(inspected["progress"]["phase"], "discovering")
        decision = adapter.call_tool("prepare_decision", {
            "objectId": "object-product", "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
            "result": "scanned_no_issue", "reasonText": "Evidence satisfies the required dimensions",
            "evidenceRefs": ["evidence-product"], "caseRefs": ["case-product"],
            "findings": [{"dimension": dimension, "status": "satisfied",
                          "reasonText": "The frontend dimension is satisfied", "evidenceRefs": ["evidence-product"],
                          "caseRefs": ["case-product"]}
                         for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")],
        })["structuredContent"]
        self.assertEqual(decision["progress"]["phase"], "deciding")
        self.assertEqual(decision["progress"]["counts"]["decisionsCommitted"], 1)
        completed = adapter.call_tool("complete_audit", {})["structuredContent"]
        self.assertEqual(completed["progress"]["phase"], "completed")
        self.assertEqual(completed["progress"]["status"], "completed")
        self.assertTrue(completed["progress"]["terminal"])
        adapter.close()

    def test_product_mcp_summarizes_completed_partial_and_failed_results(self):
        completion = {
            "visitedPageStateRefs": ["page-one", "page-two"],
            "processedObjectRefs": ["object-one"],
            "processedEntrypointRefs": ["entry-one"],
            "skippedEntrypoints": [{
                "entrypointId": "entry-two",
                "reason": {"code": "OUT_OF_SCOPE", "message": "Outside the declared audit scope"},
            }],
            "unprocessedEntrypointRefs": ["entry-three"],
            "ruleSummaries": [{
                "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
                "assessmentCount": 2,
                "resultCounts": {"issue_found": 1, "needs_review": 1},
                "coverageComplete": False,
                "reason": "Needs review because filter_region on /orders: list ownership is unresolved.",
            }],
            "completionReason": "Finish the current durable scope",
        }
        expectations = {
            "completed": (True, "Review the audit summary"),
            "partial": (True, "Review uncovered scope"),
            "failed": (False, "Review diagnostics"),
        }
        for status, (valid, next_step) in expectations.items():
            with self.subTest(status=status):
                adapter = ProductMcpToolTransport(ProductRecordingCore(
                    terminal_status=status,
                    conclusions_valid=valid,
                    completion_payload=completion,
                ))
                try:
                    adapter.call_tool("start_audit", {"url": "https://test.example.com"})
                    response = adapter.call_tool("complete_audit", {})["structuredContent"]
                    summary = response["summary"]
                    self.assertEqual(summary["status"], status)
                    self.assertEqual(summary["conclusionsValid"], valid)
                    self.assertEqual(summary["coverage"], {
                        "pagesVisited": 2,
                        "objectsProcessed": 1,
                        "entrypointsProcessed": 1,
                        "entrypointsSkipped": 1,
                        "entrypointsRemaining": 1,
                    })
                    self.assertEqual(summary["outcomes"]["issues"], 1)
                    self.assertEqual(summary["outcomes"]["needsReview"], 1)
                    self.assertIn("list ownership is unresolved", summary["outcomes"]["needsReviewDetails"][0]["detail"])
                    self.assertIn(next_step, summary["nextStep"])
                    self.assertEqual(response["progress"]["status"], status)
                    self.assertTrue(response["progress"]["terminal"])
                finally:
                    adapter.close()

    def test_product_mcp_retry_starts_independent_scan_and_resets_public_progress(self):
        core = ProductRecordingCore()
        adapter = ProductMcpToolTransport(core)
        try:
            first = adapter.call_tool("start_audit", {"url": "https://test.example.com"})["structuredContent"]
            decided = adapter.call_tool("prepare_decision", {
                "objectId": "object-product", "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
                "result": "scanned_no_issue", "reasonText": "The evidence closes the rule dimensions",
                "evidenceRefs": ["evidence-product"], "caseRefs": ["case-product"],
                "findings": [{
                    "dimension": dimension, "status": "satisfied",
                    "reasonText": "The frontend dimension is satisfied", "evidenceRefs": ["evidence-product"],
                    "caseRefs": ["case-product"],
                } for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")],
            })["structuredContent"]
            self.assertEqual(decided["progress"]["counts"]["decisionsCommitted"], 1)
            adapter.call_tool("complete_audit", {})
            second = adapter.call_tool("start_audit", {"url": "https://test.example.com"})["structuredContent"]
            bootstrap_requests = [request for request in core.requests if request["tool"] == "start_audit"]
            self.assertEqual(len(bootstrap_requests), 2)
            self.assertNotEqual(bootstrap_requests[0]["idempotencyKey"], bootstrap_requests[1]["idempotencyKey"])
            self.assertEqual(first["progress"]["counts"]["decisionsCommitted"], 0)
            self.assertEqual(second["progress"]["counts"], {
                "pagesVisited": 0,
                "objectsDiscovered": 0,
                "objectsVerified": 0,
                "decisionsCommitted": 0,
                "entrypointsProcessed": 0,
                "entrypointsRemaining": 0,
            })
        finally:
            adapter.close()

    def test_product_mcp_progress_explains_recoverable_block(self):
        adapter = ProductMcpToolTransport(ProductBlockedCore())
        adapter.call_tool("start_audit", {"url": "https://test.example.com"})
        blocked = adapter.call_tool("inspect_object", {"candidateId": "candidate-product"})["structuredContent"]
        self.assertEqual(blocked["progress"]["status"], "blocked")
        self.assertFalse(blocked["progress"]["terminal"])
        self.assertEqual(blocked["progress"]["message"], "The current page object is stale")
        self.assertIn("Read durable audit progress", blocked["progress"]["nextStep"])
        self.assertNotIn("runRevision", json.dumps(blocked["progress"]))
        adapter.close()

    def test_product_mcp_rejects_any_model_supplied_protocol_field(self):
        adapter = ProductMcpToolTransport(ProductRecordingCore())
        with self.assertRaises(HostError) as error:
            adapter.call_tool("start_audit", {
                "url": "https://test.example.com", "protocolVersion": "1",
            })
        self.assertEqual(error.exception.code, "INVALID_REQUEST")
        with self.assertRaises(HostError):
            adapter.call_tool("start_audit", {
                "url": "https://test.example.com", "authMode": "anonymous",
            })
        adapter.close()

    def test_product_mcp_reconciles_unknown_operation_without_model_id(self):
        core = ProductUnknownCore()
        adapter = ProductMcpToolTransport(core)
        adapter.call_tool("start_audit", {"url": "https://test.example.com"})
        unknown = adapter.call_tool("perform_action", {
            "pageStateId": "page-001", "caseId": "case-001", "objectId": "object-001",
            "type": "focus", "intent": "Observe the current control", "parameters": {},
        })
        self.assertNotIn("operationId", unknown["structuredContent"]["result"])
        with self.assertRaises(HostError) as blocked:
            adapter.call_tool("get_audit_progress", {})
        self.assertEqual(blocked.exception.code, "REQUEST_RESULT_UNKNOWN")
        reconciled = adapter.call_tool("get_operation", {})
        self.assertEqual(core.requests[-1]["input"], {"operationId": "operation-unknown"})
        self.assertNotIn("operationId", reconciled["structuredContent"]["result"])
        adapter.call_tool("get_audit_progress", {})
        adapter.close()

    def test_product_mcp_atomically_records_prepares_and_commits_findings(self):
        core = ProductRecordingCore()
        adapter = ProductMcpToolTransport(core)
        adapter.call_tool("start_audit", {"url": "https://test.example.com"})
        result = adapter.call_tool("prepare_decision", {
            "objectId": "object-product", "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
            "result": "scanned_no_issue", "reasonText": "Evidence satisfies all four dimensions",
            "evidenceRefs": ["evidence-product"], "caseRefs": ["case-product"],
            "findings": [{"dimension": dimension, "status": "satisfied",
                           "reasonText": "The frontend dimension is satisfied", "evidenceRefs": ["evidence-product"],
                           "caseRefs": ["case-product"]}
                          for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")],
        })
        self.assertEqual(result["structuredContent"]["result"]["assessmentId"], "assessment-product")
        self.assertEqual([item["tool"] for item in core.requests],
                         ["start_audit", "record_findings", "prepare_decision", "commit_decision"])
        self.assertEqual(len({item["agentTurnId"] for item in core.requests[1:]}), 1)
        self.assertTrue(core.requests[1]["agentTurnId"].endswith(":prepare_decision"))
        self.assertNotEqual(core.requests[0]["agentTurnId"], core.requests[1]["agentTurnId"])
        self.assertNotIn("pendingDecisionId", result["structuredContent"]["result"])
        adapter.close()

    def test_product_mcp_applies_frontend_plugin_gate_before_host_writes(self):
        core = ProductRecordingCore()
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            with self.assertRaises(HostError) as caught:
                adapter.call_tool("prepare_decision", {
                    "objectId": "object-product",
                    "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
                    "result": "scanned_no_issue",
                    "reasonText": "Only one dimension was supplied",
                    "evidenceRefs": ["evidence-product"], "caseRefs": ["case-product"],
                    "findings": [{
                        "dimension": "filter_present", "status": "satisfied",
                        "reasonText": "The filter is visible", "evidenceRefs": ["evidence-product"],
                        "caseRefs": ["case-product"],
                    }],
                })
            self.assertEqual(caught.exception.code, "FINDING_CLOSURE")
            self.assertEqual([request["tool"] for request in core.requests], ["start_audit"])
        finally:
            adapter.close()

    def test_product_mcp_investigate_object_runs_bounded_evidence_lifecycle(self):
        core = ProductInvestigateCore()
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            response = adapter.call_tool("investigate_object", {
                "candidateId": "candidate-product", "pageStateId": "page-product",
                "purpose": "Verify the visible filter region",
            })
            self.assertEqual(response["structuredContent"]["status"], "ok")
            result = response["structuredContent"]["result"]
            self.assertEqual(result["objectId"], "object-product")
            self.assertEqual(result["rule"], {"ruleId": "FUA-10", "version": "1.1.0"})
            self.assertEqual(result["caseId"], "case-product")
            self.assertEqual(result["evidenceRefs"], ["evidence-observation", "evidence-structured"])
            self.assertEqual(result["rawVisualRef"], "screenshot-observation")
            self.assertTrue(result["readyForDecision"])
            self.assertEqual(
                [item["tool"] for item in core.requests],
                ["start_audit", "inspect_object", "begin_case", "observe_page", "capture_evidence", "restore_case"],
            )
            self.assertEqual(len({item["agentTurnId"] for item in core.requests[1:]}), 1)
            self.assertTrue(core.requests[1]["agentTurnId"].endswith(":investigate_object"))
        finally:
            adapter.close()

    def test_product_mcp_investigate_object_restores_case_after_observation_failure(self):
        core = ProductInvestigateCore()
        core.fail_observe = True
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            response = adapter.call_tool("investigate_object", {
                "candidateId": "candidate-product", "pageStateId": "page-product",
            })
            self.assertEqual(response["structuredContent"]["status"], "rejected")
            self.assertEqual(response["structuredContent"]["error"]["code"], "CAPABILITY_MISSING")
            self.assertEqual(
                [item["tool"] for item in core.requests],
                ["start_audit", "inspect_object", "begin_case", "observe_page", "restore_case"],
            )
        finally:
            adapter.close()

    def test_product_mcp_reuses_identical_restored_investigation(self):
        core = ProductInvestigateCore()
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            arguments = {
                "candidateId": "candidate-product", "pageStateId": "page-product",
                "purpose": "Verify the visible filter region",
            }
            first = adapter.call_tool("investigate_object", arguments)
            request_count = len(core.requests)
            second = adapter.call_tool("investigate_object", arguments)
            self.assertEqual(len(core.requests), request_count)
            self.assertEqual(second["structuredContent"]["result"]["caseId"],
                             first["structuredContent"]["result"]["caseId"])
            self.assertEqual(second["structuredContent"]["result"]["evidenceRefs"],
                             first["structuredContent"]["result"]["evidenceRefs"])
        finally:
            adapter.close()

    def test_product_mcp_uses_investigation_refs_for_atomic_decision(self):
        core = ProductInvestigateCore()
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            investigated = adapter.call_tool("investigate_object", {
                "candidateId": "candidate-product", "pageStateId": "page-product",
            })["structuredContent"]["result"]
            adapter.call_tool("prepare_decision", {
                "objectId": investigated["objectId"], "rule": investigated["rule"],
                "result": "scanned_no_issue", "reasonText": "All dimensions are satisfied",
                "evidenceRefs": ["evidence-wrong"], "caseRefs": ["case-wrong"],
                "findings": [{
                    "dimension": dimension, "status": "satisfied",
                    "reasonText": "The investigation supports this dimension",
                    "evidenceRefs": ["evidence-wrong"], "caseRefs": ["case-wrong"],
                } for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")],
            })
            record = next(item for item in core.requests if item["tool"] == "record_findings")
            prepare = next(item for item in core.requests if item["tool"] == "prepare_decision")
            self.assertEqual(record["input"]["findings"][0]["evidenceRefs"],
                             investigated["evidenceRefs"])
            self.assertEqual(record["input"]["findings"][0]["caseRefs"],
                             [investigated["caseId"]])
            self.assertEqual(prepare["input"]["evidenceRefs"], investigated["evidenceRefs"])
            self.assertEqual(prepare["input"]["caseRefs"], [investigated["caseId"]])
        finally:
            adapter.close()

    def test_product_mcp_discover_scope_returns_host_deduplicated_batch(self):
        core = HostCore(
            login_adapter=DeterministicLoginAdapter(),
            page_adapter=DeterministicPageAdapter(),
            object_identity_adapter=DeterministicObjectIdentityAdapter(),
        )
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            response = adapter.call_tool("discover_scope", {})
            self.assertEqual(response["structuredContent"]["status"], "ok")
            result = response["structuredContent"]["result"]
            self.assertEqual(result["nextAction"]["type"], "investigate_objects")
            self.assertEqual(result["entrypointCounts"]["duplicates"], 0)
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(response["structuredContent"]["progress"]["phase"], "discovering")
        finally:
            adapter.close()

    def test_product_mcp_discover_scope_advances_candidate_free_tabs_internally(self):
        page_adapter = ProductSwitchingPageAdapter()
        core = HostCore(
            login_adapter=DeterministicLoginAdapter(),
            page_adapter=page_adapter,
            object_identity_adapter=DeterministicObjectIdentityAdapter(),
            entrypoint_adapter=ProductSwitchingEntrypointAdapter(page_adapter),
        )
        adapter = ProductMcpToolTransport(core)
        try:
            adapter.call_tool("start_audit", {"url": "https://test.example.com"})
            response = adapter.call_tool("discover_scope", {})["structuredContent"]
            result = response["result"]
            self.assertEqual(result["steps"], ["inspect_page", "explore_entrypoint"])
            self.assertEqual(result["currentPage"]["title"], "App")
            self.assertEqual(result["nextAction"]["type"], "ready_to_complete")
            self.assertEqual(result["entrypointCounts"]["duplicates"], 2)
        finally:
            adapter.close()

    def test_product_mcp_builds_completion_from_runtime_ledger(self):
        core = ProductRecordingCore()
        adapter = ProductMcpToolTransport(core)
        adapter.call_tool("start_audit", {"url": "https://test.example.com"})
        result = adapter.call_tool("complete_audit", {"completionReason": "Converge the current ledger"})
        self.assertEqual(result["structuredContent"]["result"]["scanStatus"], "completed")
        self.assertEqual(core.requests[-1]["tool"], "complete_audit")
        self.assertEqual(core.requests[-1]["input"]["completionReason"], "Converge the current ledger")
        self.assertIn("visitedPageStateRefs", core.requests[-1]["input"])
        adapter.close()

    def test_product_mcp_real_core_atomically_decides_and_auto_completes(self):
        with tempfile.TemporaryDirectory() as output:
            core = HostCore(
                login_adapter=DeterministicLoginAdapter(),
                page_adapter=DeterministicPageAdapter(),
                object_identity_adapter=DeterministicObjectIdentityAdapter(),
                evidence_adapter=DeterministicEvidenceAdapter(),
                recovery_adapter=DeterministicRecoveryAdapter(),
            )
            adapter = ProductMcpToolTransport(core)
            try:
                started = adapter.call_tool("start_audit", {"url": "https://test.example.com"})["structuredContent"]["result"]
                scan_id = core._store._conn.execute("SELECT scan_id FROM scans").fetchone()[0]
                with core._store.transaction() as connection:
                    connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (output, scan_id))
                page_id = started["currentPageStateId"]
                page = adapter.call_tool("inspect_page", {"pageStateId": page_id, "include": ["objects"]})["structuredContent"]["result"]
                verified = adapter.call_tool("inspect_object", {"candidateId": page["candidateRefs"][0]})["structuredContent"]["result"]
                object_id = verified["objectId"]
                rule = {"ruleId": "FUA-10", "version": "1.1.0"}
                case = adapter.call_tool("begin_case", {
                    "objectId": object_id, "rule": rule, "kind": "observation",
                    "purpose": "Verify frontend filter-region capabilities",
                    "plannedCoverageDimensions": ["filter_present", "query_action", "reset_action", "binding_to_list"],
                })["structuredContent"]["result"]
                evidence = adapter.call_tool("capture_evidence", {
                    "pageStateId": page_id, "objectId": object_id, "caseId": case["caseId"],
                    "includeRawVisual": False,
                })["structuredContent"]["result"]
                adapter.call_tool("restore_case", {
                    "caseId": case["caseId"], "pageStateId": page_id, "objectId": object_id,
                    "fallback": "refresh_and_replay_safe_entrypoints",
                })
                decision = adapter.call_tool("prepare_decision", {
                    "objectId": object_id, "rule": rule, "result": "scanned_no_issue",
                    "reasonText": "Evidence for one object supports all four frontend dimensions",
                    "evidenceRefs": [evidence["evidenceId"]], "caseRefs": [case["caseId"]],
                    "findings": [
                        {"dimension": dimension, "status": "satisfied", "reasonText": "Object evidence supports this dimension",
                         "evidenceRefs": [evidence["evidenceId"]], "caseRefs": [case["caseId"]]}
                        for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")
                    ],
                })["structuredContent"]
                self.assertEqual(decision["status"], "ok")
                self.assertIn("assessmentId", decision["result"])
                completion = adapter.call_tool("complete_audit", {})["structuredContent"]
                self.assertEqual(completion["result"]["scanStatus"], "completed")
                self.assertTrue(completion["result"]["conclusionsValid"])
                self.assertTrue({
                    "platform-ledger.json", "platform-events.jsonl", "platform-run.log",
                }.issubset(set(completion["result"]["artifactPaths"])))
                self.assertTrue((Path(output) / "platform-ledger.json").is_file())
                self.assertTrue((Path(output) / "platform-events.jsonl").is_file())
                self.assertTrue((Path(output) / "platform-run.log").is_file())
                run_id = core._store._conn.execute(
                    "SELECT run_id FROM scans WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
                platform_ledger = core.platform_ledger_store.load(run_id)
                self.assertEqual(platform_ledger["status"], "completed")
                self.assertEqual(len(platform_ledger["work_items"]), 1)
                self.assertEqual(len(platform_ledger["investigations"]), 1)
                self.assertEqual(len(platform_ledger["decisions"]), 1)
                self.assertEqual(len(platform_ledger["receipts"]), 1)
                self.assertEqual(platform_ledger["decision_authority"], "platform")
                receipt = platform_ledger["receipts"][0]
                self.assertEqual(receipt["authority"], "platform")
                self.assertEqual(receipt["metadata"]["hostAssessmentId"], decision["result"]["assessmentId"])
            finally:
                adapter.close()

    def test_mcp_attaches_observe_page_visual_to_structured_response(self):
        core = VisualRecordingCore()
        adapter = McpToolTransport(core)
        request = {"protocolVersion": "1.0", "requestId": "req-001", "scanId": "scan-001", "runId": "run-001",
                   "tool": "observe_page", "input": {}}
        result = adapter.call_tool("observe_page", request)
        self.assertEqual(result["content"][1]["type"], "image")
        self.assertEqual(result["content"][1]["mimeType"], "image/png")
        self.assertEqual(core.read_args, ("scan-001", "run-001", "screenshot-001"))
        adapter.close()

    def test_mcp_serializes_calls_and_shutdown_on_one_dedicated_thread(self):
        core = ThreadRecordingCore()
        adapter = McpToolTransport(core)
        request = {"protocolVersion": "1.0", "requestId": "req-001",
                   "tool": "inspect_page", "input": {}}
        adapter.call_tool("inspect_page", request)
        adapter.call_tool("inspect_page", dict(request, requestId="req-002"))
        adapter.close()
        self.assertEqual(len(core.thread_ids), 3)
        self.assertEqual(len(set(core.thread_ids)), 1)
        self.assertNotEqual(core.thread_ids[0], threading.get_ident())

    def test_mcp_heartbeat_runs_on_the_same_runtime_owning_thread(self):
        core = HeartbeatRecordingCore()
        adapter = McpToolTransport(core)
        self.assertTrue(core.heartbeat_seen.wait(timeout=1.0))
        adapter.call_tool("inspect_page", {
            "protocolVersion": "1.0", "requestId": "req-heartbeat",
            "tool": "inspect_page", "input": {},
        })
        adapter.close()
        self.assertGreaterEqual(len(core.thread_ids), 3)
        self.assertEqual(len(set(core.thread_ids)), 1)

    def test_json_and_mcp_are_idempotently_equivalent_on_same_core(self):
        vault = CredentialVault(); vault.put("credential-001", LoginSecret("user", "password"))
        core = HostCore(credential_vault=vault, login_adapter=DeterministicLoginAdapter(),
                        page_adapter=DeterministicPageAdapter(),
                        object_identity_adapter=DeterministicObjectIdentityAdapter())
        try:
            request = {"protocolVersion": "1.0", "requestId": "req-start", "agentTurnId": "turn-001",
                       "tool": "start_audit", "idempotencyKey": "start-001",
                       "input": {"url": "https://test.example.com", "ruleRegistryVersion": "1.0.0",
                                 "outputDir": "/tmp/assayer-transport-test", "browserProfile": "default",
                                 "credentialHandle": "credential-001"}}
            direct = JsonLineTransport(core).invoke(request)
            via_mcp = McpToolTransport(core).call_tool("start_audit", request)["structuredContent"]
            self.assertEqual(via_mcp, direct)
        finally:
            core.close()

    def test_optional_fastmcp_server_registers_single_argument_tools(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("MCP optional dependency is not installed")
        server = create_mcp_server(RecordingCore())
        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertIn("start_audit", names)
        self.assertIn("get_operation", names)
        self.assertEqual(len(names), 17)
        complete = server._tool_manager.get_tool("complete_audit")
        self.assertIsNotNone(complete)
        self.assertEqual(complete.parameters["properties"]["request"]["properties"]["tool"],
                         {"const": "complete_audit"})
        self.assertIn("resultCounts", complete.parameters["properties"]["request"]["properties"]["input"]["properties"]["ruleSummaries"]["items"]["required"])

    def test_product_fastmcp_server_executes_url_only_start(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("MCP optional dependency is not installed")
        core = ProductRecordingCore()
        server = create_product_mcp_server(core)
        start = server._tool_manager.get_tool("start_audit")
        self.assertEqual(set(start.parameters["properties"]), {"url", "decisionReason"})
        result = asyncio.run(start.run({"url": "https://test.example.com"}))
        self.assertEqual(result["structuredContent"]["status"], "ok")
        self.assertEqual(core.requests[0]["protocolVersion"], "1.0")
        server._assayer_transport.close()


if __name__ == "__main__":
    unittest.main()
