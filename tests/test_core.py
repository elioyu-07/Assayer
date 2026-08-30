import unittest

from agent_f_host import HostCore, HostError


def bootstrap(core, key="boot-001"):
    return core.handle({"protocolVersion":"1.0","requestId":"req-001","agentTurnId":"turn-001","tool":"start_audit","idempotencyKey":key,"input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-001"}})


def session(scan, tool="inspect_page", key="op-001", revision=0, input=None):
    return {"protocolVersion":"1.0","requestId":"req-002","scanId":scan["scanId"],"runId":scan["runId"],"agentTurnId":"turn-002","tool":tool,"idempotencyKey":key,"expectedRunRevision":revision,"input":input or {"pageStateId":"page-001","include":["objects"]}}


class HostCoreTest(unittest.TestCase):
    def test_bootstrap_is_idempotent(self):
        core = HostCore()
        first = bootstrap(core)
        second = bootstrap(core)
        self.assertEqual(first["result"]["scanId"], second["result"]["scanId"])
        self.assertEqual(first["result"]["operationId"], second["result"]["operationId"])

    def test_bootstrap_conflict_is_rejected(self):
        core = HostCore()
        bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle({"protocolVersion":"1.0","requestId":"req-003","agentTurnId":"turn-003","tool":"start_audit","idempotencyKey":"boot-001","input":{"url":"https://other.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-002"}})
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_stale_revision_fails_before_adapter(self):
        core = HostCore()
        started = bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], revision=99))
        self.assertEqual(caught.exception.code, "STALE_STATE")

    def test_idempotent_retry_wins_over_later_revision(self):
        core = HostCore()
        started = bootstrap(core)
        request = session(started["result"])
        first = core.handle(request)
        core._scans[started["result"]["scanId"]]["runRevision"] = 1
        second = core.handle(request)
        self.assertEqual(first["error"]["code"], "INTERNAL_FAILURE")
        self.assertEqual(second["error"]["code"], "INTERNAL_FAILURE")

    def test_same_key_on_different_tool_conflicts(self):
        core = HostCore()
        started = bootstrap(core)
        core.handle(session(started["result"], key="same-key"))
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], tool="inspect_object", key="same-key", input={"objectId":"obj-001"}))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_unknown_operation_reference_is_rejected(self):
        core = HostCore()
        started = bootstrap(core)
        req = session(started["result"], tool="get_operation", input={"operationId":"operation-missing"})
        with self.assertRaises(HostError) as caught:
            core.handle(req)
        self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")


if __name__ == "__main__":
    unittest.main()
