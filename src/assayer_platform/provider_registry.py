"""Capability-provider selection for the Assayer platform.

The provider-facing contracts (``ProviderRegistration``, descriptor loading and
validation, and the descriptor projection) live in the plugin SDK
(:mod:`assayer_plugin_sdk.provider`) and are re-exported here.  This module owns
only the platform-side registry and deterministic selection.
"""

from __future__ import annotations

from importlib import metadata
from typing import Iterable

from assayer_plugin_sdk.provider import (  # noqa: F401
    ProviderRegistration,
    _descriptor_payload,
    _plain,
    load_provider_descriptor,
    validate_provider_descriptor,
)

from .contract import PlatformContractError


__all__ = [
    "ProviderRegistration",
    "ProviderRegistry",
    "load_provider_descriptor",
    "validate_provider_descriptor",
    "_descriptor_payload",
    "_plain",
]


class ProviderRegistry:
    """Fail-closed provider catalog with explicit ambiguity handling."""

    def __init__(self, registrations: Iterable[ProviderRegistration] = ()) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}
        for registration in registrations:
            self.register(registration)

    @classmethod
    def from_entry_points(
        cls, *, group: str = "assayer.providers",
        registrations: Iterable[ProviderRegistration] = (),
    ) -> "ProviderRegistry":
        registry = cls(registrations)
        try:
            discovered = metadata.entry_points()
            entries = discovered.select(group=group) if hasattr(discovered, "select") else discovered.get(group, ())
        except Exception as error:
            raise PlatformContractError(
                "PROVIDER_DISCOVERY_FAILED",
                "Installed provider entry points could not be loaded",
            ) from error
        for entry in entries:
            try:
                value = entry.load()
                if callable(value) and not isinstance(value, ProviderRegistration):
                    value = value()
                if not isinstance(value, ProviderRegistration):
                    value = getattr(value, "registration", None)
                if not isinstance(value, ProviderRegistration):
                    raise TypeError("entry point did not provide ProviderRegistration")
                registry.register(value)
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "INVALID_PROVIDER_REGISTRATION",
                    f"Provider entry point {entry.name!r} is invalid",
                ) from error
        return registry

    def register(self, registration: ProviderRegistration) -> None:
        from .provider_conformance import require_provider_registration_conformance

        require_provider_registration_conformance(registration)
        provider_id = registration.descriptor.provider_id
        if provider_id in self._registrations:
            raise PlatformContractError(
                "PROVIDER_CONFLICT", f"Provider is already registered: {provider_id}",
            )
        self._registrations[provider_id] = registration

    def list(self) -> tuple[ProviderRegistration, ...]:
        return tuple(self._registrations.values())

    def get(self, provider_id: str) -> ProviderRegistration:
        registration = self._registrations.get(provider_id)
        if registration is None:
            raise PlatformContractError(
                "UNKNOWN_PROVIDER", f"Provider is not registered: {provider_id}",
            )
        return registration

    def select(
        self, *, provider_id: str | None = None,
        capability: str | None = None,
    ) -> ProviderRegistration:
        candidates = [self.get(provider_id)] if provider_id is not None else list(self._registrations.values())
        if capability is not None:
            candidates = [
                item for item in candidates
                if capability in {entry.name for entry in item.descriptor.capabilities}
            ]
        if not candidates:
            raise PlatformContractError(
                "PROVIDER_NOT_FOUND", "No registered provider matches the requested capability",
            )
        if len(candidates) > 1:
            identifiers = ", ".join(item.descriptor.provider_id for item in candidates)
            raise PlatformContractError(
                "PROVIDER_AMBIGUOUS",
                f"Multiple registered providers match the requested capability: {identifiers}",
            )
        return candidates[0]

    def select_for_capabilities(
        self, required_capabilities: Iterable[str], *, provider_id: str | None = None,
    ) -> ProviderRegistration:
        required = frozenset(required_capabilities)
        if any(not isinstance(item, str) or not item for item in required):
            raise PlatformContractError(
                "CAPABILITY_REQUEST_INVALID",
                "Required capabilities must be nonempty names",
            )
        candidates = [self.get(provider_id)] if provider_id is not None else list(self._registrations.values())
        candidates = [
            item for item in candidates
            if required.issubset({capability.name for capability in item.descriptor.capabilities})
        ]
        if not candidates:
            raise PlatformContractError(
                "PROVIDER_NOT_FOUND",
                "No registered provider supplies every required capability",
            )
        if len(candidates) > 1:
            identifiers = ", ".join(item.descriptor.provider_id for item in candidates)
            raise PlatformContractError(
                "PROVIDER_AMBIGUOUS",
                f"Multiple registered providers supply every required capability: {identifiers}",
            )
        return candidates[0]
