"""Managed-browser structured Evidence and object-level screenshots (B07a/B07b)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .action_safety import ActionSafetyPolicy
from .browser_action import BrowserNetworkGuard
from .browser_readonly import BrowserLocatorRegistry, BrowserObjectIdentityAdapter, BrowserReadOnlyPageAdapter
from .browser_session import BrowserSession, BrowserSessionFailure
from .evidence import EvidenceCapture
from .evidence import RawVisualCapture
from .errors import HostError
from .locale_terms import inject_action_labels


EVIDENCE_PROBE_V1 = """({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const clean = (value, limit = 120) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const safeRoute = location.hash.startsWith('#/') ? location.hash.slice(1).split('?')[0] : location.pathname;
  const semanticAction = (value, element = null) => {
    const type = String(element?.getAttribute('type') || '').toLowerCase();
    if (type === 'reset') return 'reset';
    const label = clean(value, 80).toLowerCase();
    const resetLabels = __ASSAYER_RESET_ACTION_LABELS__;
    const queryLabels = __ASSAYER_QUERY_ACTION_LABELS__;
    if (resetLabels.some((term) => label.includes(term))) return 'reset';
    if (queryLabels.some((term) => label.includes(term))) return 'query';
    return 'other';
  };
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  if (!target) return {status: 'not_found', route: safeRoute};
  const role = clean(target.getAttribute('role') || (target.tagName === 'FORM' ? 'form' : 'region'), 64);
  const label = clean(target.getAttribute('aria-label') || target.querySelector('legend')?.textContent || target.textContent, 200);
  const controls = [...target.querySelectorAll('input, select, textarea, button, [aria-expanded], [role="tab"]')].slice(0, 128);
  const isAuxiliaryList = (element) => Boolean(element.closest(
    '.el-pagination, [class*="pagination" i], [class*="pager" i], [role="navigation"], nav, [role="tablist"], [role="menu"]'
  ));
  const listNodes = [...document.querySelectorAll('table, [role="grid"], [role="list"], ul, ol')]
    .filter((element) => visible(element) && !isAuxiliaryList(element));
  const targetList = target.closest('section, main, article, [role="region"]')?.querySelector('table, [role="grid"], [role="list"], ul, ol');
  const bindingSignals = {
    pageListCount: listNodes.length,
    sameContainerList: Boolean(targetList),
    formOwner: target.tagName === 'FORM' ? Boolean(target.querySelector('input, select, textarea')) : false
  };
  return {
    status: 'matched', route: safeRoute,
    stateKind: document.querySelector('[role="dialog"]') ? 'dialog' : 'page',
    identityMaterial: ['filter_region', role, clean(label), safeRoute, targetIndex].join('|'),
    object: {role, accessibleNamePresent: Boolean(label), accessibleNameLength: label.length, visibleTextLength: clean(target.innerText, 1000).length},
    controls: controls.map((item) => ({
      semanticAction: semanticAction(item.getAttribute('aria-label') || item.innerText || item.getAttribute('title') || item.getAttribute('name') || item.getAttribute('id') || '', item),
      tag: item.tagName.toLowerCase(), role: clean(item.getAttribute('role') || '', 32),
      type: clean(item.getAttribute('type') || '', 32), namePresent: Boolean(item.getAttribute('name') || item.getAttribute('aria-label')),
      accessibleNamePresent: Boolean(item.getAttribute('aria-label') || item.innerText || item.labels?.[0]?.innerText),
      accessibleNameLength: clean(item.getAttribute('aria-label') || item.innerText || item.labels?.[0]?.innerText || '', 80).length,
      valueClass: ('value' in item) ? (String(item.value || '').length ? 'non_empty' : 'empty') : 'not_applicable',
      checked: item.checked === true ? true : item.checked === false ? false : null,
      expanded: item.getAttribute('aria-expanded')
    })),
    bindingSignals
  };
}"""
EVIDENCE_PROBE_V1 = inject_action_labels(EVIDENCE_PROBE_V1)


SCREENSHOT_PROBE_V1 = """({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const clean = (value, limit = 120) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const safeRoute = location.hash.startsWith('#/') ? location.hash.slice(1).split('?')[0] : location.pathname;
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  if (!target) return {status: 'not_found', route: safeRoute};
  const role = clean(target.getAttribute('role') || (target.tagName === 'FORM' ? 'form' : 'region'), 64);
  const label = clean(target.getAttribute('aria-label') || target.querySelector('legend')?.textContent || target.textContent, 200);
  const box = target.getBoundingClientRect();
  return {
    status: 'matched', route: safeRoute,
    identityMaterial: ['filter_region', role, clean(label), safeRoute, targetIndex].join('|'),
    boundingBox: {x: box.x, y: box.y, width: box.width, height: box.height},
    viewportWidth: innerWidth, viewportHeight: innerHeight
  };
}"""


PAGE_OBSERVATION_PROBE_V1 = """({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const clean = (value, limit = 160) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const boxOf = (element) => {
    const box = element.getBoundingClientRect();
    return {x: Math.max(0, box.x), y: Math.max(0, box.y), width: box.width, height: box.height};
  };
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  if (!target) return {status: 'not_found'};
  const role = clean(target.getAttribute('role') || (target.tagName === 'FORM' ? 'form' : 'region'), 64);
  const label = clean(target.getAttribute('aria-label') || target.querySelector('legend')?.textContent || target.textContent, 200);
  const safeRoute = location.hash.startsWith('#/') ? location.hash.slice(1).split('?')[0] : location.pathname;
  const targetBox = boxOf(target);
  const isAuxiliaryList = (element) => Boolean(element.closest(
    '.el-pagination, [class*="pagination" i], [class*="pager" i], [role="navigation"], nav, [role="tablist"], [role="menu"]'
  ));
  const rawLists = [...document.querySelectorAll('table, [role="grid"], [role="list"], ul, ol')]
    .filter((element) => visible(element) && !isAuxiliaryList(element)).slice(0, 128);
  const logicalRoots = [];
  const rootFor = (node) => node.closest('.el-table, [data-testid*="table" i], [class~="data-table"], [role="grid"]') || node;
  for (const node of rawLists) {
    const root = rootFor(node);
    if (!logicalRoots.some((item) => item.root === root)) logicalRoots.push({root, nodes: []});
    logicalRoots.find((item) => item.root === root).nodes.push(node);
  }
  const owner = target.closest('section, main, article, [role="region"], [class*="card" i], [class*="panel" i]') || target.parentElement;
  const ownedIds = new Set(String(target.getAttribute('aria-controls') || '').split(/\\s+/).filter(Boolean));
  const logicalLists = logicalRoots.slice(0, 32).map(({root, nodes}, index) => {
    const box = boxOf(root);
    const headers = [...root.querySelectorAll('th, [role="columnheader"]')]
      .map((item) => clean(item.innerText || item.textContent, 80)).filter(Boolean);
    const uniqueHeaders = [...new Set(headers)].slice(0, 24);
    const heading = root.getAttribute('aria-label') || root.querySelector('caption')?.textContent ||
      root.closest('section, article, [role="region"], [class*="card" i], [class*="panel" i]')?.querySelector('h1,h2,h3,h4,[class*="title" i]')?.textContent;
    let relationship = 'unrelated';
    if ((root.id && ownedIds.has(root.id)) || nodes.some((node) => node.id && ownedIds.has(node.id))) relationship = 'aria_owned';
    else if (owner && owner.contains(root)) relationship = 'same_container';
    else if (box.y >= targetBox.y + targetBox.height && box.y - (targetBox.y + targetBox.height) <= 320 &&
             box.x < targetBox.x + targetBox.width && box.x + box.width > targetBox.x) relationship = 'below_nearby';
    return {
      listRef: `logical-list-browser-${index}`,
      kind: root.matches('table') ? 'table' : (root.getAttribute('role') === 'grid' ? 'grid' : 'list'),
      label: clean(heading, 120), columns: uniqueHeaders, bounds: box,
      rowCount: Math.max(...nodes.map((node) => node.matches('table') ? node.querySelectorAll('tbody tr').length : node.querySelectorAll(':scope > *').length), 0),
      domNodeCount: nodes.length, relationship,
      verticalDistancePx: Math.round(box.y - (targetBox.y + targetBox.height))
    };
  });
  const controls = [...target.querySelectorAll('input, select, textarea, button, [role="button"]')]
    .filter(visible).slice(0, 128).map((item, index) => ({
      controlRef: `control-browser-${targetIndex}-${index}`,
      label: clean(item.getAttribute('aria-label') || item.getAttribute('title') || item.innerText || item.labels?.[0]?.innerText || item.getAttribute('name'), 100),
      bounds: boxOf(item)
    }));
  return {
    status: 'matched', route: safeRoute,
    identityMaterial: ['filter_region', role, clean(label), safeRoute, targetIndex].join('|'),
    observationScope: 'viewport', viewport: {width: innerWidth, height: innerHeight},
    object: {label, role, bounds: targetBox}, controls, logicalLists,
    relations: logicalLists.filter((item) => item.relationship !== 'unrelated').map((item) => ({
      fromObject: 'current_object', toListRef: item.listRef, type: item.relationship
    }))
  };
}"""


class BrowserEvidenceAdapter:
    """Collect only structured, value-classified DOM facts for the target object."""

    LOCATOR_ID = re.compile(r"^locator-browser-(\d+)$")

    def __init__(self, session: BrowserSession, page_adapter: BrowserReadOnlyPageAdapter,
                 identity_adapter: BrowserObjectIdentityAdapter,
                 *, network_guard: BrowserNetworkGuard | None = None,
                 policy: ActionSafetyPolicy | None = None):
        self._session = session
        self._page = page_adapter
        self._identity = identity_adapter
        self._guard = network_guard
        self._policy = policy or (network_guard.policy if network_guard else ActionSafetyPolicy())

    def capture(self, page_state: dict, target: dict, case: dict | None, include_raw_visual: bool) -> EvidenceCapture:
        locator_id = target.get("identity", {}).get("hostLocatorId", "")
        match = self.LOCATOR_ID.fullmatch(locator_id)
        if not match:
            raise HostError("UNKNOWN_REFERENCE", "Evidence target is not a Host browser handle")
        verification = self._identity.rebind_object(target, page_state)
        if verification.status != "matched" or not verification.match:
            raise HostError("UNKNOWN_REFERENCE", "Evidence target cannot be rebound uniquely")

        def collect(context):
            page = self._page.page_for_context(context)
            value = page.evaluate(EVIDENCE_PROBE_V1, {"targetIndex": int(match.group(1))})
            if not isinstance(value, dict) or value.get("status") != "matched":
                raise HostError("UNKNOWN_REFERENCE", "Evidence target does not exist on the page")
            current_origin = self._page._origin(urlparse(str(page.url)))
            if current_origin != page_state.get("origin"):
                raise HostError("NAVIGATION_BLOCKED", "Evidence page is outside the allowed origin")
            expected_fingerprint = target.get("identity", {}).get("fingerprint")
            observed_fingerprint = BrowserLocatorRegistry.digest(value.get("identityMaterial", ""))
            if observed_fingerprint != expected_fingerprint:
                raise HostError("UNKNOWN_REFERENCE", "Evidence object identity fingerprint changed")
            network = self._guard.summary() if self._guard else {"status": "unavailable"}
            payload = {
                "objectId": target["objectId"], "pageStateId": page_state["pageStateId"],
                "route": value.get("route"), "stateKind": value.get("stateKind", "page"),
                "object": value.get("object", {}), "controls": value.get("controls", []),
                "bindingSignals": value.get("bindingSignals", {}),
                "networkSummary": network,
            }
            if include_raw_visual:
                visual = self._capture_visual(page, int(match.group(1)), target)
                return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata",
                                       payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"],
                                                "stateKind": value.get("stateKind", "page"),
                                                "identityFingerprint": expected_fingerprint,
                                                "imageType": visual.image_type, "width": visual.width, "height": visual.height,
                                                "boundingBox": visual.bounding_box,
                                                "sanitizationStatus": visual.sanitization_status},
                                       raw_visual=visual, source_binding={"status": "verified", "bindingReason": "The Host screenshot probe matches the object fingerprint"},
                                       collector_version="1.0.0", sanitization_policy_version="1.0.0",
                                       normalization_algorithm_version="1.0.0")
            return EvidenceCapture(kind="runtime_dom", payload_type="json", payload=payload,
                                   source_binding={"status": "verified", "bindingReason": "The fixed Host Evidence probe matches the object fingerprint"},
                                   collector_version="1.0.0", sanitization_policy_version="1.0.0",
                                   normalization_algorithm_version="1.0.0")
        return self._session.run_serial(collect)

    def observe_page(self, page_state: dict, target: dict, case: dict | None) -> EvidenceCapture:
        locator_id = target.get("identity", {}).get("hostLocatorId", "")
        match = self.LOCATOR_ID.fullmatch(locator_id)
        if not match:
            raise HostError("UNKNOWN_REFERENCE", "Page observation target is not a Host browser handle")
        verification = self._identity.rebind_object(target, page_state)
        if verification.status != "matched" or not verification.match:
            raise HostError("UNKNOWN_REFERENCE", "Page observation target cannot be rebound uniquely")

        def collect(context):
            page = self._page.page_for_context(context)
            value = page.evaluate(PAGE_OBSERVATION_PROBE_V1, {"targetIndex": int(match.group(1))})
            if not isinstance(value, dict) or value.get("status") != "matched":
                raise HostError("UNKNOWN_REFERENCE", "Page observation target does not exist on the page")
            expected_fingerprint = target.get("identity", {}).get("fingerprint")
            if BrowserLocatorRegistry.digest(value.get("identityMaterial", "")) != expected_fingerprint:
                raise HostError("UNKNOWN_REFERENCE", "Page observation object identity fingerprint changed")
            screenshot = getattr(page, "screenshot", None)
            if not callable(screenshot):
                raise HostError("INTERNAL_FAILURE", "The browser Page does not support page-observation screenshots")
            try:
                image = screenshot(type="png", animations="disabled", timeout=self._session.profile.operation_timeout_ms)
            except TypeError:
                image = screenshot(type="png", timeout=self._session.profile.operation_timeout_ms)
            except Exception as error:
                raise BrowserSessionFailure("Page-observation screenshot failed; the browser context is no longer valid") from error
            image = bytes(image)
            if len(image) < 24 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise HostError("INTERNAL_FAILURE", "Page-observation screenshot is not a valid PNG")
            width, height = int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")
            target_box = value.get("object", {}).get("bounds")
            raw = RawVisualCapture(image_bytes=image, image_type="png", width=width, height=height,
                                   bounding_box=target_box, source_bounding_box=target_box,
                                   annotation="Current Host browser viewport; problemBoundingBox marks the verified object",
                                   sanitized=False, sanitization_status="not_performed")
            payload = {key: value.get(key) for key in (
                "route", "observationScope", "viewport", "object", "controls", "logicalLists", "relations"
            )}
            payload.update({"objectId": target["objectId"], "pageStateId": page_state["pageStateId"]})
            return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload=payload,
                                   raw_visual=raw,
                                   source_binding={"status": "verified", "bindingReason": "The current Host viewport, structure probe, and object fingerprint match"},
                                   collector_version="1.1.0", sanitization_policy_version="1.0.0",
                                   normalization_algorithm_version="1.0.0")
        return self._session.run_serial(collect)

    def _capture_visual(self, page: object, target_index: int, target: dict) -> RawVisualCapture:
        try:
            value = page.evaluate(SCREENSHOT_PROBE_V1, {"targetIndex": target_index})
        except Exception as error:
            raise HostError("INTERNAL_FAILURE", "Screenshot probe failed") from error
        if not isinstance(value, dict) or value.get("status") != "matched":
            return RawVisualCapture(status="not_located", reason="Screenshot target does not exist on the page", sanitized=False, sanitization_status="failed")
        box = value.get("boundingBox")
        target_box = target.get("location", {}).get("boundingBox")
        if not isinstance(box, dict) or not isinstance(target_box, dict):
            return RawVisualCapture(status="rejected", reason="Screenshot is missing object bounds", sanitized=False, sanitization_status="failed")
        expected = BrowserLocatorRegistry.digest(value.get("identityMaterial", ""))
        if expected != target.get("identity", {}).get("fingerprint"):
            return RawVisualCapture(status="rejected", reason="Screenshot object identity fingerprint changed", sanitized=False, sanitization_status="failed")
        if any(box.get(key) != target_box.get(key) for key in ("x", "y", "width", "height")):
            return RawVisualCapture(status="rejected", reason="Screenshot object bounds changed", sanitized=False, sanitization_status="failed")
        if any(not isinstance(box.get(key), (int, float)) or box[key] <= 0 for key in ("width", "height")):
            return RawVisualCapture(status="rejected", reason="Screenshot object bounds are invalid", sanitized=False, sanitization_status="failed")
        screenshot = getattr(page, "screenshot", None)
        if not callable(screenshot):
            return RawVisualCapture(status="rejected", reason="The browser Page does not support screenshots", sanitized=False, sanitization_status="failed")
        try:
            image = screenshot(type="png", clip=box, animations="disabled", timeout=self._session.profile.operation_timeout_ms)
        except TypeError:
            image = screenshot(type="png", clip=box, timeout=self._session.profile.operation_timeout_ms)
        except Exception as error:
            raise BrowserSessionFailure("Browser screenshot failed; the browser context is no longer valid") from error
        if not isinstance(image, (bytes, bytearray)) or not bytes(image).startswith(b"\x89PNG\r\n\x1a\n"):
            return RawVisualCapture(status="rejected", reason="The browser returned a non-PNG screenshot", sanitized=False, sanitization_status="failed")
        image = bytes(image)
        if len(image) < 24:
            return RawVisualCapture(status="rejected", reason="PNG screenshot header is incomplete", sanitized=False, sanitization_status="failed")
        width = int.from_bytes(image[16:20], "big")
        height = int.from_bytes(image[20:24], "big")
        if width <= 0 or height <= 0:
            return RawVisualCapture(status="rejected", reason="PNG screenshot dimensions are invalid", sanitized=False, sanitization_status="failed")
        return RawVisualCapture(image_bytes=image, image_type="png", width=width, height=height,
                                bounding_box={"x": 0, "y": 0, "width": width, "height": height},
                                source_bounding_box=box,
                                annotation="Host-located object region (automatic pixel sanitization was not performed)",
                                sanitized=False, sanitization_status="not_performed")


def create_browser_evidence_adapter(session: BrowserSession, *, page_adapter: BrowserReadOnlyPageAdapter,
                                    identity_adapter: BrowserObjectIdentityAdapter,
                                    network_guard: BrowserNetworkGuard | None = None,
                                    policy: ActionSafetyPolicy | None = None) -> BrowserEvidenceAdapter:
    """Create the B07a structured Evidence adapter for an existing bundle."""
    return BrowserEvidenceAdapter(session, page_adapter, identity_adapter,
                                  network_guard=network_guard, policy=policy)
