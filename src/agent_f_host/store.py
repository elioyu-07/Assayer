from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class SQLiteStore:
    """Small transactional store for the Scan/Operation slice."""

    def __init__(self, path: str | Path = ":memory:"):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS scans (
              scan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
              run_revision INTEGER NOT NULL, status TEXT NOT NULL,
              login_status TEXT NOT NULL, created_at TEXT NOT NULL,
              rule_registry_digest TEXT NOT NULL, current_page_state_id TEXT,
              capabilities_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
              operation_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              request_id TEXT NOT NULL, tool TEXT NOT NULL, operation_kind TEXT NOT NULL,
              idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,
              status TEXT NOT NULL, accepted_at_revision INTEGER NOT NULL,
              error_code TEXT, error_message TEXT,
              UNIQUE(scan_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS bootstrap_idempotency (
              idempotency_key TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
              request_digest TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    @contextmanager
    def transaction(self):
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_scan(self, scan_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
        return dict(row) if row else None

    def get_scan_by_run(self, scan_id: str, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM scans WHERE scan_id = ? AND run_id = ?", (scan_id, run_id)).fetchone()
        return dict(row) if row else None

    def insert_scan(self, scan: dict) -> None:
        self._conn.execute(
            "INSERT INTO scans(scan_id,run_id,run_revision,status,login_status,created_at,rule_registry_digest,current_page_state_id,capabilities_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (scan["scanId"], scan["runId"], scan["runRevision"], scan["status"], scan["loginStatus"], scan["createdAt"], scan["ruleRegistryDigest"], scan.get("currentPageStateId"), scan["capabilitiesJson"]),
        )

    def update_scan(self, scan: dict) -> None:
        self._conn.execute(
            "UPDATE scans SET run_revision=?, status=?, login_status=?, current_page_state_id=?, capabilities_json=? WHERE scan_id=?",
            (scan["runRevision"], scan["status"], scan["loginStatus"], scan.get("currentPageStateId"), scan["capabilitiesJson"], scan["scanId"]),
        )

    def get_operation(self, operation_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def get_by_idempotency(self, scan_id: str, key: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM operations WHERE scan_id = ? AND idempotency_key = ?", (scan_id, key)).fetchone()
        return dict(row) if row else None

    def get_bootstrap(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT o.* FROM bootstrap_idempotency b JOIN operations o ON o.operation_id=b.operation_id WHERE b.idempotency_key=?",
            (key,),
        ).fetchone()
        return dict(row) if row else None

    def insert_operation(self, op: dict) -> None:
        self._conn.execute(
            "INSERT INTO operations(operation_id,scan_id,request_id,tool,operation_kind,idempotency_key,request_digest,status,accepted_at_revision,error_code,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (op["operationId"], op["scanId"], op["requestId"], op["tool"], op["operationKind"], op["idempotencyKey"], op["requestDigest"], op["status"], op["acceptedAtRevision"], op.get("errorCode"), op.get("errorMessage")),
        )

    def update_operation(self, op: dict) -> None:
        self._conn.execute(
            "UPDATE operations SET status=?, error_code=?, error_message=? WHERE operation_id=?",
            (op["status"], op.get("errorCode"), op.get("errorMessage"), op["operationId"]),
        )

    def insert_bootstrap_key(self, key: str, operation_id: str, digest: str) -> None:
        self._conn.execute("INSERT INTO bootstrap_idempotency(idempotency_key,operation_id,request_digest) VALUES(?,?,?)", (key, operation_id, digest))

    def close(self) -> None:
        self._conn.close()
