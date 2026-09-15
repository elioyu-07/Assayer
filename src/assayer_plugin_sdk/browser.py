"""Frozen, domain-neutral browser observations for SDK plugins.

The Host owns browser I/O and creates :class:`BrowserSnapshot` instances after
one bounded, read-only probe.  Plugins receive the snapshot as data; they do
not receive a Playwright page, locator, or mutable browser handle.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from .contract import PlatformContractError


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformContractError(
            "INVALID_BROWSER_SNAPSHOT",
            f"Browser snapshot field '{field}' must be an object",
        )
    return _freeze(value)


def _records(value: Any, *, field: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PlatformContractError(
            "INVALID_BROWSER_SNAPSHOT",
            f"Browser snapshot field '{field}' must be a sequence",
        )
    records: list[Mapping[str, Any]] = []
    for item in value:
        records.append(_mapping(item, field=field))
    return tuple(records)


@dataclass(frozen=True)
class BrowserSnapshot:
    """Immutable output of one Host-owned read-only browser probe.

    ``state_digest`` is derived from the complete probe payload unless the
    Host supplies an already verified digest.  Mapping and record fields are
    recursively frozen so a plugin cannot mutate the evidence it was given.
    """

    visible_text: str
    entrypoints: tuple[Mapping[str, Any], ...] = ()
    candidates: tuple[Mapping[str, Any], ...] = ()
    network_summary: Mapping[str, Any] | None = None
    route: str | None = None
    state_kind: str = "page"
    structure_summary: Mapping[str, Any] | None = None
    active_tab: str | None = None
    dom_digest: str | None = None
    visual_digest: str | None = None
    state_digest: str = ""
    url: str | None = None
    origin: str | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.visible_text, str):
            raise PlatformContractError(
                "INVALID_BROWSER_SNAPSHOT",
                "Browser snapshot visible_text must be a string",
            )
        for field in (
            "route", "state_kind", "active_tab", "dom_digest", "visual_digest",
            "url", "origin", "title",
        ):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise PlatformContractError(
                    "INVALID_BROWSER_SNAPSHOT",
                    f"Browser snapshot {field} must be a nonempty string when provided",
                )
        object.__setattr__(self, "entrypoints", _records(self.entrypoints, field="entrypoints"))
        object.__setattr__(self, "candidates", _records(self.candidates, field="candidates"))
        for field in ("network_summary", "structure_summary"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _mapping(value, field=field))
        digest = self.state_digest
        if digest:
            try:
                valid_digest = isinstance(digest, str) and len(digest) == 64
                int(digest, 16)
            except (TypeError, ValueError):
                valid_digest = False
            if not valid_digest:
                raise PlatformContractError(
                    "INVALID_BROWSER_SNAPSHOT",
                    "Browser snapshot state_digest must be a SHA-256 hex digest",
                )
        if not digest:
            material = {
                "visibleText": self.visible_text,
                "entrypoints": self.entrypoints,
                "candidates": self.candidates,
                "networkSummary": self.network_summary,
                "route": self.route,
                "stateKind": self.state_kind,
                "structureSummary": self.structure_summary,
                "activeTab": self.active_tab,
                "domDigest": self.dom_digest,
                "visualDigest": self.visual_digest,
                "url": self.url,
                "origin": self.origin,
                "title": self.title,
            }
            encoded = json.dumps(
                _plain(material),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            object.__setattr__(self, "state_digest", hashlib.sha256(encoded).hexdigest())


@runtime_checkable
class BrowserSnapshotSource(Protocol):
    """Host-injected source that returns one frozen browser snapshot."""

    def observe_snapshot(self) -> BrowserSnapshot:
        """Collect a bounded read-only snapshot from the current page."""
        ...


__all__ = ["BrowserSnapshot", "BrowserSnapshotSource"]
