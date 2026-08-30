import unittest

from agent_f_host import (BrowserProfile, BrowserSession, CredentialVault,
                          DeterministicLoginAdapter, HostCore, LoginSecret,
                          ObjectMatch)
from agent_f_host.browser_readonly import (BrowserLocatorRegistry,
                                           BrowserObjectIdentityAdapter,
                                           BrowserReadOnlyPageAdapter,
                                           create_readonly_browser_adapters)
from agent_f_host.errors import HostError


class FakePage:
    def __init__(self, url="https://test.example.com/orders", probe=None):
        self.url = url
        self.probe = probe or {
            "visibleText": "订单列表 查询 重置",
            "route": "/orders",
            "stateKind": "page",
            "entrypoints": [{"kind": "safe_action", "label": "订单筛选", "intent": "查看筛选区"}],
            "candidates": [{
                "kind": "filter_region", "label": "订单筛选", "role": "search",
                "locator_material": "orders-filter", "host_locator_id": "locator-browser-0",
                "identity_material": "filter_region|search|订单筛选|orders", "accessible_name": "订单筛选",
                "visible_text": "查询 重置", "x": 20, "y": 80, "width": 640, "height": 120,
                "viewport_width": 1280, "viewport_height": 800,
            }],
            "networkSummary": {"pendingReadRequests": 0, "observedWrites": 0},
        }
        self.goto_calls = []
        self.evaluated = []

    def title(self):
        return "订单列表"

    def content(self):
        return "<main>订单筛选</main>"

    def evaluate(self, expression):
        self.evaluated.append(expression)
        return self.probe

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.calls = 0

    def new_page(self):
        self.calls += 1
        return self.page


class FakeBackend:
    def __init__(self, context):
        self.context = context

    def launch(self, profile):
        return self.context

    def close(self, handle):
        return None


def match():
    return ObjectMatch("locator-001", "filter_region|search|订单筛选|orders", "search", "订单筛选", "查询 重置", 20, 80, 640, 120, 1280, 800)


