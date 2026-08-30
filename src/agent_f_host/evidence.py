from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True)
class RawVisualCapture:
    status: str = "captured"  # captured, not_located, ambiguous, rejected
    image_bytes: bytes = b""
    image_type: str = "png"
    width: int = 1
    height: int = 1
    bounding_box: dict | None = None
    annotation: str | None = None
    reason: str | None = None
    sanitized: bool = True


@dataclass(frozen=True)
class EvidenceCapture:
    kind: str = "runtime_dom"
    payload_type: str = "json"
    payload: object = None
    raw_visual: RawVisualCapture | None = None
    source_binding: dict | None = None
    collector_version: str = "1.0.0"
    sanitization_policy_version: str = "1.0.0"
    normalization_algorithm_version: str = "1.0.0"


class EvidenceAdapter(Protocol):
    def capture(self, page_state: dict, target: dict, case: dict | None, include_raw_visual: bool) -> EvidenceCapture: ...


class UnavailableEvidenceAdapter:
    """Default adapter: structured evidence may be captured from Host snapshots, visuals remain unavailable."""
    def capture(self, page_state, target, case, include_raw_visual):
        if include_raw_visual:
            return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={"status": "unavailable"},
                                   raw_visual=RawVisualCapture(status="rejected", reason="截图适配器未配置"))
        return EvidenceCapture(kind="runtime_dom", payload_type="json",
                               payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"], "identity": target.get("identity", {}), "location": target.get("location", {})})


class DeterministicEvidenceAdapter:
    """Deterministic adapter for contract tests and snapshot-only evidence."""
    def __init__(self, capture: EvidenceCapture | None = None):
        self.capture_result = capture
        self.calls = 0

    def capture(self, page_state, target, case, include_raw_visual):
        self.calls += 1
        if self.capture_result is not None:
            return self.capture_result
        raw = None
        if include_raw_visual:
            bbox = target.get("location", {}).get("boundingBox") or {"x": 0, "y": 0, "width": 1, "height": 1}
            location = target.get("location", {})
            raw = RawVisualCapture(image_bytes=b"\x89PNG\r\n\x1a\nagent-f-deterministic-v1", width=location.get("viewportWidth", max(1, int(bbox["width"]))), height=location.get("viewportHeight", max(1, int(bbox["height"]))), bounding_box=bbox, annotation="Host 定位的对象区域")
        return EvidenceCapture(kind="runtime_visual" if include_raw_visual else "runtime_dom",
                               payload_type="image_metadata" if include_raw_visual else "json",
                               payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"], "stateKind": page_state.get("stateKind"), "identityFingerprint": target.get("identity", {}).get("fingerprint")},
                               raw_visual=raw)


class EvidenceSanitizer:
    """Small structural sanitizer. Unsupported payloads fail closed."""
    POLICY_VERSION = "1.0.0"
    SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "password", "passwd", "token", "secret", "credential", "session"}

    def sanitize(self, value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                name = str(key)
                normalized = name.lower().replace("_", "").replace("-", "")
                sensitive = name.lower() in self.SENSITIVE_KEYS or any(token.replace("-", "") in normalized for token in self.SENSITIVE_KEYS)
                result[name] = "[REDACTED]" if sensitive else self.sanitize(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
            value = re.sub(r"(?i)(password|passwd|token|secret)=([^&\s]+)", r"\1=[REDACTED]", value)
            value = re.sub(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", "[REDACTED_EMAIL]", value)
            value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[REDACTED_PHONE]", value)
            value = re.sub(r"(?<!\d)\d{8,19}(?!\d)", "[REDACTED_NUMBER]", value)
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise ValueError("证据 payload 包含不可安全规范化的类型")
