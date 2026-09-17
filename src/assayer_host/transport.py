"""MCP transport for the data-only Assayer plugin contract.

The Host is deliberately the only runtime boundary. An ordinary plugin is
selected by its installed ``compiled-plugin.json`` artifact; it never supplies
Python registrations, runners, result objects, or platform identity fields.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from assayer_platform import (
    CapabilityProfile,
    CompiledInteractiveController,
    CompiledPluginLifecycleManager,
    PlatformContractError,
    grant_check_capabilities,
    installed_provider_registry,
    load_installed_compiled_plugin,
)
from assayer_platform.plugin_installation import PluginInstallationStore

from .errors import HostError
from .plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport
from .plugin_store_registry import default_store_root


_LOG = logging.getLogger(__name__)


class CompiledPlatformMcpToolTransport:
    """Expose only the platform-owned compiled contract workflow."""

    _SCHEMAS: dict[str, dict[str, Any]] = {
        "list_compiled_plugins": {"type": "object", "additionalProperties": False, "properties": {}},
        "start_compiled_run": {
            "type": "object", "additionalProperties": False,
            "required": ["pluginId", "checkId", "scope"],
            "properties": {"pluginId": {"type": "string", "minLength": 1}, "checkId": {"type": "string", "minLength": 1}, "scope": {}},
        },
        "bind_provider": {
            "type": "object", "additionalProperties": False, "required": ["runId"],
            "properties": {"runId": {"type": "string", "minLength": 1}, "providerId": {"type": "string", "minLength": 1}},
        },
        "discover_sources": {"type": "object", "additionalProperties": False, "required": ["runId"], "properties": {"runId": {"type": "string", "minLength": 1}}},
        "collect_evidence": {"type": "object", "additionalProperties": False, "required": ["runId"], "properties": {"runId": {"type": "string", "minLength": 1}}},
        "plan_review_batches": {
            "type": "object", "additionalProperties": False, "required": ["runId"],
            "properties": {"runId": {"type": "string", "minLength": 1}, "maxBatchItems": {"type": "integer", "minimum": 1, "maximum": 1000}, "maxBatchBytes": {"type": "integer", "minimum": 1024, "maximum": 1048576}},
        },
        "submit_review_batch": {
            "type": "object", "additionalProperties": False, "required": ["runId", "batchId", "decisions"],
            "properties": {"runId": {"type": "string", "minLength": 1}, "batchId": {"type": "string", "minLength": 1}, "decisions": {"type": "array", "items": {"type": "object"}}, "submissionId": {"type": "string", "minLength": 1}},
        },
        "finalize_compiled_run": {"type": "object", "additionalProperties": False, "required": ["runId"], "properties": {"runId": {"type": "string", "minLength": 1}}},
        "get_compiled_result": {"type": "object", "additionalProperties": False, "required": ["runId"], "properties": {"runId": {"type": "string", "minLength": 1}}},
    }

    def __init__(self, output_root: str | Path = "./assayer-output", *, store_root: str | Path | None = None,
                 provider_registry: Any = None, provider_runtime_resolver: Any = None,
                 platform_profile: CapabilityProfile | None = None, user_profile: CapabilityProfile | None = None) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        self._store_root = Path(store_root or default_store_root()).expanduser().resolve()
        self._lifecycle = CompiledPluginLifecycleManager(PluginInstallationStore(self._store_root))
        self._provider_registry = provider_registry or installed_provider_registry()
        # Concrete Provider runtimes are never bundled by the Host. An
        # embedding may explicitly supply a runtime resolver for an installed
        # Provider; absent that explicit dependency, binding is attempted with
        # no runtime and the Provider contract decides whether it is usable.
        self._provider_runtime_resolver = provider_runtime_resolver
        self._platform_profile = platform_profile or grant_check_capabilities
        self._user_profile = user_profile or grant_check_capabilities
        self._controllers: dict[str, CompiledInteractiveController] = {}
        self._active_run_id: str | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        descriptions = {
            "list_compiled_plugins": "List installed data-only compiled-plugin.json contracts.",
            "start_compiled_run": "Start a platform-owned Run from pluginId, checkId, and business scope.",
            "bind_provider": "Bind the platform provider required by the compiled input contract.",
            "discover_sources": "Discover and freeze every source WorkItem for the Run.",
            "collect_evidence": "Collect immutable Evidence for every discovered WorkItem.",
            "plan_review_batches": "Create exhaustive element x Check x Dimension ReviewBatches.",
            "submit_review_batch": "Submit exactly one five-state verdict for every atom in a ReviewBatch.",
            "finalize_compiled_run": "Finalize only after every ReviewAtom has a terminal verdict.",
            "get_compiled_result": "Read the terminal compiled Run result.",
        }
        return [{"name": name, "description": descriptions[name], "inputSchema": deepcopy(schema)} for name, schema in self._SCHEMAS.items()]

    def _controller(self, run_id: str) -> CompiledInteractiveController:
        controller = self._controllers.get(run_id)
        if controller is None:
            raise PlatformContractError("UNKNOWN_RUN", f"Compiled Run does not exist: {run_id}")
        return controller

    def _respond(self, payload: dict[str, Any]) -> dict[str, Any]:
        failed = payload.get("status") in {"failed", "blocked"}
        return {"structuredContent": {"status": "failed" if failed else "ok", "result": payload}, "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}], "isError": failed}

    def _error(self, error: PlatformContractError) -> dict[str, Any]:
        return self._respond({"status": "failed", "error": {"code": error.code, "message": error.message}})

    def call_tool(self, name: str, arguments: object) -> dict[str, Any]:
        if name not in self._SCHEMAS:
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict):
            raise HostError("INVALID_REQUEST", f"{name} arguments must be an object")
        validation_error = next(Draft202012Validator(self._SCHEMAS[name]).iter_errors(arguments), None)
        if validation_error is not None:
            return self._respond({"status": "failed", "error": {"code": "INVALID_REQUEST", "message": f"{name} arguments do not satisfy the compiled runtime schema"}})
        try:
            if name == "list_compiled_plugins":
                return self._respond({"status": "completed", "plugins": self._lifecycle.list()})
            if name == "start_compiled_run":
                if self._active_run_id is not None:
                    raise PlatformContractError("RUN_CONFLICT", "A compiled Run is already active")
                contract = load_installed_compiled_plugin(PluginInstallationStore(self._store_root), arguments["pluginId"])
                controller = CompiledInteractiveController(contract, self._output_root)
                result = controller.start(plugin_id=arguments["pluginId"], check_id=arguments["checkId"], scope=arguments["scope"])
                run_id = result["runId"]
                self._controllers[run_id] = controller
                self._active_run_id = run_id
                return self._respond(result)
            run_id = arguments["runId"]
            if name == "get_compiled_result":
                return self._respond(CompiledInteractiveController.read_result(self._output_root, run_id))
            controller = self._controller(run_id)
            if name == "bind_provider":
                run = controller._run(run_id)
                runtime = (
                    self._provider_runtime_resolver(None, run.check, run.scope)
                    if self._provider_runtime_resolver is not None else None
                )
                platform_profile = self._platform_profile
                user_profile = self._user_profile
                if callable(platform_profile):
                    platform_profile = platform_profile(None, run.check)
                if callable(user_profile):
                    user_profile = user_profile(None, run.check)
                return self._respond(controller.bind_provider(run_id, provider_registry=self._provider_registry, platform_profile=platform_profile, user_profile=user_profile, provider_id=arguments.get("providerId"), runtime=runtime))
            if name == "discover_sources":
                return self._respond(controller.discover(run_id))
            if name == "collect_evidence":
                return self._respond(controller.collect(run_id))
            if name == "plan_review_batches":
                return self._respond(controller.plan_review_batches(run_id, max_batch_items=arguments.get("maxBatchItems", 32), max_batch_bytes=arguments.get("maxBatchBytes", 24 * 1024)))
            if name == "submit_review_batch":
                return self._respond(controller.submit_review_batch(run_id, arguments["batchId"], arguments["decisions"], submission_id=arguments.get("submissionId")))
            if name == "finalize_compiled_run":
                result = controller.finalize(run_id)
                if self._active_run_id == run_id:
                    self._active_run_id = None
                return self._respond(result)
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        except PlatformContractError as error:
            return self._error(error)

    def close(self) -> None:
        for run_id, controller in tuple(self._controllers.items()):
            try:
                controller.close(run_id)
            except PlatformContractError as error:
                _LOG.warning(
                    "Compiled Run teardown failed for %s: %s (%s)",
                    run_id, error.message, error.code,
                )
        self._active_run_id = None


def _load_fast_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.server import Settings
    except ImportError as error:
        raise RuntimeError("MCP SDK is not installed; install assayer[mcp]") from error
    Settings.model_rebuild()
    return FastMCP


def _mcp_tool_annotations(item: Mapping[str, Any]):
    annotations = item.get("annotations")
    if annotations is None:
        return None
    from mcp.types import ToolAnnotations
    return ToolAnnotations(**annotations)


def create_compiled_mcp_server(*, output_root: str | Path = "./assayer-output", store_root: str | Path | None = None,
                               provider_registry: Any = None, provider_runtime_resolver: Any = None,
                               platform_profile: CapabilityProfile | None = None, user_profile: CapabilityProfile | None = None):
    """Create the compiled-contract MCP server and lifecycle tools."""
    FastMCP = _load_fast_mcp()
    server = FastMCP("Assayer Compiled Plugins")
    adapter = CompiledPlatformMcpToolTransport(output_root, store_root=store_root, provider_registry=provider_registry, provider_runtime_resolver=provider_runtime_resolver, platform_profile=platform_profile, user_profile=user_profile)
    server._assayer_transport = adapter

    def make_invoke(transport, tool_name: str, input_schema: dict[str, Any]):
        def invoke(**kwargs: Any) -> dict[str, Any]:
            return transport.call_tool(tool_name, {key: value for key, value in kwargs.items() if value is not None})
        invoke.__name__ = f"assayer_{tool_name}"
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))
        ordered = [key for key in properties if key in required] + [key for key in properties if key not in required]
        invoke.__signature__ = inspect.Signature(parameters=[inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY, annotation=Any, default=inspect.Parameter.empty if key in required else None) for key in ordered])
        return invoke

    def register(transport):
        for item in transport.list_tools():
            server.tool(name=item["name"], description=item["description"], annotations=_mcp_tool_annotations(item))(make_invoke(transport, item["name"], item["inputSchema"]))
            registered = server._tool_manager.get_tool(item["name"])
            if registered is not None:
                registered.parameters = deepcopy(item["inputSchema"])

    register(adapter)
    lifecycle = PluginLifecycleMcpToolTransport(str(store_root or default_store_root()), active_run_guard=lambda: adapter._active_run_id is not None)
    server._assayer_lifecycle_transport = lifecycle
    register(lifecycle)
    return server


def mcp_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assayer compiled-plugin MCP stdio server")
    parser.add_argument("--output-root", default="./assayer-output")
    parser.add_argument("--store", default=str(default_store_root()))
    args = parser.parse_args(argv)
    server = create_compiled_mcp_server(output_root=args.output_root, store_root=args.store, provider_registry=installed_provider_registry(), platform_profile=grant_check_capabilities, user_profile=grant_check_capabilities)
    try:
        server.run(transport="stdio")
    finally:
        transport = getattr(server, "_assayer_transport", None)
        if transport is not None:
            transport.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return mcp_main(argv)


if __name__ == "__main__":
    raise SystemExit(mcp_main())


__all__ = ["CompiledPlatformMcpToolTransport", "create_compiled_mcp_server", "mcp_main"]
