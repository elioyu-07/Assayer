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
    reason_message: str = "Entrypoint has not been explored."
    host_locator_id: str | None = None
    logical_identity_material: str | None = None
    target_locator_material: str | None = None


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
        return EntrypointExecution("unavailable", diagnostic="Page entrypoint adapter is not configured")


class ReadOnlyPageAdapter(Protocol):
    """Adapter contract forbids action methods by construction."""

    def observe(self, page_state_id: str) -> PageObservation: ...


class UnavailablePageAdapter:
    """Production default; never substitutes a fixture for browser facts."""

    def observe(self, page_state_id: str) -> PageObservation:
        raise HostError("INTERNAL_FAILURE", "Read-only page adapter is not configured")


class DeterministicPageAdapter:
    """Static adapter used by contract tests before a browser adapter exists."""

    def __init__(self, observation: PageObservation | None = None):
        self.observation = observation or PageObservation(
            url="https://test.example.com/orders",
            origin="https://test.example.com",
            route="/orders",
            title="Orders",
            state_kind="page",
            dom_material='<main><section role="search">Order filters</section></main>',
            identity_material="/orders|page|Orders",
            visible_text="Orders Order filters Query Reset",
            entrypoints=(EntrypointObservation(
                "safe_action", "Order filters", "Inspect filter region",
                logical_identity_material="filter_region|search|Order filters|/orders|0",
                target_locator_material="orders-filter",
            ),),
            candidates=(CandidateObservation("filter_region", "Order filters", "search", "orders-filter"),),
            network_summary={"pendingReadRequests": 0, "observedWrites": 0},
        )

    def observe(self, page_state_id: str) -> PageObservation:
        return self.observation
