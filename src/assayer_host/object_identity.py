from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import HostError


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
    controls: tuple[dict, ...] = ()
    lists: tuple[dict, ...] = ()


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


class UnavailableObjectIdentityAdapter:
    """Production default; refuses to invent a browser object match."""

    def verify_candidate(self, candidate: dict, page_state: dict) -> ObjectVerification:
        raise HostError("INTERNAL_FAILURE", "Object identity adapter is not configured")

    def rebind_object(self, audit_object: dict, page_state: dict) -> ObjectVerification:
        raise HostError("INTERNAL_FAILURE", "Object identity adapter is not configured")


class DeterministicObjectIdentityAdapter:
    """Deterministic identity adapter for contract tests."""

    def __init__(self, verification: ObjectVerification | None = None):
        self.verification = verification or ObjectVerification(
            status="matched",
            candidate_count=1,
            matched_dimensions=("role", "accessible_name", "business_region"),
            match=ObjectMatch(
                host_locator_id="locator-orders-filter-001",
                identity_material="filter_region|search|Order filters|orders",
                role="search",
                accessible_name="Order filters",
                visible_text="Order filters Query Reset",
                x=20, y=80, width=640, height=120,
                viewport_width=1280, viewport_height=800,
                controls=(
                    {"controlRef":"control-filter-input-001","kind":"input","semanticAction":"filter_input","visible":True,"disabled":False,"valueClass":"empty"},
                    {"controlRef":"control-query-001","kind":"button","semanticAction":"query","visible":True,"disabled":False,"valueClass":"unknown"},
                    {"controlRef":"control-reset-001","kind":"button","semanticAction":"reset","visible":True,"disabled":False,"valueClass":"unknown"},
                ),
                lists=({"listRef":"list-orders-001","kind":"table","visible":True,"relationship":"same_container"},),
            ),
        )

    def verify_candidate(self, candidate: dict, page_state: dict) -> ObjectVerification:
        return self.verification

    def rebind_object(self, audit_object: dict, page_state: dict) -> ObjectVerification:
        return self.verification
