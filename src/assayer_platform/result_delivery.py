"""Generic summary-first delivery for potentially large terminal results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contract import PlatformContractError


DEFAULT_RESULT_PAGE_SIZE = 10
MAX_RESULT_PAGE_SIZE = 50
MAX_INLINE_TEXT_BYTES = 4096
MAX_RESULT_PAGE_BYTES = 65536


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def _text_chunks(value: str, limit: int = MAX_INLINE_TEXT_BYTES) -> tuple[str, ...]:
    """Split text without breaking Unicode characters or losing content."""
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        size = len(character.encode("utf-8"))
        if current and current_bytes + size > limit:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += size
    if current:
        chunks.append("".join(current))
    return tuple(chunks)


@dataclass(frozen=True)
class _ResultSection:
    section_id: str
    kind: str
    items: tuple[Any, ...]


class StagedResultDocument:
    """Recursively externalize result arrays and long text behind stable pages."""

    def __init__(self, value: Mapping[str, Any], *, source_digest: str | None = None):
        source = _plain(value)
        canonical = json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        self.digest = source_digest or hashlib.sha256(canonical).hexdigest()
        self._sections: dict[str, _ResultSection] = {}
        self._section_by_content: dict[tuple[str, str], str] = {}
        self._sequence = 0
        self.overview = self._stage(source)

    @property
    def section_count(self) -> int:
        return len(self._sections)

    def descriptor(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "mode": "summary_first",
            "sourceDigest": self.digest,
            "detailSectionCount": self.section_count,
            "detailsAvailable": bool(self._sections),
            "pageBudgetBytes": MAX_RESULT_PAGE_BYTES,
            "nextAction": "Call get_plugin_result with a sectionId from the summary when detail is needed.",
        }

    def page(
        self, section_id: str, *, cursor: str | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        section = self._sections.get(section_id)
        if section is None:
            raise PlatformContractError(
                "UNKNOWN_RESULT_SECTION", "Result page references an unknown staged section",
            )
        if page_size is not None and (
            not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1
        ):
            raise PlatformContractError("INVALID_PAGE_SIZE", "Result page size must be a positive integer")
        size = min(page_size or DEFAULT_RESULT_PAGE_SIZE, MAX_RESULT_PAGE_SIZE)
        fingerprint = hashlib.sha256(
            f"{self.digest}\x1f{section_id}".encode("utf-8")
        ).hexdigest()[:16]
        start = 0
        if cursor is not None:
            prefix = f"{fingerprint}:"
            if not isinstance(cursor, str) or not cursor.startswith(prefix):
                raise PlatformContractError(
                    "INVALID_CURSOR", "Result cursor does not match the requested section",
                )
            try:
                start = int(cursor[len(prefix):])
            except ValueError:
                raise PlatformContractError("INVALID_CURSOR", "Result cursor is malformed") from None
            if start < 0 or start > len(section.items):
                raise PlatformContractError("INVALID_CURSOR", "Result cursor is outside the section")
        end = start
        approximate_bytes = 2
        maximum_end = min(start + size, len(section.items))
        while end < maximum_end:
            item_bytes = len(json.dumps(
                section.items[end], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")) + 1
            if end > start and approximate_bytes + item_bytes > MAX_RESULT_PAGE_BYTES:
                break
            approximate_bytes += item_bytes
            end += 1
        next_cursor = f"{fingerprint}:{end}" if end < len(section.items) else None
        return {
            "sectionId": section_id,
            "kind": section.kind,
            "items": list(section.items[start:end]),
            "page": {
                "start": start, "count": end - start, "total": len(section.items),
                "approxBytes": approximate_bytes,
            },
            "nextCursor": next_cursor,
        }

    def _new_section(self, kind: str, items: Sequence[Any]) -> dict[str, Any]:
        section_items = tuple(items)
        content_digest = hashlib.sha256(json.dumps(
            section_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        existing_id = self._section_by_content.get((kind, content_digest))
        if existing_id is not None:
            existing = self._sections[existing_id]
            reference = {
                "delivery": "paged" if kind == "items" else "chunked_text",
                "sectionId": existing_id,
                "itemCount": len(existing.items),
            }
            if kind == "text":
                reference["byteCount"] = sum(len(item.encode("utf-8")) for item in existing.items)
            return reference
        self._sequence += 1
        section_id = f"result:{self.digest[:16]}:{self._sequence:04d}"
        section = _ResultSection(section_id, kind, section_items)
        self._sections[section_id] = section
        self._section_by_content[(kind, content_digest)] = section_id
        reference = {
            "delivery": "paged" if kind == "items" else "chunked_text",
            "sectionId": section_id,
            "itemCount": len(section.items),
        }
        if kind == "text":
            reference["byteCount"] = sum(len(item.encode("utf-8")) for item in section.items)
        return reference

    def _stage(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): self._stage(item) for key, item in value.items()}
        if isinstance(value, list):
            if not value:
                return []
            staged_items = tuple(self._stage(item) for item in value)
            return self._new_section("items", staged_items)
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_INLINE_TEXT_BYTES:
            return self._new_section("text", _text_chunks(value))
        return value


__all__ = [
    "DEFAULT_RESULT_PAGE_SIZE", "MAX_RESULT_PAGE_SIZE", "MAX_INLINE_TEXT_BYTES",
    "MAX_RESULT_PAGE_BYTES",
    "StagedResultDocument",
]
