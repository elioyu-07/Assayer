"""Playwright request interception and safe action adapter (B05)."""

from __future__ import annotations

import hashlib
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
from .browser_session import BrowserSession, BrowserSessionFailure
from .errors import HostError
from .page import EntrypointExecution


INTERACTION_PROBE_V1 = r"""async ({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const digest = async (value) => {
    if (!globalThis.crypto?.subtle) return 'unavailable';
    const bytes = new TextEncoder().encode(String(value || '').replace(/\s+/g, ' ').trim());
    const result = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return `sha256-${[...new Uint8Array(result)].map((item) => item.toString(16).padStart(2, '0')).join('')}`;
  };
  const allRegions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 512);
  const target = allRegions[targetIndex] || null;
  if (!target) return {status: 'not_found'};
  const controls = [...target.querySelectorAll('input, select, textarea, button, [role="button"]')].filter(visible).slice(0, 128);
  const lists = [...document.querySelectorAll('table, [role="grid"], [role="list"], ul, ol')].filter(visible).slice(0, 128);
  return {
    status: 'matched',
    controls: controls.map((item, index) => ({
      controlRef: `control-browser-${targetIndex}-${index}`,
      valueClass: ('value' in item) ? (String(item.value || '').length ? 'non_empty' : 'empty') : 'unknown',
      disabled: item.disabled === true || item.getAttribute('aria-disabled') === 'true',
      readOnly: item.readOnly === true || item.getAttribute('aria-readonly') === 'true',
      expanded: item.getAttribute('aria-expanded')
    })),
    lists: await Promise.all(lists.map(async (item, index) => ({
      listRef: `list-browser-${index}`, visible: true,
      itemCount: item.matches('table') ? item.querySelectorAll('tbody tr').length : item.querySelectorAll(':scope > *').length,
      contentDigest: await digest(item.innerText || item.textContent),
      busy: item.getAttribute('aria-busy') === 'true' || Boolean(item.querySelector('[aria-busy="true"], [class*="loading" i]'))
    }))),
    page: {
      loadingCount: [...document.querySelectorAll('[aria-busy="true"], [class*="loading" i]')].filter(visible).length,
      paginationCount: [...document.querySelectorAll('[role="navigation"], [class*="pagination" i]')].filter(visible).length
    }
  };
}"""


