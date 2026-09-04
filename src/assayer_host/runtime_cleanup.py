"""Fail-closed cleanup for a verified, uninstalled Assayer private runtime."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from .resources import default_schema_root


_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_MAX_IDENTITY_BYTES = 4096


class PrivateRuntimeCleaner:
    """Delete only the runtime proven absent by a terminal uninstall journal.

    The caller supplies the cache root as trusted process configuration, never
    as Agent tool input. Cleanup must run outside the runtime being removed.
    """

    def __init__(self, cache_root: str | Path) -> None:
        self._cache_root = Path(cache_root).expanduser().resolve()
        if self._cache_root == Path(self._cache_root.anchor):
            raise ValueError("cache_root cannot be a filesystem root")
        self._assayer_root = self._cache_root / "assayer"
        self._transaction_validator = Draft202012Validator(
            json.loads(
                (default_schema_root() / "plugin-lifecycle-transaction.schema.json")
                .read_text(encoding="utf-8")
            )
        )
        self._result_validator = Draft202012Validator(
            json.loads(
                (default_schema_root() / "private-runtime-cleanup.schema.json")
                .read_text(encoding="utf-8")
            )
        )

    def cleanup_after_uninstall(self, transaction: object) -> dict:
        error = next(self._transaction_validator.iter_errors(transaction), None)
        if error is not None or not isinstance(transaction, dict):
            return self._result(
                None, "rejected", None, False, False,
                "UNINSTALL_PROOF_INVALID",
                "Private-runtime cleanup requires a valid terminal lifecycle transaction.",
            )

        transaction_id = transaction["transactionId"]
        current = transaction["current"]
        final = transaction["finalInstallation"]
        version = current.get("version")
        runtime_ref = f"runtime-{version}" if isinstance(version, str) and _VERSION.fullmatch(version) else None
        proof_valid = (
            transaction["operation"] == "uninstall"
            and transaction["status"] == "completed"
            and transaction.get("result", {}).get("code") == "LIFECYCLE_CHANGE_COMPLETED"
            and isinstance(final, dict)
            and final.get("selector") == current.get("selector")
            and final.get("version") == version
            and final.get("installed") is False
            and final.get("enabled") is False
            and runtime_ref is not None
        )
        if not proof_valid:
            return self._result(
                transaction_id, "rejected", runtime_ref, False, False,
                "UNINSTALL_NOT_VERIFIED",
                "Codex plugin absence was not proven by a completed uninstall transaction.",
            )

        if self._assayer_root.is_symlink():
            return self._unsafe_root(transaction_id, runtime_ref)
        runtime = self._assayer_root / runtime_ref
        quarantine = self._assayer_root / f".runtime-cleanup-{transaction_id}"
        if runtime.is_symlink() or quarantine.is_symlink():
            return self._unsafe_root(transaction_id, runtime_ref)
        if runtime.exists() and quarantine.exists():
            return self._result(
                transaction_id, "rejected", runtime_ref, False, False,
                "RUNTIME_CLEANUP_AMBIGUOUS",
                "Both the active and quarantined runtime exist; automatic cleanup stopped.",
            )
        candidate = quarantine if quarantine.exists() else runtime
        if not candidate.exists():
            return self._result(
                transaction_id, "already_absent", runtime_ref, False, False,
                "PRIVATE_RUNTIME_ALREADY_ABSENT",
                "The verified uninstalled release has no remaining private runtime.",
            )
        if not candidate.is_dir() or not self._identity_matches(candidate, version):
            return self._unsafe_root(transaction_id, runtime_ref)

        if candidate == runtime:
            try:
                runtime.rename(quarantine)
            except OSError:
                return self._result(
                    transaction_id, "pending", runtime_ref, False, True,
                    "RUNTIME_QUARANTINE_FAILED",
                    "The verified runtime could not be quarantined; retry cleanup after it is no longer in use.",
                )
        try:
            shutil.rmtree(quarantine)
        except OSError:
            return self._result(
                transaction_id, "pending", runtime_ref, False, True,
                "RUNTIME_DELETE_PENDING",
                "The verified runtime is quarantined but deletion is incomplete; retry cleanup.",
            )
        return self._result(
            transaction_id, "removed", runtime_ref, True, False,
            "PRIVATE_RUNTIME_REMOVED",
            "The verified uninstalled release private runtime was removed.",
        )

    @staticmethod
    def _identity_matches(root: Path, version: str) -> bool:
        identity = root / "runtime-identity.json"
        try:
            if identity.is_symlink() or not identity.is_file() or identity.stat().st_size > _MAX_IDENTITY_BYTES:
                return False
            value = json.loads(identity.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return value == {
            "schemaVersion": "1.0.0",
            "pluginVersion": version,
            "runtimeVersion": version.split("+", 1)[0],
        }

    def _unsafe_root(self, transaction_id: str, runtime_ref: str) -> dict:
        return self._result(
            transaction_id, "rejected", runtime_ref, False, False,
            "RUNTIME_OWNERSHIP_UNVERIFIED",
            "The cleanup target is not a uniquely owned Assayer private runtime.",
        )

    def _result(
        self,
        transaction_id: str | None,
        status: str,
        runtime_ref: str | None,
        deleted: bool,
        retryable: bool,
        code: str,
        message: str,
    ) -> dict:
        result = {
            "schemaVersion": "1.0.0",
            "transactionId": transaction_id,
            "status": status,
            "runtimeRef": runtime_ref,
            "deleted": deleted,
            "retryable": retryable,
            "result": {"code": code, "message": message},
        }
        self._result_validator.validate(result)
        return result
