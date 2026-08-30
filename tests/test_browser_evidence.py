import unittest
import struct

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
        if "boundingBox" in expression:
            return {"status": "matched", "route": "/orders",
                    "identityMaterial": "filter_region|search|订单筛选|orders",
                    "boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120},
                    "viewportWidth": 1280, "viewportHeight": 800}
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

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 120) + b"fixture"


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

    def test_raw_visual_is_captured_and_explicitly_not_sanitized(self):
        page = FakePage(); context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from agent_f_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001",
                      "location": {"boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120}},
                      "identity": {"hostLocatorId": "locator-browser-0", "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|订单筛选|orders")}}
            adapter = BrowserEvidenceAdapter(session, page_adapter, identity)
            captured = adapter.capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, True)
            self.assertEqual(captured.kind, "runtime_visual")
            self.assertEqual(captured.raw_visual.status, "captured")
            self.assertEqual(captured.raw_visual.sanitization_status, "not_performed")
            self.assertFalse(captured.raw_visual.sanitized)
            self.assertEqual(captured.raw_visual.source_bounding_box, target["location"]["boundingBox"])
            self.assertEqual(captured.raw_visual.bounding_box, {"x": 0, "y": 0, "width": 640, "height": 120})
        finally:
            session.close()

    def test_visual_target_disappearance_is_failed_closed(self):
        page = FakePage(); context = FakeContext(page); session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from agent_f_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry, refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "location": {"boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120}},
                      "identity": {"hostLocatorId": "locator-browser-0", "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|订单筛选|orders")}}
            original = page.evaluate
            page.evaluate = lambda expression, arg=None: {"status": "not_found"} if "boundingBox" in expression else original(expression, arg)
            captured = BrowserEvidenceAdapter(session, page_adapter, identity).capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, True)
            self.assertEqual(captured.raw_visual.status, "not_located")
            self.assertEqual(captured.raw_visual.sanitization_status, "failed")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
