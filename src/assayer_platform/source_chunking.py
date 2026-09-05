"""Domain-neutral source chunking and source-reference location.

Plugins split their source into bounded, stable, source-located reference
chunks so an Agent can cite exact evidence.  This module owns that mechanical
work: heading-aware splitting, deterministic chunk identity, and line-to-chunk
reference location.  It never interprets what a chunk means.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


SOURCE_CHUNK_LIMIT = 2400


def build_source_chunks(
    text: str, path: Path, source_digest: str, *,
    document_id: str | None = None, document_role: str | None = None,
    chunk_limit: int = SOURCE_CHUNK_LIMIT,
) -> list[dict[str, Any]]:
    """Split source text into bounded, stable, source-located chunks."""
    lines = text.splitlines()
    if not lines:
        return []
    headings: list[tuple[int, int, str]] = []
    for line_no, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((line_no, len(match.group(1)), match.group(2).strip()))
    boundaries = sorted({1, *(line_no for line_no, _, _ in headings), len(lines) + 1})
    heading_by_line = {line_no: (level, title) for line_no, level, title in headings}
    heading_path: list[str] = []
    chunks: list[dict[str, Any]] = []
    for block_index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        markdown_level = 0
        if start in heading_by_line:
            markdown_level, title = heading_by_line[start]
            heading_path = heading_path[: markdown_level - 1]
            heading_path.append(title)
        block_lines = lines[start - 1:end - 1]
        pending: list[tuple[int, str]] = []
        pending_chars = 0

        def flush() -> None:
            nonlocal pending, pending_chars
            if not pending:
                return
            excerpt = "\n".join(value for _, value in pending).strip()
            if excerpt:
                first_line, last_line = pending[0][0], pending[-1][0]
                if document_id is None:
                    # Preserve the historical single-document identity so
                    # existing resumable Runs keep their checkpoint refs.
                    chunk_id = f"source:{source_digest[:12]}:{block_index}:{len(chunks) + 1}"
                else:
                    document_token = re.sub(r"[^A-Za-z0-9_-]+", "-", str(document_id))[:24]
                    chunk_id = f"source:{document_token}:{source_digest[:12]}:{block_index}:{len(chunks) + 1}"
                chunks.append({
                    "source_chunk_id": chunk_id,
                    "document_id": document_id,
                    "document_role": document_role,
                    "document_path": str(path),
                    "source_digest": source_digest,
                    "heading_path": list(heading_path),
                    "primary_heading": heading_path[-1] if heading_path else "Document preamble",
                    "heading_level": markdown_level,
                    "start_line": first_line,
                    "end_line": last_line,
                    "excerpt": excerpt,
                })
            pending, pending_chars = [], 0

        for offset, line in enumerate(block_lines):
            line_no = start + offset
            parts = [line[index:index + chunk_limit] for index in range(0, len(line), chunk_limit)] or [""]
            for part in parts:
                added = len(part) + (1 if pending else 0)
                if pending and pending_chars + added > chunk_limit:
                    flush()
                pending.append((line_no, part))
                pending_chars += len(part) + (1 if len(pending) > 1 else 0)
                if pending_chars >= chunk_limit:
                    flush()
        flush()
    return chunks


def source_ref_for_line(
    chunks: Sequence[Mapping[str, Any]], line_no: int,
) -> dict[str, Any] | None:
    """Return the narrowest immutable source reference containing a line."""
    matches = [
        chunk for chunk in chunks
        if int(chunk.get("start_line", 0)) <= line_no <= int(chunk.get("end_line", 0))
    ]
    if not matches:
        return None
    chunk = min(matches, key=lambda item: int(item.get("end_line", 0)) - int(item.get("start_line", 0)))
    return {
        "source_chunk_id": chunk["source_chunk_id"],
        "document_path": chunk["document_path"],
        "source_digest": chunk["source_digest"],
        "start_line": line_no,
        "end_line": line_no,
        "excerpt": chunk.get("excerpt", ""),
    }


__all__ = ["SOURCE_CHUNK_LIMIT", "build_source_chunks", "source_ref_for_line"]
