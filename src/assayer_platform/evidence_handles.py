"""Host-owned, task-local Evidence handle registry.

Agents see only opaque handles.  The registry keeps the platform identifiers
and lineage private to the Host and resolves a handle only within its issuing
semantic task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
import re

from .contract import InvestigationPacket, PlatformContractError


@dataclass(frozen=True)
class EvidenceHandle:
    handle: str
    task_key: str
    evidence_id: str
    source_chunk_id: str | None = None
    content: Any = None

    def as_public(self, content: Any = None) -> dict[str, Any]:
        value: dict[str, Any] = {"evidenceRef": self.handle}
        if content is None:
            content = self.content
        if content is not None:
            value["content"] = content
        return value


class EvidenceHandleRegistry:
    """Resolve opaque handles to immutable Host-owned Evidence lineage."""

    def __init__(self, task_key: str, handles: Mapping[str, EvidenceHandle]):
        if not task_key:
            raise ValueError("task_key must be non-empty")
        self.task_key = task_key
        self._handles = MappingProxyType(dict(handles))

    @classmethod
    def from_packet(
        cls, task_key: str, packet: InvestigationPacket, *, start_ordinal: int = 0,
    ) -> "EvidenceHandleRegistry":
        if start_ordinal < 0:
            raise ValueError("start_ordinal must be non-negative")
        handles: dict[str, EvidenceHandle] = {}
        ordinal = start_ordinal
        for evidence in packet.evidence:
            payload = evidence.payload if isinstance(evidence.payload, Mapping) else {}
            chunks = payload.get("sourceChunks", ())
            if isinstance(chunks, (tuple, list)) and chunks:
                for chunk in chunks:
                    if not isinstance(chunk, Mapping):
                        continue
                    source_chunk_id = str(chunk.get("source_chunk_id") or "").strip()
                    if not source_chunk_id:
                        continue
                    ordinal += 1
                    handle = f"R{ordinal}"
                    handles[handle] = EvidenceHandle(
                        handle=handle,
                        task_key=task_key,
                        evidence_id=evidence.evidence_id,
                        source_chunk_id=source_chunk_id,
                        content=chunk.get("excerpt") or chunk.get("content"),
                    )
            else:
                ordinal += 1
                handle = f"R{ordinal}"
                handles[handle] = EvidenceHandle(
                    handle=handle,
                    task_key=task_key,
                    evidence_id=evidence.evidence_id,
                )
        return cls(task_key, handles)

    @property
    def handles(self) -> tuple[str, ...]:
        return tuple(self._handles)

    def handle_for_reference(self, reference: str) -> str | None:
        """Return the task-local handle for an internal Evidence reference."""
        for handle, value in self._handles.items():
            if reference in {value.evidence_id, value.source_chunk_id}:
                return handle
        return None

    def resolve(self, handle: str, *, task_key: str | None = None) -> EvidenceHandle:
        if task_key is not None and task_key != self.task_key:
            raise PlatformContractError(
                "CROSS_TASK_EVIDENCE_HANDLE",
                "Evidence handle belongs to a different semantic task",
            )
        value = self._handles.get(str(handle))
        if value is None:
            match = re.fullmatch(r"R([1-9][0-9]*)", str(handle))
            current_ordinals = [
                int(item[1:]) for item in self._handles if item.startswith("R")
            ]
            if match is not None and current_ordinals and int(match.group(1)) < min(current_ordinals):
                raise PlatformContractError(
                    "STALE_EVIDENCE_HANDLE",
                    f"Evidence handle belongs to an earlier semantic task: {handle}",
                )
            raise PlatformContractError(
                "UNKNOWN_EVIDENCE_HANDLE",
                f"Unknown Evidence handle: {handle}",
            )
        return value


__all__ = ["EvidenceHandle", "EvidenceHandleRegistry"]
