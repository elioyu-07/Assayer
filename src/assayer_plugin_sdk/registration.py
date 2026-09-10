"""The plugin registration contract.

A registration is the declarative description a plugin provides at install
time: its manifest, factories, execution modes, scope schema, and compatibility
window. It depends only on SDK contracts and the standard library, so a plugin
can build one without importing :mod:`assayer_platform`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable

from .agent_contract import AgentContractBundle, DomainResultContract
from .contract import CheckContract, PlatformContractError, PluginManifest
from .plugin_compatibility import PluginCompatibility


@dataclass(frozen=True)
class PluginRegistration:
    """One validated plugin registration.

    ``runtime_factory`` is optional because the generic kernel accepts an
    already-created runtime/plugin object.  Product adapters may use it to
    construct a domain runtime after the Host has selected the registration.
    """

    manifest: PluginManifest
    runtime_factory: Callable[..., Any] | None = None
    plugin_factory: Callable[..., Any] | None = None
    decision_provider_factory: Callable[..., Any] | None = None
    committer_factory: Callable[..., Any] | None = None
    capabilities: frozenset[str] = frozenset()
    result_features: frozenset[str] = frozenset()
    execution_modes: frozenset[str] = frozenset({"batch"})
    scope_schema: Mapping[str, Any] = field(default_factory=dict)
    review_payload_schema: Mapping[str, Any] = field(default_factory=dict)
    agent_contracts: tuple[AgentContractBundle, ...] = ()
    domain_result_contracts: tuple[DomainResultContract, ...] = ()
    # Independent protocol/SDK compatibility declaration.  Defaults preserve
    # registrations created before the handshake was introduced.
    compatibility: PluginCompatibility | None = None
    # Convenience aliases for factories that construct registrations directly.
    protocol_min_version: str | None = None
    protocol_max_version: str | None = None
    sdk_min_version: str | None = None
    sdk_max_version: str | None = None
    protocol_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "protocol_capabilities", frozenset(self.protocol_capabilities))
        modes = frozenset(self.execution_modes)
        if not modes or not modes.issubset({"batch", "interactive"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin execution modes are invalid",
            )
        object.__setattr__(self, "execution_modes", modes)
        result_features = frozenset(self.result_features)
        if not result_features.issubset({"evidence_graph"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin result features are invalid",
            )
        object.__setattr__(self, "result_features", result_features)
        if not isinstance(self.scope_schema, Mapping):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin scope schema must be an object",
            )
        object.__setattr__(self, "scope_schema", dict(self.scope_schema))
        if not isinstance(self.review_payload_schema, Mapping):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin review payload schema must be an object",
            )
        object.__setattr__(self, "review_payload_schema", dict(self.review_payload_schema))
        if modes.intersection({"interactive"}) and self.review_payload_schema:
            raise PlatformContractError(
                "PLUGIN_LEGACY_CONTRACT_UNSUPPORTED",
                "Interactive registrations must publish AgentContractBundle schemas; review_payload_schema is unsupported",
            )
        try:
            agent_contracts = tuple(self.agent_contracts)
        except TypeError as error:
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin Agent contracts must be an array",
            ) from error
        if any(not isinstance(item, AgentContractBundle) for item in agent_contracts):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION",
                "Every plugin Agent contract must be an AgentContractBundle",
            )
        if agent_contracts:
            raise PlatformContractError(
                "PLUGIN_LEGACY_CONTRACT_UNSUPPORTED",
                "AgentContractBundle is removed from the public Plugin SDK; publish DomainResultContract instead",
            )
        object.__setattr__(self, "agent_contracts", agent_contracts)
        try:
            domain_result_contracts = tuple(self.domain_result_contracts)
        except TypeError as error:
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin domain result contracts must be an array",
            ) from error
        if any(not isinstance(item, DomainResultContract) for item in domain_result_contracts):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION",
                "Every plugin domain result contract must be a DomainResultContract",
            )
        object.__setattr__(self, "domain_result_contracts", domain_result_contracts)
        direct_values = (
            self.protocol_min_version, self.protocol_max_version,
            self.sdk_min_version, self.sdk_max_version,
        )
        if self.compatibility is None and (
            any(value is not None for value in direct_values)
            or bool(self.protocol_capabilities)
        ):
            object.__setattr__(self, "compatibility", PluginCompatibility(
                protocol_min_version=self.protocol_min_version or "1.0.0",
                protocol_max_version=self.protocol_max_version or "1.2.0",
                sdk_min_version=self.sdk_min_version or "0.1.0",
                sdk_max_version=self.sdk_max_version or "0.1.2",
                capabilities=frozenset(self.protocol_capabilities),
            ))
        elif self.compatibility is None and getattr(self.manifest, "compatibility", None) is not None:
            object.__setattr__(self, "compatibility", self.manifest.compatibility)
        if self.compatibility is not None and not isinstance(self.compatibility, PluginCompatibility):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION",
                "Plugin compatibility must be a PluginCompatibility declaration",
            )
        manifest_compatibility = getattr(self.manifest, "compatibility", None)
        if (
            manifest_compatibility is not None
            and self.compatibility is not None
            and manifest_compatibility != self.compatibility
        ):
            raise PlatformContractError(
                "PLUGIN_COMPATIBILITY_IDENTITY_MISMATCH",
                "Plugin registration compatibility differs from its manifest declaration",
            )
        if self.compatibility is not None:
            object.__setattr__(self, "protocol_min_version", self.compatibility.protocol_min_version)
            object.__setattr__(self, "protocol_max_version", self.compatibility.protocol_max_version)
            object.__setattr__(self, "sdk_min_version", self.compatibility.sdk_min_version)
            object.__setattr__(self, "sdk_max_version", self.compatibility.sdk_max_version)
            object.__setattr__(self, "protocol_capabilities", self.compatibility.capabilities)

    @staticmethod
    def _invoke(factory: Callable[..., Any], runtime: Any) -> Any:
        """Invoke a factory without swallowing TypeError from its body."""
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            # Some extension callables do not expose a signature.  Their
            # contract is the one-argument form, so let any implementation
            # error propagate instead of silently retrying with another shape.
            return factory(runtime)
        try:
            signature.bind(runtime)
        except TypeError:
            signature.bind()
            return factory()
        return factory(runtime)

    def create_plugin(self, runtime: Any = None) -> Any:
        if self.plugin_factory is None:
            raise PlatformContractError(
                "PLUGIN_RUNTIME_UNAVAILABLE",
                f"Plugin {self.manifest.plugin_id} does not expose a platform plugin factory",
            )
        return self._invoke(self.plugin_factory, runtime)

    def create_decision_provider(self, runtime: Any = None) -> Any:
        if self.decision_provider_factory is None:
            raise PlatformContractError(
                "PLUGIN_DECISION_UNAVAILABLE",
                f"Plugin {self.manifest.plugin_id} does not expose a decision provider",
            )
        return self._invoke(self.decision_provider_factory, runtime)

    def create_committer(self, runtime: Any = None) -> Any | None:
        if self.committer_factory is None:
            return None
        return self._invoke(self.committer_factory, runtime)

    def agent_contract_for(
        self, check_ref: tuple[str, str],
    ) -> AgentContractBundle | None:
        """Return the executable Agent contract for a Check when declared."""
        return next(
            (item for item in self.agent_contracts if item.check_ref == check_ref),
            None,
        )

    def domain_result_contract_for(
        self, check_ref: tuple[str, str],
    ) -> DomainResultContract | None:
        """Return the target domain-only Agent contract for a Check."""
        return next(
            (item for item in self.domain_result_contracts if item.check_ref == check_ref),
            None,
        )


__all__ = ["PluginRegistration"]
