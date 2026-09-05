"""Read-only Markdown structure navigation for provider-backed plugins.

This module deliberately emits structure and immutable source locators only.
It never decides whether a document is correct or compliant.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from assayer_platform import (
    CapabilityProviderDescriptor,
    ProviderCapability,
    ProviderFact,
    ProviderFailure,
    ProviderRegistration,
    ProviderResponse,
    load_provider_descriptor,
)


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+.+$")
_QUOTE = re.compile(r"^\s*>\s?.*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|?\s*$")
_INCLUDE_KINDS = {
    "all": None,
    "heading": "heading", "headings": "heading",
    "paragraph": "paragraph", "paragraphs": "paragraph",
    "list": "list", "lists": "list",
    "table": "table", "tables": "table",
    "code": "code_block", "code_block": "code_block", "code_blocks": "code_block",
    "quote": "blockquote", "quotes": "blockquote", "blockquote": "blockquote",
    "front_matter": "front_matter", "front-matter": "front_matter",
}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")
    return value[:48] or "unit"


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _unit(
    *, kind: str, lines: Sequence[str], start: int, end: int,
    heading_path: Sequence[str], ordinal: int, metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    excerpt = "\n".join(lines[start - 1:end])
    material = f"{kind}\0{'/'.join(heading_path)}\0{excerpt}\0{ordinal}".encode("utf-8")
    unit_id = f"{kind}:{_slug(heading_path[-1] if heading_path else excerpt)}:{hashlib.sha256(material).hexdigest()[:12]}"
    value: dict[str, Any] = {
        "unitId": unit_id,
        "kind": kind,
        "headingPath": list(heading_path),
        "startLine": start,
        "endLine": end,
        "excerpt": excerpt,
    }
    if metadata:
        value["metadata"] = dict(metadata)
    return value


def parse_markdown(raw: str | bytes, *, path: str = "<memory>") -> dict[str, Any]:
    """Parse Markdown into deterministic, line-addressable navigation units."""
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    text = raw_bytes.decode("utf-8-sig")
    lines = text.splitlines()
    units: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    ordinals: dict[str, int] = {}

    def add(kind: str, start: int, end: int, metadata: Mapping[str, Any] | None = None, heading_path: Sequence[str] | None = None) -> None:
        key = f"{kind}:{start}:{end}"
        ordinals[key] = ordinals.get(key, 0) + 1
        units.append(_unit(
            kind=kind, lines=lines, start=start, end=end,
            heading_path=heading_path if heading_path is not None else [item[1] for item in heading_stack],
            ordinal=ordinals[key], metadata=metadata,
        ))

    index = 0
    if lines and lines[0].strip() == "---":
        close = next((pos for pos in range(1, len(lines)) if lines[pos].strip() in {"---", "..."}), None)
        if close is not None:
            add("front_matter", 1, close + 1, {"format": "yaml"}, [])
            index = close + 1

    while index < len(lines):
        line_no = index + 1
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            close = index + 1
            while close < len(lines) and not re.match(rf"^\s*{re.escape(marker)}{{{len(fence.group(1))},}}\s*$", lines[close]):
                close += 1
            end = min(close + 1, len(lines))
            language = fence.group(2).strip().split()[0] if fence.group(2).strip() else ""
            add("code_block", line_no, end, {"language": language})
            index = end
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).rstrip("#").strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            add("heading", line_no, line_no, {"level": level, "title": title}, [item[1] for item in heading_stack])
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _TABLE_SEPARATOR.match(lines[index + 1]):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            headers = _split_table_row(line)
            rows = [_split_table_row(item) for item in lines[index + 2:end]]
            add("table", line_no, end, {"headers": headers, "rowCount": len(rows), "rows": rows})
            index = end
            continue
        if _LIST.match(line):
            end = index + 1
            while end < len(lines) and (_LIST.match(lines[end]) or (lines[end].strip() and lines[end][0].isspace())):
                end += 1
            add("list", line_no, end, {"itemCount": sum(bool(_LIST.match(item)) for item in lines[index:end])})
            index = end
            continue
        if _QUOTE.match(line):
            end = index + 1
            while end < len(lines) and _QUOTE.match(lines[end]):
                end += 1
            add("blockquote", line_no, end)
            index = end
            continue
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if not candidate.strip() or _HEADING.match(candidate) or _FENCE.match(candidate) or _LIST.match(candidate) or _QUOTE.match(candidate):
                break
            if end + 1 < len(lines) and "|" in candidate and _TABLE_SEPARATOR.match(lines[end + 1]):
                break
            end += 1
        add("paragraph", line_no, end)
        index = end

    return {
        "format": "markdown",
        "document": {"path": str(path), "lineCount": len(lines)},
        "sourceDigest": f"sha256:{_digest(raw_bytes)}",
        "units": units,
        "unitCount": len(units),
    }


def _descriptor() -> CapabilityProviderDescriptor:
    return load_provider_descriptor({
        "providerId": "assayer.document-navigation",
        "version": "1.0.0",
        "platformApiVersion": "1.0.0",
        "capabilities": [{
            "name": "document_navigation",
            "version": "1.0.0",
            "accessMode": "read_only",
            "evidenceKinds": ["structured"],
        }],
        "scopeSchema": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "format"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "format": {"const": "markdown"},
                "page": {"type": "object", "additionalProperties": False, "properties": {
                    "cursor": {"type": ["string", "null"]},
                    "size": {"type": "integer", "minimum": 1, "maximum": 1000},
                }},
                "include": {"type": "array", "items": {"type": "string"}},
            },
        },
        "authorization": {"userScopeRequired": True, "secretHandling": "none"},
        "limits": {"timeoutMs": 30000, "maxBytes": 10_000_000, "maxItems": 1000, "maxConcurrency": 1},
        "failurePolicy": [{"code": code, "retry": "never" if code != "result_unknown" else "resolve_unknown_first"} for code in (
            "capability_unavailable", "authorization_denied", "timeout", "budget_exceeded",
            "source_changed", "stale_state", "source_error", "result_unknown",
        )],
        "algorithmVersions": {"markdownParser": "1.0.0", "sourceIdentity": "1.0.0", "stateDigest": "1.0.0"},
    })


class MarkdownNavigationProvider:
    """Provider implementation for one immutable Markdown source."""

    descriptor = _descriptor()

    def collect(self, request: Any, context: Any) -> ProviderResponse:
        del context
        scope = request.scope
        if scope.get("format") != "markdown":
            return ProviderResponse(request.request_id, request.provider_id, request.provider_version, request.capability, "failed", failure=ProviderFailure("source_error", "Only Markdown is supported by this provider."))
        try:
            path = Path(str(scope["path"])).expanduser().resolve()
            raw = path.read_bytes()
            digest_value = _digest(raw)
            digest = f"sha256:{digest_value}"
            # WorkItems in the platform kernel historically carry the raw
            # SHA-256 value, while the provider payload uses an algorithm-
            # qualified digest.  Accept both equivalent spellings at this
            # boundary; the returned Evidence remains bound to the exact
            # request state digest validated by the Host.
            if request.state_digest not in {digest_value, digest}:
                return ProviderResponse(request.request_id, request.provider_id, request.provider_version, request.capability, "failed", failure=ProviderFailure("source_changed", "The Markdown source changed after discovery."))
            payload = parse_markdown(raw, path=str(path))
            page = scope.get("page") or {}
            cursor = int(page.get("cursor") or 0)
            size = min(int(page.get("size") or request.limits.get("maxItems", 1000)), int(request.limits.get("maxItems", 1000)))
            if cursor < 0 or size < 1:
                raise ValueError("Markdown navigation page cursor and size must be positive")
            requested_include = scope.get("include") or ()
            if not isinstance(requested_include, (tuple, list)):
                raise ValueError("Markdown navigation include must be an array")
            selected_kinds: set[str] = set()
            for raw_kind in requested_include:
                kind = str(raw_kind).strip().lower()
                if kind not in _INCLUDE_KINDS:
                    raise ValueError(f"Unsupported Markdown navigation unit kind: {raw_kind}")
                mapped = _INCLUDE_KINDS[kind]
                if mapped is None:
                    selected_kinds.clear()
                    break
                selected_kinds.add(mapped)
            all_units = payload["units"]
            filtered_units = [
                unit for unit in all_units
                if not selected_kinds or unit["kind"] in selected_kinds
            ]
            page_units = filtered_units[cursor:cursor + size]
            next_cursor = str(cursor + size) if cursor + size < len(filtered_units) else None
            payload = {**payload, "units": page_units, "nextCursor": next_cursor, "coverage": {
                "discovered": len(all_units), "selected": len(filtered_units),
                "returned": len(page_units), "cursor": str(cursor),
            }}
            fact = ProviderFact("structured", request.source_identity, request.state_digest, payload)
            return ProviderResponse(request.request_id, request.provider_id, request.provider_version, request.capability, "succeeded", (fact,))
        except (OSError, UnicodeError, ValueError) as error:
            return ProviderResponse(request.request_id, request.provider_id, request.provider_version, request.capability, "failed", failure=ProviderFailure("source_error", str(error)))


def markdown_registration() -> ProviderRegistration:
    return ProviderRegistration(MarkdownNavigationProvider.descriptor, provider_factory=lambda _runtime=None: MarkdownNavigationProvider())