class BrowserNetworkGuard:
    """Install a send-time route guard before a managed Page is created."""

    def __init__(self, allowed_origin: str, *, policy: ActionSafetyPolicy | None = None):
        self.allowed_origin = BrowserReadOnlyPageAdapter._normalize_origin(allowed_origin)
        self.policy = policy or ActionSafetyPolicy()
        self._installed_contexts: set[int] = set()
        self._active_operation: str | None = None
        self._requests: list[NetworkRequest] = []
        self._decisions: list[RequestDecision] = []
        self._pending_requests: set[int] = set()
        self._sent_writes = 0
        self._tracking_complete = False
        self._lock = RLock()

    def install(self, context: object) -> None:
        key = id(context)
        with self._lock:
            if key in self._installed_contexts:
                return
            route = getattr(context, "route", None)
            if not callable(route):
                raise RuntimeError("Browser Context does not support pre-send route interception")
            route("**/*", self._handle_route)
            route_web_socket = getattr(context, "route_web_socket", None)
            if callable(route_web_socket):
                route_web_socket("**/*", self._handle_websocket)
            on = getattr(context, "on", None)
            if callable(on):
                on("requestfinished", self._request_completed)
                on("requestfailed", self._request_completed)
                self._tracking_complete = True
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
            decisions = tuple(self._decisions)
            pending = len(self._pending_requests)
            sent_writes = self._sent_writes
            tracking_complete = self._tracking_complete
        return {
            "status": "active",
            "observedRequests": len(requests),
            "observedWrites": sum(1 for item in requests if item.method.upper() not in {"GET", "HEAD", "OPTIONS"}),
            "blockedRequests": sum(1 for item in decisions if item.outcome == "blocked"),
            "unknownRequests": sum(1 for item in decisions if item.outcome in {"unknown", "already_sent"}),
            "pendingReadRequests": pending,
            "sentWrites": sent_writes,
            "trackingStatus": "proven" if tracking_complete else "unavailable",
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
            with self._lock:
                self._pending_requests.add(id(request))
                if network.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                    self._sent_writes += 1
            try:
                route.continue_()
            except Exception:
                self._request_completed(request)
                raise
        else:
            route.abort()

    def _request_completed(self, request: object) -> None:
        with self._lock:
            self._pending_requests.discard(id(request))

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
        # Deliberately do not call ``close()`` from this synchronous Playwright
        # callback: that waits on the same dispatcher and can deadlock page
        # navigation.  Returning without ``connect_to_server()`` makes
        # Playwright expose a local mock socket; page messages are discarded
        # and no server handshake or frame is sent.

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
    CONTROL_REF = re.compile(r"^control-browser-(\d+)-(\d+)$")
    SUPPORTED = frozenset({
        "focus", "scroll", "expand", "collapse", "switch_tab", "refresh",
        "input_synthetic_value", "select_synthetic_option", "activate_query",
        "activate_reset", "restore_value",
    })
    INTERACTION_ACTIONS = frozenset({
        "input_synthetic_value", "select_synthetic_option", "activate_query", "activate_reset", "restore_value",
    })

    def __init__(self, session: BrowserSession, page_adapter: BrowserReadOnlyPageAdapter,
                 guard: BrowserNetworkGuard, *, locator_registry: BrowserLocatorRegistry | None = None):
        self._session = session
        self._page_adapter = page_adapter
        self._guard = guard
        self._registry = locator_registry or page_adapter.locator_registry
        self._original_values: dict[tuple[str, str], tuple[str, str]] = {}

    def execute(self, action: dict, target: dict, page_state: dict, intercept: Callable[[NetworkRequest], RequestDecision]) -> ActionExecution:
        operation_id = action.get("operationId") or action.get("operation_id")
        if not isinstance(operation_id, str):
            return ActionExecution(status="result_unknown", diagnostic="Action is missing operation attribution")
        if action.get("type") not in self.SUPPORTED:
            return ActionExecution(status="unavailable", diagnostic="This browser action is not connected to the safe executor")

        def operation(context):
            page = self._page_adapter.page_for_context(context)
            index_match = self.LOCATOR_ID.fullmatch(target.get("identity", {}).get("hostLocatorId", ""))
            if not index_match:
                return ActionExecution(status="result_unknown", diagnostic="Object locator is not a Host browser handle")
            locator = page.locator(self.TARGET_SELECTOR).nth(int(index_match.group(1)))
            interaction_before = None
            control = None
            if action["type"] in self.INTERACTION_ACTIONS:
                try:
                    control = self._resolve_control(page, locator, target, action, int(index_match.group(1)))
                    interaction_before = self._interaction_snapshot(page, int(index_match.group(1)))
                except HostError as error:
                    return ActionExecution(status="unavailable", diagnostic=error.message)
            try:
                with self._guard.operation(operation_id, intercept):
                    if action["type"] in self.INTERACTION_ACTIONS:
                        self._execute_control_action(control, action)
                    else:
                        self._execute_locator_action(page, locator, action["type"])
                    page.wait_for_timeout(self._session.profile.network_idle_window_ms)
            except BrowserSessionFailure:
                raise
            except HostError as error:
                return ActionExecution(status="unavailable", diagnostic=error.message)
            except Exception:
                requests = self._guard.drain_requests()
                decisions = self._guard.drain_decisions()
                if any(item.outcome == "blocked" for item in decisions):
                    return ActionExecution(status="request_blocked", requests=requests, diagnostic="Request was blocked by Host before sending", local_state_changed=True)
                raise BrowserSessionFailure("Browser action failed; the browser context is no longer valid")
            requests = self._guard.drain_requests()
            interaction = None
            if interaction_before is not None:
                try:
                    interaction_after = self._interaction_snapshot(page, int(index_match.group(1)))
                    interaction = self._interaction_diff(action, interaction_before, interaction_after)
                except Exception:
                    return ActionExecution(status="result_unknown", requests=requests,
                                           diagnostic="Post-action interaction state cannot be observed safely", local_state_changed=True)
            for decision in self._guard.drain_decisions():
                if decision.outcome in {"already_sent", "unknown"}:
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic=decision.reason,
                                           interaction=interaction)
                if decision.outcome == "blocked":
                    return ActionExecution(status="request_blocked", requests=requests, diagnostic=decision.reason,
                                           local_state_changed=True, interaction=interaction)
            try:
                self._page_adapter.refresh_locators(context, page_state["pageStateId"])
                rebound = self._registry.resolve(target, page_state)
                if len(rebound) != 1:
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic="Post-action object cannot be rebound uniquely")
                if self._registry.digest(rebound[0].identity_material) != target.get("identity", {}).get("fingerprint"):
                    return ActionExecution(status="result_unknown", requests=requests, diagnostic="Post-action object identity changed")
            except Exception:
                return ActionExecution(status="result_unknown", requests=requests, diagnostic="Post-action object rebinding failed")
            return ActionExecution(status="succeeded", after_page_state_id=page_state["pageStateId"],
                                   requests=requests, interaction=interaction)

        return self._session.run_serial(operation)

    def _resolve_control(self, page: object, region: object, target: dict, action: dict, target_index: int) -> object:
        parameters = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
        control_ref = parameters.get("controlRef")
        match = self.CONTROL_REF.fullmatch(control_ref or "")
        if not match or int(match.group(1)) != target_index:
            raise HostError("UNKNOWN_REFERENCE", "Action control is not a Host reference for the current object")
        declared = next((item for item in target.get("controls", ()) if item.get("controlRef") == control_ref), None)
        if not declared or not declared.get("visible") or declared.get("disabled"):
            raise HostError("UNKNOWN_REFERENCE", "Action control is hidden, disabled, or outside the current object")
        if action["type"] in {"input_synthetic_value", "restore_value"} and declared.get("readOnly"):
            raise HostError("ACTION_BLOCKED", "Read-only controls cannot receive text input or value restoration")
        expected = {
            "input_synthetic_value": ("filter_input", {"input", "textarea"}),
            "select_synthetic_option": ("filter_input", {"select"}),
            "activate_query": ("query", {"input", "button", "other"}),
            "activate_reset": ("reset", {"input", "button", "other"}),
            "restore_value": ("filter_input", {"input", "textarea", "select"}),
        }[action["type"]]
        if declared.get("semanticAction") != expected[0] or declared.get("kind") not in expected[1]:
            raise HostError("ACTION_BLOCKED", "Action type does not match the control semantics recognized by Host")
        visible_controls = region.locator('input, select, textarea, button, [role="button"]')
        wanted = int(match.group(2))
        observed = []
        for index in range(min(visible_controls.count(), 128)):
            item = visible_controls.nth(index)
            if item.is_visible():
                observed.append(item)
        if wanted >= len(observed):
            raise HostError("UNKNOWN_REFERENCE", "Action control cannot be rebound from the Host reference")
        return observed[wanted]

    def _execute_control_action(self, control: object, action: dict) -> None:
        parameters = action.get("parameters", {})
        control_ref = parameters["controlRef"]
        case_id = action.get("caseId")
        key = (str(case_id or ""), control_ref)
        if action["type"] in {"input_synthetic_value", "select_synthetic_option"}:
            if parameters.get("valueClass") not in {"valid", "boundary_low", "boundary_high", "special_characters"}:
                raise HostError("ACTION_BLOCKED", "Synthetic input valueClass is not on the safety allowlist")
            if key not in self._original_values:
                tag = str(control.evaluate("element => element.tagName.toLowerCase()"))
                self._original_values[key] = (tag, str(control.input_value()))
            if action["type"] == "input_synthetic_value":
                control.fill(self._synthetic_value(control, action.get("operationId", ""), parameters["valueClass"]))
            else:
                current = str(control.input_value())
                options = control.locator("option")
                selected = None
                for index in range(min(options.count(), 128)):
                    option = options.nth(index)
                    value = str(option.get_attribute("value") or "")
                    if value and value != current and not option.is_disabled():
                        selected = index
                        break
                if selected is None:
                    raise HostError("ACTION_BLOCKED", "Select has no safe synthetic option to choose")
                control.select_option(index=selected)
        elif action["type"] in {"activate_query", "activate_reset"}:
            control.click()
        elif action["type"] == "restore_value":
            original = self._original_values.get(key)
            if original is None:
                raise HostError("UNKNOWN_REFERENCE", "Host no longer holds the original value for this control")
            if original[0] == "select":
                control.select_option(value=original[1])
            else:
                control.fill(original[1])
            self._original_values.pop(key, None)

    @staticmethod
    def _synthetic_value(control: object, operation_id: str, value_class: str) -> str:
        token = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:10]
        input_type = str(control.get_attribute("type") or "").lower()
        if value_class == "valid":
            if input_type in {"number", "range"}:
                return "1"
            if input_type in {"date"}:
                return "2000-01-01"
            if input_type in {"datetime-local"}:
                return "2000-01-01T00:00"
            if input_type == "month":
                return "2000-01"
            if input_type == "time":
                return "00:00"
            if input_type == "email":
                return f"assayer-{token}@example.invalid"
        values = {
            "valid": f"assayer-{token}", "boundary_low": "0",
            "boundary_high": "999999", "special_characters": f"assayer-{token}-_",
        }
        return values[value_class]

    @staticmethod
    def _interaction_snapshot(page: object, target_index: int) -> dict:
        value = page.evaluate(INTERACTION_PROBE_V1, {"targetIndex": target_index})
        if not isinstance(value, dict) or value.get("status") != "matched":
            raise HostError("UNKNOWN_REFERENCE", "Interaction observation target cannot be bound uniquely")
        return value

    @staticmethod
    def _interaction_diff(action: dict, before: dict, after: dict) -> dict:
        before_lists = {item["listRef"]: item for item in before.get("lists", ())}
        after_lists = {item["listRef"]: item for item in after.get("lists", ())}
        changed_lists = sorted(
            ref for ref in set(before_lists) | set(after_lists)
            if before_lists.get(ref) != after_lists.get(ref)
        )
        return {
            "actionType": action["type"],
            "controlRef": action.get("parameters", {}).get("controlRef"),
            "syntheticValueClass": action.get("parameters", {}).get("valueClass"),
            "before": before, "after": after,
            "diff": {"changedListRefs": changed_lists,
                     "controlStateChanged": before.get("controls") != after.get("controls"),
                     "loadingStateChanged": before.get("page", {}).get("loadingCount") != after.get("page", {}).get("loadingCount")},
        }

    @staticmethod
    def _execute_locator_action(page: object, locator: object, action_type: str) -> None:
        if action_type == "focus":
            focusable = locator.locator(
                'input, select, textarea, button, a[href], [tabindex]:not([tabindex="-1"])'
            )
            if focusable.count():
                focusable.first.focus()
            else:
                locator.scroll_into_view_if_needed()
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
            raise HostError("ACTION_BLOCKED", "This browser action is not connected to the safe executor")


