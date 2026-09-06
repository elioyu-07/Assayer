"""Read-only browser adapters for the B04 vertical slice.

The Playwright import is optional and lazy.  Tests can provide a small context
and page double, while production must explicitly install Playwright and pass a
real ``BrowserSession`` backend.  No selector or arbitrary script crosses this
module's Host/Agent boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol, Sequence
from urllib.parse import urlparse

from .browser_session import BrowserBackend, BrowserProfile, BrowserSession, BrowserSessionFailure
from .errors import HostError
from .locale_terms import inject_action_labels
from .object_identity import ObjectMatch, ObjectVerification
from .page import CandidateObservation, EntrypointObservation, PageObservation


class ReadonlyBrowserPage(Protocol):
    @property
    def url(self) -> str: ...
    def title(self) -> str: ...
    def content(self) -> str: ...
    def evaluate(self, expression: str, arg: object = None) -> object: ...


class ReadonlyBrowserContext(Protocol):
    def new_page(self) -> ReadonlyBrowserPage: ...


LOCAL_BROWSER_CHANNELS = ("chrome", "msedge")
_MIN_PLAYWRIGHT_VERSION = (1, 40, 0)


def _load_sync_playwright():
    """Import Playwright lazily and enforce a supported version floor.

    Playwright is a user-supplied environment prerequisite, never a declared
    dependency.  A missing or too-old installation fails here with an
    actionable message instead of surfacing later as a launch error.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is not installed; a real browser cannot be started. "
            "Install it locally with: pip install playwright"
        ) from error
    try:
        from importlib.metadata import version
    except ImportError:  # pragma: no cover - requires-python >= 3.11
        version = None
    if version is not None:
        try:
            installed = tuple(int(part) for part in version("playwright").split(".")[:3])
        except Exception:  # metadata can be unavailable in unusual environments
            installed = None
        if installed is not None and installed < _MIN_PLAYWRIGHT_VERSION:
            raise RuntimeError(
                "Playwright is too old for the browser backend; "
                f"install playwright >= {'.'.join(map(str, _MIN_PLAYWRIGHT_VERSION))}"
            )
    return sync_playwright


def launch_local_chromium(playwright, *, headless: bool = True, timeout_ms: int = 30_000) -> object:
    """Launch a locally installed Chromium-family browser (Chrome then Edge).

    Never downloads a bundled Chromium; the user must have Google Chrome or
    Microsoft Edge installed on the machine.
    """
    last_error = None
    for channel in LOCAL_BROWSER_CHANNELS:
        try:
            return playwright.chromium.launch(headless=headless, channel=channel, timeout=timeout_ms)
        except Exception as error:
            last_error = error
    raise RuntimeError(
        "No local Chromium-based browser found; install Google Chrome or Microsoft Edge"
    ) from last_error


class PlaywrightBrowserBackend:
    """Launch a locally installed Chromium-family browser from ``BrowserProfile``."""

    def __init__(self):
        self._playwright = None
        self._browser = None

    def launch(self, profile: BrowserProfile) -> object:
        profile.validate()
        self._playwright = _load_sync_playwright()().start()
        try:
            self._browser = launch_local_chromium(
                self._playwright, headless=profile.headless,
                timeout_ms=max(profile.operation_timeout_ms, 1_000),
            )
            context = self._browser.new_context(
                viewport={"width": profile.viewport_width, "height": profile.viewport_height},
                locale=profile.locale,
                timezone_id=profile.timezone_id,
                service_workers="block",
            )
            context.set_default_timeout(profile.operation_timeout_ms)
            context.set_default_navigation_timeout(profile.navigation_timeout_ms)
            return context
        except Exception:
            if self._browser is not None:
                self._browser.close()
            self._playwright.stop()
            self._browser = None
            self._playwright = None
            raise

    def close(self, handle: object) -> None:
        errors = []
        try:
            handle.close()
        except Exception as caught:
            errors.append(caught)
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception as caught:
            errors.append(caught)
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as caught:
            errors.append(caught)
        finally:
            self._browser = None
            self._playwright = None
        if errors:
            raise errors[0]


