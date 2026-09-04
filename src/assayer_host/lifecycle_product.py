"""Two-phase product authorization for Assayer installation changes."""

from __future__ import annotations

import json
import hashlib
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator, RefResolver

from .errors import HostError
from .lifecycle_transaction import PluginLifecycleClient, PluginLifecycleTransaction
from .release_lifecycle import PluginInstallation, PluginLifecyclePlanner
from .resources import default_schema_root


_PLAN_TOKEN = re.compile(r"^lifecycle-token-[0-9a-f]{64}$")


class LifecyclePlanStore:
    """Atomically issue, claim, consume, and replay one-use plan tokens."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lifecycle_plan_token (
                        token_digest TEXT PRIMARY KEY,
                        created_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        plan_json TEXT NOT NULL,
                        result_json TEXT
                    )
                    """
                )

    def issue(self, plan: dict, *, ttl_seconds: int = 1800) -> dict:
        Draft202012Validator(_plan_schema()).validate(plan)
        if plan.get("status") != "ready":
            raise ValueError("only a ready lifecycle plan can receive a token")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be an integer from 60 through 3600")
        now = int(self._clock())
        expires_at = now + ttl_seconds
        for _ in range(4):
            token = f"lifecycle-token-{secrets.token_hex(32)}"
            token_digest = self._digest(token)
            try:
                with closing(self._connect()) as connection:
                    with connection:
                        connection.execute(
                            "INSERT INTO lifecycle_plan_token VALUES (?, ?, ?, 'awaiting_confirmation', ?, NULL)",
                            (token_digest, now, expires_at, json.dumps(plan, sort_keys=True, separators=(",", ":"))),
                        )
                return {"planToken": token, "createdAt": now, "expiresAt": expires_at}
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("a unique lifecycle plan token could not be issued")

    def read(self, token: str) -> dict | None:
        self._validate_token(token)
        token_digest = self._digest(token)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT created_at, expires_at, state, plan_json, result_json "
                "FROM lifecycle_plan_token WHERE token_digest = ?",
                (token_digest,),
            ).fetchone()
        return self._row(token_digest, row) if row is not None else None

    def claim(self, token: str) -> dict:
        self._validate_token(token)
        token_digest = self._digest(token)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT created_at, expires_at, state, plan_json, result_json "
                "FROM lifecycle_plan_token WHERE token_digest = ?",
                (token_digest,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return {"claimStatus": "unknown"}
            record = self._row(token_digest, row)
            if record["state"] in {"terminal", "expired"}:
                connection.commit()
                return {"claimStatus": record["state"], **record}
            if record["state"] == "executing":
                connection.commit()
                return {"claimStatus": "executing", **record}
            if record["expiresAt"] <= int(self._clock()):
                connection.execute(
                    "UPDATE lifecycle_plan_token SET state = 'expired' WHERE token_digest = ? AND state = 'awaiting_confirmation'",
                    (token_digest,),
                )
                connection.commit()
                record["state"] = "expired"
                return {"claimStatus": "expired", **record}
            changed = connection.execute(
                "UPDATE lifecycle_plan_token SET state = 'executing' "
                "WHERE token_digest = ? AND state = 'awaiting_confirmation'",
                (token_digest,),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return {"claimStatus": "executing", **record}
            connection.commit()
            record["state"] = "executing"
            return {"claimStatus": "claimed", **record}
        finally:
            connection.close()

    def complete(self, token: str, result: dict) -> None:
        self._validate_token(token)
        token_digest = self._digest(token)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as connection:
            with connection:
                changed = connection.execute(
                    "UPDATE lifecycle_plan_token SET state = 'terminal', result_json = ? "
                    "WHERE token_digest = ? AND state = 'executing'",
                    (payload, token_digest),
                ).rowcount
        if changed != 1:
            raise RuntimeError("only an executing lifecycle plan token can become terminal")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _validate_token(token: str) -> None:
        if not isinstance(token, str) or not _PLAN_TOKEN.fullmatch(token):
            raise ValueError("plan token is invalid")

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _row(token_digest: str, row) -> dict:
        created_at, expires_at, state, plan_json, result_json = row
        return {
            "authorizationRef": token_digest[:16],
            "createdAt": created_at,
            "expiresAt": expires_at,
            "state": state,
            "plan": json.loads(plan_json),
            "result": json.loads(result_json) if result_json is not None else None,
        }


class LifecycleProductController:
    """Expose separate read-only planning and externally authorized execution."""

    def __init__(
        self,
        client: PluginLifecycleClient,
        plan_store: LifecyclePlanStore,
        transaction_factory: Callable[[], PluginLifecycleTransaction],
        *,
        current_selector: str,
        target_resolver: Callable[[str, PluginInstallation], PluginInstallation | None],
        direction_verifier: Callable[[str, PluginInstallation, PluginInstallation], bool],
        active_run_probe: Callable[[], bool],
        external_authorizer: Callable[[str, dict], bool] | None = None,
        transaction_loader: Callable[[str], dict | None] | None = None,
        token_ttl_seconds: int = 1800,
    ) -> None:
        PluginInstallation(current_selector, None, False, False, False)
        self._client = client
        self._store = plan_store
        self._transaction_factory = transaction_factory
        self._current_selector = current_selector
        self._target_resolver = target_resolver
        self._direction_verifier = direction_verifier
        self._active_run_probe = active_run_probe
        self._external_authorizer = external_authorizer
        self._transaction_loader = transaction_loader
        self._token_ttl = token_ttl_seconds
        self._planner = PluginLifecyclePlanner()

    def plan(self, operation: str) -> dict:
        current = self._client.snapshot(self._current_selector)
        target = None if operation == "uninstall" else self._target_resolver(operation, current)
        direction = (
            self._direction_verifier(operation, current, target)
            if target is not None else False
        )
        plan = self._planner.build(
            operation, current, target=target,
            active_run=self._active_run_probe(),
            release_direction_verified=direction,
        )
        result = {
            "schemaVersion": "1.0.0", "phase": "planned", "plan": plan,
            "planToken": None, "expiresAt": None,
        }
        if plan["status"] == "ready":
            token = self._store.issue(plan, ttl_seconds=self._token_ttl)
            result.update({
                "phase": "awaiting_confirmation",
                "planToken": token["planToken"],
                "expiresAt": token["expiresAt"],
            })
        return result

    def execute(self, plan_token: str, *, confirmed: bool) -> dict:
        record = self._store.read(plan_token)
        if record is None:
            return self._outcome(
                "rejected", "UNKNOWN_PLAN_TOKEN",
                "The lifecycle plan token does not exist; create a new plan.",
            )
        if record["state"] == "terminal":
            return {
                "schemaVersion": "1.0.0", "phase": "terminal", "replayed": True,
                "transaction": record["result"],
            }
        if record["state"] == "executing":
            recovered = self._recover_executing(plan_token)
            if recovered is not None:
                return {
                    "schemaVersion": "1.0.0", "phase": "terminal", "replayed": True,
                    "transaction": recovered,
                }
            return self._outcome(
                "result_unknown", "LIFECYCLE_RESULT_UNKNOWN",
                "This plan token is already executing; reconcile its transaction journal before any retry.",
            )
        if not confirmed:
            return self._outcome(
                "confirmation_required", "CONFIRMATION_REQUIRED",
                "Explicit user confirmation is required before installation mutation.",
            )
        try:
            authorized = self._external_authorizer is not None and self._external_authorizer(
                record["authorizationRef"], record["plan"],
            ) is True
        except Exception:
            authorized = False
        if not authorized:
            return self._outcome(
                "authorization_required", "EXTERNAL_AUTHORIZATION_REQUIRED",
                "The client did not provide a trusted external authorization for this destructive operation.",
            )

        claim = self._store.claim(plan_token)
        if claim["claimStatus"] == "terminal":
            return {
                "schemaVersion": "1.0.0", "phase": "terminal", "replayed": True,
                "transaction": claim["result"],
            }
        if claim["claimStatus"] == "expired":
            return self._outcome(
                "expired", "PLAN_TOKEN_EXPIRED",
                "The lifecycle plan token expired; create and confirm a fresh plan.",
            )
        if claim["claimStatus"] == "executing":
            return self._outcome(
                "result_unknown", "LIFECYCLE_RESULT_UNKNOWN",
                "This plan token is already executing; reconcile its transaction journal before any retry.",
            )
        if claim["claimStatus"] != "claimed":
            return self._outcome(
                "rejected", "UNKNOWN_PLAN_TOKEN",
                "The lifecycle plan token does not exist; create a new plan.",
            )

        transaction_id = self._transaction_id(plan_token)
        try:
            transaction = self._transaction_factory().execute(
                claim["plan"], confirmed=True, transaction_id=transaction_id,
            )
        except Exception:
            return self._outcome(
                "result_unknown", "LIFECYCLE_RESULT_UNKNOWN",
                "Lifecycle execution ended without a trusted result; reconcile its transaction journal before any retry.",
            )
        try:
            self._store.complete(plan_token, transaction)
        except Exception:
            return self._outcome(
                "result_unknown", "LIFECYCLE_RESULT_UNKNOWN",
                "The transaction finished but authorization-state persistence failed; replay from its journal before any retry.",
            )
        return {
            "schemaVersion": "1.0.0", "phase": "terminal", "replayed": False,
            "transaction": transaction,
        }

    def _recover_executing(self, plan_token: str) -> dict | None:
        if self._transaction_loader is None:
            return None
        try:
            transaction = self._transaction_loader(self._transaction_id(plan_token))
        except Exception:
            return None
        if not isinstance(transaction, dict) or transaction.get("status") not in {
            "completed", "rolled_back", "failed", "rejected", "confirmation_required",
        }:
            return None
        try:
            self._store.complete(plan_token, transaction)
        except Exception:
            return None
        return transaction

    @staticmethod
    def _transaction_id(plan_token: str) -> str:
        return f"lifecycle-{hashlib.sha256(plan_token.encode()).hexdigest()[:32]}"

    @staticmethod
    def _outcome(phase: str, code: str, message: str) -> dict:
        return {
            "schemaVersion": "1.0.0", "phase": phase, "replayed": False,
            "result": {"code": code, "message": message},
        }


class LifecycleProductToolTransport:
    """MCP-shaped two-stage surface; authorization remains controller-owned."""

    _SCHEMAS = {
        "plan_plugin_change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation"],
            "properties": {
                "operation": {"enum": ["upgrade", "rollback", "uninstall"]},
            },
        },
        "execute_plugin_change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["planToken", "confirmed"],
            "properties": {
                "planToken": {"type": "string", "pattern": "^lifecycle-token-[0-9a-f]{64}$"},
                "confirmed": {"const": True},
            },
        },
    }

    def __init__(self, controller: LifecycleProductController) -> None:
        self._controller = controller
        self._result_validator = _product_result_validator()

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "plan_plugin_change",
                "description": (
                    "Create a fail-closed Assayer upgrade, rollback, or uninstall plan. "
                    "This stage does not change the Codex plugin installation."
                ),
                "inputSchema": self._SCHEMAS["plan_plugin_change"],
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": False,
                },
            },
            {
                "name": "execute_plugin_change",
                "description": (
                    "Execute one previously displayed Assayer lifecycle plan. Call only after explicit "
                    "user confirmation; the controller also requires trusted external authorization."
                ),
                "inputSchema": self._SCHEMAS["execute_plugin_change"],
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: object) -> dict:
        schema = self._SCHEMAS.get(name)
        if schema is None:
            raise HostError("UNKNOWN_TOOL", "The lifecycle product tool does not exist")
        if not isinstance(arguments, dict) or next(Draft202012Validator(schema).iter_errors(arguments), None):
            raise HostError("INVALID_REQUEST", "Lifecycle product arguments do not satisfy the tool schema")
        if name == "plan_plugin_change":
            result = self._controller.plan(arguments["operation"])
        else:
            result = self._controller.execute(arguments["planToken"], confirmed=arguments["confirmed"])
        validation_error = next(self._result_validator.iter_errors(result), None)
        if validation_error is not None:
            raise HostError(
                "INTERNAL_FAILURE",
                "Lifecycle product output does not satisfy the published result contract",
            )
        public = {"status": "ok", "result": result}
        return {
            "structuredContent": public,
            "content": [{"type": "text", "text": json.dumps(public, separators=(",", ":"))}],
            "isError": False,
        }


def _plan_schema() -> dict:
    return json.loads(
        (default_schema_root() / "plugin-lifecycle-plan.schema.json").read_text(encoding="utf-8")
    )


def _product_result_validator() -> Draft202012Validator:
    root = default_schema_root()
    filenames = (
        "plugin-lifecycle-product.schema.json",
        "plugin-lifecycle-plan.schema.json",
        "plugin-lifecycle-transaction.schema.json",
    )
    schemas = {
        filename: json.loads((root / filename).read_text(encoding="utf-8"))
        for filename in filenames
    }
    store = {
        key: schema
        for filename, schema in schemas.items()
        for key in (filename, schema["$id"])
    }
    schema = schemas[filenames[0]]
    return Draft202012Validator(
        schema, resolver=RefResolver(schema["$id"], schema, store=store),
    )
