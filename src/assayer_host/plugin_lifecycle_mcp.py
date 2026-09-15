"""MCP facade for the plugin installation lifecycle.

The Agent-facing product surface for plugin management.  Each tool maps one
deterministic lifecycle operation to the same ``execute_intent_step`` path the
CLI uses, so a natural-language request ("install ass-spec") and a scripted
tool call land on identical, fail-closed code.

The product-facing ``apply_plugin_change`` tool collapses the normal mutation
path into one Host transaction for a trusted additive install. Destructive or
ambiguous changes still return a compact one-time confirmation token. The
lower-level ``plan_plugin_change``/``execute_plugin_change`` pair remains
available for advanced clients, CI, and recovery. The token is bound to the
store digest, catalog digest (or package digest for local sources), and
resolved source, so changes underneath a plan invalidate it.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from jsonschema import Draft202012Validator
from assayer_platform import PlatformContractError
from assayer_platform.plugin_catalog import version_key
from assayer_platform.plugin_verify import verify_plugin_source as run_plugin_source_verification

from .plugin_intent import IntentStep
from .plugin_lifecycle_ops import (
    DEFAULT_CATALOG_URL,
    apply_resolved_catalog_change,
    execute_intent_step,
    latest_available_from,
    plan_plugin_change,
    store_index_digest,
    verify_plan_binding,
)

_ALLOWED_OPERATIONS = ("install", "upgrade", "downgrade", "rollback", "uninstall")


class PluginLifecycleMcpToolTransport:
    """Deterministic MCP tools for install/upgrade/uninstall/list/info/downgrade/rollback."""

    def __init__(self, store_root: str, *, catalog_index: str = DEFAULT_CATALOG_URL,
                 active_run_guard=None, plan_ttl: float = 300.0):
        self._store_root = str(store_root)
        self._catalog_index = catalog_index
        self._active_run_guard = active_run_guard
        self._plan_ttl = plan_ttl
        self._plans: dict[str, dict] = {}

    _SCHEMAS = {
        "verify_plugin_source": {
            "type": "object", "additionalProperties": False,
            "required": ["source"],
            "properties": {
                "source": {"type": "string", "minLength": 1},
            },
        },
        "list_plugins": {"type": "object", "additionalProperties": False, "properties": {}},
        "get_plugin_info": {
            "type": "object", "additionalProperties": False,
            "required": ["pluginId"],
            "properties": {"pluginId": {"type": "string", "minLength": 1}},
        },
        "plan_plugin_change": {
            "type": "object", "additionalProperties": False,
            "required": ["operation"],
            "properties": {
                "operation": {"type": "string", "enum": sorted(_ALLOWED_OPERATIONS)},
                "plugin": {"type": "string", "minLength": 1},
                "pluginId": {"type": "string", "minLength": 1},
                "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"},
            },
        },
        "execute_plugin_change": {
            "type": "object", "additionalProperties": False,
            "required": ["token", "confirmed"],
            "properties": {
                "token": {"type": "string", "minLength": 1},
                "confirmed": {"type": "boolean"},
            },
        },
        "apply_plugin_change": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "operation": {"type": "string", "enum": sorted(_ALLOWED_OPERATIONS)},
                "plugin": {"type": "string", "minLength": 1},
                "pluginId": {"type": "string", "minLength": 1},
                "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"},
                "token": {"type": "string", "minLength": 1},
                "confirmed": {"type": "boolean"},
            },
        },
    }

    def _guard_blocked(self) -> dict | None:
        """Reject mutating operations while an interactive Run is active.

        The interactive Run transport owns active-run state; this transport is
        only told about it through the injected guard so the two facades never
        share mutable state directly.
        """
        if self._active_run_guard is None:
            return None
        if self._active_run_guard():
            return {
                "operation": "blocked",
                "status": "failed",
                "error": {
                    "code": "RUN_ACTIVE",
                    "message": "A plugin Run is active; finish or abort it before changing installed plugins.",
                },
            }
        return None

    def _run(self, step: IntentStep) -> dict:
        latest_available = (
            latest_available_from(self._catalog_index)
            if step.operation in {"list", "info"} else None
        )
        result = execute_intent_step(
            step, self._store_root, "./assayer-output",
            latest_available, self._catalog_index,
        )
        if step.operation == "info" and result.get("status") == "completed":
            latest_version = (
                latest_available(step.plugin_id)
                if latest_available is not None else None
            )
            active_version = result.get("activeVersion")
            version_relation = "unknown"
            if latest_version is not None and active_version is not None:
                if version_key(latest_version) > version_key(active_version):
                    version_relation = "update_available"
                elif version_key(latest_version) < version_key(active_version):
                    version_relation = "installed_ahead_of_catalog"
                else:
                    version_relation = "current"
            result.update({
                "catalogStatus": (
                    "available" if latest_available is not None else "unavailable"
                ),
                "latestAvailableVersion": latest_version,
                "latestVersionKnown": latest_version is not None,
                "versionRelation": version_relation,
            })
        return result

    def _respond(self, payload: dict) -> dict:
        failed = payload.get("status") not in {"completed", "passed", "quarantined"}
        return {
            "structuredContent": {"status": "failed" if failed else "ok", "result": payload},
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}],
            "isError": failed,
        }

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "verify_plugin_source",
                "description": (
                    "Verify a local Assayer plugin authoring directory as one exact wheel. "
                    "The Host recognizes Policy Pack, Simple SDK, or Advanced SPI source; "
                    "then compiles when needed, builds in isolation, validates and exercises "
                    "the exact wheel, and writes a passed artifact under SOURCE/.assayer/verified. "
                    "This does not install or publish the plugin."
                ),
                "inputSchema": self._SCHEMAS["verify_plugin_source"],
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
            {
                "name": "list_plugins",
                "description": "List installed plugins, their versions and lifecycle state, and mark plugins that have a newer version available in the public catalog as upgradable.",
                "inputSchema": self._SCHEMAS["list_plugins"],
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
            {
                "name": "get_plugin_info",
                "description": "Use this single read-only lookup for an installed plugin's active version, lifecycle state, catalog status, latest available catalog version, and versionRelation. If latestVersionKnown is false, report that the upstream latest version is unknown; do not scan repositories or retry.",
                "inputSchema": self._SCHEMAS["get_plugin_info"],
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
            {
                "name": "plan_plugin_change",
                "description": "Resolve a read-only, deterministic plan for a plugin mutation (install/upgrade/downgrade/rollback/uninstall) and return a one-time token for execute_plugin_change.",
                "inputSchema": self._SCHEMAS["plan_plugin_change"],
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            },
            {
                "name": "execute_plugin_change",
                "description": "Execute a previously planned plugin mutation. Requires the plan token and confirmed=true.",
                "inputSchema": self._SCHEMAS["execute_plugin_change"],
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            },
            {
                "name": "apply_plugin_change",
                "description": "Apply one natural-language plugin lifecycle intent. A trusted first-time catalog install completes in one Host transaction; upgrades, downgrades, rollbacks, uninstalls, and local sources return one compact confirmation plan.",
                "inputSchema": self._SCHEMAS["apply_plugin_change"],
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            },
        ]

    def call_tool(self, name: str, arguments: object) -> dict:
        if not isinstance(arguments, dict):
            from .errors import HostError
            raise HostError("INVALID_REQUEST", f"{name} arguments must be an object")
        schema = self._SCHEMAS.get(name)
        if schema is None:
            from .errors import HostError
            raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")
        if not isinstance(arguments, dict) or next(Draft202012Validator(schema).iter_errors(arguments), None):
            return self._respond({
                "operation": name,
                "status": "failed",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": f"{name} arguments do not satisfy the lifecycle tool schema.",
                    "retryable": False,
                    "nextAction": "correct_request",
                },
            })

        if name == "list_plugins":
            return self._respond(self._run(IntentStep("list")))
        if name == "verify_plugin_source":
            return self._verify_plugin_source(arguments)
        if name == "get_plugin_info":
            return self._respond(self._run(IntentStep("info", plugin_id=arguments["pluginId"])))
        if name == "plan_plugin_change":
            return self._plan_change(arguments)
        if name == "execute_plugin_change":
            return self._execute_change(arguments)
        if name == "apply_plugin_change":
            return self._apply_change(arguments)

        from .errors import HostError
        raise HostError("UNKNOWN_TOOL", f"Tool {name} does not exist")

    def _verify_plugin_source(self, arguments: dict) -> dict:
        source = Path(arguments["source"]).expanduser().resolve()
        output = source / ".assayer" / "verified"
        try:
            report = run_plugin_source_verification(source, output_dir=output)
        except PlatformContractError as error:
            report = {
                "schemaVersion": "1.0.0",
                "status": "failed",
                "error": {"code": error.code, "message": error.message},
                "stages": [],
            }
        except (OSError, UnicodeError) as error:
            report = {
                "schemaVersion": "1.0.0",
                "status": "failed",
                "error": {
                    "code": "PLUGIN_VERIFY_IO_FAILED",
                    "message": f"Plugin verification could not access its local files: {error}",
                },
                "stages": [],
            }
        return self._respond({"operation": "verify_plugin_source", **report})

    def _apply_change(self, arguments: dict) -> dict:
        """Collapse the normal Agent lifecycle path into one Host transaction."""
        token = arguments.get("token")
        if token is not None:
            if arguments.get("confirmed") is not True:
                return self._respond({
                    "operation": "apply", "status": "failed",
                    "error": {
                        "code": "CONFIRMATION_REQUIRED",
                        "message": "This lifecycle change requires confirmed=true.",
                    },
                })
            return self._execute_change({"token": token, "confirmed": True})

        operation = arguments.get("operation")
        if operation is None:
            return self._respond({
                "operation": "apply", "status": "failed",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "apply_plugin_change requires operation or a confirmation token.",
                },
            })
        planned_response = self._plan_change(arguments)
        planned = planned_response.get("structuredContent", {}).get("result", {})
        plan = planned.get("plan") if isinstance(planned, dict) else None
        plan_token = planned.get("token") if isinstance(planned, dict) else None
        if not isinstance(plan, dict) or not isinstance(plan_token, str):
            return planned_response
        # An explicit by-name first install from the trusted catalog is
        # additive and completes without another Agent round trip. Local
        # packages and every replacement/removal operation retain confirmation.
        plugin = arguments.get("plugin")
        safe_install = (
            operation == "install"
            and isinstance(plugin, str)
            and not Path(plugin).expanduser().is_dir()
            and arguments.get("confirmed") is not False
        )
        if safe_install:
            return self._execute_change({"token": plan_token, "confirmed": True})
        return self._respond({
            "operation": "apply", "status": "confirmation_required",
            "token": plan_token,
            "plan": plan,
        })

    def _plan_change(self, arguments: dict) -> dict:
        operation = arguments["operation"]
        plugin_id: str | None = None
        package: str | None = None
        plugin = arguments.get("plugin")
        if operation in {"install", "upgrade"}:
            if plugin is None:
                plugin_id = arguments.get("pluginId")
            elif Path(plugin).expanduser().is_dir():
                package = str(Path(plugin).expanduser().resolve())
            else:
                plugin_id = plugin
        else:
            plugin_id = arguments.get("pluginId")

        blocked = self._guard_blocked()
        if blocked is not None:
            return self._respond(blocked)

        try:
            plan = plan_plugin_change(
                operation,
                plugin_id=plugin_id,
                version=arguments.get("version"),
                package=package,
                store_root=self._store_root,
                catalog_index=self._catalog_index,
            )
        except PlatformContractError as error:
            return self._respond({"operation": "plan", "status": "failed",
                                  "error": {"code": error.code, "message": error.message}})

        if plan.get("status") != "ready":
            return self._respond({"operation": "plan", "status": "completed", "plan": plan})

        token = secrets.token_urlsafe(24)
        self._plans[token] = {
            "plan": plan,
            "step": self._step_for(operation, plan),
            "createdAt": time.time(),
            "storeDigest": store_index_digest(self._store_root),
            "used": False,
        }
        return self._respond({"operation": "plan", "status": "completed",
                              "token": token, "plan": plan})

    def _execute_change(self, arguments: dict) -> dict:
        if arguments.get("confirmed") is not True:
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": "CONFIRMATION_REQUIRED",
                                            "message": "Execute requires confirmed=true."}})
        token = arguments["token"]
        entry = self._plans.get(token)
        if entry is None:
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": "PLAN_TOKEN_INVALID",
                                            "message": "The plan token is unknown."}})
        if entry["used"]:
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": "PLAN_TOKEN_USED",
                                            "message": "The plan token was already used."}})
        if time.time() - entry["createdAt"] > self._plan_ttl:
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": "PLAN_TOKEN_EXPIRED",
                                            "message": "The plan token has expired."}})
        if entry["storeDigest"] != store_index_digest(self._store_root):
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": "PLAN_STALE",
                                            "message": "The store changed after the plan was made; plan again."}})
        binding_error = verify_plan_binding(entry["plan"], self._store_root, self._catalog_index)
        if binding_error is not None:
            if binding_error == "PLAN_STALE":
                message = "The plan source changed after the plan was made; plan again."
            else:
                message = "The plan source is unavailable; retry the plan when it is reachable."
            return self._respond({"operation": "execute", "status": "failed",
                                  "error": {"code": binding_error, "message": message}})
        blocked = self._guard_blocked()
        if blocked is not None:
            return self._respond(blocked)
        entry["used"] = True
        plan = entry["plan"]
        step = entry["step"]
        if plan.get("catalogDigest") is not None and step.operation in {"install", "upgrade"}:
            try:
                result = apply_resolved_catalog_change(
                    operation=step.operation,
                    plugin_id=plan["pluginId"],
                    version=plan["targetVersion"],
                    source=plan["source"],
                    checksum=plan["checksum"],
                    store_root=self._store_root,
                )
            except PlatformContractError as error:
                result = {
                    "operation": step.operation,
                    "status": "failed",
                    "error": {"code": error.code, "message": error.message},
                }
        else:
            result = self._run(step)
        if result.get("status") == "completed":
            result["resultingState"] = "absent" if step.operation == "uninstall" else "installed"
        elif result.get("status") == "quarantined":
            result["resultingState"] = "dirty"
        result.setdefault("previousVersion", plan.get("currentVersion"))
        result.setdefault("activeVersion", None if result.get("resultingState") == "absent" else result.get("version"))
        result.setdefault("retryable", result.get("status") not in {"completed", "quarantined"})
        result.setdefault(
            "nextAction",
            "inspect_plugin" if result.get("status") == "quarantined" else (
                "retry_plan" if result.get("retryable") else "continue"
            ),
        )
        return self._respond(result)

    @staticmethod
    def _step_for(operation: str, plan: dict) -> IntentStep:
        if operation == "rollback":
            return IntentStep("rollback", plugin_id=plan["pluginId"])
        if operation == "uninstall":
            return IntentStep("uninstall", plugin_id=plan["pluginId"])
        if operation == "downgrade":
            return IntentStep("downgrade", plugin_id=plan["pluginId"], version=plan["targetVersion"])
        if operation == "upgrade":
            if plan["source"] and not plan["source"].startswith(("http://", "https://")):
                return IntentStep("upgrade", package=plan["source"])
            return IntentStep("upgrade", plugin_id=plan["pluginId"], version=plan["targetVersion"])
        if operation == "install":
            if plan["source"] and not plan["source"].startswith(("http://", "https://")):
                return IntentStep("install", package=plan["source"])
            return IntentStep("install", plugin_id=plan["pluginId"], version=plan["targetVersion"])
        raise PlatformContractError(
            "INVALID_OPERATION",
            f"Unsupported plugin lifecycle operation: {operation}",
        )


__all__ = ["PluginLifecycleMcpToolTransport"]
