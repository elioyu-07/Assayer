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
    source_bounding_box: dict | None = None
    annotation: str | None = None
    reason: str | None = None
    sanitized: bool = True
    # Image sanitization is intentionally distinct from structured Evidence
    # sanitization.  B07b can capture controlled real-browser pixels before
    # B07c's automatic masking exists; that state must remain explicit.
    sanitization_status: str = "sanitized"  # sanitized, not_performed, failed


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


def is_page_observation(evidence: dict) -> bool:
    """Whether persisted Evidence includes the aligned viewport/list observation."""
    if not isinstance(evidence, dict) or evidence.get("kind") != "runtime_visual":
        return False
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        return False
    content = payload.get("content")
    return (
        isinstance(content, dict)
        and content.get("observationScope") == "viewport"
        and isinstance(content.get("logicalLists"), list)
    )


class EvidenceAdapter(Protocol):
    def capture(self, page_state: dict, target: dict, case: dict | None, include_raw_visual: bool) -> EvidenceCapture: ...
    def observe_page(self, page_state: dict, target: dict, case: dict | None) -> EvidenceCapture: ...


class UnavailableEvidenceAdapter:
    """Default adapter: structured evidence may be captured from Host snapshots, visuals remain unavailable."""
    def capture(self, page_state, target, case, include_raw_visual):
        if include_raw_visual:
            return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={"status": "unavailable"},
                                   raw_visual=RawVisualCapture(status="rejected", reason="Screenshot adapter is not configured"))
        return EvidenceCapture(kind="runtime_dom", payload_type="json",
                               payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"], "identity": target.get("identity", {}), "location": target.get("location", {})})

    def observe_page(self, page_state, target, case):
        return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata",
                               payload={"status": "unavailable"},
                               raw_visual=RawVisualCapture(status="rejected", reason="Page observation adapter is not configured"))


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
            raw = RawVisualCapture(image_bytes=b"\x89PNG\r\n\x1a\nassayer-deterministic-v1", width=location.get("viewportWidth", max(1, int(bbox["width"]))), height=location.get("viewportHeight", max(1, int(bbox["height"]))), bounding_box=bbox, annotation="Host located object region")
        return EvidenceCapture(kind="runtime_visual" if include_raw_visual else "runtime_dom",
                               payload_type="image_metadata" if include_raw_visual else "json",
                               payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"], "stateKind": page_state.get("stateKind"), "identityFingerprint": target.get("identity", {}).get("fingerprint")},
                               raw_visual=raw)

    def observe_page(self, page_state, target, case):
        bbox = target.get("location", {}).get("boundingBox") or {"x": 0, "y": 0, "width": 1, "height": 1}
        location = target.get("location", {})
        width = location.get("viewportWidth", max(1, int(bbox["x"] + bbox["width"])))
        height = location.get("viewportHeight", max(1, int(bbox["y"] + bbox["height"])))
        raw = RawVisualCapture(image_bytes=b"\x89PNG\r\n\x1a\nassayer-deterministic-v1", width=width, height=height,
                               bounding_box=bbox, source_bounding_box=bbox,
                               annotation="Host viewport and verified object region")
        return EvidenceCapture(kind="runtime_visual", payload_type="image_metadata",
                               payload={"objectId": target["objectId"], "pageStateId": page_state["pageStateId"],
                                        "observationScope": "viewport", "logicalLists": [], "relations": []},
                               raw_visual=raw,
                               source_binding={"status": "verified", "bindingReason": "Host deterministic page observation matches the object reference"})


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
        raise ValueError("Evidence payload contains a type that cannot be normalized safely")
