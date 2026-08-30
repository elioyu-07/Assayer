import io
import json
import unittest

from agent_f_host import (CredentialVault, DeterministicLoginAdapter,
                          DeterministicObjectIdentityAdapter,
                          DeterministicPageAdapter, HostCore, HostError,
                          JsonLineTransport, LoginSecret, McpToolTransport)


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

    def test_mcp_lists_protocol_tools_and_returns_same_structured_response(self):
        core = RecordingCore()
        adapter = McpToolTransport(core)
        names = {item["name"] for item in adapter.list_tools()}
        self.assertIn("start_audit", names)
        request = {"protocolVersion": "1.0", "requestId": "req-001", "tool": "inspect_page", "input": {}}
        result = adapter.call_tool("inspect_page", request)
        self.assertEqual(result["structuredContent"], VALID_RESPONSE)
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), VALID_RESPONSE)

    def test_mcp_cannot_change_tool_name_or_invent_partial_envelope(self):
        adapter = McpToolTransport(RecordingCore())
        with self.assertRaises(HostError) as mismatch:
            adapter.call_tool("inspect_page", {"tool": "start_audit"})
        self.assertEqual(mismatch.exception.code, "INVALID_REQUEST")
        with self.assertRaises(HostError) as unknown:
            adapter.call_tool("run arbitrary script", {})
        self.assertEqual(unknown.exception.code, "UNKNOWN_TOOL")

    def test_json_and_mcp_are_idempotently_equivalent_on_same_core(self):
        vault = CredentialVault(); vault.put("credential-001", LoginSecret("user", "password"))
        core = HostCore(credential_vault=vault, login_adapter=DeterministicLoginAdapter(),
                        page_adapter=DeterministicPageAdapter(),
                        object_identity_adapter=DeterministicObjectIdentityAdapter())
        try:
            request = {"protocolVersion": "1.0", "requestId": "req-start", "agentTurnId": "turn-001",
                       "tool": "start_audit", "idempotencyKey": "start-001",
                       "input": {"url": "https://test.example.com", "ruleRegistryVersion": "1.0.0",
                                 "outputDir": "/tmp/agent-f-transport-test", "browserProfile": "default",
                                 "credentialHandle": "credential-001"}}
            direct = JsonLineTransport(core).invoke(request)
            via_mcp = McpToolTransport(core).call_tool("start_audit", request)["structuredContent"]
            self.assertEqual(via_mcp, direct)
        finally:
            core.close()


if __name__ == "__main__":
    unittest.main()
