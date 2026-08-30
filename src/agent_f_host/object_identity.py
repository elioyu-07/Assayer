from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObjectMatch:
    host_locator_id: str
    identity_material: str
    role: str
    accessible_name: str
    visible_text: str
    x: float
    y: float
    width: float
    height: float
    viewport_width: int
    viewport_height: int


@dataclass(frozen=True)
class ObjectVerification:
    status: str
    candidate_count: int
    matched_dimensions: tuple[str, ...] = ()
    excluded_reasons: tuple[str, ...] = ()
    changed_dimensions: tuple[str, ...] = ()
    match: ObjectMatch | None = None


class ObjectIdentityAdapter(Protocol):
    def verify_candidate(self, candidate: dict, page_state: dict) -> ObjectVerification: ...
    def rebind_object(self, audit_object: dict, page_state: dict) -> ObjectVerification: ...


class DeterministicObjectIdentityAdapter:
    """Deterministic identity adapter for contract tests."""

    def __init__(self, verification: ObjectVerification | None = None):
        self.verification = verification or ObjectVerification(
            status="matched",
            candidate_count=1,
            matched_dimensions=("role", "accessible_name", "business_region"),
            match=ObjectMatch(
                host_locator_id="locator-orders-filter-001",
                identity_material="filter_region|search|订单筛选|orders",
                role="search",
                accessible_name="订单筛选",
                visible_text="订单筛选 查询 重置",
                x=20, y=80, width=640, height=120,
                viewport_width=1280, viewport_height=800,
            ),
        )

    def verify_candidate(self, candidate: dict, page_state: dict) -> ObjectVerification:
        return self.verification

    def rebind_object(self, audit_object: dict, page_state: dict) -> ObjectVerification:
        return self.verification
