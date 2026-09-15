"""The exact current contract for the interactive plugin boundary.

The Host owns this handshake.  This is deliberately not a compatibility
window: a plugin must declare the exact protocol and SDK contract used by the
Host.  Old protocol envelopes, old SDK ranges, and adapter identities are
rejected before a Run is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contract import PlatformContractError


HOST_PROTOCOL_VERSION = "1.2.0"
HOST_SDK_VERSION = "0.1.2"
HOST_PROTOCOL_CAPABILITIES = frozenset({
    "task_local_evidence_handles",
    "domain_result",
    "supported_by",
})


def _version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError("version must be a string")
    core = value.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid semantic version: {value}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _validate_exact(minimum: str, maximum: str, *, label: str) -> None:
    try:
        low, high = _version(minimum), _version(maximum)
    except ValueError as error:
        raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", f"Invalid {label} range") from error
    if low != high:
        raise PlatformContractError(
            "PLUGIN_COMPATIBILITY_RANGE_UNSUPPORTED",
            f"The hard-cut contract requires one exact {label} version",
        )


@dataclass(frozen=True)
class PluginCompatibility:
    """Exact protocol/SDK declaration carried by a registration."""

    protocol_min_version: str = HOST_PROTOCOL_VERSION
    protocol_max_version: str = HOST_PROTOCOL_VERSION
    sdk_min_version: str = HOST_SDK_VERSION
    sdk_max_version: str = HOST_SDK_VERSION
    capabilities: frozenset[str] = frozenset()
    domain_contract_version: str | None = None

    def __post_init__(self) -> None:
        _validate_exact(self.protocol_min_version, self.protocol_max_version, label="protocol")
        _validate_exact(self.sdk_min_version, self.sdk_max_version, label="SDK")
        if self.domain_contract_version is not None:
            try:
                _version(self.domain_contract_version)
            except ValueError as error:
                raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", "Invalid domain contract version") from error
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


@dataclass(frozen=True)
class CompatibilityResult:
    protocol_version: str
    sdk_version: str
    capabilities: frozenset[str]

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "protocolVersion": self.protocol_version,
            "sdkVersion": self.sdk_version,
            "capabilities": sorted(self.capabilities),
        }
        return value


def negotiate_plugin_compatibility(
    declaration: PluginCompatibility | None,
    *,
    protocol_version: str = HOST_PROTOCOL_VERSION,
    sdk_version: str = HOST_SDK_VERSION,
    host_capabilities: Iterable[str] = HOST_PROTOCOL_CAPABILITIES,
) -> CompatibilityResult:
    """Fail closed unless the plugin declares the exact current contract."""
    if declaration is None:
        raise PlatformContractError(
            "PLUGIN_COMPATIBILITY_REQUIRED",
            "The plugin must declare the current exact protocol and SDK contract",
        )
    try:
        protocol = _version(declaration.protocol_min_version)
        sdk_declared = _version(declaration.sdk_min_version)
        host_protocol = _version(protocol_version)
        sdk = _version(sdk_version)
    except ValueError as error:
        raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", "Plugin compatibility declaration is invalid") from error
    if protocol != host_protocol:
        raise PlatformContractError(
            "PLUGIN_PROTOCOL_INCOMPATIBLE",
            f"Plugin requires protocol {declaration.protocol_min_version}; Host provides {protocol_version}",
        )
    if sdk_declared != sdk:
        raise PlatformContractError(
            "PLUGIN_SDK_INCOMPATIBLE",
            f"Plugin requires SDK {declaration.sdk_min_version}; Host provides {sdk_version}",
        )
    host = frozenset(host_capabilities)
    missing = sorted(declaration.capabilities - host)
    if missing:
        raise PlatformContractError(
            "PLUGIN_CAPABILITY_INCOMPATIBLE",
            "Host does not support plugin protocol capabilities: " + ", ".join(missing),
        )
    return CompatibilityResult(
        protocol_version, sdk_version, declaration.capabilities & host,
    )


__all__ = [
    "HOST_PROTOCOL_VERSION",
    "HOST_SDK_VERSION", "HOST_PROTOCOL_CAPABILITIES",
    "PluginCompatibility", "CompatibilityResult", "negotiate_plugin_compatibility",
]
