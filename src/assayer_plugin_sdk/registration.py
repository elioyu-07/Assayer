"""The plugin registration contract.

A registration is the declarative description a plugin provides at install
time: its manifest, factories, execution modes, scope schema, and exact
current-contract declaration. It depends only on SDK contracts and the
standard library, so a plugin can build one without importing
:mod:`assayer_platform`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable

from .agent_contract import DomainResultContract
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
    # Subset of the Check capabilities the plugin consumes from a Host-bound
    # capability provider rather than a Host direct grant.  Declaring them lets
    # the Host fail closed when a provider is missing instead of silently
    # treating a provider capability as host-granted.
    provider_capabilities: frozenset[str] = frozenset()
    # Subset of provider capabilities that own WorkItem/source discovery. When
    # present, the Host asks the bound provider to create WorkItems instead of
    # invoking plugin discovery code.
    provider_source_capabilities: frozenset[str] = frozenset()
    result_features: frozenset[str] = frozenset()
    execution_modes: frozenset[str] = frozenset({"batch"})
    scope_schema: Mapping[str, Any] = field(default_factory=dict)
    # Optional plugin-declared mapping from the plugin's business scope to the
    # capability provider authorization scope.  It keeps domain knowledge in
    # the plugin while letting the Host stay domain-agnostic; the Host invokes
    # it as ``resolver(scope, check)`` and validates the result against the
    # provider schema during capability negotiation.
    provider_scope_resolver: Callable[[Any, Any], Mapping[str, Any] | None] | None = None
    domain_result_contracts: tuple[DomainResultContract, ...] = ()
    # Exact current protocol/SDK contract declaration. Installed manifests
    # must declare the same value; in-process registrations inherit only that
    # already-validated manifest declaration.
    compatibility: PluginCompatibility | None = None
    # Compiler-owned semantic guidance for the common-review path. Ordinary
    # authors provide Markdown; the compiler freezes its package path and
    # digest so the Host can expose it without guessing source locations.
    semantic_instructions_path: str | None = None
    semantic_instructions_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "provider_capabilities", frozenset(self.provider_capabilities))
        source_capabilities = frozenset(self.provider_source_capabilities)
        if not source_capabilities.issubset(self.provider_capabilities):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION",
                "Provider source capabilities must be declared provider capabilities",
            )
        object.__setattr__(self, "provider_source_capabilities", source_capabilities)
        modes = frozenset(self.execution_modes)
        if not modes or not modes.issubset({"batch", "interactive"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin execution modes are invalid",
            )
        object.__setattr__(self, "execution_modes", modes)
        result_features = frozenset(self.result_features)
        if not result_features.issubset({"evidence_graph", "common_review"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin result features are invalid",
            )
        object.__setattr__(self, "result_features", result_features)
        if not isinstance(self.scope_schema, Mapping):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin scope schema must be an object",
            )
        object.__setattr__(self, "scope_schema", dict(self.scope_schema))
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
        if self.compatibility is None and getattr(self.manifest, "compatibility", None) is not None:
            object.__setattr__(self, "compatibility", self.manifest.compatibility)
        elif self.compatibility is None:
            raise PlatformContractError(
                "PLUGIN_COMPATIBILITY_REQUIRED",
                "Plugin registration must declare the exact current protocol and SDK contract",
            )
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
        semantic_path = self.semantic_instructions_path
        semantic_digest = self.semantic_instructions_sha256
        if (semantic_path is None) != (semantic_digest is None):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION",
                "Semantic instructions path and digest must be declared together",
            )
        if semantic_path is not None:
            parts = semantic_path.split("/")
            if (
                not parts or any(not part or part in {".", ".."} for part in parts)
                or "\\" in semantic_path or semantic_path.startswith("/")
            ):
                raise PlatformContractError(
                    "INVALID_PLUGIN_REGISTRATION",
                    "Semantic instructions path must be package-relative",
                )
            if (
                not isinstance(semantic_digest, str)
                or len(semantic_digest) != 64
                or any(character not in "0123456789abcdef" for character in semantic_digest)
            ):
                raise PlatformContractError(
                    "INVALID_PLUGIN_REGISTRATION",
                    "Semantic instructions digest must be lowercase SHA-256",
                )

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

    def domain_result_contract_for(
        self, check_ref: tuple[str, str],
    ) -> DomainResultContract | None:
        """Return the target domain-only Agent contract for a Check."""
        return next(
            (item for item in self.domain_result_contracts if item.check_ref == check_ref),
            None,
        )


__all__ = ["PluginRegistration"]
