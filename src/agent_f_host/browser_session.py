"""Browser session ownership and safety boundaries.

This module deliberately does not import a browser driver.  A future Playwright
adapter supplies a backend implementing ``BrowserBackend``; the registry and
serial execution rules remain the same for every backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol, TypeVar

from .errors import HostError


class BrowserSessionFailure(HostError):
    """A browser/backend failure that invalidates all facts from the Session."""

    def __init__(self, message: str = "浏览器 Session 已失败，不能继续使用"):
        super().__init__("BROWSER_SESSION_FAILED", message, next_step="stop_scan")


class BrowserBackend(Protocol):
    def launch(self, profile: "BrowserProfile") -> object: ...
    def close(self, handle: object) -> None: ...


@dataclass(frozen=True)
class BrowserProfile:
    """Allow-listed browser settings; no executable, proxy or arbitrary args."""

    browser: str = "chromium"
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 800
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    navigation_timeout_ms: int = 30_000
    operation_timeout_ms: int = 30_000
    network_idle_window_ms: int = 500

    ALLOWED_BROWSERS = frozenset({"chromium"})
    _FIELDS = frozenset({
        "browser", "headless", "viewport_width", "viewport_height", "locale", "timezone_id",
        "navigation_timeout_ms", "operation_timeout_ms", "network_idle_window_ms",
    })

    @classmethod
    def from_mapping(cls, value: dict | None) -> "BrowserProfile":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("browser profile must be an object")
        unknown = set(value) - cls._FIELDS
        if unknown:
            raise ValueError(f"unsupported browser profile fields: {sorted(unknown)}")
        profile = cls(**value)
        profile.validate()
        return profile

    def validate(self) -> None:
        if self.browser not in self.ALLOWED_BROWSERS:
            raise ValueError("browser must be chromium")
        if not isinstance(self.headless, bool):
            raise ValueError("headless must be boolean")
        for name, value, minimum, maximum in (
            ("viewport_width", self.viewport_width, 320, 7680),
            ("viewport_height", self.viewport_height, 240, 4320),
            ("navigation_timeout_ms", self.navigation_timeout_ms, 100, 300_000),
            ("operation_timeout_ms", self.operation_timeout_ms, 100, 300_000),
            ("network_idle_window_ms", self.network_idle_window_ms, 0, 30_000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} outside allowed range")
        for name in ("locale", "timezone_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 64:
                raise ValueError(f"{name} must be a short non-empty string")


class BrowserSession:
    """One browser context owned by one Scan and serialized at the Page boundary."""

    def __init__(self, scan_id: str, profile: BrowserProfile | None = None, *, backend: BrowserBackend | None = None):
        if not isinstance(scan_id, str) or not scan_id:
            raise ValueError("scan_id must be non-empty")
        self.scan_id = scan_id
        self.profile = profile or BrowserProfile()
        self.profile.validate()
        self._backend = backend
        self._handle: object | None = None
        self._state = "created"
        self._lock = RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def handle(self) -> object:
        with self._lock:
            if self._state != "open" or self._handle is None:
                raise RuntimeError("browser session is not open")
            return self._handle

    def open(self) -> object:
        with self._lock:
            if self._state == "open":
                return self._handle
            if self._state in {"closed", "failed"}:
                raise RuntimeError(f"browser session is {self._state}")
            if self._backend is None:
                self._state = "failed"
                raise RuntimeError("browser backend is not configured")
            try:
                self._handle = self._backend.launch(self.profile)
            except Exception:
                self._state = "failed"
                raise
            self._state = "open"
            return self._handle

    T = TypeVar("T")

    def run_serial(self, callback: Callable[[object], T]) -> T:
        """Run one browser operation while holding the Scan's session lock."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._state != "open" or self._handle is None:
                raise RuntimeError("browser session is not open")
            try:
                return callback(self._handle)
            except BrowserSessionFailure:
                self._state = "failed"
                raise
            except HostError:
                # A deliberate Host rejection (for example navigation policy)
                # does not prove the browser Context itself is broken.
                raise
            except Exception as error:
                # A backend exception means the context can no longer be trusted.
                self._state = "failed"
                raise BrowserSessionFailure() from error

    def close(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            handle, backend = self._handle, self._backend
            self._handle = None
            try:
                if handle is not None and backend is not None:
                    backend.close(handle)
            finally:
                self._state = "closed"

    def __enter__(self) -> "BrowserSession":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class ScanSessionRegistry:
    """Exclusive Scan → BrowserSession ownership with terminal release."""

    TERMINAL_STATES = frozenset({"completed", "partial", "failed"})

    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = RLock()

    def register(self, scan_id: str, session: BrowserSession) -> BrowserSession:
        if session.scan_id != scan_id:
            raise ValueError("session scan_id does not match registry key")
        with self._lock:
            if scan_id in self._sessions:
                raise RuntimeError("scan already owns a browser session")
            self._sessions[scan_id] = session
            return session

    def get(self, scan_id: str) -> BrowserSession:
        with self._lock:
            session = self._sessions.get(scan_id)
            if session is None:
                raise KeyError(scan_id)
            return session

    def release(self, scan_id: str, terminal_state: str) -> None:
        if terminal_state not in self.TERMINAL_STATES:
            raise ValueError("browser session may only be released at a terminal Scan state")
        with self._lock:
            session = self._sessions.pop(scan_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        errors = []
        for session in sessions:
            try:
                session.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
