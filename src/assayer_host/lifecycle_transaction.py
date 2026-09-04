"""Durable, compensating execution for accepted plugin lifecycle plans."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Callable, Protocol

from jsonschema import Draft202012Validator

from .release_lifecycle import PluginInstallation, PluginLifecyclePlanner
from .resources import default_schema_root


_TRANSACTION_ID = re.compile(r"^lifecycle-[0-9a-f]{32}$")
_LOG = logging.getLogger(__name__)


class LifecycleCommandError(RuntimeError):
    """A sanitized adapter failure safe for a lifecycle journal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PluginLifecycleClient(Protocol):
    def snapshot(self, selector: str) -> PluginInstallation: ...

    def add(self, selector: str) -> None: ...

    def remove(self, selector: str) -> None: ...

    def verify_installation(self, installation: PluginInstallation) -> bool: ...


class LifecycleJournalStore:
    """Atomically persist one sanitized transaction journal per directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, journal: dict) -> Path:
        transaction_id = journal.get("transactionId")
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID.fullmatch(transaction_id):
            raise ValueError("transactionId is not safe for lifecycle persistence")
        Draft202012Validator(_transaction_schema()).validate(journal)
        destination_root = self.root / transaction_id
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / "lifecycle-transaction.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        temporary.replace(destination)
        return destination

    def load(self, transaction_id: str) -> dict | None:
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID.fullmatch(transaction_id):
            raise ValueError("transactionId is not safe for lifecycle persistence")
        path = self.root / transaction_id / "lifecycle-transaction.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        Draft202012Validator(_transaction_schema()).validate(value)
        return value


class PluginLifecycleTransaction:
    """Execute one fresh accepted plan and restore the prior state on failure."""

    def __init__(
        self,
        client: PluginLifecycleClient,
        journal_store: LifecycleJournalStore,
        *,
        planner: PluginLifecyclePlanner | None = None,
        active_run_probe: Callable[[], bool] | None = None,
        direction_verifier: Callable[[str, PluginInstallation, PluginInstallation], bool] | None = None,
    ) -> None:
        self._client = client
        self._store = journal_store
        self._planner = planner or PluginLifecyclePlanner()
        self._active_run_probe = active_run_probe or (lambda: False)
        self._direction_verifier = direction_verifier or (lambda operation, current, target: False)

    def execute(
        self,
        accepted_plan: dict,
        *,
        confirmed: bool,
        transaction_id: str | None = None,
    ) -> dict:
        Draft202012Validator(_plan_schema()).validate(accepted_plan)
        transaction_id = transaction_id or f"lifecycle-{uuid.uuid4().hex}"
        if not _TRANSACTION_ID.fullmatch(transaction_id):
            raise ValueError("transaction_id must use the lifecycle UUID form")
        journal = self._journal(transaction_id, accepted_plan)

        if accepted_plan["status"] != "ready":
            return self._finish_without_mutation(
                journal, "rejected", "PLAN_NOT_READY",
                "The accepted lifecycle plan is not ready for execution.",
            )
        if not confirmed:
            return self._finish_without_mutation(
                journal, "confirmation_required", "CONFIRMATION_REQUIRED",
                "Explicit user confirmation is required immediately before plugin mutation.",
            )

        try:
            current, target, fresh_plan = self._refresh_plan(accepted_plan)
        except LifecycleCommandError as error:
            return self._finish_without_mutation(journal, "rejected", error.code, error.message)
        if fresh_plan != accepted_plan:
            return self._finish_without_mutation(
                journal, "rejected", "STALE_LIFECYCLE_PLAN",
                "Installation or release state changed after planning; create a new lifecycle plan.",
            )

        journal["status"] = "running"
        self._event(journal, "verify_preconditions", "succeeded", "Live lifecycle preconditions match the accepted plan.")
        try:
            self._store.save(journal)
        except Exception:
            return self._finish_without_mutation(
                journal, "failed", "LIFECYCLE_JOURNAL_UNAVAILABLE",
                "The lifecycle journal could not be persisted before mutation.",
                persist=False,
            )

        mutation_started = False
        try:
            mutation_started = True
            self._remove(current, journal)
            if accepted_plan["operation"] in {"upgrade", "rollback"}:
                assert target is not None
                self._install_and_verify(target, journal)
                live_current = self._client.snapshot(current.selector)
                if live_current.installed:
                    raise LifecycleCommandError(
                        "OLD_RELEASE_STILL_INSTALLED",
                        "The old Assayer selector is still installed after replacement.",
                    )
                self._event(journal, "verify_terminal_state", "succeeded", "The target is healthy and the previous selector is absent.")
                journal["finalInstallation"] = self._client.snapshot(target.selector).as_dict()
            else:
                self._event(journal, "verify_absent", "succeeded", "The Assayer selector is absent from the Codex plugin catalog.")
                journal["finalInstallation"] = self._client.snapshot(current.selector).as_dict()
            journal["status"] = "completed"
            journal["result"] = {
                "code": "LIFECYCLE_CHANGE_COMPLETED",
                "message": "The accepted plugin lifecycle change completed and passed terminal verification.",
            }
            self._store.save(journal)
            return journal
        except Exception as error:
            code, message = self._safe_error(error)
            self._event(journal, "forward_change", "failed", message, code=code)
            if not mutation_started:
                journal["status"] = "failed"
                journal["result"] = {"code": code, "message": message}
                self._best_effort_save(journal)
                return journal
            restored = self._compensate(current, target, journal)
            journal["status"] = "rolled_back" if restored else "failed"
            journal["result"] = {
                "code": "LIFECYCLE_CHANGE_ROLLED_BACK" if restored else "LIFECYCLE_COMPENSATION_FAILED",
                "message": (
                    "The lifecycle change failed and the previous installation was restored."
                    if restored else
                    "The lifecycle change and its compensation failed; manual recovery is required."
                ),
            }
            try:
                journal["finalInstallation"] = self._client.snapshot(current.selector).as_dict()
            except Exception:
                journal["finalInstallation"] = None
            self._best_effort_save(journal)
            return journal

    def _refresh_plan(self, accepted_plan: dict) -> tuple[PluginInstallation, PluginInstallation | None, dict]:
        current = self._client.snapshot(accepted_plan["current"]["selector"])
        target_data = accepted_plan.get("target")
        target = self._client.snapshot(target_data["selector"]) if isinstance(target_data, dict) else None
        direction = (
            self._direction_verifier(accepted_plan["operation"], current, target)
            if target is not None else False
        )
        fresh = self._planner.build(
            accepted_plan["operation"], current, target=target,
            active_run=self._active_run_probe(),
            release_direction_verified=direction,
        )
        return current, target, fresh

    def _remove(self, current: PluginInstallation, journal: dict) -> None:
        try:
            self._client.remove(current.selector)
        except Exception as error:
            live = self._client.snapshot(current.selector)
            if live.installed:
                raise error
        if self._client.snapshot(current.selector).installed:
            raise LifecycleCommandError(
                "PLUGIN_REMOVE_NOT_APPLIED",
                "Codex still reports the previous selector as installed after removal.",
            )
        self._event(journal, "remove_current", "succeeded", "Codex removed the previous selector.")
        self._store.save(journal)

    def _install_and_verify(self, target: PluginInstallation, journal: dict) -> None:
        try:
            self._client.add(target.selector)
        except Exception as error:
            live = self._client.snapshot(target.selector)
            if not live.installed:
                raise error
        self._event(journal, "install_target", "succeeded", "Codex installed the target selector.")
        self._store.save(journal)
        live = self._client.snapshot(target.selector)
        if (
            not live.installed or not live.enabled or live.version != target.version
            or not self._client.verify_installation(live)
        ):
            raise LifecycleCommandError(
                "TARGET_VERIFICATION_FAILED",
                "The installed target did not pass identity, enabled-state, and installation-health verification.",
            )
        self._event(journal, "verify_target", "succeeded", "The target identity and installation health are verified.")
        self._store.save(journal)

    def _compensate(
        self,
        current: PluginInstallation,
        target: PluginInstallation | None,
        journal: dict,
    ) -> bool:
        try:
            if target is not None and self._client.snapshot(target.selector).installed:
                self._client.remove(target.selector)
                if self._client.snapshot(target.selector).installed:
                    raise LifecycleCommandError(
                        "FAILED_TARGET_REMOVE_FAILED",
                        "The failed target selector could not be removed during compensation.",
                    )
                self._event(journal, "remove_failed_target", "succeeded", "The failed target selector was removed.")
            live_current = self._client.snapshot(current.selector)
            if not live_current.installed:
                self._client.add(current.selector)
                self._event(journal, "restore_current", "succeeded", "The previous immutable selector was restored.")
            restored = self._client.snapshot(current.selector)
            if (
                not restored.installed or not restored.enabled
                or restored.version != current.version
                or not self._client.verify_installation(restored)
            ):
                raise LifecycleCommandError(
                    "RESTORED_INSTALLATION_INVALID",
                    "The previous selector did not pass restoration verification.",
                )
            self._event(journal, "verify_restored", "succeeded", "The previous installation is restored and healthy.")
            return True
        except Exception as error:
            code, message = self._safe_error(error)
            self._event(journal, "compensation", "failed", message, code=code)
            return False

    def _finish_without_mutation(
        self,
        journal: dict,
        status: str,
        code: str,
        message: str,
        *,
        persist: bool = True,
    ) -> dict:
        journal["status"] = status
        journal["result"] = {"code": code, "message": message}
        self._event(journal, "preflight", "failed", message, code=code)
        if persist:
            self._best_effort_save(journal)
        return journal

    @staticmethod
    def _event(journal: dict, action: str, outcome: str, message: str, *, code: str | None = None) -> None:
        event = {
            "sequence": len(journal["events"]) + 1,
            "action": action,
            "outcome": outcome,
            "message": message,
        }
        if code is not None:
            event["code"] = code
        journal["events"].append(event)

    @staticmethod
    def _safe_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, LifecycleCommandError):
            return error.code, error.message
        return (
            "LIFECYCLE_COMMAND_FAILED",
            "A lifecycle adapter operation failed without a safe structured result.",
        )

    def _best_effort_save(self, journal: dict) -> None:
        try:
            self._store.save(journal)
        except Exception:
            _LOG.error(
                "Lifecycle journal persistence failed during terminal handling; "
                "the in-memory terminal result remains authoritative for this process."
            )

    @staticmethod
    def _journal(transaction_id: str, plan: dict) -> dict:
        digest = hashlib.sha256(
            json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "schemaVersion": "1.0.0",
            "transactionId": transaction_id,
            "planDigest": digest,
            "operation": plan["operation"],
            "status": "planned",
            "current": plan["current"],
            "target": plan["target"],
            "events": [],
            "finalInstallation": None,
            "result": None,
        }


def _plan_schema() -> dict:
    return json.loads(
        (default_schema_root() / "plugin-lifecycle-plan.schema.json").read_text(encoding="utf-8")
    )


def _transaction_schema() -> dict:
    return json.loads(
        (default_schema_root() / "plugin-lifecycle-transaction.schema.json").read_text(encoding="utf-8")
    )
