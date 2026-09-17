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

from assayer_platform import CompiledPluginLifecycleManager, PluginInstallationStore


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


def store_backed_plugin_registry(store_root: str | Path | None) -> CompiledPluginLifecycleManager:
    """Resolve compiled contract records without importing plugin code."""
    root = Path(store_root).expanduser().resolve() if store_root is not None else default_store_root()
    return CompiledPluginLifecycleManager(PluginInstallationStore(root))
