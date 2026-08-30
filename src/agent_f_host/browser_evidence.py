"""Minimal structured Evidence collection from a managed browser Page (B07a)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .action_safety import ActionSafetyPolicy
from .browser_action import BrowserNetworkGuard
from .browser_readonly import BrowserLocatorRegistry, BrowserObjectIdentityAdapter, BrowserReadOnlyPageAdapter
from .browser_session import BrowserSession
from .evidence import EvidenceCapture
from .errors import HostError


EVIDENCE_PROBE_V1 = """({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const clean = (value, limit = 120) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const semanticAction = (value) => {
    const label = clean(value, 80).toLowerCase();
    if (/(reset|clear|重置|清空|恢复默认)/.test(label)) return 'reset';
    if (/(query|search|filter|查询|搜索|筛选)/.test(label)) return 'query';
    return 'other';
  };
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  if (!target) return {status: 'not_found', route: location.pathname};
  const role = clean(target.getAttribute('role') || (target.tagName === 'FORM' ? 'form' : 'region'), 64);
  const label = clean(target.getAttribute('aria-label') || target.querySelector('legend')?.textContent || target.textContent, 200);
  const controls = [...target.querySelectorAll('input, select, textarea, button, [aria-expanded], [role="tab"]')].slice(0, 128);
  return {
    status: 'matched', route: location.pathname,
    stateKind: document.querySelector('[role="dialog"]') ? 'dialog' : 'page',
    identityMaterial: ['filter_region', role, clean(label), location.pathname, targetIndex].join('|'),
    object: {role, accessibleNamePresent: Boolean(label), accessibleNameLength: label.length, visibleTextLength: clean(target.innerText, 1000).length},
    controls: controls.map((item) => ({
      semanticAction: semanticAction(item.getAttribute('aria-label') || item.innerText || ''),
      tag: item.tagName.toLowerCase(), role: clean(item.getAttribute('role') || '', 32),
      type: clean(item.getAttribute('type') || '', 32), namePresent: Boolean(item.getAttribute('name') || item.getAttribute('aria-label')),
      accessibleNamePresent: Boolean(item.getAttribute('aria-label') || item.innerText || item.labels?.[0]?.innerText),
      accessibleNameLength: clean(item.getAttribute('aria-label') || item.innerText || item.labels?.[0]?.innerText || '', 80).length,
      valueClass: ('value' in item) ? (String(item.value || '').length ? 'non_empty' : 'empty') : 'not_applicable',
      checked: item.checked === true ? true : item.checked === false ? false : null,
      expanded: item.getAttribute('aria-expanded')
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
        if include_raw_visual:
            raise HostError("SCREENSHOT_ADAPTER_UNAVAILABLE", "B07b 真实截图与像素脱敏暂缓，当前仅支持结构化 Evidence")
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
                "networkSummary": network,
            }
            return EvidenceCapture(kind="runtime_dom", payload_type="json", payload=payload,
                                   source_binding={"status": "verified", "bindingReason": "Host 固定 Evidence 探针与对象 fingerprint 一致"},
                                   collector_version="1.0.0", sanitization_policy_version="1.0.0",
                                   normalization_algorithm_version="1.0.0")
        return self._session.run_serial(collect)


def create_browser_evidence_adapter(session: BrowserSession, *, page_adapter: BrowserReadOnlyPageAdapter,
                                    identity_adapter: BrowserObjectIdentityAdapter,
                                    network_guard: BrowserNetworkGuard | None = None,
                                    policy: ActionSafetyPolicy | None = None) -> BrowserEvidenceAdapter:
    """Create the B07a structured Evidence adapter for an existing bundle."""
    return BrowserEvidenceAdapter(session, page_adapter, identity_adapter,
                                  network_guard=network_guard, policy=policy)