class BrowserReadonlyAdapterTest(unittest.TestCase):
    def make(self, page=None, origin="https://test.example.com"):
        page = page or FakePage()
        context = FakeContext(page)
        session = BrowserSession("scan-001", BrowserProfile(), backend=FakeBackend(context))
        session.open()
        return session, context, page

    def test_observe_materializes_page_without_exposing_selector(self):
        session, context, page = self.make()
        adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
        observed = adapter.observe("page-001")
        self.assertEqual(observed.origin, "https://test.example.com")
        self.assertEqual(observed.route, "/orders")
        self.assertNotIn("?", observed.url)
        self.assertEqual(observed.candidates[0].kind, "filter_region")
        self.assertEqual(context.calls, 1)
        self.assertEqual(len(page.evaluated), 1)
        self.assertIn("querySelectorAll", page.evaluated[0])
        session.close()

    def test_factory_refreshes_registry_before_object_verification(self):
        session, _, page = self.make()
        page_adapter, identity = create_readonly_browser_adapters(
            session, allowed_origin="https://test.example.com",
        )
        observed = page_adapter.observe("page-001")
        source = {"locatorDigest": BrowserLocatorRegistry.digest(observed.candidates[0].locator_material)}
        page.probe["candidates"] = []
        outcome = identity.verify_candidate(source, {"pageStateId": "page-001"})
        self.assertEqual(outcome.status, "not_found")
        session.close()

    def test_host_core_reads_page_and_binds_object_through_browser_adapters(self):
        session, _, _ = self.make()
        page_adapter, identity_adapter = create_readonly_browser_adapters(
            session, allowed_origin="https://test.example.com",
        )
        vault = CredentialVault()
        vault.put("credential-browser", LoginSecret("browser-user", "browser-password"))
        core = HostCore(
            credential_vault=vault, login_adapter=DeterministicLoginAdapter(),
            page_adapter=page_adapter, object_identity_adapter=identity_adapter,
        )
        try:
            started = core.handle({
                "protocolVersion": "1.0", "requestId": "browser-start", "agentTurnId": "turn-001",
                "tool": "start_audit", "idempotencyKey": "browser-start",
                "input": {"url": "https://test.example.com/orders", "ruleRegistryVersion": "1.0.0",
                          "outputDir": "/tmp/agent-f-browser-readonly", "browserProfile": "default",
                          "credentialHandle": "credential-browser"},
            })["result"]
            inspected = core.handle({
                "protocolVersion": "1.0", "requestId": "browser-page", "scanId": started["scanId"],
                "runId": started["runId"], "agentTurnId": "turn-002", "tool": "inspect_page",
                "idempotencyKey": "browser-page", "expectedRunRevision": 1,
                "input": {"pageStateId": started["currentPageStateId"], "include": ["route", "objects"]},
            })["result"]
            verified = core.handle({
                "protocolVersion": "1.0", "requestId": "browser-object", "scanId": started["scanId"],
                "runId": started["runId"], "agentTurnId": "turn-003", "tool": "inspect_object",
                "idempotencyKey": "browser-object", "expectedRunRevision": 1,
                "input": {"candidateId": inspected["candidateRefs"][0]},
            })
            self.assertEqual(verified["status"], "ok")
            self.assertEqual(verified["result"]["rebindStatus"], "matched")
            stored_candidate = core._store.get_candidate(inspected["candidateRefs"][0])
            self.assertNotIn("locator_material", stored_candidate)
            self.assertNotIn("orders-filter", str(stored_candidate))
        finally:
            core.close(); session.close()

    def test_observe_reuses_one_page_and_rejects_cross_origin(self):
        session, context, page = self.make()
        adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
        adapter.observe("page-001"); adapter.observe("page-002")
        self.assertEqual(context.calls, 1)
        page.url = "https://evil.example/orders"
        with self.assertRaises(HostError) as caught:
            adapter.observe("page-003")
        self.assertEqual(caught.exception.code, "NAVIGATION_BLOCKED")
        session.close()

    def test_navigation_is_same_origin_only(self):
        session, _, page = self.make()
        adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
        self.assertEqual(adapter.navigate("https://test.example.com/orders?page=2"), "https://test.example.com/orders?page=2")
        with self.assertRaises(HostError) as caught:
            adapter.navigate("https://evil.example/orders")
        self.assertEqual(caught.exception.code, "NAVIGATION_BLOCKED")
        session.close()

    def test_probe_invalid_data_fails_closed(self):
        session, _, _ = self.make(FakePage(probe={"visibleText": "text", "entrypoints": "bad", "candidates": []}))
        adapter = BrowserReadOnlyPageAdapter(session)
        with self.assertRaises(HostError):
            adapter.observe("page-001")
        session.close()

    def test_unique_object_match_is_required(self):
        session, _, _ = self.make()
        adapter = BrowserObjectIdentityAdapter(session, lambda context, source, page: [match()])
        outcome = adapter.verify_candidate({"candidateId": "candidate-001"}, {"pageStateId": "page-001"})
        self.assertEqual(outcome.status, "matched")
        self.assertEqual(outcome.match, match())
        session.close()

    def test_page_and_identity_share_host_only_locator_registry(self):
        session, _, _ = self.make()
        registry = BrowserLocatorRegistry()
        page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com",
                                                  locator_registry=registry)
        identity = BrowserObjectIdentityAdapter(session, locator_registry=registry)
        observed = page_adapter.observe("page-001")
        source = {"locatorDigest": registry.digest(observed.candidates[0].locator_material)}
        outcome = identity.verify_candidate(source, {"pageStateId": "page-001"})
        self.assertEqual(outcome.status, "matched")
        self.assertEqual(outcome.match.host_locator_id, "locator-browser-0")
        session.close()

    def test_ambiguous_and_missing_objects_never_guess(self):
        session, _, _ = self.make()
        ambiguous = BrowserObjectIdentityAdapter(session, lambda context, source, page: [match(), match()])
        self.assertEqual(ambiguous.verify_candidate({}, {}).status, "ambiguous")
        missing = BrowserObjectIdentityAdapter(session, lambda context, source, page: [])
        self.assertEqual(missing.verify_candidate({}, {}).status, "not_found")
        session.close()

    def test_invalid_matcher_result_is_rejected(self):
        session, _, _ = self.make()
        adapter = BrowserObjectIdentityAdapter(session, lambda context, source, page: "not-a-list")
        with self.assertRaises(HostError):
            adapter.verify_candidate({}, {})
        session.close()


if __name__ == "__main__":
    unittest.main()
