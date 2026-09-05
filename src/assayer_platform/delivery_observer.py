"""Unified platform result publication and observation entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

from .contract import Artifact, PlatformRunResult
from .observability import build_platform_observability
from .reporting import JsonSummaryPublisher


class PlatformDeliveryObserver:
    """Compose the existing durable publisher and observability projection."""

    def __init__(self, output_root: str | Path):
        self.output_root = Path(output_root).expanduser().resolve()

    def publish(self, result: PlatformRunResult) -> Artifact:
        return JsonSummaryPublisher(self.output_root).publish(result)

    def observe(self, result: PlatformRunResult) -> Mapping[str, Any]:
        if result.ledger is None:
            return {"status": result.status, "available": False, "reason": "No platform ledger was produced."}
        return build_platform_observability(result.ledger)


__all__ = ["PlatformDeliveryObserver"]
