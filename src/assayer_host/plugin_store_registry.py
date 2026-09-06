"""Store-backed plugin registry resolution shared by the CLI and MCP transports.

The CLI installs plugins into a durable store (``assayer plugins add``), while
the MCP runtime historically discovered plugins only through Python entry
points.  This module is the single resolution boundary that merges both, so a
store-installed plugin becomes visible to the MCP server the same way it is to
the CLI.
"""

from __future__ import annotations

from pathlib import Path

from assayer_platform import PluginInstallationStore, PluginRegistry, discover_plugin_registry
from assayer_platform.builtin_plugins import installed_plugin_registry


def store_backed_plugin_registry(store_root: str | Path | None) -> PluginRegistry:
    """Resolve built-ins plus entry-point and store-installed plugins.

    Falls back to the entry-point registry when no store index exists so that
    read-only callers never create a store directory as a side effect.
    """
    if store_root is None:
        return installed_plugin_registry()
    store_path = Path(store_root).expanduser().resolve()
    if not (store_path / "index.json").is_file():
        return installed_plugin_registry()
    return discover_plugin_registry(
        PluginInstallationStore(store_path),
        builtins=installed_plugin_registry().list(),
    )
