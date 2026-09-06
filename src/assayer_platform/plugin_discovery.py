"""Local plugin discovery: the platform's installed-plugin boundary.

The platform kernel no longer ships domain plugins as built-ins.  Domain
plugins (such as ``assayer.frontend-audit`` or any independent distribution)
are discovered through the ``assayer.plugins`` entry-point group, exactly as
capability providers are discovered through ``assayer.providers``.
"""

from __future__ import annotations

from .plugin_registry import PluginRegistry


def builtin_plugin_registry() -> PluginRegistry:
    """Return an empty registry: the distribution ships no domain built-ins.

    Kept for API compatibility with callers and tests that still distinguish
    built-ins from independently installed plugins.  No domain plugin is
    bundled with the platform kernel anymore.
    """
    return PluginRegistry()


def installed_plugin_registry() -> PluginRegistry:
    """Return every plugin exposed through the ``assayer.plugins`` entry-point group."""
    return PluginRegistry.from_entry_points(registrations=builtin_plugin_registry().list())


__all__ = ["builtin_plugin_registry", "installed_plugin_registry"]