@dataclass(frozen=True)
class BrowserSnapshot:
    """Host-side shape returned by the fixed browser probe."""

    visible_text: str
    entrypoints: tuple[dict, ...] = ()
    candidates: tuple[dict, ...] = ()
    network_summary: dict | None = None
    route: str | None = None
    state_kind: str = "page"
    structure_summary: dict | None = None
    active_tab: str | None = None


class BrowserLocatorRegistry:
    """Session-memory lookup; raw locator material never enters persisted entities."""

    def __init__(self):
        self._by_digest: dict[tuple[str, str], tuple[ObjectMatch, ...]] = {}
        self._by_locator: dict[tuple[str, str], tuple[ObjectMatch, ...]] = {}
        self._by_fingerprint: dict[tuple[str, str], tuple[ObjectMatch, ...]] = {}
        self._lock = RLock()

    @staticmethod
    def digest(value: str) -> str:
        material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def replace(self, page_state_id: str, records: Sequence[tuple[str, ObjectMatch]]) -> None:
        by_digest: dict[str, list[ObjectMatch]] = {}
        by_locator: dict[str, list[ObjectMatch]] = {}
        by_fingerprint: dict[str, list[ObjectMatch]] = {}
        for material, match in records:
            by_digest.setdefault(self.digest(material), []).append(match)
            by_locator.setdefault(match.host_locator_id, []).append(match)
            by_fingerprint.setdefault(self.digest(match.identity_material), []).append(match)
        with self._lock:
            for mapping in (self._by_digest, self._by_locator, self._by_fingerprint):
                for key in [key for key in mapping if key[0] == page_state_id]:
                    mapping.pop(key)
            self._by_digest.update({(page_state_id, key): tuple(value) for key, value in by_digest.items()})
            self._by_locator.update({(page_state_id, key): tuple(value) for key, value in by_locator.items()})
            self._by_fingerprint.update({(page_state_id, key): tuple(value) for key, value in by_fingerprint.items()})

    def resolve(self, source: dict, page_state: dict) -> tuple[ObjectMatch, ...]:
        page_id = page_state.get("pageStateId") or source.get("pageStateRef")
        if not isinstance(page_id, str) or not page_id:
            raise HostError("INTERNAL_FAILURE", "Object matching is missing a PageState")
        with self._lock:
            if isinstance(source.get("locatorDigest"), str):
                return self._by_digest.get((page_id, source["locatorDigest"]), ())
            identity = source.get("identity") if isinstance(source.get("identity"), dict) else {}
            locator_id = identity.get("hostLocatorId")
            fingerprint = identity.get("fingerprint")
            by_locator = self._by_locator.get((page_id, locator_id), ()) if isinstance(locator_id, str) else ()
            if by_locator:
                return by_locator
            return self._by_fingerprint.get((page_id, fingerprint), ()) if isinstance(fingerprint, str) else ()


