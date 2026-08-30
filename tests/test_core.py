import tempfile
import unittest
from pathlib import Path

from agent_f_host import CredentialVault, DeterministicLoginAdapter, DeterministicPageAdapter, HostCore, HostError, SQLiteStore


class ExplodingLoginAdapter:
    def authenticate(self, url, secret):
        raise RuntimeError("adapter crash")


class CountingPageAdapter(DeterministicPageAdapter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def observe(self, page_state_id):
        self.calls += 1
        return super().observe(page_state_id)


class ExplodingPageAdapter:
    def observe(self, page_state_id):
        raise RuntimeError("page adapter crash")


def bootstrap(core, key="boot-001"):
    return core.handle({"protocolVersion":"1.0","requestId":"req-001","agentTurnId":"turn-001","tool":"start_audit","idempotencyKey":key,"input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-001"}})


def session(scan, tool="inspect_page", key="op-001", revision=1, input=None):
    return {"protocolVersion":"1.0","requestId":"req-002","scanId":scan["scanId"],"runId":scan["runId"],"agentTurnId":"turn-002","tool":tool,"idempotencyKey":key,"expectedRunRevision":revision,"input":input or {"pageStateId":"page-001","include":["objects"]}}


class HostCoreTest(unittest.TestCase):
    def setUp(self):
        self.cores = []

    def tearDown(self):
        for core in self.cores:
            try:
                core.close()
            except Exception:
                pass

    def make_core(self, *, succeed=True, store=None, page_adapter=None):
        vault = CredentialVault()
        vault.put("cred-001", "secret")
        core = HostCore(store=store, credential_vault=vault, login_adapter=DeterministicLoginAdapter(succeed=succeed), page_adapter=page_adapter)
        self.cores.append(core)
        return core

    def test_bootstrap_is_idempotent(self):
        core = self.make_core()
        first = bootstrap(core)
        second = bootstrap(core)
        self.assertEqual(first["result"]["scanId"], second["result"]["scanId"])
        self.assertEqual(first["result"]["operationId"], second["result"]["operationId"])

    def test_bootstrap_conflict_is_rejected(self):
        core = self.make_core()
        bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle({"protocolVersion":"1.0","requestId":"req-003","agentTurnId":"turn-003","tool":"start_audit","idempotencyKey":"boot-001","input":{"url":"https://other.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-002"}})
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_stale_revision_fails_before_adapter(self):
        core = self.make_core()
        started = bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], revision=99))
        self.assertEqual(caught.exception.code, "STALE_STATE")

    def test_idempotent_retry_wins_over_later_revision(self):
        core = self.make_core()
        started = bootstrap(core)
        request = session(started["result"], tool="inspect_object", input={"candidateId":"candidate-001"})
        first = core.handle(request)
        with core._store.transaction() as connection:
            connection.execute("UPDATE scans SET run_revision=2 WHERE scan_id=?", (started["result"]["scanId"],))
        second = core.handle(request)
        self.assertEqual(first["error"]["code"], "INTERNAL_FAILURE")
        self.assertEqual(second["error"]["code"], "INTERNAL_FAILURE")

    def test_same_key_on_different_tool_conflicts(self):
        core = self.make_core()
        started = bootstrap(core)
        core.handle(session(started["result"], key="same-key"))
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], tool="inspect_object", key="same-key", input={"objectId":"obj-001"}))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_unknown_operation_reference_is_rejected(self):
        core = self.make_core()
        started = bootstrap(core)
        req = session(started["result"], tool="get_operation", input={"operationId":"operation-missing"})
        with self.assertRaises(HostError) as caught:
            core.handle(req)
        self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")

    def test_credential_handle_is_one_shot(self):
        core = self.make_core()
        first = bootstrap(core)
        self.assertEqual(first["status"], "ok")
        second = bootstrap(core, key="boot-002")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["error"]["code"], "CREDENTIAL_CHANNEL_FAILED")

    def test_login_failure_invalidates_scan(self):
        core = self.make_core(succeed=False)
        result = bootstrap(core)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "LOGIN_FAILED")
        self.assertEqual(result["runRevision"], 1)

    def test_login_adapter_exception_is_fail_closed(self):
        vault = CredentialVault()
        vault.put("cred-001", "secret")
        core = HostCore(credential_vault=vault, login_adapter=ExplodingLoginAdapter())
        self.cores.append(core)
        result = bootstrap(core)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_registry_version_mismatch_fails_before_scan(self):
        core = self.make_core()
        request = {"protocolVersion":"1.0","requestId":"req-version","agentTurnId":"turn-version","tool":"start_audit","idempotencyKey":"boot-version","input":{"url":"https://test.example.com","ruleRegistryVersion":"9.9.9","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-001"}}
        with self.assertRaises(HostError) as caught:
            core.handle(request)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_default_login_adapter_fails_closed(self):
        vault = CredentialVault()
        vault.put("cred-001", "secret")
        core = HostCore(credential_vault=vault)
        self.cores.append(core)
        result = bootstrap(core)
        self.assertEqual(result["error"]["code"], "LOGIN_FAILED")

    def test_operation_cannot_be_read_from_another_scan(self):
        core = self.make_core()
        first = bootstrap(core)
        core.credential_vault.put("cred-002", "secret")
        second = core.handle({"protocolVersion":"1.0","requestId":"req-second","agentTurnId":"turn-second","tool":"start_audit","idempotencyKey":"boot-second","input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-002"}})
        req = session(second["result"], tool="get_operation", input={"operationId":first["result"]["operationId"]})
        with self.assertRaises(HostError) as caught:
            core.handle(req)
        self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")

    def test_sqlite_store_survives_core_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store)
            started = bootstrap(first)
            first_store.close()
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store)
            retry = bootstrap(second)
            self.assertEqual(retry["result"]["scanId"], started["result"]["scanId"])
            self.assertEqual(retry["result"]["operationId"], started["result"]["operationId"])
            second_store.close()

    def test_inspect_page_persists_read_only_facts_without_revision_change(self):
        adapter = CountingPageAdapter()
        core = self.make_core(page_adapter=adapter)
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":started["currentPageStateId"],"include":["route","visibleText","objects","safeEntrypoints","networkSummary"]}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["runRevision"], 1)
        self.assertEqual(len(result["result"]["candidateRefs"]), 1)
        self.assertEqual(len(result["result"]["entrypointRefs"]), 1)
        self.assertEqual(adapter.calls, 1)
        candidate = core._store.get_candidates(started["currentPageStateId"])[0]
        self.assertEqual(candidate["potentialRules"], [{"ruleId":"FUA-10","version":"1.0.0"}])

    def test_inspect_page_reuses_immutable_snapshot_for_new_read(self):
        adapter = CountingPageAdapter()
        core = self.make_core(page_adapter=adapter)
        started = bootstrap(core)["result"]
        first = session(started, key="inspect-1", input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
        second = session(started, key="inspect-2", input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
        core.handle(first)
        core.handle(second)
        self.assertEqual(adapter.calls, 1)

    def test_inspect_page_idempotency_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store)
            started = bootstrap(first)["result"]
            request = session(started, input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
            response = first.handle(request)
            first_store.close()
            self.cores.remove(first)
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store, page_adapter=ExplodingPageAdapter())
            retry = second.handle(request)
            self.assertEqual(retry["result"], response["result"])
            second_store.close()
            self.cores.remove(second)

    def test_inspect_page_adapter_failure_is_fail_closed(self):
        core = self.make_core(page_adapter=ExplodingPageAdapter())
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":started["currentPageStateId"],"include":["objects"]}))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_inspect_page_rejects_non_current_state(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":"page-foreign","include":["objects"]}))
        self.assertEqual(result["error"]["code"], "UNKNOWN_REFERENCE")


if __name__ == "__main__":
    unittest.main()
