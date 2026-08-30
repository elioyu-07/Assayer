from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
import json


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
              error_code TEXT, error_message TEXT, result_json TEXT,
              UNIQUE(scan_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS bootstrap_idempotency (
              idempotency_key TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
              request_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS page_states (
              page_state_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              entity_json TEXT NOT NULL, inspection_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entrypoints (
              entrypoint_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id), entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS page_candidates (
              candidate_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id), entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_objects (
              object_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id), entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS object_verifications (
              verification_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id),
              source_kind TEXT NOT NULL, source_ref TEXT NOT NULL, entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reverse_cases (
              case_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, status TEXT NOT NULL,
              entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_attempts (
              action_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              case_id TEXT NOT NULL REFERENCES reverse_cases(case_id), operation_id TEXT NOT NULL REFERENCES operations(operation_id),
              entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_observations (
              observation_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              operation_id TEXT NOT NULL REFERENCES operations(operation_id), entity_json TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(operations)")}
        if "result_json" not in columns:
            self._conn.execute("ALTER TABLE operations ADD COLUMN result_json TEXT")
        if "case_ref" not in columns:
            self._conn.execute("ALTER TABLE operations ADD COLUMN case_ref TEXT")
        scan_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(scans)")}
        for name, definition in {
            "rule_registry_digest": "TEXT NOT NULL DEFAULT ''",
            "current_page_state_id": "TEXT",
            "capabilities_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if name not in scan_columns:
                self._conn.execute(f"ALTER TABLE scans ADD COLUMN {name} {definition}")
        page_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(page_states)")}
        if "inspection_json" not in page_columns:
            self._conn.execute("ALTER TABLE page_states ADD COLUMN inspection_json TEXT NOT NULL DEFAULT '{}'")
        case_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(reverse_cases)")}
        for name, definition in {"rule_id":"TEXT NOT NULL DEFAULT ''", "rule_version":"TEXT NOT NULL DEFAULT ''", "status":"TEXT NOT NULL DEFAULT 'planned'"}.items():
            if name not in case_columns:
                self._conn.execute(f"ALTER TABLE reverse_cases ADD COLUMN {name} {definition}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_case_per_object_rule ON reverse_cases(scan_id,object_id,rule_id,rule_version) WHERE status IN ('planned','safety_check','executing','evidence_captured','decision_prepared','restoring')")
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

    def get_interrupted_operations(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM operations WHERE status='running' AND operation_kind IN ('browser_action','recovery') ORDER BY operation_id").fetchall()
        return [dict(row) for row in rows]

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
            "INSERT INTO operations(operation_id,scan_id,request_id,tool,operation_kind,idempotency_key,request_digest,status,accepted_at_revision,error_code,error_message,result_json,case_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (op["operationId"], op["scanId"], op["requestId"], op["tool"], op["operationKind"], op["idempotencyKey"], op["requestDigest"], op["status"], op["acceptedAtRevision"], op.get("errorCode"), op.get("errorMessage"), op.get("resultJson"), op.get("caseRef")),
        )

    def update_operation(self, op: dict) -> None:
        operation_id = op.get("operationId") or op.get("operation_id")
        self._conn.execute(
            "UPDATE operations SET status=?, error_code=?, error_message=?, result_json=?, case_ref=? WHERE operation_id=?",
            (op["status"], op.get("errorCode") or op.get("error_code"), op.get("errorMessage") or op.get("error_message"), op.get("resultJson") or op.get("result_json"), op.get("caseRef") or op.get("case_ref"), operation_id),
        )

    def insert_page_inspection(self, page_state: dict, inspection: dict, entrypoints: list[dict], candidates: list[dict]) -> None:
        self._conn.execute("INSERT OR IGNORE INTO page_states(page_state_id,scan_id,entity_json,inspection_json) VALUES(?,?,?,?)",
                           (page_state["pageStateId"], page_state["scanId"], json.dumps(page_state, ensure_ascii=False, separators=(",", ":")), json.dumps(inspection, ensure_ascii=False, separators=(",", ":"))))
        self._conn.executemany("INSERT OR IGNORE INTO entrypoints(entrypoint_id,scan_id,page_state_id,entity_json) VALUES(?,?,?,?)",
                               [(item["entrypointId"], item["scanId"], item["pageStateRef"], json.dumps(item, ensure_ascii=False, separators=(",", ":"))) for item in entrypoints])
        self._conn.executemany("INSERT OR IGNORE INTO page_candidates(candidate_id,scan_id,page_state_id,entity_json) VALUES(?,?,?,?)",
                               [(item["candidateId"], item["scanId"], item["pageStateRef"], json.dumps(item, ensure_ascii=False, separators=(",", ":"))) for item in candidates])

    def get_page_state(self, page_state_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM page_states WHERE page_state_id=?", (page_state_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_page_inspection(self, page_state_id: str) -> dict | None:
        row = self._conn.execute("SELECT inspection_json FROM page_states WHERE page_state_id=?", (page_state_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_entrypoints(self, page_state_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT entity_json FROM entrypoints WHERE page_state_id=? ORDER BY entrypoint_id", (page_state_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_candidates(self, page_state_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT entity_json FROM page_candidates WHERE page_state_id=? ORDER BY candidate_id", (page_state_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_candidate(self, candidate_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM page_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_audit_object(self, object_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM audit_objects WHERE object_id=?", (object_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_audit_object(self, audit_object: dict) -> None:
        self._conn.execute("UPDATE audit_objects SET page_state_id=?, entity_json=? WHERE object_id=?",
                           (audit_object["pageStateRef"], json.dumps(audit_object, ensure_ascii=False, separators=(",", ":")), audit_object["objectId"]))

    def insert_object_verification(self, verification: dict, audit_object: dict | None) -> None:
        if audit_object is not None:
            self._conn.execute("INSERT OR REPLACE INTO audit_objects(object_id,scan_id,page_state_id,entity_json) VALUES(?,?,?,?)",
                               (audit_object["objectId"], audit_object["scanId"], audit_object["pageStateRef"], json.dumps(audit_object, ensure_ascii=False, separators=(",", ":"))))
        self._conn.execute("INSERT INTO object_verifications(verification_id,scan_id,page_state_id,source_kind,source_ref,entity_json) VALUES(?,?,?,?,?,?)",
                           (verification["verificationId"], verification["scanId"], verification["pageStateRef"], verification["sourceKind"], verification["sourceRef"], json.dumps(verification, ensure_ascii=False, separators=(",", ":"))))

    def insert_candidate(self, candidate: dict) -> None:
        self._conn.execute("INSERT OR IGNORE INTO page_candidates(candidate_id,scan_id,page_state_id,entity_json) VALUES(?,?,?,?)",
                           (candidate["candidateId"], candidate["scanId"], candidate["pageStateRef"], json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))))

    def get_object_verification(self, verification_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM object_verifications WHERE verification_id=?", (verification_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def insert_case(self, case: dict) -> bool:
        try:
            self._conn.execute("INSERT INTO reverse_cases(case_id,scan_id,object_id,rule_id,rule_version,status,entity_json) VALUES(?,?,?,?,?,?,?)",
                               (case["caseId"], case["scanId"], case["objectRef"], case["rule"]["ruleId"], case["rule"]["version"], case["status"], json.dumps(case, ensure_ascii=False, separators=(",", ":"))))
            return True
        except sqlite3.IntegrityError:
            return False

    def get_case(self, case_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM reverse_cases WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_active_case(self, scan_id: str, object_ref: str, rule: dict) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM reverse_cases WHERE scan_id=? AND object_id=? AND rule_id=? AND rule_version=? AND status IN ('planned','safety_check','executing','evidence_captured','decision_prepared','restoring')",
                                 (scan_id, object_ref, rule["ruleId"], rule["version"])).fetchone()
        return json.loads(row[0]) if row else None

    def update_case(self, case: dict) -> None:
        self._conn.execute("UPDATE reverse_cases SET status=?, entity_json=? WHERE case_id=?",
                           (case["status"], json.dumps(case, ensure_ascii=False, separators=(",", ":")), case["caseId"]))

    def insert_action_attempt(self, action: dict) -> None:
        self._conn.execute("INSERT OR REPLACE INTO action_attempts(action_id,scan_id,case_id,operation_id,entity_json) VALUES(?,?,?,?,?)",
                           (action["actionId"], action["scanId"], action["caseId"], action["operationId"], json.dumps(action, ensure_ascii=False, separators=(",", ":"))))

    def get_action_attempt(self, action_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM action_attempts WHERE action_id=?", (action_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def insert_request_observation(self, observation: dict) -> None:
        self._conn.execute("INSERT OR REPLACE INTO request_observations(observation_id,scan_id,operation_id,entity_json) VALUES(?,?,?,?)",
                           (observation["observationId"], observation["scanId"], observation["operationId"], json.dumps(observation, ensure_ascii=False, separators=(",", ":"))))

    def get_request_observation(self, observation_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM request_observations WHERE observation_id=?", (observation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def insert_bootstrap_key(self, key: str, operation_id: str, digest: str) -> None:
        self._conn.execute("INSERT INTO bootstrap_idempotency(idempotency_key,operation_id,request_digest) VALUES(?,?,?)", (key, operation_id, digest))

    def close(self) -> None:
        self._conn.close()