READONLY_PROBE_V1 = """() => {
  const clean = (value, limit = 240) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const hashRoute = location.hash.startsWith('#/') ? location.hash.slice(1).split('?')[0] : '';
  const safeRoute = hashRoute || location.pathname;
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const semanticAction = (element) => {
    const type = clean(element.getAttribute('type') || '', 32).toLowerCase();
    const tag = element.tagName.toLowerCase();
    const label = clean(element.getAttribute('aria-label') || element.getAttribute('title') ||
      element.innerText || element.labels?.[0]?.innerText || element.getAttribute('name') || '', 80).toLowerCase();
    const resetLabels = __ASSAYER_RESET_ACTION_LABELS__;
    const queryLabels = __ASSAYER_QUERY_ACTION_LABELS__;
    if (type === 'reset' || resetLabels.some((term) => label.includes(term))) return 'reset';
    if (['select', 'textarea'].includes(tag) || (tag === 'input' && !['button', 'submit', 'reset'].includes(type))) return 'filter_input';
    if (queryLabels.some((term) => label.includes(term))) return 'query';
    return 'other';
  };
  const controlKind = (element) => {
    const tag = element.tagName.toLowerCase();
    return ['input', 'select', 'textarea', 'button'].includes(tag) ? tag : 'other';
  };
  const regionSelector = '[role="search"], form, [data-testid*="filter" i], [class*="filter" i]';
  const allRegions = [...document.querySelectorAll(regionSelector)].filter(visible).slice(0, 512);
  const regions = allRegions.map((element, index) => ({element, index}))
    .filter(({element}) => !allRegions.some((other) => other !== element && other.contains(element))).slice(0, 128);
  const pageLists = [...document.querySelectorAll('table, [role="grid"], [role="list"], ul, ol')]
    .filter(visible).slice(0, 128);
  const candidates = regions.map(({element, index}) => {
    const box = element.getBoundingClientRect();
    const role = clean(element.getAttribute('role') || (element.tagName === 'FORM' ? 'form' : 'region'), 64);
    const label = clean(element.getAttribute('aria-label') || element.querySelector('legend')?.textContent || element.textContent, 200);
    const material = ['filter_region', role, label, safeRoute, index].join('|');
    const controls = [...element.querySelectorAll('input, select, textarea, button, [role="button"]')]
      .filter(visible).slice(0, 128).map((control, controlIndex) => ({
        controlRef: `control-browser-${index}-${controlIndex}`, kind: controlKind(control),
        semanticAction: semanticAction(control), visible: true,
        disabled: control.disabled === true || control.getAttribute('aria-disabled') === 'true',
        readOnly: control.readOnly === true || control.getAttribute('aria-readonly') === 'true',
        valueClass: ('value' in control) ? (String(control.value || '').length ? 'non_empty' : 'empty') : 'unknown'
      }));
    const owner = element.closest('section, main, article, [role="region"]') || element.parentElement;
    const ownedIds = new Set(String(element.getAttribute('aria-controls') || '').split(/\\s+/).filter(Boolean));
    const lists = pageLists.map((list, listIndex) => {
      let relationship = 'unknown';
      if (list.id && ownedIds.has(list.id)) relationship = 'aria_owned';
      else if (owner && owner.contains(list)) relationship = 'same_container';
      else if (element.parentElement && element.parentElement.contains(list)) relationship = 'nearby';
      return {list, listIndex, relationship};
    }).filter((item) => item.relationship !== 'unknown' || pageLists.length === 1).slice(0, 16)
      .map(({list, listIndex, relationship}) => ({
        listRef: `list-browser-${listIndex}`,
        kind: list.tagName === 'TABLE' ? 'table' : (list.getAttribute('role') === 'grid' ? 'grid' : 'list'),
        visible: true, relationship: relationship === 'unknown' ? 'nearby' : relationship
      }));
    return {
      kind: 'filter_region', label: label || `filter-region-${index}`, role,
      locator_material: material, host_locator_id: `locator-browser-${index}`,
      identity_material: material, accessible_name: label, visible_text: clean(element.innerText, 1000),
      x: Math.max(0, box.x), y: Math.max(0, box.y), width: box.width, height: box.height,
      viewport_width: innerWidth, viewport_height: innerHeight, controls, lists
    };
  });
  const filterEntrypoints = candidates.map((item) => ({
    kind: 'safe_action', label: item.label, intent: 'inspect_filter_region', status: 'unprocessed',
    logicalIdentityMaterial: item.locator_material, targetLocatorMaterial: item.locator_material
  }));
  const tabSelector = '[role="tab"], a[class*="tabs__item"], button[class*="tabs__item"], .tabs button, .tab';
  const tabs = [...document.querySelectorAll(tabSelector)].filter(visible).slice(0, 64);
  const tabLabels = tabs.map((element) => clean(element.getAttribute('aria-label') || element.innerText || element.textContent, 120));
  const tabEntrypoints = tabs.map((element) => ({
    kind: 'tab', label: clean(element.getAttribute('aria-label') || element.innerText || element.textContent, 120),
    intent: 'switch_tab',
    status: element.getAttribute('aria-selected') === 'true' || /(^|\\s)(active|is-active|selected)(\\s|$)/.test(String(element.className)) || element.getAttribute('data-active') === 'true' ? 'processed' : 'unprocessed',
    hostLocatorId: `tab-browser-${tabs.indexOf(element)}`,
    logicalIdentityMaterial: ['tab', tabLabels.join('|'), tabs.indexOf(element), tabLabels[tabs.indexOf(element)]].join('|')
  })).filter((item) => item.label);
  const active = tabs.find((element) => element.getAttribute('aria-selected') === 'true' ||
    /(^|\\s)(active|is-active|selected)(\\s|$)/.test(String(element.className)) || element.getAttribute('data-active') === 'true') || null;
  const activeTab = active ? clean(active.getAttribute('aria-label') || active.innerText || active.textContent, 120) : null;
  const visibleCount = (selector) => [...document.querySelectorAll(selector)].filter(visible).length;
  const errorTexts = [...document.querySelectorAll('[role="alert"], .el-message--error, [class*="error" i]')]
    .filter(visible).map((element) => clean(element.innerText || element.textContent, 160)).filter(Boolean).slice(0, 32);
  return {
    visibleText: clean(document.body?.innerText, 100000), route: safeRoute,
    stateKind: visibleCount('[role="dialog"], dialog[open]') ? 'dialog' : (activeTab ? 'tab' : 'page'),
    activeTab, entrypoints: [...tabEntrypoints, ...filterEntrypoints], candidates,
    structureSummary: {tabs: tabs.length, buttons: visibleCount('button'), fields: visibleCount('input, select, textarea'),
      tables: visibleCount('table, [role="grid"]'), dialogs: visibleCount('[role="dialog"], dialog[open]'),
      links: visibleCount('a'), errorCount: errorTexts.length, errorTexts},
    networkSummary: {status: 'unavailable_until_B05'}
  };
}"""
READONLY_PROBE_V1 = inject_action_labels(READONLY_PROBE_V1)


