"""Host-owned, task-local Evidence handle registry.

Agents see only opaque handles.  The registry keeps the platform identifiers
and lineage private to the Host and resolves a handle only within its issuing
semantic task.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
import re
import hashlib

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

    @classmethod
    def from_bindings(
        cls,
        task_key: str,
        bindings: Sequence[tuple[str, str]],
        packets: Sequence[InvestigationPacket],
        *,
        content_by_reference: Mapping[str, Any] | None = None,
    ) -> "EvidenceHandleRegistry":
        """Bind Host-selected task refs to frozen packet Evidence lineage.

        This is used by incremental common review, whose current batch may
        expose only a small subset of the Run's Evidence and source chunks.
        """
        known: dict[str, tuple[str, str | None]] = {}
        for packet in packets:
            for evidence in packet.evidence:
                known[evidence.evidence_id] = (evidence.evidence_id, None)
                payload = evidence.payload if isinstance(evidence.payload, Mapping) else {}
                chunks = payload.get("sourceChunks", ())
                if not isinstance(chunks, (tuple, list)):
                    continue
                for chunk in chunks:
                    if not isinstance(chunk, Mapping):
                        continue
                    chunk_id = str(chunk.get("source_chunk_id") or "").strip()
                    if chunk_id:
                        known[chunk_id] = (evidence.evidence_id, chunk_id)
        content = content_by_reference or {}
        handles: dict[str, EvidenceHandle] = {}
        internal_refs: list[str] = []
        for binding in bindings:
            if not isinstance(binding, (tuple, list)) or len(binding) != 2:
                raise PlatformContractError(
                    "INVALID_REVIEW_BINDING", "Common Evidence binding is malformed",
                )
            handle, internal_ref = binding
            if not isinstance(handle, str) or re.fullmatch(r"R[1-9][0-9]*", handle) is None:
                raise PlatformContractError(
                    "INVALID_REVIEW_BINDING", "Common Evidence handle is malformed",
                )
            if not isinstance(internal_ref, str) or not internal_ref:
                raise PlatformContractError(
                    "INVALID_REVIEW_BINDING", "Common Evidence identity is malformed",
                )
            lineage = known.get(internal_ref)
            if lineage is None:
                raise PlatformContractError(
                    "INVALID_REVIEW_BINDING",
                    "Common Evidence binding is outside the frozen InvestigationPacket",
                )
            if handle in handles:
                raise PlatformContractError(
                    "INVALID_REVIEW_BINDING", "Common Evidence handles must be unique",
                )
            internal_refs.append(internal_ref)
            evidence_id, chunk_id = lineage
            handles[handle] = EvidenceHandle(
                handle=handle,
                task_key=task_key,
                evidence_id=evidence_id,
                source_chunk_id=chunk_id,
                content=content.get(internal_ref),
            )
        if len(internal_refs) != len(set(internal_refs)):
            raise PlatformContractError(
                "INVALID_REVIEW_BINDING", "Common Evidence identities must be unique",
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

    def page(self, *, cursor: str | None = None, page_size: int = 20) -> dict[str, Any]:
        """Return a bounded page of task-local evidence for Host expansion.

        The cursor is derived from this registry's immutable handle set and
        never exposes WorkItem, Evidence, or source identities.  This keeps
        pagination in the platform/SDK while plugins only provide evidence.
        """
        if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
            raise PlatformContractError(
                "INVALID_PAGE_SIZE", "Semantic evidence page size must be a positive integer",
            )
        size = min(page_size, 100)
        handles = self.handles
        fingerprint = hashlib.sha256(
            "\x1f".join(handles).encode("utf-8"),
        ).hexdigest()[:16]
        start = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor.startswith(f"{fingerprint}:"):
                raise PlatformContractError(
                    "INVALID_CURSOR", "Semantic evidence cursor does not match the active task",
                )
            try:
                start = int(cursor.split(":", 1)[1])
            except (TypeError, ValueError):
                raise PlatformContractError(
                    "INVALID_CURSOR", "Semantic evidence cursor is malformed",
                ) from None
            if start < 0 or start > len(handles):
                raise PlatformContractError(
                    "INVALID_CURSOR", "Semantic evidence cursor is outside the active task",
                )
        selected = handles[start:start + size]
        end = start + len(selected)
        return {
            "items": [self.resolve(handle).as_public() for handle in selected],
            "itemIds": list(selected),
            "page": {"start": start, "count": len(selected), "total": len(handles)},
            "nextCursor": f"{fingerprint}:{end}" if end < len(handles) else None,
        }


__all__ = ["EvidenceHandle", "EvidenceHandleRegistry"]
