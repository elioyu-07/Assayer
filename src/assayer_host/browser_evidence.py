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
    if (/(reset|clear|重置|清空|恢复默认)/.test(label)) return 'reset';
    if (/(query|search|filter|查询|搜索|筛选)/.test(label)) return 'query';
    return 'other';
  };
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  if (!target) return {status: 'not_found', route: safeRoute};
  const role = clean(target.getAttribute('role') || (target.tagName === 'FORM' ? 'form' : 'region'), 64);
  const label = clean(target.getAttribute('aria-label') || target.querySelector('legend')?.textContent || target.textContent, 200);
  const controls = [...target.querySelectorAll('input, select, textarea, button, [aria-expanded], [role="tab"]')].slice(0, 128);
  const listNodes = [...document.querySelectorAll('table, [role="grid"], [role="list"], ul, ol')].filter(visible);
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
            raise HostError("UNKNOWN_REFERENCE", "Evidence 目标不是 Host 浏览器句柄")
        verification = self._identity.rebind_object(target, page_state)
        if verification.status != "matched" or not verification.match:
            raise HostError("UNKNOWN_REFERENCE", "Evidence 目标无法唯一重新绑定")

        def collect(context):
            page = self._page.page_for_context(context)
            value = page.evaluate(EVIDENCE_PROBE_V1, {"targetIndex": int(match.group(1))})
            if not isinstance(value, dict) or value.get("status") != "matched":
                raise HostError("UNKNOWN_REFERENCE", "Evidence 目标在页面中不存在")
            current_origin = self._page._origin(urlparse(str(page.url)))
            if current_origin != page_state.get("origin"):
                raise HostError("NAVIGATION_BLOCKED", "Evidence 页面不在当前允许 origin")
            expected_fingerprint = target.get("identity", {}).get("fingerprint")
            observed_fingerprint = BrowserLocatorRegistry.digest(value.get("identityMaterial", ""))
            if observed_fingerprint != expected_fingerprint:
                raise HostError("UNKNOWN_REFERENCE", "Evidence 对象身份 fingerprint 已变化")
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
                                       raw_visual=visual, source_binding={"status": "verified", "bindingReason": "Host 固定截图探针与对象 fingerprint 一致"},
                                       collector_version="1.0.0", sanitization_policy_version="1.0.0",
                                       normalization_algorithm_version="1.0.0")
            return EvidenceCapture(kind="runtime_dom", payload_type="json", payload=payload,
                                   source_binding={"status": "verified", "bindingReason": "Host 固定 Evidence 探针与对象 fingerprint 一致"},
                                   collector_version="1.0.0", sanitization_policy_version="1.0.0",
                                   normalization_algorithm_version="1.0.0")
        return self._session.run_serial(collect)

    def _capture_visual(self, page: object, target_index: int, target: dict) -> RawVisualCapture:
        try:
            value = page.evaluate(SCREENSHOT_PROBE_V1, {"targetIndex": target_index})
        except Exception as error:
            raise HostError("INTERNAL_FAILURE", "截图探针执行失败") from error
        if not isinstance(value, dict) or value.get("status") != "matched":
            return RawVisualCapture(status="not_located", reason="截图目标在页面中不存在", sanitized=False, sanitization_status="failed")
        box = value.get("boundingBox")
        target_box = target.get("location", {}).get("boundingBox")
        if not isinstance(box, dict) or not isinstance(target_box, dict):
            return RawVisualCapture(status="rejected", reason="截图缺少对象边界", sanitized=False, sanitization_status="failed")
        expected = BrowserLocatorRegistry.digest(value.get("identityMaterial", ""))
        if expected != target.get("identity", {}).get("fingerprint"):
            return RawVisualCapture(status="rejected", reason="截图对象身份 fingerprint 已变化", sanitized=False, sanitization_status="failed")
        if any(box.get(key) != target_box.get(key) for key in ("x", "y", "width", "height")):
            return RawVisualCapture(status="rejected", reason="截图对象边界已变化", sanitized=False, sanitization_status="failed")
        if any(not isinstance(box.get(key), (int, float)) or box[key] <= 0 for key in ("width", "height")):
            return RawVisualCapture(status="rejected", reason="截图对象边界无效", sanitized=False, sanitization_status="failed")
        screenshot = getattr(page, "screenshot", None)
        if not callable(screenshot):
            return RawVisualCapture(status="rejected", reason="浏览器 Page 不支持截图", sanitized=False, sanitization_status="failed")
        try:
            image = screenshot(type="png", clip=box, animations="disabled", timeout=self._session.profile.operation_timeout_ms)
        except TypeError:
            image = screenshot(type="png", clip=box, timeout=self._session.profile.operation_timeout_ms)
        except Exception as error:
            raise BrowserSessionFailure("浏览器截图执行失败，浏览器上下文已失效") from error
        if not isinstance(image, (bytes, bytearray)) or not bytes(image).startswith(b"\x89PNG\r\n\x1a\n"):
            return RawVisualCapture(status="rejected", reason="浏览器返回的截图不是 PNG", sanitized=False, sanitization_status="failed")
        image = bytes(image)
        if len(image) < 24:
            return RawVisualCapture(status="rejected", reason="PNG 截图头不完整", sanitized=False, sanitization_status="failed")
        width = int.from_bytes(image[16:20], "big")
        height = int.from_bytes(image[20:24], "big")
        if width <= 0 or height <= 0:
            return RawVisualCapture(status="rejected", reason="PNG 截图尺寸无效", sanitized=False, sanitization_status="failed")
        return RawVisualCapture(image_bytes=image, image_type="png", width=width, height=height,
                                bounding_box={"x": 0, "y": 0, "width": width, "height": height},
                                source_bounding_box=box,
                                annotation="Host 固定定位的对象区域（未执行自动像素脱敏）",
                                sanitized=False, sanitization_status="not_performed")


def create_browser_evidence_adapter(session: BrowserSession, *, page_adapter: BrowserReadOnlyPageAdapter,
                                    identity_adapter: BrowserObjectIdentityAdapter,
                                    network_guard: BrowserNetworkGuard | None = None,
                                    policy: ActionSafetyPolicy | None = None) -> BrowserEvidenceAdapter:
    """Create the B07a structured Evidence adapter for an existing bundle."""
    return BrowserEvidenceAdapter(session, page_adapter, identity_adapter,
                                  network_guard=network_guard, policy=policy)
