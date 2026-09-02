"""High-level runner for one registered platform plugin."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contract import PlatformContext, PlatformContractError, PlatformRunResult
from .kernel import PlatformKernel
from .ledger import JsonPlatformLedgerStore
from .plugin_registry import PluginRegistry
from .reporting import JsonSummaryPublisher


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
            result = kernel.publish(result, JsonSummaryPublisher(run_root))
        return result
