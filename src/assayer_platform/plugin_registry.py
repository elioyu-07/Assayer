"""Plugin registration and selection for the Assayer platform.

The registry is deliberately small: it owns plugin identity and manifest
selection, while runtime adapters remain injected by the caller.  This keeps
the platform kernel independent from browsers, files, or any other domain
runtime and gives product transports one explicit plugin-selection boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import metadata
import inspect
from typing import Any, Callable, Iterable

from .contract import CheckContract, PlatformContractError, PluginManifest


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
    execution_modes: frozenset[str] = frozenset({"batch"})
    scope_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        modes = frozenset(self.execution_modes)
        if not modes or not modes.issubset({"batch", "interactive"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin execution modes are invalid",
            )
        object.__setattr__(self, "execution_modes", modes)
        if not isinstance(self.scope_schema, Mapping):
            raise PlatformContractError(
                "INVALID_PLUGIN_REGISTRATION", "Plugin scope schema must be an object",
            )
        object.__setattr__(self, "scope_schema", dict(self.scope_schema))

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


class PluginRegistry:
    """Validated registry of plugins available to one Assayer installation."""

    def __init__(self, registrations: Iterable[PluginRegistration] = ()) -> None:
        self._registrations: dict[str, PluginRegistration] = {}
        for registration in registrations:
            self.register(registration)

    @classmethod
    def from_entry_points(
        cls, *, group: str = "assayer.plugins",
        registrations: Iterable[PluginRegistration] = (),
    ) -> "PluginRegistry":
        """Load plugin registrations exported by installed distributions.

        An entry point may resolve to a ``PluginRegistration``, a callable
        returning one, or an object exposing ``registration``.  Invalid third
        party metadata fails closed with a stable platform error instead of
        being silently ignored.
        """
        registry = cls(registrations)
        try:
            discovered = metadata.entry_points()
            entries = discovered.select(group=group) if hasattr(discovered, "select") else discovered.get(group, ())
        except Exception as error:
            raise PlatformContractError("PLUGIN_DISCOVERY_FAILED", "Installed plugin entry points could not be loaded") from error
        for entry in entries:
            try:
                value = entry.load()
                if callable(value) and not isinstance(value, PluginRegistration):
                    value = value()
                if not isinstance(value, PluginRegistration):
                    value = getattr(value, "registration", None)
                if not isinstance(value, PluginRegistration):
                    raise TypeError("entry point did not provide PluginRegistration")
                registry.register(value)
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "INVALID_PLUGIN_REGISTRATION",
                    f"Plugin entry point {entry.name!r} is invalid",
                ) from error
        return registry

    def register(self, registration: PluginRegistration) -> None:
        plugin_id = registration.manifest.plugin_id
        if plugin_id in self._registrations:
            raise PlatformContractError("PLUGIN_CONFLICT", f"Plugin is already registered: {plugin_id}")
        self._registrations[plugin_id] = registration

    def unregister(self, plugin_id: str) -> None:
        if plugin_id not in self._registrations:
            raise PlatformContractError("UNKNOWN_PLUGIN", f"Plugin is not registered: {plugin_id}")
        del self._registrations[plugin_id]

    def list(self) -> tuple[PluginRegistration, ...]:
        return tuple(self._registrations.values())

    def get(self, plugin_id: str) -> PluginRegistration:
        registration = self._registrations.get(plugin_id)
        if registration is None:
            raise PlatformContractError("UNKNOWN_PLUGIN", f"Plugin is not registered: {plugin_id}")
        return registration

    def select(
        self, *, plugin_id: str | None = None, domain: str | None = None,
        check_id: str | None = None, check_ref: tuple[str, str] | None = None,
    ) -> PluginRegistration:
        """Select exactly one compatible registration or fail closed."""
        if plugin_id is not None:
            candidates = [self.get(plugin_id)]
        else:
            candidates = list(self._registrations.values())
        if domain is not None:
            candidates = [item for item in candidates if domain in item.manifest.domains]
        if check_id is not None:
            candidates = [
                item for item in candidates
                if any(check.check_id == check_id for check in item.manifest.checks)
            ]
        if check_ref is not None:
            candidates = [
                item for item in candidates
                if any(check.ref == check_ref for check in item.manifest.checks)
            ]
        if not candidates:
            raise PlatformContractError("PLUGIN_NOT_FOUND", "No registered plugin matches the requested scope")
        if len(candidates) > 1:
            ids = ", ".join(item.manifest.plugin_id for item in candidates)
            raise PlatformContractError("PLUGIN_AMBIGUOUS", f"Multiple registered plugins match the requested scope: {ids}")
        return candidates[0]

    @staticmethod
    def check(registration: PluginRegistration, check_ref: tuple[str, str]) -> CheckContract:
        for check in registration.manifest.checks:
            if check.ref == check_ref:
                return check
        raise PlatformContractError(
            "UNKNOWN_CHECK",
            f"Plugin {registration.manifest.plugin_id} does not declare Check {check_ref[0]}@{check_ref[1]}",
        )
