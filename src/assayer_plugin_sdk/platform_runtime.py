"""Platform-owned frozen-document runtime for generated adapters.

This module is not an authoring surface.  Ordinary plugins are declaration-only
and must never import it directly.  It is kept separate from the deleted
the deleted author module so generated artifacts have an explicit
platform-runtime dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any

from .browser import BrowserSnapshot
from .contract import PlatformContractError


_SEVERITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})
def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError("INVALID_PLATFORM_RUNTIME", f"{label} must be nonempty")
    return value.strip()


def _json_value(value: Any, label: str) -> Any:
    try:
        detached = json.loads(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ))
    except (TypeError, ValueError) as error:
        raise PlatformContractError(
            "INVALID_PLATFORM_RUNTIME", f"{label} must be a finite JSON value",
        ) from error
    return _freeze_json(detached)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


@dataclass(frozen=True, init=False, repr=False)
class Support:
    """Opaque source support created only by a Host-owned ``Document``."""

    _anchor: str
    _evidence_refs: tuple[str, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PlatformContractError(
            "SIMPLE_SUPPORT_HOST_OWNED",
            "Support must come from the frozen Document API",
        )

    @classmethod
    def _bind(cls, anchor: str, evidence_refs: Sequence[str]) -> "Support":
        refs = tuple(_text(item, "Evidence support") for item in evidence_refs)
        if not refs or len(refs) != len(set(refs)):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Host Support requires unique Evidence references",
            )
        value = object.__new__(cls)
        object.__setattr__(value, "_anchor", _text(anchor, "Support anchor"))
        object.__setattr__(value, "_evidence_refs", refs)
        return value

    def __repr__(self) -> str:
        return "Support(<host-bound>)"


@dataclass(frozen=True)
class Unknown:
    reason: str
    missing_information: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "Unknown reason"))
        values = tuple(
            _text(item, "Missing information") for item in self.missing_information
        )
        if len(values) != len(set(values)):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Missing information must be unique",
            )
        object.__setattr__(self, "missing_information", values)


@dataclass(frozen=True)
class Candidate:
    rule: str
    subject: str
    message: str
    support: Support
    severity: str
    recommendation: str

    def __post_init__(self) -> None:
        for field_name, label in (
            ("rule", "Candidate rule"), ("subject", "Candidate subject"),
            ("message", "Candidate message"),
            ("recommendation", "Candidate recommendation"),
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), label))
        if self.severity not in _SEVERITIES:
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Candidate severity is unsupported",
            )
        if not isinstance(self.support, Support):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Candidate support must come from Document",
            )


@dataclass(frozen=True)
class Fact:
    name: str
    value: Any
    support: Support

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "Fact name"))
        object.__setattr__(self, "value", _json_value(self.value, "Fact value"))
        if not isinstance(self.support, Support):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Fact support must come from Document",
            )


@dataclass(frozen=True)
class Relation:
    relationship: str
    left: str
    right: str
    instruction: str
    support: Support

    def __post_init__(self) -> None:
        for field_name, label in (
            ("relationship", "Relationship name"),
            ("left", "Relationship left subject"),
            ("right", "Relationship right subject"),
            ("instruction", "Relationship instruction"),
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), label))
        if not isinstance(self.support, Support):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Relationship support must come from Document",
            )


@dataclass(frozen=True)
class _DocumentScope:
    document_token: object
    closed: bool


class Document:
    """Read-only view over text already frozen by a Source provider."""

    __slots__ = (
        "__text", "__support", "__token", "__closed", "__chunks", "__coverage_refs",
        "__browser_snapshot",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PlatformContractError(
            "SIMPLE_DOCUMENT_HOST_OWNED",
            "Document is supplied by the Host and cannot be opened from a path",
        )

    @classmethod
    def _from_snapshot(
        cls, text: str, *, evidence_refs: Sequence[str], closed: bool = True,
        chunks: Sequence[Mapping[str, Any]] = (),
        coverage_refs: Sequence[str] = (),
    ) -> "Document":
        if not isinstance(text, str):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Frozen Document text must be a string",
            )
        if not isinstance(closed, bool):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Document scope closure must be boolean",
            )
        value = object.__new__(cls)
        token = object()
        normalized_chunks: list[Mapping[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, Mapping) or set(chunk) != {
                "anchor", "startLine", "endLine", "text", "headingPath",
            }:
                raise PlatformContractError(
                    "INVALID_DOCUMENT_SNAPSHOT", "Frozen Document chunk is malformed",
                )
            anchor = _text(chunk["anchor"], "Document chunk anchor")
            start = chunk["startLine"]
            end = chunk["endLine"]
            heading_path = chunk["headingPath"]
            if (
                not isinstance(start, int) or isinstance(start, bool) or start < 1
                or not isinstance(end, int) or isinstance(end, bool) or end < start
                or not isinstance(chunk["text"], str)
                or not isinstance(heading_path, (tuple, list))
            ):
                raise PlatformContractError(
                    "INVALID_DOCUMENT_SNAPSHOT", "Frozen Document chunk bounds are invalid",
                )
            headings = tuple(_text(item, "Document section heading") for item in heading_path)
            normalized_chunks.append(MappingProxyType({
                "anchor": anchor,
                "startLine": start,
                "endLine": end,
                "text": chunk["text"],
                "headingPath": headings,
            }))
        chunk_anchors = tuple(item["anchor"] for item in normalized_chunks)
        if len(chunk_anchors) != len(set(chunk_anchors)):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Frozen Document chunk anchors must be unique",
            )
        normalized_coverage = tuple(
            _text(item, "Document coverage Evidence") for item in coverage_refs
        )
        if normalized_coverage and len(normalized_coverage) != len(set(normalized_coverage)):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Document coverage Evidence must be unique",
            )
        object.__setattr__(value, "_Document__text", text)
        object.__setattr__(value, "_Document__token", token)
        object.__setattr__(value, "_Document__closed", closed)
        object.__setattr__(value, "_Document__chunks", tuple(normalized_chunks))
        object.__setattr__(value, "_Document__coverage_refs", normalized_coverage)
        object.__setattr__(value, "_Document__browser_snapshot", None)
        object.__setattr__(
            value,
            "_Document__support",
            Support._bind("document:full", evidence_refs),
        )
        return value

    @classmethod
    def _from_browser_snapshot(
        cls,
        snapshot: BrowserSnapshot,
        *,
        evidence_refs: Sequence[str],
    ) -> "Document":
        """Create the author-facing read-only view of a Host browser snapshot.

        Browser I/O remains outside this runtime.  The Host reconstructs this
        view from provider Evidence, so generated adapters can use the same
        ``Document`` concept for text and browser inputs without receiving a
        page, locator, or provider request envelope.
        """
        if not isinstance(snapshot, BrowserSnapshot):
            raise PlatformContractError(
                "INVALID_BROWSER_SNAPSHOT",
                "Browser input must be a Host-owned BrowserSnapshot",
            )
        value = cls._from_snapshot(
            snapshot.visible_text,
            evidence_refs=evidence_refs,
            # A bounded browser observation is not a proof that text is absent
            # from the complete page.  Keep absence conservative unless a
            # future provider publishes an explicit closed search scope.
            closed=False,
        )
        object.__setattr__(value, "_Document__browser_snapshot", snapshot)
        return value

    @property
    def browser_snapshot(self) -> BrowserSnapshot | None:
        """Return the immutable browser observation, when this is browser input."""
        return self.__browser_snapshot

    @property
    def visible_text(self) -> str:
        """Visible text for browser input; equivalent to the document text."""
        return self.__text

    def _browser_value(self, name: str, default: Any = None) -> Any:
        snapshot = self.__browser_snapshot
        return getattr(snapshot, name, default) if snapshot is not None else default

    @property
    def url(self) -> str | None:
        return self._browser_value("url")

    @property
    def origin(self) -> str | None:
        return self._browser_value("origin")

    @property
    def title(self) -> str | None:
        return self._browser_value("title")

    @property
    def route(self) -> str | None:
        return self._browser_value("route")

    @property
    def state_kind(self) -> str | None:
        return self._browser_value("state_kind")

    @property
    def entrypoints(self) -> tuple[Mapping[str, Any], ...]:
        return self._browser_value("entrypoints", ())

    @property
    def candidates(self) -> tuple[Mapping[str, Any], ...]:
        return self._browser_value("candidates", ())

    @property
    def network_summary(self) -> Mapping[str, Any] | None:
        return self._browser_value("network_summary")

    @property
    def structure_summary(self) -> Mapping[str, Any] | None:
        return self._browser_value("structure_summary")

    @property
    def active_tab(self) -> str | None:
        return self._browser_value("active_tab")

    @property
    def dom_digest(self) -> str | None:
        return self._browser_value("dom_digest")

    @property
    def visual_digest(self) -> str | None:
        return self._browser_value("visual_digest")

    @property
    def state_digest(self) -> str | None:
        return self._browser_value("state_digest")

    @property
    def full_scope(self) -> _DocumentScope:
        return _DocumentScope(self.__token, self.__closed)

    def contains(self, text: str) -> bool:
        return _text(text, "Document query") in self.__text

    def contains_any(self, *texts: str) -> bool:
        if len(texts) == 1 and isinstance(texts[0], (tuple, list)):
            texts = tuple(texts[0])
        if not texts:
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "contains_any requires at least one query",
            )
        return any(self.contains(item) for item in texts)

    def lines(self, start: int, end: int) -> Support:
        lines = self.__text.splitlines()
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 1 or end < start or end > max(1, len(lines))
        ):
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Document line support range is invalid",
            )
        refs = tuple(
            str(chunk["anchor"])
            for chunk in self.__chunks
            if int(chunk["startLine"]) <= end and int(chunk["endLine"]) >= start
        )
        return Support._bind(
            f"document:lines:{start}-{end}", refs or self.__support._evidence_refs,
        )

    def search(self, query: str, *, limit: int = 20) -> tuple[Support, ...]:
        needle = _text(query, "Document search query")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Document search limit must be between 1 and 100",
            )
        if not self.__chunks:
            return (
                (Support._bind("document:search:1", self.__support._evidence_refs),)
                if needle in self.__text else ()
            )
        matches = tuple(
            chunk for chunk in self.__chunks if needle in str(chunk["text"])
        )[:limit]
        return tuple(
            Support._bind(
                f"document:search:{index}", (str(chunk["anchor"]),),
            )
            for index, chunk in enumerate(matches, 1)
        )

    def sections(self, title: str | None = None) -> tuple[Support, ...]:
        wanted = _text(title, "Document section title").casefold() if title is not None else None
        groups: dict[tuple[str, ...], list[str]] = {}
        for chunk in self.__chunks:
            heading_path = tuple(str(item) for item in chunk["headingPath"])
            if not heading_path:
                continue
            if wanted is not None and wanted not in heading_path[-1].casefold():
                continue
            groups.setdefault(heading_path, []).append(str(chunk["anchor"]))
        return tuple(
            Support._bind(
                f"document:section:{index}", tuple(refs),
            )
            for index, refs in enumerate(groups.values(), 1)
        )

    def absence(
        self, query: str | Sequence[str], *, scope: _DocumentScope,
    ) -> Support | Unknown:
        if not isinstance(scope, _DocumentScope) or scope.document_token is not self.__token:
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Absence scope belongs to another Document",
            )
        queries = (query,) if isinstance(query, str) else tuple(query)
        normalized = tuple(_text(item, "Absence query") for item in queries)
        if not normalized:
            raise PlatformContractError(
                "INVALID_PLATFORM_RUNTIME", "Absence proof requires at least one query",
            )
        if not scope.closed:
            return Unknown(
                "The requested search scope is not closed.",
                ("complete source scope",),
            )
        found = tuple(item for item in normalized if item in self.__text)
        if found:
            raise PlatformContractError(
                "SIMPLE_ABSENCE_NOT_PROVEN",
                "An absence proof cannot be created because the query is present",
            )
        return Support._bind(
            "document:absence:" + "|".join(sorted(set(normalized))),
            self.__coverage_refs or self.__support._evidence_refs,
        )


def _support_projection(support: Support) -> dict[str, Any]:
    return {
        "anchor": support._anchor,
        "supportRefs": list(support._evidence_refs),
    }


__all__ = [
    "Document", "Candidate", "Fact", "Relation", "Support", "Unknown",
]
