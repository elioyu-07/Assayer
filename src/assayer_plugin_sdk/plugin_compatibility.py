"""Compatibility negotiation for the interactive plugin boundary.

The Host owns this handshake.  Plugin authors declare independent protocol and
SDK ranges; a Run is created only after those ranges (and protocol
capabilities) are known to be compatible.  The implementation intentionally
uses a tiny semver subset so it does not pull package-version policy into the
plugin contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contract import PlatformContractError


HOST_PROTOCOL_VERSION = "1.2.0"
HOST_SUPPORTED_PROTOCOL_VERSIONS = ("1.0.0", "1.1.0", HOST_PROTOCOL_VERSION)
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


def _validate_range(minimum: str, maximum: str, *, label: str) -> None:
    try:
        low, high = _version(minimum), _version(maximum)
    except ValueError as error:
        raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", f"Invalid {label} range") from error
    if low > high:
        raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", f"Invalid {label} range: minimum exceeds maximum")


@dataclass(frozen=True)
class PluginCompatibility:
    """Independent compatibility declaration carried by a registration."""

    protocol_min_version: str = "1.0.0"
    protocol_max_version: str = HOST_PROTOCOL_VERSION
    sdk_min_version: str = "0.1.0"
    sdk_max_version: str = HOST_SDK_VERSION
    capabilities: frozenset[str] = frozenset()
    domain_contract_version: str | None = None

    def __post_init__(self) -> None:
        _validate_range(self.protocol_min_version, self.protocol_max_version, label="protocol")
        _validate_range(self.sdk_min_version, self.sdk_max_version, label="SDK")
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
    adapter: str | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "protocolVersion": self.protocol_version,
            "sdkVersion": self.sdk_version,
            "capabilities": sorted(self.capabilities),
        }
        if self.adapter is not None:
            value["adapter"] = self.adapter
        return value


def negotiate_plugin_compatibility(
    declaration: PluginCompatibility | None,
    *,
    protocol_version: str = HOST_PROTOCOL_VERSION,
    sdk_version: str = HOST_SDK_VERSION,
    host_capabilities: Iterable[str] = HOST_PROTOCOL_CAPABILITIES,
    supported_protocol_versions: Iterable[str] = HOST_SUPPORTED_PROTOCOL_VERSIONS,
) -> CompatibilityResult:
    """Fail closed when a plugin cannot run on this Host.

    ``None`` is a legacy declaration and receives the broad compatibility
    window used by pre-negotiation registrations.
    """
    declaration = declaration or PluginCompatibility()
    try:
        sdk = _version(sdk_version)
        pmin, pmax = _version(declaration.protocol_min_version), _version(declaration.protocol_max_version)
        smin, smax = _version(declaration.sdk_min_version), _version(declaration.sdk_max_version)
    except ValueError as error:
        raise PlatformContractError("PLUGIN_COMPATIBILITY_INVALID", "Plugin compatibility declaration is invalid") from error
    supported = sorted(
        (
            (_version(value), value)
            for value in supported_protocol_versions
            if pmin <= _version(value) <= pmax
        ),
        reverse=True,
    )
    if not supported:
        raise PlatformContractError(
            "PLUGIN_PROTOCOL_INCOMPATIBLE",
            f"Plugin supports protocol {declaration.protocol_min_version}..{declaration.protocol_max_version}; Host provides {protocol_version}",
        )
    if not smin <= sdk <= smax:
        raise PlatformContractError(
            "PLUGIN_SDK_INCOMPATIBLE",
            f"Plugin supports SDK {declaration.sdk_min_version}..{declaration.sdk_max_version}; Host provides {sdk_version}",
        )
    host = frozenset(host_capabilities)
    missing = sorted(declaration.capabilities - host)
    if missing:
        raise PlatformContractError(
            "PLUGIN_CAPABILITY_INCOMPATIBLE",
            "Host does not support plugin protocol capabilities: " + ", ".join(missing),
        )
    negotiated_protocol = supported[0][1]
    adapter = (
        None if _version(negotiated_protocol) == _version(protocol_version)
        else f"host.compat.protocol-{negotiated_protocol}"
    )
    return CompatibilityResult(
        negotiated_protocol, sdk_version, declaration.capabilities & host, adapter,
    )


__all__ = [
    "HOST_PROTOCOL_VERSION", "HOST_SUPPORTED_PROTOCOL_VERSIONS",
    "HOST_SDK_VERSION", "HOST_PROTOCOL_CAPABILITIES",
    "PluginCompatibility", "CompatibilityResult", "negotiate_plugin_compatibility",
]
