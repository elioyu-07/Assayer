from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


RECOVERY_DIMENSIONS = (
    "url_route", "page_layer", "active_tab", "overlay_state", "control_state",
    "object_identity", "pending_requests", "write_request", "local_visual",
)


@dataclass(frozen=True)
class RecoveryCheck:
    dimension: str
    outcome: str  # match, mismatch, unknown
    expected: object = None
    observed: object = None


@dataclass(frozen=True)
class RecoveryAttempt:
    method: str  # targeted_inverse, refresh_replay
    outcome: str  # restored, uncertain, failed
    checks: tuple[RecoveryCheck, ...]
    reason: str | None = None


class RecoveryAdapter(Protocol):
    def restore(self, case: dict, target: dict, page_state: dict | None, method: str) -> RecoveryAttempt: ...


class UnavailableRecoveryAdapter:
    """Default adapter. It never claims that an unobserved browser is clean."""
    def restore(self, case, target, page_state, method):
        checks = tuple(RecoveryCheck(dimension, "unknown") for dimension in RECOVERY_DIMENSIONS)
        return RecoveryAttempt(method, "failed", checks, "Browser recovery adapter is not configured")


class DeterministicRecoveryAdapter:
    """Test adapter with independent targeted and refresh outcomes."""
    def __init__(self, targeted: RecoveryAttempt | None = None, refresh: RecoveryAttempt | None = None):
        self.targeted = targeted or RecoveryAttempt("targeted_inverse", "restored", tuple(RecoveryCheck(d, "match") for d in RECOVERY_DIMENSIONS))
        self.refresh = refresh or RecoveryAttempt("refresh_replay", "restored", tuple(RecoveryCheck(d, "match") for d in RECOVERY_DIMENSIONS))
        self.calls: list[str] = []

    def restore(self, case, target, page_state, method):
        self.calls.append(method)
        return self.targeted if method == "targeted_inverse" else self.refresh
