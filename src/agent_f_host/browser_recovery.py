"""Real Chromium recovery barrier for the B06 vertical slice."""

from __future__ import annotations

from urllib.parse import urlparse

from .action_safety import ActionSafetyPolicy
from .browser_action import BrowserEntrypointAdapter, BrowserNetworkGuard, BrowserSafeActionAdapter
from .browser_readonly import (BrowserLocatorRegistry, BrowserObjectIdentityAdapter,
                               BrowserReadOnlyPageAdapter, ReadonlyBrowserPage)
from .browser_session import BrowserSession
from .browser_evidence import BrowserEvidenceAdapter
from .object_identity import ObjectVerification
from .recovery import RECOVERY_DIMENSIONS, RecoveryAttempt, RecoveryCheck
from dataclasses import dataclass
from typing import Callable


RECOVERY_PROBE_V1 = """({targetIndex}) => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
  };
  const regions = [...document.querySelectorAll('[role="search"], form, [data-testid*="filter" i], [class*="filter" i]')]
    .filter(visible).slice(0, 128);
  const target = regions[targetIndex] || null;
  const tabs = [...document.querySelectorAll('[role="tab"]')].filter(visible);
  const selectedTab = tabs.findIndex((item) => item.getAttribute('aria-selected') === 'true');
  const dialogs = [...document.querySelectorAll('[role="dialog"], dialog[open]')].filter(visible);
  const modals = dialogs.filter((item) => item.getAttribute('aria-modal') === 'true');
  const drawers = [...document.querySelectorAll('[role="complementary"], [data-drawer], [class*="drawer" i]')].filter(visible);
  const controls = target ? [...target.querySelectorAll('input, select, textarea, button, [aria-expanded], [role="tab"]')] : [];
  const expanded = controls.map((item) => item.getAttribute('aria-expanded')).filter((value) => value !== null);
  const box = target?.getBoundingClientRect();
  return {
    route: location.pathname,
    pageLayer: dialogs.length ? 'dialog' : 'page',
    activeTab: {count: tabs.length, selectedIndex: selectedTab},
    overlayState: {dialogs: dialogs.length, modals: modals.length, drawers: drawers.length},
    controlState: target ? {
      expanded, checked: controls.filter((item) => item.checked === true).length,
      selected: controls.filter((item) => item.selected === true).length,
      disabled: controls.filter((item) => item.disabled === true || item.getAttribute('aria-disabled') === 'true').length,
      nonEmptyInputs: controls.filter((item) => 'value' in item && String(item.value || '').length > 0).length,
      controlCount: controls.length
    } : null,
    localVisual: box ? {visible: visible(target), x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height)} : null
  };
}"""


