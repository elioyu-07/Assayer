from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .action_safety import NetworkRequest

from .errors import HostError


@dataclass(frozen=True)
class EntrypointObservation:
    kind: str
    label: str
    intent: str
    status: str = "unprocessed"
    reason_code: str = "NOT_YET_EXPLORED"
    reason_message: str = "入口尚未探索。"
    host_locator_id: str | None = None


@dataclass(frozen=True)
class CandidateObservation:
    kind: str
    label: str
    role: str
    locator_material: str


@dataclass(frozen=True)
class PageObservation:
    url: str
    origin: str
    route: str
    title: str
    state_kind: str
    dom_material: str
    identity_material: str
    visible_text: str
    entrypoints: tuple[EntrypointObservation, ...] = ()
    candidates: tuple[CandidateObservation, ...] = ()
    network_summary: dict | None = None
    structure_summary: dict | None = None
    active_tab: str | None = None


@dataclass(frozen=True)
class EntrypointExecution:
    status: str  # succeeded, request_blocked, result_unknown, unavailable
    requests: tuple[NetworkRequest, ...] = ()
    diagnostic: str | None = None
    page_changed: bool = False


class EntrypointAdapter(Protocol):
    def explore(self, entrypoint: dict, page_state: dict, operation_id: str) -> EntrypointExecution: ...


class UnavailableEntrypointAdapter:
    def explore(self, entrypoint, page_state, operation_id):
        return EntrypointExecution("unavailable", diagnostic="页面入口探索适配器未配置")


class ReadOnlyPageAdapter(Protocol):
    """Adapter contract forbids action methods by construction."""

    def observe(self, page_state_id: str) -> PageObservation: ...


class UnavailablePageAdapter:
    """Production default; never substitutes a fixture for browser facts."""

    def observe(self, page_state_id: str) -> PageObservation:
        raise HostError("INTERNAL_FAILURE", "只读页面适配器未配置")


class DeterministicPageAdapter:
    """Static adapter used by contract tests before a browser adapter exists."""

    def __init__(self, observation: PageObservation | None = None):
        self.observation = observation or PageObservation(
            url="https://test.example.com/orders",
            origin="https://test.example.com",
            route="/orders",
            title="订单列表",
            state_kind="page",
            dom_material='<main><section role="search">订单筛选</section></main>',
            identity_material="/orders|page|订单列表",
            visible_text="订单列表 订单筛选 查询 重置",
            entrypoints=(EntrypointObservation("safe_action", "订单筛选", "查看筛选区"),),
            candidates=(CandidateObservation("filter_region", "订单筛选", "search", "orders-filter"),),
            network_summary={"pendingReadRequests": 0, "observedWrites": 0},
        )

    def observe(self, page_state_id: str) -> PageObservation:
        return self.observation
