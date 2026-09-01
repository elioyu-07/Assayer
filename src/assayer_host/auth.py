from __future__ import annotations

import getpass
import re
import uuid
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable, Protocol


class LoginSecret:
    """Host-owned username/password buffers with explicit best-effort zeroization."""

    MAX_USERNAME_BYTES = 1_024
    MAX_PASSWORD_BYTES = 16_384

    def __init__(self, username: str, password: str):
        if not isinstance(username, str) or not isinstance(password, str):
            raise TypeError("username and password must be strings")
        encoded_username = username.encode("utf-8")
        encoded_password = password.encode("utf-8")
        if not encoded_username or len(encoded_username) > self.MAX_USERNAME_BYTES:
            raise ValueError("username length is invalid")
        if not encoded_password or len(encoded_password) > self.MAX_PASSWORD_BYTES:
            raise ValueError("password length is invalid")
        self._username = bytearray(encoded_username)
        self._password = bytearray(encoded_password)
        self._cleared = False
        self._lock = RLock()

    def __repr__(self) -> str:
        return "LoginSecret([REDACTED])"

    __str__ = __repr__

    @property
    def is_cleared(self) -> bool:
        with self._lock:
            return self._cleared

    def reveal_username(self) -> str:
        return self._reveal(self._username)

    def reveal_password(self) -> str:
        return self._reveal(self._password)

    def _reveal(self, value: bytearray) -> str:
        with self._lock:
            if self._cleared:
                raise RuntimeError("login secret has been cleared")
            return value.decode("utf-8")

    def redact(self, value: str | None) -> str | None:
        """Remove owned secret values and common credential assignments from diagnostics."""
        if value is None:
            return None
        if not isinstance(value, str):
            return "Login failed"
        with self._lock:
            if not self._cleared:
                for secret in (self._username.decode("utf-8"), self._password.decode("utf-8")):
                    if secret:
                        value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(password|passwd|token|secret)=([^&\s]+)", r"\1=[REDACTED]", value)
        return " ".join(value.split())[:512] or "Login failed"

    def clear(self) -> None:
        with self._lock:
            if self._cleared:
                return
            for value in (self._username, self._password):
                for index in range(len(value)):
                    value[index] = 0
                value.clear()
            self._cleared = True


class CredentialVault:
    """One-shot in-memory credential handles; values are never serialized."""

    MAX_TTL_SECONDS = 300
    HANDLE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")

    def __init__(self):
        self._values: dict[str, tuple[LoginSecret, float]] = {}
        self._lock = RLock()

    def put(self, handle: str, secret: LoginSecret, *, ttl_seconds: float = 60) -> None:
        if not isinstance(handle, str) or not self.HANDLE_PATTERN.fullmatch(handle):
            raise ValueError("handle format is invalid")
        if not isinstance(secret, LoginSecret) or secret.is_cleared:
            raise TypeError("secret must be an uncleared LoginSecret")
        if type(ttl_seconds) not in {int, float} or not 0 < ttl_seconds <= self.MAX_TTL_SECONDS:
            raise ValueError("ttl_seconds outside allowed range")
        with self._lock:
            if handle in self._values:
                raise ValueError("credential handle already exists")
            self._values[handle] = (secret, monotonic() + ttl_seconds)

    def consume(self, handle: str) -> LoginSecret | None:
        with self._lock:
            item = self._values.pop(handle, None)
        if item is None:
            return None
        secret, expires_at = item
        if monotonic() > expires_at:
            secret.clear()
            return None
        return secret

    def discard(self, handle: str) -> None:
        with self._lock:
            item = self._values.pop(handle, None)
        if item:
            item[0].clear()

    def clear_all(self) -> None:
        with self._lock:
            secrets = [item[0] for item in self._values.values()]
            self._values.clear()
        for secret in secrets:
            secret.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)


class LocalCredentialIntake:
    """Interactive local intake. Passwords are read with echo disabled by getpass."""

    def __init__(self, vault: CredentialVault, *, username_reader: Callable[[str], str] = input,
                 password_reader: Callable[[str], str] = getpass.getpass,
                 handle_factory: Callable[[], str] | None = None):
        self._vault = vault
        self._username_reader = username_reader
        self._password_reader = password_reader
        self._handle_factory = handle_factory or (lambda: f"credential-{uuid.uuid4().hex}")

    def capture_username_password(self, *, ttl_seconds: float = 60) -> str:
        username = self._username_reader("Username: ")
        password = self._password_reader("Password: ")
        secret = LoginSecret(username, password)
        handle = self._handle_factory()
        try:
            self._vault.put(handle, secret, ttl_seconds=ttl_seconds)
        except Exception:
            secret.clear()
            raise
        return handle


@dataclass(frozen=True)
class LoginResult:
    status: str
    reason: str | None = None
    current_page_state_id: str | None = None
    capabilities: tuple[str, ...] = ()


