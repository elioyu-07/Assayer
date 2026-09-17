"""Fail-closed gate for the declaration-only ordinary-plugin source surface."""

from __future__ import annotations

from pathlib import Path

from assayer_plugin_sdk.contract import PlatformContractError


def validate_simple_author_source(path: str | Path) -> None:
    """Reject every executable source file in an ordinary plugin package."""
    source_path = Path(path)
    raise PlatformContractError(
        "ORDINARY_PLUGIN_PYTHON_FORBIDDEN",
        f"Ordinary plugins are declaration-only; remove executable source {source_path.name}",
        errors=({
            "pointer": f"/{source_path.name}",
            "keyword": "ordinaryAuthorSurface",
            "message": "ordinary plugin authors must use plugin.yaml, checks.yaml, semantic-review.md, and cases/",
        },),
    )


__all__ = ["validate_simple_author_source"]
