"""Capability-provider registration and descriptor loading.

These are plugin/provider-facing contracts: a capability provider builds a
:class:`ProviderRegistration` and may load its own descriptor without importing
:mod:`assayer_platform`.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import SchemaError

from .contract import (
    PLATFORM_API_VERSION,
    CapabilityProviderDescriptor,
    PlatformContractError,
    ProviderCapability,
)
from .resources import schema_root


def _version_core(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


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
    root = schema_root()
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


__all__ = [
    "ProviderRegistration",
    "load_provider_descriptor",
    "validate_provider_descriptor",
    "_descriptor_payload",
]