class BrowserReadOnlyPageAdapter:
    """Materialize ``PageObservation`` from one managed browser Page."""

    MAX_DOM_BYTES = 2_000_000
    MAX_TEXT_CHARS = 100_000
    PROBE = READONLY_PROBE_V1

    def __init__(self, session: BrowserSession, *, page_factory: Callable[[object], ReadonlyBrowserPage] | None = None,
                 allowed_origin: str | None = None, locator_registry: BrowserLocatorRegistry | None = None,
                 context_initializer: Callable[[object], None] | None = None,
                 network_summary_provider: Callable[[], dict] | None = None):
        self._session = session
        self._page_factory = page_factory or self._default_page_factory
        self._allowed_origin = self._normalize_origin(allowed_origin) if allowed_origin else None
        self.locator_registry = locator_registry or BrowserLocatorRegistry()
        self._context_initializer = context_initializer
        self._network_summary_provider = network_summary_provider
        self._page: ReadonlyBrowserPage | None = None

    @staticmethod
    def _default_page_factory(context: object) -> ReadonlyBrowserPage:
        return context.new_page()

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlparse(value)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("allowed origin must be an http(s) origin")
        try:
            return BrowserReadOnlyPageAdapter._origin(parsed)
        except HostError as error:
            raise ValueError("allowed origin must be an http(s) origin") from error

    @staticmethod
    def _origin(parsed) -> str:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise HostError("INVALID_REQUEST", "URL must be an http(s) address without user information")
        try:
            port = parsed.port
        except ValueError as error:
            raise HostError("INVALID_REQUEST", "URL port is invalid") from error
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        suffix = "" if port is None or default else f":{port}"
        return f"{parsed.scheme}://{host}{suffix}"

    def navigate(self, url: str, *, timeout_ms: int | None = None) -> str:
        parsed = urlparse(url)
        origin = self._origin(parsed)
        if self._allowed_origin and origin != self._allowed_origin:
                raise HostError("NAVIGATION_BLOCKED", "Navigation target is outside the origin allowed for this Scan")

        def operation(context):
            page = self._get_page(context)
            goto = getattr(page, "goto", None)
            if not callable(goto):
                raise RuntimeError("The browser Page does not support read-only navigation")
            goto(url, wait_until="domcontentloaded", timeout=timeout_ms or self._session.profile.navigation_timeout_ms)
            observed = urlparse(page.url)
            observed_origin = self._origin(observed)
            if observed_origin != origin:
                raise HostError("NAVIGATION_BLOCKED", "Browser navigation crossed origins")
            return page.url

        return self._session.run_serial(operation)

    def observe(self, page_state_id: str) -> PageObservation:
        if not isinstance(page_state_id, str) or not page_state_id:
            raise HostError("INVALID_REQUEST", "page_state_id cannot be empty")

        def operation(context):
            page = self._get_page(context)
            url = page.url
            parsed = urlparse(url)
            try:
                origin = self._origin(parsed)
            except HostError as error:
                raise HostError("INTERNAL_FAILURE", "The current browser page URL is invalid") from error
            if self._allowed_origin and origin != self._allowed_origin:
                raise HostError("NAVIGATION_BLOCKED", "The current page is outside the allowed origin")
            title = page.title()
            dom = page.content()
            if not isinstance(dom, str) or len(dom.encode("utf-8")) > self.MAX_DOM_BYTES:
                raise HostError("INTERNAL_FAILURE", "Page DOM exceeds the read-only collection limit")
            snapshot = self._probe(page)
            safe_url = f"{origin}{parsed.path or '/'}"
            return self._materialize(page_state_id, safe_url, origin, parsed.path or "/", title, dom, snapshot)

        return self._session.run_serial(operation)

    def _get_page(self, context: object) -> ReadonlyBrowserPage:
        if self._page is None:
            if self._context_initializer is not None:
                self._context_initializer(context)
            self._page = self._page_factory(context)
        return self._page

    def page_for_context(self, context: object) -> ReadonlyBrowserPage:
        """Return the managed Page; callers must already hold BrowserSession serialization."""
        return self._get_page(context)

    def _probe(self, page: ReadonlyBrowserPage) -> BrowserSnapshot:
        try:
            value = page.evaluate(self.PROBE)
        except Exception as error:
            raise BrowserSessionFailure("The read-only browser probe failed; the browser context is no longer valid") from error
        if not isinstance(value, dict):
            raise HostError("INTERNAL_FAILURE", "The read-only browser probe returned an invalid format")
        text = value.get("visibleText", "")
        if not isinstance(text, str):
            raise HostError("INTERNAL_FAILURE", "The read-only probe returned invalid visible text")
        entrypoints = value.get("entrypoints", [])
        candidates = value.get("candidates", [])
        if not isinstance(entrypoints, list) or not isinstance(candidates, list):
            raise HostError("INTERNAL_FAILURE", "The read-only probe returned an invalid candidate collection")
        snapshot = BrowserSnapshot(
            visible_text=text[: self.MAX_TEXT_CHARS],
            entrypoints=tuple(entrypoints), candidates=tuple(candidates),
            network_summary=value.get("networkSummary") if isinstance(value.get("networkSummary"), dict) else {},
            route=value.get("route") if isinstance(value.get("route"), str) else None,
            state_kind=value.get("stateKind") if isinstance(value.get("stateKind"), str) else "page",
            structure_summary=value.get("structureSummary") if isinstance(value.get("structureSummary"), dict) else {},
            active_tab=value.get("activeTab") if isinstance(value.get("activeTab"), str) else None,
        )
        if self._network_summary_provider is not None:
            snapshot = BrowserSnapshot(
                visible_text=snapshot.visible_text, entrypoints=snapshot.entrypoints,
                candidates=snapshot.candidates, network_summary=self._network_summary_provider(),
                route=snapshot.route, state_kind=snapshot.state_kind,
                structure_summary=snapshot.structure_summary, active_tab=snapshot.active_tab,
            )
        return snapshot

    def _materialize(self, page_state_id: str, url: str, origin: str, route: str, title: str, dom: str,
                     snapshot: BrowserSnapshot) -> PageObservation:
        entries = tuple(self._entrypoint(item) for item in snapshot.entrypoints)
        materialized = tuple(self._candidate(item) for item in snapshot.candidates)
        candidates = tuple(item[0] for item in materialized)
        self.locator_registry.replace(page_state_id, tuple(item[1] for item in materialized))
        snapshot_route = urlparse(snapshot.route).path if snapshot.route else route
        safe_route = snapshot_route if snapshot_route.startswith("/") else route
        identity_material = "|".join((origin, safe_route, snapshot.state_kind, snapshot.active_tab or "", str(title)))
        return PageObservation(
            url=url, origin=origin, route=safe_route, title=str(title), state_kind=snapshot.state_kind,
            dom_material=dom, identity_material=identity_material, visible_text=snapshot.visible_text,
            entrypoints=entries, candidates=candidates, network_summary=snapshot.network_summary,
            structure_summary=snapshot.structure_summary, active_tab=snapshot.active_tab,
        )

    def settle_readonly(self) -> None:
        """Keep the caller-owned network operation open for one bounded quiet window."""
        def operation(context):
            page = self._get_page(context)
            page.wait_for_timeout(self._session.profile.network_idle_window_ms)
        self._session.run_serial(operation)

    @staticmethod
    def _entrypoint(item: dict) -> EntrypointObservation:
        if not isinstance(item, dict):
            raise HostError("INTERNAL_FAILURE", "A read-only probe entrypoint is not an object")
        required = ("kind", "label", "intent")
        if any(not isinstance(item.get(key), str) or not item[key] for key in required):
            raise HostError("INTERNAL_FAILURE", "A read-only probe entrypoint has invalid fields")
        status = item.get("status", "unprocessed")
        if status not in {"unprocessed", "processed", "skipped"}:
            raise HostError("INTERNAL_FAILURE", "A read-only probe entrypoint has an invalid status")
        identity_material = item.get("logicalIdentityMaterial")
        target_material = item.get("targetLocatorMaterial")
        if identity_material is not None and (not isinstance(identity_material, str) or not identity_material or len(identity_material) > 2_000):
            raise HostError("INTERNAL_FAILURE", "A read-only probe entrypoint has an invalid logical identity")
        if target_material is not None and (not isinstance(target_material, str) or not target_material or len(target_material) > 2_000):
            raise HostError("INTERNAL_FAILURE", "A read-only probe entrypoint has an invalid target identity")
        return EntrypointObservation(item["kind"], item["label"], item["intent"], status=status,
                                     reason_code=item.get("reason_code", "NOT_YET_EXPLORED"),
                                     reason_message=item.get("reason_message", "Entrypoint has not been explored."),
                                     host_locator_id=item.get("hostLocatorId") if isinstance(item.get("hostLocatorId"), str) else None,
                                     logical_identity_material=identity_material,
                                     target_locator_material=target_material)

    @staticmethod
    def _candidate(item: dict) -> tuple[CandidateObservation, tuple[str, ObjectMatch]]:
        if not isinstance(item, dict):
            raise HostError("INTERNAL_FAILURE", "A read-only probe candidate is not an object")
        required = ("kind", "label", "role", "locator_material", "host_locator_id", "identity_material",
                    "accessible_name", "visible_text")
        if any(not isinstance(item.get(key), str) or not item[key] for key in required):
            raise HostError("INTERNAL_FAILURE", "A read-only probe candidate has invalid fields")
        numbers = ("x", "y", "width", "height", "viewport_width", "viewport_height")
        if any(type(item.get(key)) not in {int, float} for key in numbers):
            raise HostError("INTERNAL_FAILURE", "A read-only probe candidate has an invalid location")
        if item["x"] < 0 or item["y"] < 0 or item["width"] <= 0 or item["height"] <= 0:
            raise HostError("INTERNAL_FAILURE", "A read-only probe candidate has invalid bounds")
        if len(item["locator_material"]) > 2_000 or len(item["identity_material"]) > 2_000:
            raise HostError("INTERNAL_FAILURE", "Read-only probe identity material exceeds the limit")
        controls = item.get("controls", [])
        lists = item.get("lists", [])
        if not isinstance(controls, list) or not isinstance(lists, list):
                raise HostError("INTERNAL_FAILURE", "Read-only probe controls or lists have an invalid format")
        candidate = CandidateObservation(item["kind"], item["label"], item["role"], item["locator_material"])
        match = ObjectMatch(item["host_locator_id"], item["identity_material"], item["role"], item["accessible_name"],
                            item["visible_text"], item["x"], item["y"], item["width"], item["height"],
                            int(item["viewport_width"]), int(item["viewport_height"]),
                            tuple(dict(control) for control in controls), tuple(dict(candidate_list) for candidate_list in lists))
        return candidate, (item["locator_material"], match)

    def refresh_locators(self, context: object, page_state_id: str) -> None:
        """Re-run the fixed probe so object verification never trusts a stale registry."""
        page = self._get_page(context)
        snapshot = self._probe(page)
        materialized = tuple(self._candidate(item) for item in snapshot.candidates)
        self.locator_registry.replace(page_state_id, tuple(item[1] for item in materialized))


