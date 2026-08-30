"""Playwright request interception and safe action adapter (B05)."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterator
from urllib.parse import parse_qs, urlparse

from .action_safety import ActionExecution, ActionSafetyPolicy, NetworkRequest, RequestDecision
from .browser_readonly import (BrowserLocatorRegistry,
                               BrowserObjectIdentityAdapter,
                               BrowserReadOnlyPageAdapter,
                               ReadonlyBrowserPage)
from .browser_session import BrowserSession
from .errors import HostError


class BrowserNetworkGuard:
    """Install a send-time route guard before a managed Page is created."""

    def __init__(self, allowed_origin: str, *, policy: ActionSafetyPolicy | None = None):
        self.allowed_origin = BrowserReadOnlyPageAdapter._normalize_origin(allowed_origin)
        self.policy = policy or ActionSafetyPolicy()
        self._installed_contexts: set[int] = set()
        self._active_operation: str | None = None
        self._requests: list[NetworkRequest] = []
        self._decisions: list[RequestDecision] = []
        self._lock = RLock()

    def install(self, context: object) -> None:
        key = id(context)
        with self._lock:
            if key in self._installed_contexts:
                return
            route = getattr(context, "route", None)
            if not callable(route):
                raise RuntimeError("浏览器 Context 不支持发送前 route 拦截")
            route("**/*", self._handle_route)
            route_web_socket = getattr(context, "route_web_socket", None)
            if callable(route_web_socket):
                route_web_socket("**/*", self._handle_websocket)
            self._installed_contexts.add(key)

    @contextmanager
    def operation(self, operation_id: str, classifier: Callable[[NetworkRequest], RequestDecision]) -> Iterator[None]:
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("operation_id must be non-empty")
        with self._lock:
            if self._active_operation is not None:
                raise RuntimeError("browser network guard already has an active operation")
            self._active_operation = operation_id
            self._requests = []
            self._decisions = []
            self._classifier = classifier
        try:
            yield
        finally:
            with self._lock:
                self._active_operation = None
                self._classifier = None

    def drain_requests(self) -> tuple[NetworkRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    def drain_decisions(self) -> tuple[RequestDecision, ...]:
        with self._lock:
            return tuple(self._decisions)

    def summary(self) -> dict:
        with self._lock:
            requests = tuple(self._requests)
        return {
            "status": "active",
            "observedRequests": len(requests),
            "observedWrites": sum(1 for item in requests if item.method.upper() not in {"GET", "HEAD", "OPTIONS"}),
            "blockedRequests": sum(1 for item in requests if not self.policy.classify_request(item, self.allowed_origin).outcome == "allowed"),
        }

    def _handle_route(self, route: object) -> None:
        request = route.request
        network = self._network_request(request)
        with self._lock:
            classifier = getattr(self, "_classifier", None)
        decision = classifier(network) if classifier is not None else self.policy.classify_request(network, self.allowed_origin)
        with self._lock:
            self._requests.append(network)
            self._decisions.append(decision)
        if decision.outcome == "allowed":
            route.continue_()
        else:
            route.abort()

    def _handle_websocket(self, route: object) -> None:
        url = str(getattr(route, "url", ""))
        with self._lock:
            attributable = self._active_operation is not None
        network = NetworkRequest("GET", url, transport="websocket", attributable=attributable)
        with self._lock:
            classifier = getattr(self, "_classifier", None)
        decision = classifier(network) if classifier is not None else self.policy.classify_request(network, self.allowed_origin)
        with self._lock:
            self._requests.append(network)
            self._decisions.append(decision)
        close = getattr(route, "close", None)
        if callable(close):
            close()

    def _network_request(self, request: object) -> NetworkRequest:
        headers = getattr(request, "headers", {}) or {}
        normalized_headers = {str(key).lower(): value for key, value in headers.items()} if isinstance(headers, dict) else {}
        content_type = normalized_headers.get("content-type")
        post_data = getattr(request, "post_data", None)
        graphql_type = None
        if isinstance(post_data, str) and post_data:
            try:
                parsed = json.loads(post_data)
                graphql_type = parsed.get("operationType") or parsed.get("operation", {}).get("type")
                query = parsed.get("query")
                if graphql_type is None and isinstance(query, str):
                    graphql_type = self._graphql_operation_type(query)
            except (ValueError, AttributeError):
                graphql_type = self._graphql_operation_type(post_data)
        resource_type = str(getattr(request, "resource_type", "fetch"))
        transport = "http"
        if resource_type == "websocket":
            transport = "websocket"
        elif resource_type == "eventsource":
            transport = "sse"
        elif resource_type in {"ping", "beacon"}:
            transport = "beacon"
        url = str(getattr(request, "url", ""))
        parsed = urlparse(url)
        if graphql_type is None:
            query_values = parse_qs(parsed.query).get("query", ())
            if query_values:
                graphql_type = self._graphql_operation_type(query_values[0])
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
        # A request is attributable only while an explicit Host operation is
        # active, except for the static/document requests needed to bootstrap
        # the page before the first action.  Background fetch/xhr traffic
        # outside that scope is un-attributable and follows the fail-closed
        # unknown/abort path.
        with self._lock:
            active = self._active_operation is not None
        bootstrap_types = {"document", "stylesheet", "script", "image", "media", "font", "texttrack", "manifest"}
        attributable = active or resource_type.lower() in bootstrap_types
        return NetworkRequest(
            method=str(getattr(request, "method", "GET")), url=url, origin=origin,
            resource_type=resource_type, content_type=content_type,
            graphql_operation_type=str(graphql_type) if graphql_type else None,
            transport=transport, attributable=attributable,
        )

    @staticmethod
    def _graphql_operation_type(query: str) -> str | None:
        without_comments = re.sub(r"#[^\r\n]*", "", query).lstrip()
        match = re.match(r"^(mutation|query|subscription)\b", without_comments, re.IGNORECASE)
        return match.group(1).lower() if match else None


class BrowserSafeActionAdapter:
    """Execute only fixed locator actions and return all route observations."""

    LOCATOR_ID = re.compile(r"^locator-browser-(\d+)$")
    TARGET_SELECTOR = '[role="search"], form, [data-testid*="filter" i], [class*="filter" i]'
    SUPPORTED = frozenset({"focus", "scroll", "expand", "collapse", "switch_tab", "refresh"})

    def __init__(self, session: BrowserSession, page_adapter: BrowserReadOnlyPageAdapter,
                 guard: BrowserNetworkGuard, *, locator_registry: BrowserLocatorRegistry | None = None):
        self._session = session
        self._page_adapter = page_adapter
        self._guard = guard
        self._registry = locator_registry or page_adapter.locator_registry

    def execute(self, action: dict, target: dict, page_state: dict, intercept: Callable[[NetworkRequest], RequestDecision]) -> ActionExecution:
        operation_id = action.get("operationId") or action.get("operation_id")
        if not isinstance(operation_id, str):
            return ActionExecution(status="result_unknown", diagnostic="动作缺少 Operation 归因")
        if action.get("type") not in self.SUPPORTED:
            return ActionExecution(status="unavailable", diagnostic="该浏览器动作尚未接入安全执行器")

        def operation(context):
            page = self._page_adapter.page_for_context(context)
            index_match = self.LOCATOR_ID.fullmatch(target.get("identity", {}).get("hostLocatorId", ""))
            if not index_match:
                return ActionExecution(status="result_unknown", diagnostic="对象 locator 不是 Host 浏览器句柄")
            locator = page.locator(self.TARGET_SELECTOR).nth(int(index_match.group(1)))
            try:
                with self._guard.operation(operation_id, intercept):
                    self._execute_locator_action(page, locator, action["type"])
                    page.wait_for_timeout(self._session.profile.network_idle_window_ms)
            except Exception:
                requests = self._guard.drain_requests()
                decisions = self._guard.drain_decisions()
                if any(item.outcome == "blocked" for item in decisions):
                    return ActionExecution(status="request_blocked", requests=requests, diagnostic="请求在发送前被 Host 阻断", local_state_changed=True)
                return ActionExecution(status="result_unknown", requests=requests, diagnostic="浏览器动作执行失败")
            requests = self._guard.drain_requests()
            for decision in self._guard.drain_decisions():
                if decision.outcome in {"already_sent", "unknown"}:
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic=decision.reason)
                if decision.outcome == "blocked":
                    return ActionExecution(status="request_blocked", requests=requests, diagnostic=decision.reason, local_state_changed=True)
            try:
                self._page_adapter.refresh_locators(context, page_state["pageStateId"])
                rebound = self._registry.resolve(target, page_state)
                if len(rebound) != 1:
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic="动作后对象无法唯一重新绑定")
                if self._registry.digest(rebound[0].identity_material) != target.get("identity", {}).get("fingerprint"):
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic="动作后对象身份发生变化")
            except Exception:
                return ActionExecution(status="result_unknown", requests=requests, diagnostic="动作后对象重新绑定失败")
            return ActionExecution(status="succeeded", after_page_state_id=page_state["pageStateId"], requests=requests)

        return self._session.run_serial(operation)

    @staticmethod
    def _execute_locator_action(page: object, locator: object, action_type: str) -> None:
        if action_type == "focus":
            locator.focus()
        elif action_type == "scroll":
            locator.scroll_into_view_if_needed()
        elif action_type == "expand":
            locator.locator('[aria-expanded="false"]').first.click()
        elif action_type == "collapse":
            locator.locator('[aria-expanded="true"]').first.click()
        elif action_type == "switch_tab":
            locator.click()
        elif action_type == "refresh":
            page.reload(wait_until="domcontentloaded")
        else:
            raise HostError("ACTION_BLOCKED", "该浏览器动作尚未接入安全执行器")


@dataclass(frozen=True)
class SafeBrowserAdapterBundle:
    page: BrowserReadOnlyPageAdapter
    identity: BrowserObjectIdentityAdapter
    action: BrowserSafeActionAdapter
    network_guard: BrowserNetworkGuard


def create_safe_browser_adapter_bundle(session: BrowserSession, *, allowed_origin: str,
                                       page_factory: Callable[[object], ReadonlyBrowserPage] | None = None,
                                       policy: ActionSafetyPolicy | None = None) -> SafeBrowserAdapterBundle:
    registry = BrowserLocatorRegistry()
    guard = BrowserNetworkGuard(allowed_origin, policy=policy)
    page = BrowserReadOnlyPageAdapter(
        session, allowed_origin=allowed_origin, locator_registry=registry,
        page_factory=page_factory, context_initializer=guard.install,
        network_summary_provider=guard.summary,
    )
    identity = BrowserObjectIdentityAdapter(session, locator_registry=registry, refresh=page.refresh_locators)
    action = BrowserSafeActionAdapter(session, page, guard, locator_registry=registry)
    return SafeBrowserAdapterBundle(page, identity, action, guard)
