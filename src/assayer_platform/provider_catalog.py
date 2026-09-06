"""Provider catalogs owned by the platform boundary.

The platform kernel no longer ships capability providers as built-ins.  The
Markdown navigation provider (``assayer.document-navigation``) and any
independent distribution are discovered through the ``assayer.providers``
entry-point group.  Domain plugins request a capability through the normal
ProviderRegistry contract instead of importing a concrete provider
implementation directly.
"""

from __future__ import annotations

from .provider_registry import ProviderRegistry


def builtin_provider_registry() -> ProviderRegistry:
    """Return an empty registry: the distribution ships no built-in providers.

    Kept for API compatibility with callers and tests that still distinguish
    built-ins from independently installed providers.  No capability provider
    is bundled with the platform kernel anymore.
    """
    return ProviderRegistry()


def installed_provider_registry() -> ProviderRegistry:
    """Return every provider exposed through the ``assayer.providers`` entry-point group."""
    return ProviderRegistry.from_entry_points(registrations=builtin_provider_registry().list())


__all__ = ["builtin_provider_registry", "installed_provider_registry"]