class LoginAdapter(Protocol):
    def authenticate(self, url: str, secret: LoginSecret) -> LoginResult: ...


class AnonymousLoginAdapter(Protocol):
    def authenticate_anonymous(self, url: str) -> LoginResult: ...


@dataclass(frozen=True)
class LoginOutcome:
    status: str
    result: LoginResult | None
    error_code: str | None
    reason: str | None
    phases: tuple[str, ...]
    secret_cleared: bool


class LoginCoordinator:
    """One-way login phase machine that always attempts secret zeroization."""

    def authenticate(self, url: str, secret: LoginSecret, adapter: LoginAdapter) -> LoginOutcome:
        phases = ["credential_consumed", "authenticating"]
        result = None
        error_code = None
        reason = None
        try:
            candidate = adapter.authenticate(url, secret)
            error_code, reason = self._validate_result(candidate, secret)
            if error_code is None:
                result = candidate
                phases.append("succeeded")
            else:
                phases.append("failed")
        except Exception:
            error_code, reason = "INTERNAL_FAILURE", "Login adapter failed"
            phases.append("failed")
        try:
            secret.clear()
        except Exception:
            error_code, reason = "CREDENTIAL_CHANNEL_FAILED", "Credential cleanup failed"
            result = None
        cleared = secret.is_cleared
        if not cleared:
            error_code, reason = "CREDENTIAL_CHANNEL_FAILED", "Credential cleanup failed"
            result = None
        phases.append("credential_cleared" if cleared else "credential_clear_failed")
        return LoginOutcome("succeeded" if result else "failed", result, error_code, reason, tuple(phases), cleared)

    def authenticate_anonymous(self, url: str, adapter: AnonymousLoginAdapter) -> LoginOutcome:
        """Validate public-page bootstrap without inventing credential material."""
        phases = ["anonymous_access", "authenticating"]
        result = None
        error_code = None
        reason = None
        try:
            candidate = adapter.authenticate_anonymous(url)
            error_code, reason = self._validate_result(candidate, None)
            if error_code is None:
                result = candidate
                phases.append("succeeded")
            else:
                phases.append("failed")
        except Exception:
            error_code, reason = "INTERNAL_FAILURE", "Anonymous page adapter failed"
            phases.append("failed")
        return LoginOutcome("succeeded" if result else "failed", result, error_code, reason,
                            tuple(phases), True)

    @staticmethod
    def _validate_result(result: object, secret: LoginSecret | None) -> tuple[str | None, str | None]:
        if not isinstance(result, LoginResult) or result.status not in {"succeeded", "failed"}:
            return "INTERNAL_FAILURE", "Login adapter returned an invalid status"
        if result.status == "failed":
            if result.current_page_state_id is not None or result.capabilities:
                return "INTERNAL_FAILURE", "A failed login result must not include page facts"
            if secret is not None:
                reason = secret.redact(result.reason)
            else:
                reason = LoginCoordinator._sanitize_anonymous_reason(result.reason)
            return "LOGIN_FAILED", reason or "Anonymous page access failed"
        if not isinstance(result.current_page_state_id, str) or not result.current_page_state_id:
            return "INTERNAL_FAILURE", "A successful login result is missing page state"
        capabilities = result.capabilities
        if (not isinstance(capabilities, tuple) or len(capabilities) != len(set(capabilities))
                or any(not isinstance(item, str) or not item for item in capabilities)):
            return "INTERNAL_FAILURE", "A successful login result contains an invalid capability set"
        return None, None

    @staticmethod
    def _sanitize_anonymous_reason(value: object) -> str:
        if not isinstance(value, str):
            return "Anonymous page access failed"
        value = re.sub(r"(?i)(password|passwd|token|secret)=([^&\s]+)", r"\1=[REDACTED]", value)
        return " ".join(value.split())[:512] or "Anonymous page access failed"


class UnavailableLoginAdapter:
    """Default adapter; prevents accidental success before browser integration."""

    def authenticate(self, url: str, secret: LoginSecret) -> LoginResult:
        return LoginResult("failed", "Browser login adapter is not configured")

    def authenticate_anonymous(self, url: str) -> LoginResult:
        return LoginResult("failed", "Anonymous page adapter is not configured")


class DeterministicLoginAdapter:
    """Test adapter; production code must provide a browser-backed adapter."""

    def __init__(self, *, succeed: bool = True, reason: str = "Login adapter rejected the credentials"):
        self.succeed = succeed
        self.reason = reason

    def authenticate(self, url: str, secret: LoginSecret) -> LoginResult:
        return self._result()

    def authenticate_anonymous(self, url: str) -> LoginResult:
        return self._result()

    def _result(self) -> LoginResult:
        if self.succeed:
            return LoginResult("succeeded", current_page_state_id="page-bootstrap-001", capabilities=("runtime", "dom", "interaction"))
        return LoginResult("failed", reason=self.reason)
