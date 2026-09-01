"""Dynamic Scan router and Agent lease supervision for C05.

The router is a product assembly boundary.  It does not inspect pages or make
rule decisions; it allocates one browser runtime per Scan, constrains output
paths, routes complete Host envelopes, and fails abandoned runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .browser_session import BrowserProfile
from .errors import HostError


class RoutedRuntime(Protocol):
    def handle(self, request: dict) -> dict: ...
    def protocol_state(self) -> dict | None: ...
    def fail_scan(self, code: str, message: str) -> dict: ...
    def read_screenshot(self, scan_id: str, run_id: str, screenshot_ref: str) -> tuple[bytes, str]: ...
    def close(self) -> None: ...
    def record_runtime_event(self, event: dict) -> dict: ...
    def refresh_observability(self) -> None: ...
    def build_completion_input(self, scan_id: str, run_id: str, completion_reason: str | None = None) -> dict: ...


RuntimeFactory = Callable[[str, Path, str], RoutedRuntime]


@dataclass
class _RuntimeBinding:
    scan_id: str
    run_id: str
    runtime: RoutedRuntime | None
    bootstrap_key: str
    bootstrap_digest: str
    bootstrap_request: dict
    bootstrap_response: dict
    run_revision: int
    scan_status: str
    lease_deadline: float
    protocol_failure_count: int = 0
    last_failure_signature: str | None = None
    consecutive_failure_count: int = 0
    last_success_signature: str | None = None
    consecutive_success_count: int = 0
    progress_required: bool = False
    terminal_request_digest: str | None = None
    terminal_response: dict | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class RuntimeRouter:
    """Route dynamic MCP/JSON requests to isolated real-browser runtimes."""

    TERMINAL_STATES = frozenset({"completed", "partial", "failed"})
    PROTOCOL_FAILURE_CODES = frozenset({
        "INVALID_REQUEST", "UNKNOWN_TOOL", "UNKNOWN_REFERENCE",
        "IDEMPOTENCY_CONFLICT", "STALE_STATE", "AUDIT_PROGRESS_REQUIRED",
    })

    def __init__(
        self,
        output_root: str | Path,
        *,
        max_runtimes: int = 4,
        lease_timeout_seconds: float = 300.0,
        max_protocol_failures: int = 6,
        max_repeated_failures: int = 3,
        max_repeated_successes: int = 3,
        profile: BrowserProfile | None = None,
        runtime_factory: RuntimeFactory | None = None,
        scan_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        start_monitor: bool = False,
    ) -> None:
        if type(max_runtimes) is not int or max_runtimes < 1:
            raise ValueError("max_runtimes must be a positive integer")
        if not isinstance(lease_timeout_seconds, (int, float)) or isinstance(lease_timeout_seconds, bool) or lease_timeout_seconds <= 0:
            raise ValueError("lease_timeout_seconds must be positive")
        for name, value in (
            ("max_protocol_failures", max_protocol_failures),
            ("max_repeated_failures", max_repeated_failures),
            ("max_repeated_successes", max_repeated_successes),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        root = Path(output_root).expanduser().resolve()
        existed = root.exists()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("output_root must be a directory")
        if not existed:
            os.chmod(root, 0o700)
        self._output_root = root
        self._max_runtimes = max_runtimes
        self._lease_timeout = float(lease_timeout_seconds)
        self._max_protocol_failures = max_protocol_failures
        self._max_repeated_failures = max_repeated_failures
        self._max_repeated_successes = max_repeated_successes
        self._profile = profile or BrowserProfile()
        self._profile.validate()
        self._runtime_factory = runtime_factory or self._default_runtime_factory
        self._scan_id_factory = scan_id_factory or (lambda: f"scan-{uuid.uuid4().hex}")
        self._clock = clock
        self._active: dict[str, _RuntimeBinding] = {}
        self._terminal: dict[str, _RuntimeBinding] = {}
        self._by_bootstrap: dict[str, _RuntimeBinding] = {}
        self._bootstrap_failures: dict[str, tuple[str, dict]] = {}
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._closed = False
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        if start_monitor:
            interval = min(max(self._lease_timeout / 4.0, 0.1), 1.0)
            self._monitor = threading.Thread(target=self._monitor_leases, args=(interval,),
                                             name="assayer-agent-lease", daemon=True)
            self._monitor.start()

    @property
    def output_root(self) -> Path:
        return self._output_root

    @property
    def active_scan_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def heartbeat_interval_seconds(self) -> float:
        """Interval for an owning transport to prove that its Agent connection is alive."""
        return min(max(self._lease_timeout / 3.0, 0.1), 30.0)

    def heartbeat(self) -> int:
        """Renew active leases from the transport's runtime-owning thread.

        A live MCP stdio connection is the liveness signal while the model is
        reasoning.  The transport invokes this method on the same dedicated
        executor that owns Playwright, so lease maintenance never crosses the
        browser runtime's thread boundary.
        """
        deadline = self._clock() + self._lease_timeout
        with self._lock:
            bindings = list(self._active.values())
        renewed = 0
        for binding in bindings:
            with binding.lock:
                if binding.runtime is None:
                    continue
                binding.lease_deadline = deadline
                self._emit_runtime_event(
                    binding, "lease.heartbeat", "succeeded",
                    "Live Agent transport renewed the Scan lease",
                )
                renewed += 1
        return renewed

    def build_completion_input(self, scan_id: str, run_id: str, completion_reason: str | None = None) -> dict:
        """Return a completion payload assembled from the active runtime ledger."""
        with self._lock:
            binding = self._active.get(scan_id) or self._terminal.get(scan_id)
        if binding is None or binding.run_id != run_id or binding.runtime is None:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist or has ended")
        builder = getattr(binding.runtime, "build_completion_input", None)
        if not callable(builder):
            raise HostError("INTERNAL_FAILURE", "The runtime does not support automatic coverage completion")
        with binding.lock:
            return builder(scan_id, run_id, completion_reason)

    def handle(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise HostError("INVALID_REQUEST", "JSON request must be an object")
        if request.get("tool") == "start_audit":
            return self._start_audit(request)
        self.sweep_expired()
        scan_id, run_id = request.get("scanId"), request.get("runId")
        if not isinstance(scan_id, str) or not isinstance(run_id, str):
            raise HostError("INVALID_REQUEST", "Session request must include scanId and runId")
        with self._lock:
            binding = self._active.get(scan_id) or self._terminal.get(scan_id)
        if binding is None or binding.run_id != run_id:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist")
        with binding.lock:
            if binding.runtime is None:
                digest = self._request_digest(request)
                if binding.terminal_request_digest == digest and binding.terminal_response is not None:
                    return copy.deepcopy(binding.terminal_response)
                raise HostError("RUN_TERMINAL", "Scan has reached a terminal state")
            if binding.progress_required and request.get("tool") != "get_audit_progress":
                exceeded = self._record_control_failure(
                    binding, request, "AUDIT_PROGRESS_REQUIRED",
                    "Persistent progress must be read before retrying a failed complete_audit",
                )
                if exceeded:
                    self._terminate_control_budget(binding, "Agent kept bypassing progress recovery after complete_audit failed")
                    raise HostError("AGENT_CONTROL_BUDGET_EXCEEDED", "Agent control budget exhausted; formal audit stopped")
                raise HostError(
                    "AUDIT_PROGRESS_REQUIRED",
                    "After complete_audit fails, call get_audit_progress first and correct the request from persistent progress",
                    next_step="call_get_audit_progress",
                )
            binding.lease_deadline = self._clock() + self._lease_timeout
            self._emit_runtime_event(binding, "lease.renewed", "succeeded", "Agent lease renewed")
            transport_started = self._clock()
            self._emit_component_event(binding, request, "transport.request.started", "start", "started", "Transport request entered Runtime Router")
            try:
                response = binding.runtime.handle(request)
            except HostError as error:
                self._emit_component_event(binding, request, "transport.request.finished", "finish", "failed", "Transport request failed before a Host response", duration_ms=max(0, int((self._clock() - transport_started) * 1000)))
                if request.get("tool") == "complete_audit":
                    binding.progress_required = True
                if self._record_control_failure(binding, request, error.code, error.message):
                    self._terminate_control_budget(binding, "Agent produced consecutive invalid or duplicate Host requests")
                    raise HostError("AGENT_CONTROL_BUDGET_EXCEEDED", "Agent control budget exhausted; formal audit stopped") from error
                raise
            except Exception:
                self._emit_component_event(binding, request, "transport.request.finished", "finish", "failed", "Transport request failed before a Host response", duration_ms=max(0, int((self._clock() - transport_started) * 1000)))
                raise
            self._emit_component_event(binding, request, "transport.request.finished", "finish", "succeeded" if response.get("status") == "ok" else ("rejected" if response.get("status") == "rejected" else "failed"), "Transport received the Host response", duration_ms=max(0, int((self._clock() - transport_started) * 1000)))
            self._refresh_binding(binding, response)
            if response.get("status") == "rejected":
                error = response.get("error") if isinstance(response.get("error"), dict) else {}
                code = error.get("code") if isinstance(error.get("code"), str) else "HOST_REJECTED"
                message = error.get("message") if isinstance(error.get("message"), str) else "Host rejected the request"
                if request.get("tool") == "complete_audit":
                    binding.progress_required = True
                if self._record_control_failure(binding, request, code, message):
                    self._terminate_control_budget(binding, "Agent produced consecutive invalid or duplicate Host requests")
                    raise HostError("AGENT_CONTROL_BUDGET_EXCEEDED", "Agent control budget exhausted; formal audit stopped")
            elif response.get("status") == "ok":
                if request.get("tool") == "get_audit_progress":
                    binding.progress_required = False
                if self._record_control_success(binding, request, response):
                    self._terminate_control_budget(binding, "Agent called the same tool repeatedly without progress")
                    raise HostError("AGENT_CONTROL_BUDGET_EXCEEDED", "Agent stagnation budget exhausted; formal audit stopped")
            state = binding.runtime.protocol_state()
            terminal = response.get("result", {}).get("scanStatus") if isinstance(response.get("result"), dict) else None
            if terminal not in self.TERMINAL_STATES and isinstance(state, dict):
                terminal = state.get("scanStatus")
            if terminal in self.TERMINAL_STATES:
                self._emit_runtime_event(binding, "lease.released", "succeeded", f"Agent lease released at Scan terminal state: {terminal}")
                refresher = getattr(binding.runtime, "refresh_observability", None)
                if callable(refresher):
                    refresher()
                self._terminalize(binding, terminal, request, response)
            else:
                binding.lease_deadline = self._clock() + self._lease_timeout
            return response

    def _start_audit(self, request: dict) -> dict:
        key = request.get("idempotencyKey")
        if not isinstance(key, str) or not key:
            raise HostError("INVALID_REQUEST", "Bootstrap request is missing idempotencyKey")
        digest = self._request_digest(request)
        with self._start_lock:
            with self._lock:
                self._ensure_open()
                failure = self._bootstrap_failures.get(key)
                existing = self._by_bootstrap.get(key)
                at_capacity = len(self._active) >= self._max_runtimes
            if failure is not None:
                if failure[0] != digest:
                    raise HostError("IDEMPOTENCY_CONFLICT", "Bootstrap idempotency key maps to a different request digest")
                return copy.deepcopy(failure[1])
            if existing is not None:
                if existing.bootstrap_digest != digest:
                    raise HostError("IDEMPOTENCY_CONFLICT", "Bootstrap idempotency key maps to a different request digest")
                with existing.lock:
                    if existing.runtime is None:
                        return copy.deepcopy(existing.bootstrap_response)
                    rewritten = self._rewrite_bootstrap(request, Path(existing.bootstrap_request["input"]["outputDir"]))
                    response = existing.runtime.handle(rewritten)
                    self._refresh_binding(existing, response)
                    existing.bootstrap_response = copy.deepcopy(response)
                    return response
            if at_capacity:
                raise HostError("RUN_CAPACITY_EXCEEDED", "Concurrent Scan limit reached", retryable=True,
                                next_step="retry_after_capacity_is_released")
            input_data = request.get("input")
            if not isinstance(input_data, dict):
                raise HostError("INVALID_REQUEST", "start_audit input must be an object")
            if input_data.get("outputDir") != "auto":
                raise HostError("OUTPUT_PATH_REJECTED", "Dynamic Runtime accepts outputDir=auto only")
            if input_data.get("browserProfile") != "default":
                raise HostError("INVALID_REQUEST", "Dynamic Runtime accepts browserProfile=default only")
            if input_data.get("authMode") != "anonymous" or "credentialHandle" in input_data:
                raise HostError("INVALID_REQUEST", "Dynamic real-browser Runtime currently accepts anonymous mode only")
            url = input_data.get("url")
            if not isinstance(url, str) or not url:
                raise HostError("INVALID_REQUEST", "start_audit URL is invalid")
            scan_id = self._scan_id_factory()
            if not isinstance(scan_id, str) or not scan_id.startswith("scan-"):
                raise RuntimeError("scan_id_factory returned an invalid Scan ID")
            output_dir = (self._output_root / scan_id).resolve()
            if output_dir.parent != self._output_root:
                raise RuntimeError("generated Scan output escaped output_root")
            output_dir.mkdir(mode=0o700, exist_ok=False)
            runtime = None
            try:
                runtime = self._runtime_factory(url, output_dir, scan_id)
                rewritten = self._rewrite_bootstrap(request, output_dir)
                response = runtime.handle(rewritten)
            except HostError:
                if runtime is not None:
                    runtime.close()
                self._remove_empty_output(output_dir)
                raise
            except (OSError, RuntimeError, ValueError) as error:
                if runtime is not None:
                    runtime.close()
                self._remove_empty_output(output_dir)
                raise HostError("RUNTIME_START_FAILED", "Real-browser Runtime failed to start") from error
            if response.get("status") != "ok" or not isinstance(response.get("result"), dict):
                runtime.close()
                with self._lock:
                    self._bootstrap_failures[key] = (digest, copy.deepcopy(response))
                self._remove_empty_output(output_dir)
                return response
            result = response["result"]
            if result.get("scanId") != scan_id or not isinstance(result.get("runId"), str):
                runtime.close()
                self._remove_empty_output(output_dir)
                raise HostError("INTERNAL_FAILURE", "Runtime returned an invalid Scan/Run binding")
            binding = _RuntimeBinding(
                scan_id=scan_id,
                run_id=result["runId"],
                runtime=runtime,
                bootstrap_key=key,
                bootstrap_digest=digest,
                bootstrap_request=rewritten,
                bootstrap_response=copy.deepcopy(response),
                run_revision=int(response.get("runRevision", result.get("runRevision", 0))),
                scan_status="exploring",
                lease_deadline=self._clock() + self._lease_timeout,
            )
            with self._lock:
                self._active[scan_id] = binding
                self._by_bootstrap[key] = binding
            self._emit_runtime_event(binding, "lease.started", "started", "Agent lease supervision started")
            return response

    def protocol_state(self, scan_id: str, run_id: str) -> dict | None:
        with self._lock:
            binding = self._active.get(scan_id) or self._terminal.get(scan_id)
        if binding is None or binding.run_id != run_id:
            return None
        return {"scanId": scan_id, "runId": run_id, "runRevision": binding.run_revision,
                "scanStatus": binding.scan_status}

    def read_screenshot(self, scan_id: str, run_id: str, screenshot_ref: str) -> tuple[bytes, str]:
        with self._lock:
            binding = self._active.get(scan_id) or self._terminal.get(scan_id)
        if binding is None or binding.run_id != run_id:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist")
        with binding.lock:
            if binding.runtime is None:
                raise HostError("RUN_TERMINAL", "Scan has reached a terminal state")
            return binding.runtime.read_screenshot(scan_id, run_id, screenshot_ref)

    def sweep_expired(self) -> int:
        now = self._clock()
        with self._lock:
            candidates = [binding for binding in self._active.values() if binding.lease_deadline <= now]
        expired = 0
        for binding in candidates:
            if not binding.lock.acquire(blocking=False):
                continue
            try:
                if binding.runtime is not None and binding.lease_deadline <= self._clock():
                    self._fail_binding(binding, "AGENT_LEASE_EXPIRED", "Agent lease expired; formal audit failed")
                    expired += 1
            finally:
                binding.lock.release()
        return expired

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=2.0)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active.values())
        for binding in active:
            with binding.lock:
                if binding.runtime is not None:
                    self._fail_binding(binding, "AGENT_RUNTIME_EXITED", "Agent Runtime exited before the formal audit completed")

    def _record_control_failure(
        self,
        binding: _RuntimeBinding,
        request: dict,
        code: str,
        message: str,
    ) -> bool:
        signature = f"{request.get('tool', 'unknown')}:{code}"
        if signature == binding.last_failure_signature:
            binding.consecutive_failure_count += 1
        else:
            binding.last_failure_signature = signature
            binding.consecutive_failure_count = 1
        if code in self.PROTOCOL_FAILURE_CODES:
            binding.protocol_failure_count += 1
        binding.last_success_signature = None
        binding.consecutive_success_count = 0
        self._emit_control_event(
            binding,
            request,
            "agent.control.failure",
            "rejected",
            "Agent request did not advance the audit",
            {
                "errorCode": code,
                "errorMessage": message[:512],
                "protocolFailureCount": binding.protocol_failure_count,
                "repeatedFailureCount": binding.consecutive_failure_count,
            },
        )
        return (
            binding.protocol_failure_count >= self._max_protocol_failures
            or binding.consecutive_failure_count > self._max_repeated_failures
        )

    def _record_control_success(
        self,
        binding: _RuntimeBinding,
        request: dict,
        response: dict,
    ) -> bool:
        result = copy.deepcopy(response.get("result"))
        if isinstance(result, dict):
            result.pop("operationId", None)
            result.pop("runRevision", None)
        signature = self._request_digest(request) + ":" + self._stable_digest({"result": result})
        if signature == binding.last_success_signature:
            binding.consecutive_success_count += 1
        else:
            binding.last_success_signature = signature
            binding.consecutive_success_count = 1
        binding.protocol_failure_count = 0
        binding.last_failure_signature = None
        binding.consecutive_failure_count = 0
        if binding.consecutive_success_count > self._max_repeated_successes:
            self._emit_control_event(
                binding,
                request,
                "agent.control.stalled",
                "blocked",
                "Repeated successful requests produced no new progress",
                {"repeatedSuccessCount": binding.consecutive_success_count},
            )
            return True
        return False

    def _terminate_control_budget(self, binding: _RuntimeBinding, message: str) -> None:
        self._emit_control_event(
            binding,
            {},
            "agent.control.budget_exhausted",
            "failed",
            message,
            {
                "protocolFailureCount": binding.protocol_failure_count,
                "repeatedFailureCount": binding.consecutive_failure_count,
                "repeatedSuccessCount": binding.consecutive_success_count,
            },
        )
        self._fail_binding(binding, "AGENT_CONTROL_BUDGET_EXCEEDED", message)

    @staticmethod
    def _emit_control_event(
        binding: _RuntimeBinding,
        request: dict,
        name: str,
        outcome: str,
        summary: str,
        attributes: dict,
    ) -> None:
        runtime = binding.runtime
        recorder = getattr(runtime, "record_runtime_event", None) if runtime is not None else None
        if not callable(recorder):
            return
        correlation = {
            key: request[key]
            for key in ("agentTurnId", "requestId")
            if isinstance(request.get(key), str)
        }
        event = {
            "scanId": binding.scan_id,
            "runId": binding.run_id,
            "source": "router",
            "category": "decision",
            "name": name,
            "phase": "instant",
            "severity": "warning" if outcome in {"rejected", "blocked"} else "error",
            "outcome": outcome,
            "summary": summary,
            "correlation": correlation,
            "privacy": {"classification": "internal", "sanitizationStatus": "not_required"},
            "attributes": attributes,
        }
        try:
            recorder(event)
        except Exception:
            return

    def _fail_binding(self, binding: _RuntimeBinding, code: str, message: str) -> None:
        runtime = binding.runtime
        if runtime is None:
            return
        if code == "AGENT_LEASE_EXPIRED":
            self._emit_runtime_event(binding, "lease.expired", "failed", message, attributes={"errorCode": code})
        elif code == "AGENT_RUNTIME_EXITED":
            self._emit_runtime_event(binding, "lease.lost", "failed", message, attributes={"errorCode": code})
        failure = runtime.fail_scan(code, message)
        self._emit_runtime_event(binding, "supervisor.terminal", "failed", "Supervisor terminated abandoned Scan", attributes={"errorCode": code}, category="lifecycle")
        refresher = getattr(runtime, "refresh_observability", None)
        if callable(refresher):
            refresher()
        binding.run_revision = int(failure["runRevision"])
        binding.scan_status = "failed"
        response = {
            "protocolVersion": "1.0", "requestId": "runtime-supervisor",
            "scanId": binding.scan_id, "runId": binding.run_id,
            "runRevision": binding.run_revision, "status": "failed",
            "error": {"code": code, "message": message, "retryable": False, "requiredNextStep": "stop"},
            "evidenceRefs": [], "diagnosticRefs": [],
        }
        self._terminalize(binding, "failed", None, response)

    def _terminalize(self, binding: _RuntimeBinding, state: str, request: dict | None, response: dict) -> None:
        runtime = binding.runtime
        binding.scan_status = state
        binding.run_revision = int(response.get("runRevision", binding.run_revision))
        binding.terminal_response = copy.deepcopy(response)
        binding.terminal_request_digest = self._request_digest(request) if request is not None else None
        binding.runtime = None
        with self._lock:
            self._active.pop(binding.scan_id, None)
            self._terminal[binding.scan_id] = binding
        if runtime is not None:
            runtime.close()

    def _refresh_binding(self, binding: _RuntimeBinding, response: dict) -> None:
        revision = response.get("runRevision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            binding.run_revision = revision

    @staticmethod
    def _emit_runtime_event(binding: _RuntimeBinding, name: str, outcome: str, summary: str, *, attributes: dict | None = None, category: str = "lease") -> None:
        runtime = binding.runtime
        recorder = getattr(runtime, "record_runtime_event", None) if runtime is not None else None
        if not callable(recorder):
            return
        try:
            recorder({"scanId": binding.scan_id, "runId": binding.run_id, "source": "router", "category": category, "name": name, "phase": "instant", "severity": "info" if outcome in {"started", "succeeded"} else "error", "outcome": outcome, "summary": summary, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": attributes or {}})
        except Exception:
            return

    @staticmethod
    def _emit_component_event(binding: _RuntimeBinding, request: dict, name: str, phase: str, outcome: str, summary: str, *, duration_ms: int | None = None) -> None:
        runtime = binding.runtime
        recorder = getattr(runtime, "record_runtime_event", None) if runtime is not None else None
        if not callable(recorder):
            return
        correlation = {key: request[key] for key in ("agentTurnId", "requestId") if isinstance(request.get(key), str)}
        event = {"scanId": binding.scan_id, "runId": binding.run_id, "source": "transport", "category": "network", "name": name, "phase": phase, "severity": "info" if outcome in {"started", "succeeded"} else "error", "outcome": outcome, "summary": summary, "correlation": correlation, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": {"tool": str(request.get("tool", "unknown"))}}
        if duration_ms is not None:
            event["durationMs"] = duration_ms
        try:
            recorder(event)
        except Exception:
            return

    def _default_runtime_factory(self, url: str, output_dir: Path, scan_id: str) -> RoutedRuntime:
        from .browser_runtime import BrowserHostRuntime
        return BrowserHostRuntime(url, output_dir, profile=self._profile, scan_id=scan_id)

    def _monitor_leases(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.sweep_expired()
            except Exception:
                # A later request/close still retries terminal cleanup.  The
                # monitor never exposes internal exception text to clients.
                continue

    def _ensure_open(self) -> None:
        if self._closed:
            raise HostError("RUNTIME_CLOSED", "Runtime Router is closed")

    @staticmethod
    def _rewrite_bootstrap(request: dict, output_dir: Path) -> dict:
        rewritten = copy.deepcopy(request)
        rewritten["input"]["outputDir"] = str(output_dir)
        return rewritten

    @staticmethod
    def _request_digest(request: dict | None) -> str:
        if request is None:
            return ""
        material = {"tool": request.get("tool"), "input": request.get("input")}
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_digest(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _remove_empty_output(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            return
