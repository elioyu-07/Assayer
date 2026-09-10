"""Domain-neutral source-fact indexing.

Plugins extract stable identifiers and explicit statements from source text so
an Agent can review them against domain rules.  This module owns the mechanical
scanning: line-by-line pattern matching, de-duplication, and attaching each
match to its narrowest source reference.  The patterns themselves are supplied
by the plugin; the platform never hard-codes domain identifier conventions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .source_chunking import source_ref_for_line


def build_source_fact_index(
    text: str,
    chunks: Sequence[Mapping[str, Any]],
    *,
    identifier_patterns: Mapping[str, Any],
    explicit_patterns: Mapping[str, Any],
    heading_units: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a descriptive fact index from text, patterns, and heading units.

    The index records identifiers and explicit statements but never decides
    whether a document satisfies any rule.
    """
    identifiers: dict[str, list[dict[str, Any]]] = {}
    lines = text.splitlines()
    for kind, pattern in identifier_patterns.items():
        seen: set[str] = set()
        values: list[dict[str, Any]] = []
        for line_no, line in enumerate(lines, 1):
            for match in pattern.finditer(line):
                identifier = match.group(0).upper().replace("_", "-")
                if identifier in seen:
                    continue
                seen.add(identifier)
                source_ref = source_ref_for_line(chunks, line_no)
                values.append({
                    "id": identifier,
                    "line": line_no,
                    "text": line.strip(),
                    "sourceRef": source_ref,
                })
        identifiers[kind] = values

    explicit_statements: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        kinds = [name for name, pattern in explicit_patterns.items() if pattern.search(stripped)]
        if kinds:
            explicit_statements.append({
                "kinds": kinds,
                "line": line_no,
                "text": stripped,
                "sourceRef": source_ref_for_line(chunks, line_no),
            })

    headings = [
        {
            "title": str(unit.get("title") or ""),
            "level": unit.get("level"),
            "line": unit.get("startLine"),
            "headingPath": list(unit.get("headingPath") or ()),
            "sourceRef": source_ref_for_line(chunks, int(unit["startLine"])),
        }
        for unit in heading_units
    ]
    return {
        "schemaVersion": "1.0.0",
        "identifiers": identifiers,
        "explicitStatements": explicit_statements,
        "headings": headings,
    }


__all__ = ["build_source_fact_index"]
