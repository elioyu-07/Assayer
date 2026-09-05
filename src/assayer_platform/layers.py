"""Minimal interfaces for the four logical platform layers.

These protocols are intentionally small and domain-neutral.  They establish
ownership seams while the initial implementation remains one process; concrete
plugins and capability providers can adopt them incrementally.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .contract import (
    Artifact, CheckContract, DecisionProposal, InvestigationPacket,
    PlatformContext, PlatformRunResult, ReviewCheckpoint, WorkItem,
)


@runtime_checkable
class NavigationProvider(Protocol):
    """Parse a target into stable, pageable source/navigation facts."""

    def discover_units(self, target: Any, context: PlatformContext) -> Sequence[Mapping[str, Any]]: ...

    def read_document(self, target: Any, context: PlatformContext) -> Mapping[str, Any]: ...

    def read_units(self, unit_ids: Sequence[str], context: PlatformContext) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class EvidenceCollectionProvider(Protocol):
    """Expose immutable evidence candidates and bounded pages."""

    def collect(self, work_item: WorkItem, check: CheckContract, context: PlatformContext) -> InvestigationPacket: ...

    def page(self, work_item_id: str, collection_id: str, cursor: str | None, limit: int) -> Mapping[str, Any]: ...


@runtime_checkable
class ReviewProtocol(Protocol):
    """Validate and persist domain-neutral Agent review boundaries."""

    def checkpoint(self, checkpoint: ReviewCheckpoint, context: PlatformContext) -> Mapping[str, Any]: ...

    def submit(self, decisions: Sequence[DecisionProposal], context: PlatformContext) -> Mapping[str, Any]: ...


@runtime_checkable
class DeliveryObserver(Protocol):
    """Publish portable results and human/machine diagnostics."""

    def publish(self, result: PlatformRunResult) -> Artifact: ...

    def observe(self, result: PlatformRunResult) -> Mapping[str, Any]: ...


__all__ = [
    "NavigationProvider", "EvidenceCollectionProvider", "ReviewProtocol",
    "DeliveryObserver",
]
