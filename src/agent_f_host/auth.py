from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from threading import RLock
from typing import Protocol


class CredentialVault:
    """One-shot in-memory credential handles; values are never serialized."""

    def __init__(self):
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = RLock()

    def put(self, handle: str, secret: str, *, ttl_seconds: float = 60) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        with self._lock:
            self._values[handle] = (secret, monotonic() + ttl_seconds)

    def consume(self, handle: str) -> str | None:
        with self._lock:
            item = self._values.pop(handle, None)
        if item is None:
            return None
        secret, expires_at = item
        return secret if monotonic() <= expires_at else None


@dataclass(frozen=True)
class LoginResult:
    status: str
    reason: str | None = None
    current_page_state_id: str | None = None
    capabilities: tuple[str, ...] = ()


class LoginAdapter(Protocol):
    def authenticate(self, url: str, secret: str) -> LoginResult: ...


class DeterministicLoginAdapter:
    """Test adapter; production code must provide a browser-backed adapter."""

    def __init__(self, *, succeed: bool = True, reason: str = "登录适配器拒绝凭据"):
        self.succeed = succeed
        self.reason = reason

    def authenticate(self, url: str, secret: str) -> LoginResult:
        if self.succeed:
            return LoginResult("succeeded", current_page_state_id="page-bootstrap-001", capabilities=("runtime", "dom"))
        return LoginResult("failed", reason=self.reason)
