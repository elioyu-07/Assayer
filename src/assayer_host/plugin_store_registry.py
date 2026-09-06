"""Store-backed plugin registry resolution shared by the CLI and MCP transports.

The CLI installs plugins into a durable store (``assayer plugins add``), while
the MCP runtime historically discovered plugins only through Python entry
points.  This module is the single resolution boundary that merges both, so a
store-installed plugin becomes visible to the MCP server the same way it is to
the CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from assayer_platform import PluginInstallationStore, PluginRegistry, discover_plugin_registry
from assayer_platform.builtin_plugins import installed_plugin_registry


def default_store_root() -> Path:
    """The single fixed plugin-store default shared by the CLI and the MCP runtime.

    Both ``assayer plugins add`` and ``assayer-mcp`` must resolve the same store
    without the user threading ``ASSAYER_STORE`` through two processes, so the
    fallback is an absolute per-user data directory rather than a CWD-relative
    path.  ``ASSAYER_STORE`` remains an explicit override for both.
    """
    env = os.environ.get("ASSAYER_STORE")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "assayer" / "plugins"


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
