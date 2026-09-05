"""Platform-owned adapters for stable document navigation facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .contract import PlatformContext, PlatformContractError


class MarkdownNavigationAdapter:
    """Expose the Markdown capability through the platform navigation seam."""

    format = "markdown"

    def discover_units(self, target: Any, context: PlatformContext) -> Sequence[Mapping[str, Any]]:
        document = self.read_document(target, context)
        return tuple(document.get("units", ()))

    def read_document(self, target: Any, context: PlatformContext) -> Mapping[str, Any]:
        del context
        return self.parse(target)

    def read_units(self, unit_ids: Sequence[str], context: PlatformContext) -> Sequence[Mapping[str, Any]]:
        del unit_ids, context
        raise PlatformContractError(
            "NAVIGATION_SOURCE_REQUIRED",
            "Markdown unit reread requires the original source target and is not supported by this stateless adapter",
        )

    @staticmethod
    def parse(target: Any) -> Mapping[str, Any]:
        # Lazy import avoids a package-root cycle: the standalone capability
        # provider itself imports platform contracts for registration.
        from assayer_document_navigation import parse_markdown

        path = "<memory>"
        raw: str | bytes
        if isinstance(target, Mapping):
            path = str(target.get("path") or path)
            value = target.get("raw", target.get("source"))
            if value is None and target.get("path"):
                raw = Path(str(target["path"])).expanduser().resolve().read_bytes()
            elif isinstance(value, (str, bytes)):
                raw = value
            else:
                raise PlatformContractError("NAVIGATION_TARGET_INVALID", "Markdown target must provide raw content or a path")
        elif isinstance(target, (str, bytes)):
            raw = target
        elif isinstance(target, Path):
            path = str(target.expanduser().resolve())
            raw = Path(target).expanduser().resolve().read_bytes()
        else:
            raise PlatformContractError("NAVIGATION_TARGET_INVALID", "Markdown target type is unsupported")
        return parse_markdown(raw, path=path)


__all__ = ["MarkdownNavigationAdapter"]