class BrowserRecoveryAdapter:
    """Apply Host-recorded inverse actions, then prove all recovery dimensions.

    The adapter never accepts a selector or script from the caller.  It only
    consumes the opaque object identity persisted by Host and fixed actions
    already recorded in the Case.
    """

    def __init__(self, session: BrowserSession, page_adapter: BrowserReadOnlyPageAdapter,
                 identity_adapter: BrowserObjectIdentityAdapter,
                 action_adapter: BrowserSafeActionAdapter,
                 network_guard: BrowserNetworkGuard,
                 *, policy: ActionSafetyPolicy | None = None):
        self._session = session
        self._page = page_adapter
        self._identity = identity_adapter
        self._actions = action_adapter
        self._guard = network_guard
        self._policy = policy or network_guard.policy
        self._baselines: dict[str, dict] = {}

    def capture_baseline(self, case: dict, target: dict, page_state: dict | None) -> None:
        """Keep baseline facts in Session memory; never persist raw DOM/values."""
        if not page_state:
            raise RuntimeError("恢复基线 PageState 不存在")
        network = self._guard.summary()
        if network.get("trackingStatus") != "proven" or network.get("pendingReadRequests") != 0 or network.get("sentWrites") != 0:
            raise RuntimeError("恢复基线网络状态不可证明为干净")
        locator_id = target.get("identity", {}).get("hostLocatorId", "")
        match = BrowserSafeActionAdapter.LOCATOR_ID.fullmatch(locator_id)
        if not match:
            raise RuntimeError("恢复目标不是 Host 浏览器句柄")

        def capture(context):
            page = self._page.page_for_context(context)
            snapshot = self._snapshot(page, int(match.group(1)))
            return snapshot

        snapshot = self._session.run_serial(capture)
        self._baselines[case["caseId"]] = {
            "url": page_state.get("url"), "origin": page_state.get("origin"),
            "route": page_state.get("route"), "stateKind": page_state.get("stateKind", "page"),
            "objectFingerprint": target.get("identity", {}).get("fingerprint"),
            "targetIndex": int(match.group(1)), "snapshot": snapshot,
        }

    def restore(self, case: dict, target: dict, page_state: dict | None, method: str) -> RecoveryAttempt:
        baseline = self._baselines.get(case.get("caseId")) or self._baseline_from_page(page_state)
        operation_id = case.get("_hostOperationId") or f"recovery-{case.get('caseId', 'unknown')}"
        if method == "targeted_inverse":
            for action in reversed(case.get("actions", ())):
                if action.get("safetyOutcome") != "allowed":
                    continue
                inverse = action.get("inverseAction")
                if not inverse:
                    continue
                execution = self._actions.execute(
                    {"operationId": operation_id, "type": inverse["type"], "parameters": inverse.get("parameters", {})},
                    target, page_state or {},
                    lambda request: self._policy.classify_request(request, (page_state or {}).get("origin", "")),
                )
                if execution.status != "succeeded":
                    return self._attempt(method, baseline, target, page_state, "failed", execution.diagnostic or "反向动作未完成")
        elif method == "refresh_replay":
            self._refresh_to_baseline(baseline, operation_id, page_state or {})
        else:
            return self._attempt(method, baseline, target, page_state, "failed", "未知恢复方法")
        return self._attempt(method, baseline, target, page_state, "restored", None)

    @staticmethod
    def _baseline_from_page(page_state: dict | None) -> dict:
        page_state = page_state or {}
        return {"url": page_state.get("url"), "origin": page_state.get("origin"),
                "route": page_state.get("route"), "stateKind": page_state.get("stateKind", "page"),
                "objectFingerprint": None}

    def _refresh_to_baseline(self, baseline: dict, operation_id: str, page_state: dict) -> None:
        # Use the sanitized PageState URL; never replay a raw query/fragment
        # that may contain a ticket or other transient secret.
        url = baseline.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("恢复基线缺少安全 URL")
        with self._guard.operation(
            operation_id,
            lambda request: self._policy.classify_request(request, page_state.get("origin", "")),
        ):
            self._page.navigate(url)

    def _attempt(self, method: str, baseline: dict, target: dict, page_state: dict | None,
                 outcome: str, reason: str | None) -> RecoveryAttempt:
        checks = self._checks(baseline, target, page_state)
        if any(item.outcome == "mismatch" for item in checks):
            final = "failed"
        elif any(item.outcome == "unknown" for item in checks):
            final = "uncertain"
        elif outcome == "failed":
            final = "failed"
        else:
            final = "restored"
        if final == "failed" and not reason:
            reason = "恢复检查存在关键不匹配"
        if final == "uncertain" and not reason:
            reason = "恢复检查存在未知维度"
        return RecoveryAttempt(method, final, tuple(checks), reason)

    def _checks(self, baseline: dict, target: dict, page_state: dict | None) -> list[RecoveryCheck]:
        checks = {dimension: RecoveryCheck(dimension, "unknown") for dimension in RECOVERY_DIMENSIONS}
        try:
            def inspect(context):
                page = self._page.page_for_context(context)
                current_url = str(page.url)
                parsed = urlparse(current_url)
                current_origin = self._page._origin(parsed)
                current_route = parsed.path or "/"
                expected_url = str(baseline.get("url") or "")
                expected = urlparse(expected_url)
                expected_origin = baseline.get("origin") or (self._page._origin(expected) if expected.netloc else "")
                expected_route = baseline.get("route") or expected.path or "/"
                checks["url_route"] = RecoveryCheck("url_route", "match" if current_origin == expected_origin and current_route == expected_route else "mismatch", expected_route, current_route)
                snapshot = self._page._probe(page)
                recovery_snapshot = self._snapshot(page, baseline.get("targetIndex", -1))
                expected_snapshot = baseline.get("snapshot", {})
                expected_layer = expected_snapshot.get("pageLayer", baseline.get("stateKind", "page"))
                observed_layer = recovery_snapshot.get("pageLayer", snapshot.state_kind)
                checks["page_layer"] = self._equality_check("page_layer", expected_layer, observed_layer)
                checks["active_tab"] = self._equality_check("active_tab", expected_snapshot.get("activeTab"), recovery_snapshot.get("activeTab"))
                checks["overlay_state"] = self._equality_check("overlay_state", expected_snapshot.get("overlayState"), recovery_snapshot.get("overlayState"))
                checks["control_state"] = self._equality_check("control_state", expected_snapshot.get("controlState"), recovery_snapshot.get("controlState"))
                network = self._guard.summary()
                pending_outcome = "unknown" if network.get("trackingStatus") != "proven" or network.get("unknownRequests", 0) else ("match" if network.get("pendingReadRequests") == 0 else "mismatch")
                checks["pending_requests"] = RecoveryCheck("pending_requests", pending_outcome, 0, network.get("pendingReadRequests"))
                checks["write_request"] = RecoveryCheck("write_request", "match" if network.get("sentWrites") == 0 else "mismatch", 0, network.get("sentWrites"))
                expected_visual = expected_snapshot.get("localVisual")
                observed_visual = recovery_snapshot.get("localVisual")
                checks["local_visual"] = RecoveryCheck("local_visual", self._visual_outcome(expected_visual, observed_visual), expected_visual, observed_visual)
            self._session.run_serial(inspect)
            if page_state:
                verification: ObjectVerification = self._identity.rebind_object(target, page_state)
                checks["object_identity"] = RecoveryCheck("object_identity", "match" if verification.status == "matched" else ("unknown" if verification.status == "ambiguous" else "mismatch"), "matched", verification.status)
        except Exception:
            return list(checks.values())
        return list(checks.values())

    @staticmethod
    def _snapshot(page: object, target_index: int) -> dict:
        value = page.evaluate(RECOVERY_PROBE_V1, {"targetIndex": target_index})
        if not isinstance(value, dict):
            raise RuntimeError("恢复探针返回格式无效")
        return value

    @staticmethod
    def _equality_check(dimension: str, expected: object, observed: object) -> RecoveryCheck:
        if expected is None or observed is None:
            outcome = "unknown"
        else:
            outcome = "match" if expected == observed else "mismatch"
        return RecoveryCheck(dimension, outcome, expected, observed)

    @staticmethod
    def _visual_outcome(expected: object, observed: object) -> str:
        if not isinstance(expected, dict) or not isinstance(observed, dict):
            return "unknown"
        if expected.get("visible") != observed.get("visible"):
            return "mismatch"
        for key in ("x", "y", "width", "height"):
            left, right = expected.get(key), observed.get(key)
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                return "unknown"
            if abs(left - right) > 2:
                return "mismatch"
        return "match"


