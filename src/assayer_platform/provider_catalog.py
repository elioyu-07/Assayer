"""Provider catalogs owned by the platform boundary.

The catalog keeps capability-provider discovery in one place.  Domain plugins
may request a capability through the normal ProviderRegistry contract instead
of importing a concrete provider implementation directly.  Built-in
providers are registered explicitly; installed third-party providers are
loaded from the ``assayer.providers`` entry-point group.
"""

from __future__ import annotations

from importlib import metadata

from .contract import PlatformContractError
from .provider_registry import ProviderRegistration, ProviderRegistry


def builtin_provider_registry() -> ProviderRegistry:
    """Return a fresh registry containing providers shipped with Assayer."""

    # Keep this import lazy: the Markdown provider itself imports the public
    # platform contracts, so importing it at module import time would create a
    # circular dependency while ``assayer_platform`` is initializing.
    from assayer_document_navigation import markdown_registration

    return ProviderRegistry((markdown_registration(),))


def installed_provider_registry() -> ProviderRegistry:
    """Return built-ins plus providers exposed by installed distributions."""
    registry = builtin_provider_registry()
    builtin_ids = {item.descriptor.provider_id for item in registry.list()}
    try:
        discovered = metadata.entry_points()
        entries = discovered.select(group="assayer.providers") if hasattr(discovered, "select") else discovered.get("assayer.providers", ())
    except Exception as error:
        raise PlatformContractError(
            "PROVIDER_DISCOVERY_FAILED",
            "Installed provider entry points could not be loaded",
        ) from error
    for entry in entries:
        # The distribution publishes its built-in Markdown provider as an
        # entry point for standalone consumers.  When the platform assembles
        # its catalog, that one entry is already present above and must not be
        # registered a second time.  A same-ID provider from any other
        # distribution remains a conflict and is intentionally rejected.
        try:
            value = entry.load()
            if callable(value) and not isinstance(value, ProviderRegistration):
                value = value()
            if not isinstance(value, ProviderRegistration):
                value = getattr(value, "registration", None)
            if not isinstance(value, ProviderRegistration):
                raise TypeError("entry point did not provide ProviderRegistration")
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "INVALID_PROVIDER_REGISTRATION",
                f"Provider entry point {entry.name!r} is invalid",
            ) from error
        provider_id = value.descriptor.provider_id
        if (
            getattr(getattr(entry, "dist", None), "name", None) == "assayer"
            and provider_id in builtin_ids
        ):
            continue
        registry.register(value)
    return registry


__all__ = ["builtin_provider_registry", "installed_provider_registry"]
