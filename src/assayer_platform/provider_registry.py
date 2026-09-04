"""Registration and deterministic selection for capability providers."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import SchemaError

from .contract import (
    PLATFORM_API_VERSION,
    CapabilityProviderDescriptor,
    PlatformContractError,
    ProviderCapability,
)
from .registry import _schema_root, _version_core


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _descriptor_payload(descriptor: CapabilityProviderDescriptor) -> dict[str, Any]:
    return {
        "providerId": descriptor.provider_id,
        "version": descriptor.version,
        "platformApiVersion": descriptor.platform_api_version,
        "capabilities": [{
            "name": capability.name,
            "version": capability.version,
            "accessMode": capability.access_mode,
            "evidenceKinds": list(capability.evidence_kinds),
        } for capability in descriptor.capabilities],
        "scopeSchema": _plain(descriptor.scope_schema),
        "authorization": _plain(descriptor.authorization),
        "limits": _plain(descriptor.limits),
        "failurePolicy": _plain(descriptor.failure_policy),
        "algorithmVersions": _plain(descriptor.algorithm_versions),
    }


def validate_provider_descriptor(value: Mapping[str, Any]) -> None:
    root = _schema_root()
    schema = json.loads((root / "capability-provider.schema.json").read_text(encoding="utf-8"))
    common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        schema,
        resolver=RefResolver(
            schema["$id"], schema,
            store={common["$id"]: common, "common.schema.json": common},
        ),
    )
    error = next(validator.iter_errors(dict(value)), None)
    if error is not None:
        raise PlatformContractError("INVALID_PROVIDER_DESCRIPTOR", error.message)
    required_api = _version_core(value["platformApiVersion"])
    current_api = _version_core(PLATFORM_API_VERSION)
    if required_api[0] != current_api[0] or required_api[1] > current_api[1]:
        raise PlatformContractError(
            "PROVIDER_API_INCOMPATIBLE",
            f"Provider requires platform API {value['platformApiVersion']}; this Host provides {PLATFORM_API_VERSION}",
        )
    try:
        Draft202012Validator.check_schema(dict(value["scopeSchema"]))
    except SchemaError as error:
        raise PlatformContractError("PROVIDER_SCOPE_SCHEMA_INVALID", error.message) from error
    if value["scopeSchema"].get("type") != "object":
        raise PlatformContractError(
            "PROVIDER_SCOPE_SCHEMA_INVALID",
            "Provider scope schema must describe a top-level object",
        )


def load_provider_descriptor(
    source: str | Path | Mapping[str, Any],
) -> CapabilityProviderDescriptor:
    if isinstance(source, Mapping):
        value = dict(source)
    else:
        value = json.loads(Path(source).read_text(encoding="utf-8"))
    validate_provider_descriptor(value)
    return CapabilityProviderDescriptor(
        provider_id=value["providerId"],
        version=value["version"],
        platform_api_version=value["platformApiVersion"],
        capabilities=tuple(ProviderCapability(
            name=item["name"], version=item["version"],
            access_mode=item["accessMode"],
            evidence_kinds=tuple(item["evidenceKinds"]),
        ) for item in value["capabilities"]),
        scope_schema=value["scopeSchema"],
        authorization=value["authorization"],
        limits=value["limits"],
        failure_policy=tuple(value["failurePolicy"]),
        algorithm_versions=value["algorithmVersions"],
    )


@dataclass(frozen=True)
class ProviderRegistration:
    descriptor: CapabilityProviderDescriptor
    provider_factory: Callable[..., Any] | None = None

    @staticmethod
    def _invoke(factory: Callable[..., Any], runtime: Any) -> Any:
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            return factory(runtime)
        positional = tuple(
            parameter for parameter in signature.parameters.values()
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
            and parameter.default is inspect.Parameter.empty
        )
        return factory() if not positional else factory(runtime)

    def create_provider(self, runtime: Any = None) -> Any:
        if self.provider_factory is None:
            raise PlatformContractError(
                "PROVIDER_RUNTIME_UNAVAILABLE",
                f"Provider {self.descriptor.provider_id} does not expose a runtime factory",
            )
        return self._invoke(self.provider_factory, runtime)


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


__all__ = [
    "ProviderRegistration",
    "ProviderRegistry",
    "load_provider_descriptor",
    "validate_provider_descriptor",
    "_descriptor_payload",
]
