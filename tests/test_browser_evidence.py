import unittest
import struct

from assayer_host import (BrowserEvidenceAdapter,
                          BrowserProfile, BrowserReadOnlyPageAdapter,
                          BrowserSession, HostError)
from assayer_host.browser_evidence import EVIDENCE_PROBE_V1, PAGE_OBSERVATION_PROBE_V1
from assayer_host.browser_readonly import BrowserLocatorRegistry


class FakePage:
    url = "https://test.example.com/orders"

    def __init__(self):
        self.probe = {
            "visibleText": "Order list",
            "route": "/orders",
            "stateKind": "page",
            "entrypoints": [],
            "candidates": [{
                "kind": "filter_region", "label": "Order filters", "role": "search",
                "locator_material": "orders-filter", "host_locator_id": "locator-browser-0",
                "identity_material": "filter_region|search|order-filters|orders", "accessible_name": "Order filters",
                "visible_text": "Search", "x": 20, "y": 80, "width": 640, "height": 120,
                "viewport_width": 1280, "viewport_height": 800,
            }],
        }

    def title(self): return "Orders"
    def content(self): return "<form role='search'>Search</form>"

    def evaluate(self, expression, arg=None):
        if "observationScope" in expression:
            return {
                "status": "matched", "route": "/orders",
                "identityMaterial": "filter_region|search|order-filters|orders",
                "observationScope": "viewport", "viewport": {"width": 1280, "height": 800},
                "object": {"label": "Order filters", "role": "search", "bounds": {"x": 20, "y": 80, "width": 640, "height": 120}},
                "controls": [{"controlRef": "control-browser-0-0", "label": "Search", "bounds": {"x": 30, "y": 90, "width": 80, "height": 30}}],
                "logicalLists": [{"listRef": "logical-list-browser-0", "kind": "table", "label": "Order list",
                                  "columns": ["Order number", "Status"], "bounds": {"x": 20, "y": 220, "width": 640, "height": 300},
                                  "rowCount": 2, "domNodeCount": 2, "relationship": "below_nearby", "verticalDistancePx": 20}],
                "relations": [{"fromObject": "current_object", "toListRef": "logical-list-browser-0", "type": "below_nearby"}],
            }
        if "boundingBox" in expression:
            return {"status": "matched", "route": "/orders",
                    "identityMaterial": "filter_region|search|order-filters|orders",
                    "boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120},
                    "viewportWidth": 1280, "viewportHeight": 800}
        if "identityMaterial" in expression:
            return {
                "status": "matched", "route": "/orders", "stateKind": "page",
                "identityMaterial": "filter_region|search|order-filters|orders",
                "object": {"role": "search", "accessibleNamePresent": True, "accessibleNameLength": 4, "visibleTextLength": 2},
                "controls": [{"semanticAction": "other", "tag": "input", "role": "", "type": "text", "namePresent": True,
                               "accessibleNamePresent": True, "accessibleNameLength": 3, "valueClass": "non_empty", "checked": None,
                               "expanded": None}],
            }
        return self.probe

    def screenshot(self, **kwargs):
        width, height = (640, 120) if kwargs.get("clip") else (1280, 800)
        return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"fixture"


class FakeContext:
    def __init__(self, page): self.page = page; self.routes = []
    def route(self, pattern, handler): self.routes.append((pattern, handler))
    def new_page(self): return self.page


class FakeBackend:
    def __init__(self, context): self.context = context
    def launch(self, profile): return self.context
    def close(self, handle): return None


class BrowserEvidenceAdapterTest(unittest.TestCase):
    def test_browser_probes_exclude_pagination_and_navigation_from_business_lists(self):
        for probe in (EVIDENCE_PROBE_V1, PAGE_OBSERVATION_PROBE_V1):
            self.assertIn("isAuxiliaryList", probe)
            self.assertIn(".el-pagination", probe)
            self.assertIn('[role="navigation"]', probe)
            self.assertIn('[role="tablist"]', probe)

    def test_structured_evidence_is_bound_and_contains_classes_not_values(self):
        page = FakePage()
        context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context))
        session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from assayer_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "pageStateRef": "page-001",
                      "identity": {"hostLocatorId": "locator-browser-0",
                                   "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|order-filters|orders")}}
            adapter = BrowserEvidenceAdapter(session, page_adapter, identity)
            captured = adapter.capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, False)
            self.assertEqual(captured.kind, "runtime_dom")
            self.assertEqual(captured.source_binding["status"], "verified")
            self.assertEqual(captured.payload["controls"][0]["valueClass"], "non_empty")
            self.assertNotIn("secret-value", str(captured.payload))
            self.assertNotIn("Order number", str(captured.payload))
        finally:
            session.close()

    def test_raw_visual_is_captured_and_explicitly_not_sanitized(self):
        page = FakePage(); context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from assayer_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001",
                      "location": {"boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120}},
                      "identity": {"hostLocatorId": "locator-browser-0", "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|order-filters|orders")}}
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

    def test_observe_page_returns_viewport_pixels_and_aligned_logical_lists(self):
        page = FakePage(); context = FakeContext(page)
        session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from assayer_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry,
                                                    refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "pageStateRef": "page-001",
                      "identity": {"hostLocatorId": "locator-browser-0",
                                   "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|order-filters|orders")}}
            captured = BrowserEvidenceAdapter(session, page_adapter, identity).observe_page(
                {"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None
            )
            self.assertEqual(captured.kind, "runtime_visual")
            self.assertEqual(captured.payload["observationScope"], "viewport")
            self.assertEqual(captured.payload["logicalLists"][0]["domNodeCount"], 2)
            self.assertEqual(captured.payload["relations"][0]["toListRef"], "logical-list-browser-0")
            self.assertEqual((captured.raw_visual.width, captured.raw_visual.height), (1280, 800))
            self.assertEqual(captured.raw_visual.bounding_box, captured.payload["object"]["bounds"])
        finally:
            session.close()

    def test_visual_target_disappearance_is_failed_closed(self):
        page = FakePage(); context = FakeContext(page); session = BrowserSession("scan-evidence", BrowserProfile(), backend=FakeBackend(context)); session.open()
        try:
            page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com")
            page_adapter.observe("page-001")
            from assayer_host import BrowserObjectIdentityAdapter
            identity = BrowserObjectIdentityAdapter(session, locator_registry=page_adapter.locator_registry, refresh=page_adapter.refresh_locators)
            target = {"objectId": "object-001", "location": {"boundingBox": {"x": 20, "y": 80, "width": 640, "height": 120}},
                      "identity": {"hostLocatorId": "locator-browser-0", "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|order-filters|orders")}}
            original = page.evaluate
            page.evaluate = lambda expression, arg=None: {"status": "not_found"} if "boundingBox" in expression else original(expression, arg)
            captured = BrowserEvidenceAdapter(session, page_adapter, identity).capture({"pageStateId": "page-001", "origin": "https://test.example.com"}, target, None, True)
            self.assertEqual(captured.raw_visual.status, "not_located")
            self.assertEqual(captured.raw_visual.sanitization_status, "failed")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
