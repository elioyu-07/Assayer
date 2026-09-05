"""High-level runner for one registered platform plugin."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .capability_negotiation import CapabilityNegotiator
from .contract import (
    CapabilityProfile,
    PlatformContext,
    PlatformContractError,
    PlatformRunResult,
)
from .kernel import PlatformKernel
from .ledger import JsonPlatformLedgerStore
from .plugin_registry import PluginRegistry
from .provider_execution import BoundCapabilityProvider
from .provider_registry import ProviderRegistry
from .delivery_observer import PlatformDeliveryObserver


class PlatformRunner:
    """Select, execute, persist, and publish one registered plugin Check."""

    def __init__(self, registry: PluginRegistry, output_root: str | Path) -> None:
        self.registry = registry
        self.output_root = Path(output_root).expanduser().resolve()

    def run(
        self, *, plugin_id: str, check_id: str, scope: Any,
        check_version: str | None = None, run_id: str | None = None,
        runtime: Any = None,
    ) -> PlatformRunResult:
        registration = self.registry.select(plugin_id=plugin_id, check_id=check_id)
        if "batch" not in registration.execution_modes:
            raise PlatformContractError(
                "PLUGIN_EXECUTION_MODE_UNSUPPORTED",
                "The selected plugin requires an interactive execution adapter",
            )
        scope_error = next(Draft202012Validator(registration.scope_schema).iter_errors(scope), None)
        if scope_error is not None:
            raise PlatformContractError(
                "INVALID_SCOPE", "Plugin business scope does not satisfy its registered schema",
            )
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        run_root = self.output_root / run_id
        store = JsonPlatformLedgerStore(run_root)
        kernel = PlatformKernel(store)
        result = kernel.run_registered(
            self.registry, scope, check_id,
            PlatformContext(run_id, registration.capabilities),
            plugin_id=plugin_id, check_version=check_version, runtime=runtime,
        )
        if result.status != "failed" and result.receipts:
            result = kernel.publish(result, PlatformDeliveryObserver(run_root))
        return result

    def run_with_provider(
        self,
        *,
        plugin_id: str,
        check_id: str,
        scope: Any,
        provider_registry: ProviderRegistry,
        provider_scope: Mapping[str, Any],
        platform_profile: CapabilityProfile,
        user_profile: CapabilityProfile | None,
        check_version: str | None = None,
        provider_id: str | None = None,
        run_id: str | None = None,
        provider_runtime: Any = None,
    ) -> PlatformRunResult:
        """Run an external plugin through one fully negotiated provider."""
        registration = self.registry.select(plugin_id=plugin_id, check_id=check_id)
        if "batch" not in registration.execution_modes:
            raise PlatformContractError(
                "PLUGIN_EXECUTION_MODE_UNSUPPORTED",
                "The selected plugin requires an interactive execution adapter",
            )
        scope_error = next(Draft202012Validator(registration.scope_schema).iter_errors(scope), None)
        if scope_error is not None:
            raise PlatformContractError(
                "INVALID_SCOPE", "Plugin business scope does not satisfy its registered schema",
            )
        matches = tuple(
            check for check in registration.manifest.checks
            if check.check_id == check_id
            and (check_version is None or check.version == check_version)
        )
        if len(matches) != 1:
            code = "AMBIGUOUS_CHECK" if len(matches) > 1 else "UNKNOWN_CHECK"
            raise PlatformContractError(code, "A unique Check version is required for provider execution")
        check = matches[0]
        provider_registration = provider_registry.select_for_capabilities(
            check.required_capabilities,
            provider_id=provider_id,
        )
        negotiation = CapabilityNegotiator().negotiate(
            provider_registration,
            check.required_capabilities,
            platform_profile,
            user_profile=user_profile,
            scope=provider_scope,
        )
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        bound_provider = BoundCapabilityProvider(
            provider_registration,
            negotiation,
            run_id=run_id,
            scope=provider_scope,
            runtime=provider_runtime,
        )
        run_root = self.output_root / run_id
        store = JsonPlatformLedgerStore(run_root)
        kernel = PlatformKernel(store)
        try:
            result = kernel.run_registered(
                self.registry,
                scope,
                check.check_id,
                bound_provider.context,
                plugin_id=plugin_id,
                check_version=check.version,
                runtime=bound_provider,
                provider_evidence_expectation=bound_provider.evidence_expectation,
            )
        finally:
            bound_provider.close()
        provider_metrics = bound_provider.performance_metrics()
        merged_metrics = {**result.metrics, **provider_metrics}
        if result.ledger is not None:
            updated_ledger = replace(result.ledger, metrics=merged_metrics)
            result = replace(result, metrics=merged_metrics, ledger=updated_ledger)
            store.save(updated_ledger)
        else:
            result = replace(result, metrics=merged_metrics)
        if result.status != "failed" and result.receipts:
            result = kernel.publish(result, PlatformDeliveryObserver(run_root))
        return result
