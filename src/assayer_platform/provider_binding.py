"""Bind a Host capability provider for a Check that declares provider capabilities.

Release fixtures and lifecycle gates run a plugin in-process; when the plugin
declares ``provider_capabilities`` the Host must still bind the installed
provider so the plugin receives an SDK ``CapabilityAccess`` and the run can
verify provider Evidence.  This keeps a provider-backed plugin runnable outside
the interactive transport without weakening the evidence gate.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .capability_negotiation import CapabilityNegotiator
from .contract import CapabilityProfile, CheckContract, PlatformContractError
from .plugin_registry import PluginRegistration
from .provider_catalog import installed_provider_registry
from .provider_execution import BoundCapabilityProvider


def provider_required_capabilities(
    registration: PluginRegistration, check: CheckContract,
) -> tuple[str, ...]:
    declared = frozenset(getattr(registration, "provider_capabilities", ()))
    required = frozenset(check.required_capabilities) & declared
    return tuple(sorted(required))


def grant_check_capabilities(
    registration: PluginRegistration, check: CheckContract,
) -> CapabilityProfile:
    """Grant exactly the capabilities a Check declares.

    This is the default Host authorization profile for a standalone Assayer
    runtime: it keeps a provider-backed Check runnable while the provider still
    has to supply every declared capability.  Embedding products may pass a
    stricter profile.
    """
    del registration
    return CapabilityProfile(frozenset(check.required_capabilities))


def bind_capability_provider(
    registration: PluginRegistration,
    check: CheckContract,
    scope: Mapping[str, Any],
    run_id: str,
    *,
    provider_runtime: Any = None,
) -> BoundCapabilityProvider | None:
    """Return a bound provider for the Check, or ``None`` when none is needed.

    ``provider_runtime`` is an opaque Host-owned adapter passed to the
    provider factory (for example ``BrowserSnapshotSource``).  The binding
    layer never starts or inspects that runtime.  Fails closed with
    ``PROVIDER_NOT_FOUND`` when the Check requires a declared provider
    capability but no installed provider supplies it.
    """
    capabilities = provider_required_capabilities(registration, check)
    if not capabilities:
        return None
    registry = installed_provider_registry()
    provider_registration = registry.select_for_capabilities(capabilities)
    profile = CapabilityProfile(frozenset(capabilities))
    provider_scope: Mapping[str, Any] = {}
    resolver = getattr(registration, "provider_scope_resolver", None)
    if resolver is not None:
        resolved = resolver(scope, check)
        if resolved is not None:
            if not isinstance(resolved, Mapping):
                raise PlatformContractError(
                    "PROVIDER_SCOPE_INVALID",
                    "Provider scope resolver must return a mapping or None",
                )
            provider_scope = resolved
    negotiation = CapabilityNegotiator().negotiate(
        provider_registration, capabilities, profile,
        user_profile=profile, scope=provider_scope,
    )
    return BoundCapabilityProvider(
        provider_registration, negotiation, run_id=run_id, scope=provider_scope,
        runtime=provider_runtime,
    )


def check_for(
    registration: PluginRegistration, check_id: str, check_version: str | None,
) -> CheckContract | None:
    matches = tuple(
        item for item in registration.manifest.checks
        if item.check_id == check_id and (check_version is None or item.version == check_version)
    )
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "bind_capability_provider",
    "check_for",
    "provider_required_capabilities",
]