class BrowserObjectIdentityAdapter:
    """Host-owned matcher that returns a unique object, never a best guess."""

    def __init__(self, session: BrowserSession, matcher: Callable[[object, dict, dict], Sequence[ObjectMatch]] | None = None,
                 *, locator_registry: BrowserLocatorRegistry | None = None,
                 refresh: Callable[[object, str], None] | None = None):
        if matcher is None and locator_registry is None:
            raise ValueError("matcher or locator_registry is required")
        self._session = session
        self._matcher = matcher or (lambda context, source, page: locator_registry.resolve(source, page))
        self._refresh = refresh

    def verify_candidate(self, candidate: dict, page_state: dict) -> ObjectVerification:
        return self._verify(candidate, page_state)

    def rebind_object(self, audit_object: dict, page_state: dict) -> ObjectVerification:
        return self._verify(audit_object, page_state)

    def _verify(self, source: dict, page_state: dict) -> ObjectVerification:
        def operation(context):
            page_id = page_state.get("pageStateId") or source.get("pageStateRef")
            if self._refresh is not None:
                self._refresh(context, page_id)
            matches = self._matcher(context, source, page_state)
            if not isinstance(matches, Sequence) or isinstance(matches, (str, bytes)):
                raise HostError("INTERNAL_FAILURE", "Object matcher returned an invalid format")
            if any(not isinstance(item, ObjectMatch) for item in matches):
                raise HostError("INTERNAL_FAILURE", "Object matcher returned an invalid match")
            count = len(matches)
            if count == 0:
                return ObjectVerification("not_found", 0, excluded_reasons=("no_required_dimensions",))
            if count > 1:
                return ObjectVerification("ambiguous", count, matched_dimensions=("role",))
            match = matches[0]
            return ObjectVerification("matched", 1, matched_dimensions=("role", "accessible_name", "business_region"), match=match)
        return self._session.run_serial(operation)


def create_readonly_browser_adapters(session: BrowserSession, *, allowed_origin: str,
                                     page_factory: Callable[[object], ReadonlyBrowserPage] | None = None,
                                     context_initializer: Callable[[object], None] | None = None,
                                     network_summary_provider: Callable[[], dict] | None = None) -> tuple[BrowserReadOnlyPageAdapter, BrowserObjectIdentityAdapter]:
    """Create page and identity adapters sharing one Host-only locator registry."""
    registry = BrowserLocatorRegistry()
    page = BrowserReadOnlyPageAdapter(session, allowed_origin=allowed_origin, locator_registry=registry,
                                      page_factory=page_factory, context_initializer=context_initializer,
                                      network_summary_provider=network_summary_provider)
    identity = BrowserObjectIdentityAdapter(session, locator_registry=registry, refresh=page.refresh_locators)
    return page, identity
