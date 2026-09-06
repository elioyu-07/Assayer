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
                          PlatformMcpToolTransport, create_mcp_server)
from assayer_platform import PluginRegistry
from tests.helpers import config_quality_registration


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

    def test_generic_platform_mcp_runs_registered_configuration_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            transport = PlatformMcpToolTransport(
                Path(directory) / "output",
                plugin_registry=PluginRegistry((config_quality_registration(),)),
            )
            tools = transport.list_tools()
            self.assertEqual([item["name"] for item in tools], ["list_plugins", "run_plugin"])
            catalog = transport.call_tool("list_plugins", {})["structuredContent"]["result"]["plugins"]
            config = next(item for item in catalog if item["pluginId"] == "test.config-quality")
            self.assertEqual(config["scopeSchema"]["required"], ["files"])
            result = transport.call_tool("run_plugin", {
                "pluginId": "test.config-quality", "checkId": "CFG-001",
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

if __name__ == "__main__":
    unittest.main()
