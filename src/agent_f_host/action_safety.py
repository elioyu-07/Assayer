from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Callable
from urllib.parse import urlparse


@dataclass(frozen=True)
class NetworkRequest:
    method: str
    url: str
    origin: str | None = None
    resource_type: str = "fetch"
    content_type: str | None = None
    graphql_operation_type: str | None = None
    transport: str = "http"
    attributable: bool = True
    sent: bool = False


@dataclass(frozen=True)
class RequestDecision:
    outcome: str  # allowed, blocked, unknown, already_sent
    code: str
    reason: str


@dataclass(frozen=True)
class ActionExecution:
    status: str = "succeeded"  # succeeded, request_blocked, result_unknown, persistent_write_observed
    after_page_state_id: str | None = None
    requests: tuple[NetworkRequest, ...] = ()
    diagnostic: str | None = None
    local_state_changed: bool = False


class SafeActionAdapter(Protocol):
    def execute(self, action: dict, target: dict, page_state: dict, intercept: Callable[[NetworkRequest], RequestDecision]) -> ActionExecution: ...


class UnavailableActionAdapter:
    """Production default: actions cannot run before a browser adapter is explicitly configured."""
    def execute(self, action, target, page_state, intercept):
        return ActionExecution(status="unavailable", diagnostic="浏览器动作适配器未配置")


class DeterministicActionAdapter:
    """Deterministic adapter for contract tests; no network request is emitted by default."""
    def __init__(self, execution: ActionExecution | None = None):
        self.execution = execution or ActionExecution(status="succeeded")
        self.calls = 0

    def execute(self, action, target, page_state, intercept):
        self.calls += 1
        decisions = []
        for request in self.execution.requests:
            decisions.append(intercept(request))
            if decisions[-1].outcome == "blocked":
                return ActionExecution(status="request_blocked", after_page_state_id=self.execution.after_page_state_id,
                                        requests=self.execution.requests, diagnostic=decisions[-1].reason, local_state_changed=True)
            if decisions[-1].outcome == "already_sent":
                return ActionExecution(status="persistent_write_observed", after_page_state_id=self.execution.after_page_state_id,
                                        requests=self.execution.requests, diagnostic=decisions[-1].reason)
            if decisions[-1].outcome == "unknown":
                return ActionExecution(status="result_unknown", after_page_state_id=self.execution.after_page_state_id,
                                        requests=self.execution.requests, diagnostic=decisions[-1].reason)
        return self.execution


class ActionSafetyPolicy:
    """Mechanical action and network policy. Unknown input is rejected by default."""
    OBSERVATION = {"scroll", "focus"}
    REVERSIBLE = {"expand", "collapse", "switch_tab", "open_detail", "close_detail", "open_edit", "close_overlay", "refresh"}
    SYNTHETIC = {"input_synthetic_value", "restore_value"}
    WRITE_WORDS = ("save", "submit", "delete", "remove", "approve", "publish", "upload", "import", "cancel", "unbind", "作废", "删除", "保存", "提交", "审批", "发布", "上传", "导入")

    def action_decision(self, action_type: str, intent: str, parameters: dict | None = None) -> RequestDecision:
        if action_type not in self.OBSERVATION | self.REVERSIBLE | self.SYNTHETIC:
            return RequestDecision("blocked", "ACTION_BLOCKED", "动作类型不在 Host 安全白名单")
        lowered = (intent or "").lower()
        if any(word in lowered for word in self.WRITE_WORDS):
            return RequestDecision("blocked", "ACTION_BLOCKED", "动作意图包含潜在持久化写操作")
        if self._contains_forbidden_parameter(parameters or {}):
            return RequestDecision("blocked", "ACTION_BLOCKED", "动作参数包含 selector、脚本或请求秘密材料")
        return RequestDecision("allowed", "ACTION_ALLOWED", "动作类型和意图通过安全门禁")

    @classmethod
    def _contains_forbidden_parameter(cls, value) -> bool:
        forbidden = {"selector", "script", "javascript", "function", "headers", "cookie", "token", "requestbody"}
        if isinstance(value, dict):
            return any(str(key).replace("_", "").lower() in forbidden or cls._contains_forbidden_parameter(item)
                       for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_forbidden_parameter(item) for item in value)
        return False

    def classify_request(self, request: NetworkRequest, page_origin: str) -> RequestDecision:
        transport = request.transport.lower()
        method = request.method.upper()
        parsed = urlparse(request.url)
        url_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        declared_origin = request.origin or url_origin
        cross_origin = bool(declared_origin and page_origin and declared_origin != page_origin)
        origin_mismatch = bool(request.origin and url_origin and request.origin != url_origin)
        active_transport = transport in {"beacon", "websocket", "sse", "service_worker"}
        body_write = bool(request.content_type and ("multipart" in request.content_type.lower() or "form-data" in request.content_type.lower()))
        mutation = bool(request.graphql_operation_type and request.graphql_operation_type.lower() == "mutation")
        if request.sent:
            if method not in {"GET", "HEAD", "OPTIONS"} or mutation or body_write or active_transport or cross_origin or origin_mismatch:
                return RequestDecision("already_sent", "REQUEST_RESULT_UNKNOWN", "潜在写请求在 Host 拦截器接管前已发送")
            return RequestDecision("unknown", "REQUEST_RESULT_UNKNOWN", "只读形态请求已发送，但无法证明无副作用")
        if transport == "service_worker" and not request.attributable:
            return RequestDecision("unknown", "REQUEST_RESULT_UNKNOWN", "Service Worker 请求无法归因到当前动作")
        if active_transport:
            return RequestDecision("blocked", "REQUEST_BLOCKED", f"传输类型 {transport} 默认禁止")
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return RequestDecision("blocked", "REQUEST_BLOCKED", f"HTTP {method} 默认禁止")
        if mutation:
            return RequestDecision("blocked", "REQUEST_BLOCKED", "GraphQL mutation 默认禁止")
        if body_write:
            return RequestDecision("blocked", "REQUEST_BLOCKED", "multipart/form-data 请求禁止")
        if origin_mismatch:
            return RequestDecision("blocked", "CROSS_ORIGIN_BLOCKED", "请求声明 origin 与 URL origin 不一致")
        if cross_origin:
            return RequestDecision("blocked", "CROSS_ORIGIN_BLOCKED", "跨 origin 业务请求禁止")
        if not request.attributable:
            return RequestDecision("unknown", "REQUEST_RESULT_UNKNOWN", "请求无法归因到当前动作")
        if method not in {"GET", "HEAD", "OPTIONS"}:
            return RequestDecision("blocked", "REQUEST_BLOCKED", "无法分类的 HTTP 方法默认禁止")
        return RequestDecision("allowed", "REQUEST_ALLOWED", "同源只读请求允许")
