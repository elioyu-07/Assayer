"""Host persistence adapter for the domain-neutral platform ledger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assayer_platform.contract import PlatformLedger
from assayer_platform.ledger import write_platform_artifacts

from .store import SQLiteStore


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


class SQLitePlatformLedgerStore:
    """Store generic ledgers transactionally beside the Host audit ledger.

    The adapter deliberately serializes the immutable platform object rather
    than exposing SQLite details to the platform kernel. The Host store owns
    the transaction, so a process restart sees the same ledger and receipts.
    """

    def __init__(self, store: SQLiteStore):
        self._store = store
        self._path = store.path

    def _save_open_store(self, ledger: PlatformLedger, payload: str, updated_at: str) -> None:
        with self._store.transaction():
            self._store.save_platform_ledger(ledger.run.run_id, payload, updated_at)

    def save(self, ledger: PlatformLedger) -> None:
        payload = json.dumps(_plain(ledger), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self._save_open_store(ledger, payload, updated_at)
        except sqlite3.ProgrammingError:
            # RuntimeRouter releases the BrowserHostRuntime immediately after
            # Host completion.  The product facade may still need to append
            # the platform terminal checkpoint, so reopen a file-backed Host
            # store for this final write. In-memory test stores deliberately
            # keep the original failure visible.
            if not self._path or self._path == ":memory:":
                raise
            reopened = SQLiteStore(self._path)
            try:
                with reopened.transaction():
                    reopened.save_platform_ledger(ledger.run.run_id, payload, updated_at)
            finally:
                reopened.close()

    def load(self, run_id: str) -> dict[str, Any] | None:
        try:
            payload = self._store.load_platform_ledger(run_id)
        except sqlite3.ProgrammingError:
            if not self._path or self._path == ":memory:":
                raise
            reopened = SQLiteStore(self._path)
            try:
                payload = reopened.load_platform_ledger(run_id)
            finally:
                reopened.close()
        return json.loads(payload) if payload is not None else None

    def export(self, ledger: PlatformLedger, output_dir: str | None = None) -> tuple[str, ...]:
        """Write platform trace files beside the scan-local Host database."""
        root = output_dir
        if root is None:
            try:
                root = self._store.get_output_dir_for_run(ledger.run.run_id)
            except sqlite3.ProgrammingError:
                if self._path and self._path != ":memory:":
                    reopened = SQLiteStore(self._path)
                    try:
                        root = reopened.get_output_dir_for_run(ledger.run.run_id)
                    finally:
                        reopened.close()
        if root == "auto":
            return ()
        if root is None and self._path and self._path != ":memory:":
            root = str(Path(self._path).expanduser().resolve().parent)
        if root is None:
            return ()
        return tuple(str(path) for path in write_platform_artifacts(ledger, root))
