import unittest

from agent_f_host import (ActionSafetyPolicy, BrowserNetworkGuard,
                          BrowserProfile, BrowserReadOnlyPageAdapter,
                          BrowserSafeActionAdapter, BrowserSession,
                          NetworkRequest)
from agent_f_host.browser_readonly import BrowserLocatorRegistry


class FakeRequest:
    def __init__(self, method="GET", url="https://test.example.com/read", *, resource_type="fetch", headers=None, post_data=None):
        self.method = method; self.url = url; self.resource_type = resource_type
        self.headers = headers or {}; self.post_data = post_data


class FakeRoute:
    def __init__(self, request):
        self.request = request; self.continued = 0; self.aborted = 0

    def continue_(self): self.continued += 1
    def abort(self): self.aborted += 1


class FakeContext:
    def __init__(self): self.routes = []
    def route(self, pattern, handler): self.routes.append((pattern, handler))


class FakeWebSocketRoute:
    url = "wss://test.example.com/hmr"

    def close(self):
        raise AssertionError("synchronous WebSocket close must not be called from the route callback")

    def connect_to_server(self):
        raise AssertionError("blocked WebSocket must not connect to its server")


class WebSocketContext(FakeContext):
    def __init__(self):
        super().__init__()
        self.web_socket_routes = []

    def route_web_socket(self, pattern, handler):
        self.web_socket_routes.append((pattern, handler))


class FakeLocator:
    def __init__(self, page):
        self.page = page
        self.events = []

    def nth(self, index):
        return self

    @property
    def first(self):
        return self

    def locator(self, selector):
        return self

    def focus(self): self.events.append("focus")
    def scroll_into_view_if_needed(self): self.events.append("scroll")
    def click(self):
        self.events.append("click")
        if self.page.route_request is not None:
            self.page.route_request()


class ActionPage:
    url = "https://test.example.com/orders"

    def __init__(self, method="GET"):
        self.method = method
        self.route_request = None
        self.locator_instance = FakeLocator(self)
        self.probe = {
            "visibleText": "订单列表 查询",
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
    def evaluate(self, expression, arg=None): return self.probe
    def locator(self, selector): return self.locator_instance
    def wait_for_timeout(self, timeout): return None
    def reload(self, **kwargs): return None


class ActionContext(FakeContext):
    def __init__(self, page):
        super().__init__()
        self.page = page

    def new_page(self): return self.page


class FakeBackend:
    def launch(self, profile): return FakeContext()
    def close(self, handle): return None


class ActionBackend:
    def __init__(self, context): self.context = context
    def launch(self, profile): return self.context
    def close(self, handle): return None


class BrowserSafeActionAdapterTest(unittest.TestCase):
    def make(self, method):
        page = ActionPage(method)
        context = ActionContext(page)
        session = BrowserSession("scan-action", BrowserProfile(), backend=ActionBackend(context))
        session.open()
        guard = BrowserNetworkGuard("https://test.example.com")
        page_adapter = BrowserReadOnlyPageAdapter(session, allowed_origin="https://test.example.com",
                                                   context_initializer=guard.install)
        page_adapter.observe("page-001")
        page.route_request = lambda: context.routes[0][1](FakeRoute(FakeRequest(
            method, "https://test.example.com/action", resource_type="fetch")))
        adapter = BrowserSafeActionAdapter(session, page_adapter, guard)
        target = {"identity": {"hostLocatorId": "locator-browser-0",
                                "fingerprint": BrowserLocatorRegistry.digest("filter_region|search|订单筛选|orders")}}
        page_state = {"pageStateId": "page-001", "origin": "https://test.example.com"}
        return session, adapter, target, page_state

    def test_same_origin_read_action_rebinds_and_succeeds(self):
        session, adapter, target, page_state = self.make("GET")
        try:
            result = adapter.execute({"operationId": "operation-001", "type": "switch_tab"}, target, page_state,
                                     lambda req: ActionSafetyPolicy().classify_request(req, page_state["origin"]))
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.requests[0].method, "GET")
        finally:
            session.close()

    def test_write_action_is_aborted_before_send(self):
        session, adapter, target, page_state = self.make("POST")
        try:
            result = adapter.execute({"operationId": "operation-002", "type": "switch_tab"}, target, page_state,
                                     lambda req: ActionSafetyPolicy().classify_request(req, page_state["origin"]))
            self.assertEqual(result.status, "request_blocked")
            self.assertTrue(result.local_state_changed)
            self.assertEqual(result.requests[0].method, "POST")
        finally:
            session.close()


