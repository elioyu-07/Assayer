import unittest

from agent_f_host import (BrowserEvidenceAdapter,
                          BrowserProfile, BrowserReadOnlyPageAdapter,
                          BrowserSession, HostError)
from agent_f_host.browser_readonly import BrowserLocatorRegistry


class FakePage:
    url = "https://test.example.com/orders"

    def __init__(self):
        self.probe = {
            "visibleText": "订单列表",
            "route": "/orders",
            "stateKind": "page",
            "entrypoints": [],
            "candidates": [{
                "kind": "filter_region", "label": "订单筛选", "role": "search",
                "locator_material": "orders-filter", "host_locator_id": "locator-browser-0",
                "identity_material": "filter_region|search|订单筛选|orders", "accessible_name": "订单筛选",
                "visible_text": "查询", "x": 20, "y": 80, "width": 640, "height": 120,
                "viewport_width": 1280, "viewport_height": 800,
            }],
        }

    def title(self): return "Orders"
    def content(self): return "<form role='search'>查询</form>"

    def evaluate(self, expression, arg=None):
        if "identityMaterial" in expression:
            return {
                "status": "matched", "route": "/orders", "stateKind": "page",
                "identityMaterial": "filter_region|search|订单筛选|orders",
                "object": {"role": "search", "accessibleNamePresent": True, "accessibleNameLength": 4, "visibleTextLength": 2},
                "controls": [{"semanticAction": "other", "tag": "input", "role": "", "type": "text", "namePresent": True,
                               "accessibleNamePresent": True, "accessibleNameLength": 3, "valueClass": "non_empty", "checked": None,
                               "expanded": None}],
            }
        return self.probe


class FakeContext:
    def __init__(self, page): self.page = page; self.routes = []
    def route(self, pattern, handler): self.routes.append((pattern, handler))
    def new_page(self): return self.page


class FakeBackend:
    def __init__(self, context): self.context = context
    def launch(self, profile): return self.context
    def close(self, handle): return None


class BrowserEvidenceAdapterTest(unittest.TestCase):
    def test_structured_evidence_is_bound_and_contains_classes_not_values(self):
        page = FakePage()
        context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context))
        session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from agent_f_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "pageStateRef": "page-001",
                      "identity": {"hostLocatorId": "locator-browser-0",
                                   "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|订单筛选|orders")}}
            adapter = BrowserEvidenceAdapter(session, page_adapter, identity)
            captured = adapter.capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, False)
            self.assertEqual(captured.kind, "runtime_dom")
            self.assertEqual(captured.source_binding["status"], "verified")
            self.assertEqual(captured.payload["controls"][0]["valueClass"], "non_empty")
            self.assertNotIn("secret-value", str(captured.payload))
            self.assertNotIn("订单号", str(captured.payload))
        finally:
            session.close()

    def test_raw_visual_is_rejected_while_b07b_is_deferred(self):
        page = FakePage(); context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from agent_f_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "identity": {"hostLocatorId": "locator-browser-0", "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|订单筛选|orders")}}
            adapter = BrowserEvidenceAdapter(session, page_adapter, identity)
            with self.assertRaises(HostError) as caught:
                adapter.capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, True)
            self.assertEqual(caught.exception.code, "SCREENSHOT_ADAPTER_UNAVAILABLE")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