class BrowserEntrypointAdapter:
    """Activate only Host-discovered tab entrypoints through fixed locators."""

    TAB_LOCATOR_ID = re.compile(r"^tab-browser-(\d+)$")
    TAB_SELECTOR = '[role="tab"], a[class*="tabs__item"], button[class*="tabs__item"], .tabs button, .tab'

    def __init__(self, session: BrowserSession, page_adapter: BrowserReadOnlyPageAdapter,
                 guard: BrowserNetworkGuard):
        self._session = session
        self._page = page_adapter
        self._guard = guard

    def explore(self, entrypoint: dict, page_state: dict, operation_id: str) -> EntrypointExecution:
        if entrypoint.get("kind") != "tab":
            return EntrypointExecution("unavailable", diagnostic="Only tab entrypoint exploration is currently supported")
        match = self.TAB_LOCATOR_ID.fullmatch(entrypoint.get("hostLocatorId", ""))
        if not match:
            return EntrypointExecution("result_unknown", diagnostic="Entrypoint locator is not a Host browser handle")

        def operation(context):
            page = self._page.page_for_context(context)
            tabs = page.locator(self.TAB_SELECTOR)
            count = min(tabs.count(), 64)
            expected = " ".join(str(entrypoint.get("label", "")).split())
            matches = []
            for index in range(count):
                tab = tabs.nth(index)
                label = tab.get_attribute("aria-label") or tab.inner_text()
                if " ".join(str(label or "").split())[:120] == expected:
                    matches.append(tab)
            if len(matches) != 1:
                return EntrypointExecution("result_unknown", diagnostic="Tab entrypoint cannot be rebound uniquely by label")
            with self._guard.operation(operation_id, lambda req: self._guard.policy.classify_request(req, page_state.get("origin", ""))):
                matches[0].click()
                page.wait_for_timeout(self._session.profile.network_idle_window_ms)
            requests = self._guard.drain_requests()
            decisions = self._guard.drain_decisions()
            for decision in decisions:
                if decision.outcome == "blocked":
                    return EntrypointExecution("request_blocked", requests=requests, diagnostic=decision.reason, page_changed=True)
                if decision.outcome in {"unknown", "already_sent"}:
                    return EntrypointExecution("result_unknown", requests=requests, diagnostic=decision.reason, page_changed=True)
            return EntrypointExecution("succeeded", requests=requests, page_changed=True)

        return self._session.run_serial(operation)


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