class BrowserNetworkGuardTest(unittest.TestCase):
    def make(self):
        session = BrowserSession("scan-action", BrowserProfile(), backend=FakeBackend())
        context = session.open()
        guard = BrowserNetworkGuard("https://test.example.com")
        guard.install(context)
        return session, context, guard

    @staticmethod
    def classifier(request):
        return ActionSafetyPolicy().classify_request(request, "https://test.example.com")

    def test_install_is_idempotent_and_route_exists_before_navigation(self):
        session, context, guard = self.make()
        guard.install(context)
        self.assertEqual(len(context.routes), 1)
        self.assertEqual(context.routes[0][0], "**/*")
        session.close()

    def test_same_origin_get_is_continued_and_recorded(self):
        session, context, guard = self.make()
        route = FakeRoute(FakeRequest())
        with guard.operation("operation-001", self.classifier):
            context.routes[0][1](route)
            self.assertEqual(route.continued, 1)
            self.assertEqual(route.aborted, 0)
            self.assertEqual(guard.drain_requests()[0].method, "GET")
            self.assertTrue(guard.drain_requests()[0].attributable)
        session.close()

    def test_websocket_route_blocks_without_sync_close_or_server_connect(self):
        context = WebSocketContext()
        guard = BrowserNetworkGuard("https://test.example.com")
        guard.install(context)
        self.assertEqual(len(context.web_socket_routes), 1)
        context.web_socket_routes[0][1](FakeWebSocketRoute())
        request = guard.drain_requests()[0]
        decision = guard.drain_decisions()[0]
        self.assertEqual(request.transport, "websocket")
        self.assertEqual(decision.outcome, "blocked")
        self.assertEqual(guard.summary()["blockedRequests"], 1)

    def test_post_cross_origin_and_websocket_are_aborted_before_send(self):
        session, context, guard = self.make()
        for request in (
            FakeRequest("POST", "https://test.example.com/write", headers={"content-type": "application/json"}),
            FakeRequest("GET", "https://evil.example/write"),
            FakeRequest("GET", "wss://test.example.com/socket", resource_type="websocket"),
        ):
            route = FakeRoute(request)
            with guard.operation("operation-002", self.classifier):
                context.routes[0][1](route)
            self.assertEqual(route.aborted, 1)
            self.assertEqual(route.continued, 0)
        self.assertEqual(guard.summary()["blockedRequests"], 1)
        session.close()

    def test_graphql_mutation_and_unattributable_service_worker_are_blocked_or_unknown(self):
        session, context, guard = self.make()
        for data in ('{"operationType":"mutation"}', '{"query":"mutation SaveOrder { saveOrder { id } }"}'):
            mutation = FakeRoute(FakeRequest("POST", "https://test.example.com/graphql", headers={"Content-Type": "application/json"}, post_data=data))
            with guard.operation("operation-003", self.classifier):
                context.routes[0][1](mutation)
            self.assertEqual(mutation.aborted, 1)
        service = NetworkRequest("GET", "https://test.example.com/data", transport="service_worker", attributable=False)
        decision = ActionSafetyPolicy().classify_request(service, "https://test.example.com")
        self.assertEqual(decision.outcome, "unknown")
        session.close()

    def test_beacon_resource_type_is_classified_and_blocked(self):
        session, context, guard = self.make()
        route = FakeRoute(FakeRequest("POST", "https://test.example.com/beacon", resource_type="ping"))
        with guard.operation("operation-004", self.classifier):
            context.routes[0][1](route)
        self.assertEqual(route.aborted, 1)
        self.assertEqual(guard.drain_requests()[0].transport, "beacon")
        session.close()

    def test_no_active_operation_marks_request_unattributable(self):
        session, context, guard = self.make()
        route = FakeRoute(FakeRequest())
        context.routes[0][1](route)
        self.assertEqual(route.aborted, 1)
        self.assertFalse(guard.drain_requests()[0].attributable)
        session.close()


if __name__ == "__main__":
    unittest.main()
