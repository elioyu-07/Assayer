"""Four-way capability and budget negotiation for provider-backed Runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator, RefResolver

from .contract import CapabilityProfile, PlatformContext, PlatformContractError
from .provider_registry import ProviderRegistration, _plain
from .registry import _schema_root


_LIMITS = ("timeoutMs", "maxBytes", "maxItems", "maxConcurrency")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class CapabilityNegotiation:
    status: str
    provider_id: str
    provider_version: str
    requested: tuple[str, ...]
    granted: tuple[str, ...]
    denied: tuple[Mapping[str, str], ...]
    limits: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "denied",
            tuple(MappingProxyType(dict(item)) for item in self.denied),
        )
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "status": self.status,
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "requested": list(self.requested),
            "granted": list(self.granted),
            "denied": [dict(item) for item in self.denied],
            "limits": dict(self.limits),
        }

    def context(self, run_id: str) -> PlatformContext:
        if self.status != "ready":
            raise PlatformContractError(
                "CAPABILITY_NEGOTIATION_BLOCKED",
                "A blocked capability negotiation cannot create a PlatformContext",
            )
        return PlatformContext(run_id, frozenset(self.granted), self.limits)


class CapabilityNegotiator:
    """Compute the effective profile without allowing any party to widen it."""

    def __init__(self) -> None:
        root = _schema_root()
        schema = json.loads((root / "capability-negotiation.schema.json").read_text(encoding="utf-8"))
        common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            schema,
            resolver=RefResolver(
                schema["$id"], schema,
                store={common["$id"]: common, "common.schema.json": common},
            ),
        )

    def negotiate(
        self,
        registration: ProviderRegistration,
        required_capabilities: tuple[str, ...] | list[str] | frozenset[str],
        platform_profile: CapabilityProfile,
        *,
        user_profile: CapabilityProfile | None,
        scope: object,
    ) -> CapabilityNegotiation:
        descriptor = registration.descriptor
        scope_error = next(Draft202012Validator(_plain(descriptor.scope_schema)).iter_errors(scope), None)
        if scope_error is not None:
            raise PlatformContractError(
                "PROVIDER_SCOPE_INVALID",
                "Provider scope does not satisfy the registered business-input schema",
            )
        raw_requested = tuple(required_capabilities)
        if any(not isinstance(item, str) or _CAPABILITY.fullmatch(item) is None for item in raw_requested):
            raise PlatformContractError(
                "CAPABILITY_REQUEST_INVALID",
                "Required capabilities must be nonempty names",
            )
        requested = tuple(sorted(set(raw_requested)))
        declared = {item.name for item in descriptor.capabilities}
        platform = set(platform_profile.capabilities)
        if user_profile is None:
            user = set() if descriptor.authorization["userScopeRequired"] else set(requested)
        else:
            user = set(user_profile.capabilities)
        granted = tuple(sorted(set(requested) & declared & platform & user))
        denied = []
        for capability in requested:
            if capability not in declared:
                code = "provider_absent"
                message = "The selected provider does not declare this capability."
            elif capability not in platform:
                code = "platform_denied"
                message = "Platform policy does not allow this capability."
            elif capability not in user:
                code = "user_scope_missing"
                message = "The user scope does not authorize this capability."
            else:
                continue
            denied.append({
                "capability": capability,
                "code": code,
                "message": message,
            })
        limits = self._limits(
            descriptor.limits, platform_profile.limits,
            user_profile.limits if user_profile is not None else {},
        )
        result = CapabilityNegotiation(
            "blocked" if denied else "ready",
            descriptor.provider_id,
            descriptor.version,
            requested,
            granted,
            tuple(denied),
            limits,
        )
        self._validator.validate(result.as_dict())
        return result

    @staticmethod
    def _limits(*profiles: Mapping[str, Any]) -> dict[str, int]:
        effective: dict[str, int] = {}
        provider = profiles[0]
        for name in _LIMITS:
            candidates = []
            for profile in profiles:
                if name not in profile:
                    continue
                value = profile[name]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise PlatformContractError(
                        "CAPABILITY_LIMIT_INVALID",
                        f"Capability limit {name} must be a positive integer",
                    )
                candidates.append(value)
            if name not in provider or not candidates:
                raise PlatformContractError(
                    "CAPABILITY_LIMIT_INVALID",
                    f"Provider limit {name} is required",
                )
            effective[name] = min(candidates)
        return effective