@dataclass(frozen=True)
class RecoverableBrowserAdapterBundle:
    page: BrowserReadOnlyPageAdapter
    identity: BrowserObjectIdentityAdapter
    action: BrowserSafeActionAdapter
    recovery: BrowserRecoveryAdapter
    network_guard: BrowserNetworkGuard
    evidence: BrowserEvidenceAdapter
    entrypoint: BrowserEntrypointAdapter


def create_recoverable_browser_adapter_bundle(
        session: BrowserSession, *, allowed_origin: str,
        page_factory: Callable[[object], ReadonlyBrowserPage] | None = None,
        policy: ActionSafetyPolicy | None = None) -> RecoverableBrowserAdapterBundle:
    """Create B06 adapters sharing one Session, guard and locator registry."""
    registry = BrowserLocatorRegistry()
    guard = BrowserNetworkGuard(allowed_origin, policy=policy)
    page = BrowserReadOnlyPageAdapter(
        session, allowed_origin=allowed_origin, locator_registry=registry,
        page_factory=page_factory, context_initializer=guard.install,
        network_summary_provider=guard.summary,
    )
    identity = BrowserObjectIdentityAdapter(session, locator_registry=registry, refresh=page.refresh_locators)
    action = BrowserSafeActionAdapter(session, page, guard, locator_registry=registry)
    recovery = BrowserRecoveryAdapter(session, page, identity, action, guard, policy=policy)
    evidence = BrowserEvidenceAdapter(session, page, identity, network_guard=guard, policy=policy)
    entrypoint = BrowserEntrypointAdapter(session, page, guard)
    return RecoverableBrowserAdapterBundle(page, identity, action, recovery, guard, evidence, entrypoint)
