from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import json
import re
from datetime import datetime, timezone


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
              capabilities_json TEXT NOT NULL, output_dir TEXT NOT NULL, entry_url TEXT NOT NULL DEFAULT '',
              started_monotonic_ns INTEGER
            );
            CREATE TABLE IF NOT EXISTS operations (
              operation_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              request_id TEXT NOT NULL, tool TEXT NOT NULL, operation_kind TEXT NOT NULL,
              idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,
              status TEXT NOT NULL, accepted_at_revision INTEGER NOT NULL,
              accepted_at TEXT, ended_at TEXT, accepted_monotonic_ns INTEGER, duration_ms INTEGER,
              agent_turn_id TEXT, decision_reason TEXT, model_duration_ms INTEGER, model_retry_count INTEGER,
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
            CREATE TABLE IF NOT EXISTS evidence (
              evidence_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id), object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS screenshots (
              screenshot_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              page_state_id TEXT NOT NULL REFERENCES page_states(page_state_id), object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_decisions (
              pending_decision_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, status TEXT NOT NULL,
              entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dimension_findings (
              finding_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, dimension TEXT NOT NULL,
              supersedes_ref TEXT REFERENCES dimension_findings(finding_id), entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assessments (
              assessment_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              object_id TEXT NOT NULL REFERENCES audit_objects(object_id),
              rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issues (
              issue_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
              object_id TEXT NOT NULL REFERENCES audit_objects(object_id), entity_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_events (
              event_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(scan_id),
              run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_json TEXT NOT NULL,
              UNIQUE(scan_id, sequence)
            );
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(operations)")}
        if "result_json" not in columns:
            self._conn.execute("ALTER TABLE operations ADD COLUMN result_json TEXT")
        if "case_ref" not in columns:
            self._conn.execute("ALTER TABLE operations ADD COLUMN case_ref TEXT")
        for name, definition in {
            "accepted_at": "TEXT",
            "ended_at": "TEXT",
            "accepted_monotonic_ns": "INTEGER",
            "duration_ms": "INTEGER",
            "agent_turn_id": "TEXT",
            "decision_reason": "TEXT",
            "model_duration_ms": "INTEGER",
            "model_retry_count": "INTEGER",
        }.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE operations ADD COLUMN {name} {definition}")
        scan_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(scans)")}
        for name, definition in {
            "rule_registry_digest": "TEXT NOT NULL DEFAULT ''",
            "current_page_state_id": "TEXT",
            "capabilities_json": "TEXT NOT NULL DEFAULT '[]'",
            "output_dir": "TEXT NOT NULL DEFAULT ''",
            "entry_url": "TEXT NOT NULL DEFAULT ''",
            "started_monotonic_ns": "INTEGER",
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
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_assessment_per_object_rule ON assessments(scan_id,object_id,rule_id,rule_version)")
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
        started_monotonic_ns = scan.get("startedMonotonicNs") or time.monotonic_ns()
        self._conn.execute(
            "INSERT INTO scans(scan_id,run_id,run_revision,status,login_status,created_at,rule_registry_digest,current_page_state_id,capabilities_json,output_dir,entry_url,started_monotonic_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan["scanId"], scan["runId"], scan["runRevision"], scan["status"], scan["loginStatus"], scan["createdAt"], scan["ruleRegistryDigest"], scan.get("currentPageStateId"), scan["capabilitiesJson"], scan["outputDir"], scan.get("entryUrl", ""), started_monotonic_ns),
        )
        self._append_runtime_event(scan, "lifecycle", "scan.started", "start", "info", "started", "Scan started")

    def update_scan(self, scan: dict) -> None:
        previous = self._conn.execute("SELECT status FROM scans WHERE scan_id=?", (scan["scanId"],)).fetchone()
        self._conn.execute(
            "UPDATE scans SET run_revision=?, status=?, login_status=?, current_page_state_id=?, capabilities_json=? WHERE scan_id=?",
            (scan["runRevision"], scan["status"], scan["loginStatus"], scan.get("currentPageStateId"), scan["capabilitiesJson"], scan["scanId"]),
        )
        if previous and previous[0] not in {"completed", "partial", "failed"} and scan.get("status") in {"completed", "partial", "failed"}:
            outcome = "succeeded" if scan["status"] in {"completed", "partial"} else "failed"
            self._append_runtime_event(scan, "lifecycle", "scan.terminal", "finish", "info" if outcome == "succeeded" else "error", outcome, f"Scan terminal: {scan['status']}")

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
        accepted_at = op.get("acceptedAt") or self._now()
        accepted_mono = op.get("acceptedMonotonicNs") or time.monotonic_ns()
        ended_at = op.get("endedAt")
        duration_ms = op.get("durationMs")
        if op.get("status") in {"succeeded", "rejected", "failed_known", "result_unknown"}:
            ended_at = ended_at or self._now()
            duration_ms = duration_ms if duration_ms is not None else max(0, int((time.monotonic_ns() - accepted_mono) / 1_000_000))
        self._conn.execute(
            "INSERT INTO operations(operation_id,scan_id,request_id,tool,operation_kind,idempotency_key,request_digest,status,accepted_at_revision,accepted_at,ended_at,accepted_monotonic_ns,duration_ms,agent_turn_id,decision_reason,model_duration_ms,model_retry_count,error_code,error_message,result_json,case_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (op["operationId"], op["scanId"], op["requestId"], op["tool"], op["operationKind"], op["idempotencyKey"], op["requestDigest"], op["status"], op["acceptedAtRevision"], accepted_at, ended_at, accepted_mono, duration_ms, op.get("agentTurnId"), op.get("decisionReason"), (op.get("modelTelemetry") or {}).get("durationMs"), (op.get("modelTelemetry") or {}).get("retryCount"), op.get("errorCode"), op.get("errorMessage"), op.get("resultJson"), op.get("caseRef")),
        )
        event_op = {**op, "acceptedAt": accepted_at}
        self._append_operation_event(event_op, "start")
        self._append_agent_decision_events(event_op)
        if ended_at:
            self._append_operation_event({**event_op, "endedAt": ended_at, "durationMs": duration_ms}, "finish")

    def update_operation(self, op: dict) -> None:
        operation_id = op.get("operationId") or op.get("operation_id")
        previous = self._conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if previous is None:
            raise ValueError(f"unknown operation: {operation_id}")
        accepted_at = previous["accepted_at"] or op.get("acceptedAt") or self._now()
        accepted_mono = previous["accepted_monotonic_ns"] or op.get("acceptedMonotonicNs") or time.monotonic_ns()
        terminal = op["status"] in {"succeeded", "rejected", "failed_known", "result_unknown"}
        ended_at = previous["ended_at"]
        duration_ms = previous["duration_ms"]
        if terminal and not ended_at:
            ended_at = op.get("endedAt") or self._now()
            duration_ms = op.get("durationMs") if op.get("durationMs") is not None else max(0, int((time.monotonic_ns() - accepted_mono) / 1_000_000))
        self._conn.execute(
            "UPDATE operations SET status=?, accepted_at=?, ended_at=?, accepted_monotonic_ns=?, duration_ms=?, agent_turn_id=?, decision_reason=?, model_duration_ms=?, model_retry_count=?, error_code=?, error_message=?, result_json=?, case_ref=? WHERE operation_id=?",
            (op["status"], accepted_at, ended_at, accepted_mono, duration_ms, op.get("agentTurnId") or op.get("agent_turn_id") or previous["agent_turn_id"], op.get("decisionReason") or op.get("decision_reason") or previous["decision_reason"], (op.get("modelTelemetry") or {}).get("durationMs", previous["model_duration_ms"]), (op.get("modelTelemetry") or {}).get("retryCount", previous["model_retry_count"]), op.get("errorCode") or op.get("error_code"), op.get("errorMessage") or op.get("error_message"), op.get("resultJson") if op.get("resultJson") is not None else (op.get("result_json") if op.get("result_json") is not None else previous["result_json"]), op.get("caseRef") or op.get("case_ref") or previous["case_ref"], operation_id),
        )
        if not previous["ended_at"] and ended_at:
            event_op = dict(op)
            event_op.update({"operationId": operation_id, "scanId": previous["scan_id"], "requestId": previous["request_id"], "tool": previous["tool"], "operationKind": previous["operation_kind"], "acceptedAt": accepted_at, "endedAt": ended_at, "durationMs": duration_ms})
            self._append_operation_event(event_op, "finish")

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

    def get_entrypoint(self, entrypoint_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM entrypoints WHERE entrypoint_id=?", (entrypoint_id,)).fetchone()
        return json.loads(row[0]) if row else None

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

    def insert_evidence(self, evidence: dict, screenshot: dict | None = None) -> None:
        if screenshot is not None:
            self._conn.execute("INSERT INTO screenshots(screenshot_id,scan_id,page_state_id,object_id,entity_json) VALUES(?,?,?,?,?)",
                               (screenshot["screenshotId"], screenshot["scanId"], screenshot["pageStateRef"], screenshot["objectRef"], json.dumps(screenshot, ensure_ascii=False, separators=(",", ":"))))
        self._conn.execute("INSERT INTO evidence(evidence_id,scan_id,page_state_id,object_id,entity_json) VALUES(?,?,?,?,?)",
                           (evidence["evidenceId"], evidence["scanId"], evidence["pageStateRef"], evidence["objectRef"], json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))))

    def get_evidence(self, evidence_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_screenshot(self, screenshot_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM screenshots WHERE screenshot_id=?", (screenshot_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def insert_screenshot(self, screenshot: dict) -> None:
        self._conn.execute(
            "INSERT INTO screenshots(screenshot_id,scan_id,page_state_id,object_id,entity_json) VALUES(?,?,?,?,?)",
            (screenshot["screenshotId"], screenshot["scanId"], screenshot["pageStateRef"], screenshot["objectRef"], json.dumps(screenshot, ensure_ascii=False, separators=(",", ":"))),
        )

    def insert_pending_decision(self, decision: dict) -> None:
        self._conn.execute(
            "INSERT INTO pending_decisions(pending_decision_id,scan_id,object_id,rule_id,rule_version,status,entity_json) VALUES(?,?,?,?,?,?,?)",
            (decision["pendingDecisionId"], decision["scanId"], decision["objectRef"], decision["rule"]["ruleId"], decision["rule"]["version"], decision["status"], json.dumps(decision, ensure_ascii=False, separators=(",", ":"))),
        )

    def insert_dimension_finding(self, finding: dict) -> None:
        self._conn.execute(
            "INSERT INTO dimension_findings(finding_id,scan_id,object_id,rule_id,rule_version,dimension,supersedes_ref,entity_json) VALUES(?,?,?,?,?,?,?,?)",
            (finding["findingId"], finding["scanId"], finding["objectRef"], finding["rule"]["ruleId"], finding["rule"]["version"], finding["dimension"], finding.get("supersedesRef"), json.dumps(finding, ensure_ascii=False, separators=(",", ":"))),
        )

    def get_dimension_finding(self, finding_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM dimension_findings WHERE finding_id=?", (finding_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_latest_findings(self, scan_id: str, object_id: str, rule: dict) -> list[dict]:
        rows = self._conn.execute(
            "SELECT entity_json FROM dimension_findings WHERE scan_id=? AND object_id=? AND rule_id=? AND rule_version=? ORDER BY rowid",
            (scan_id, object_id, rule["ruleId"], rule["version"]),
        ).fetchall()
        findings = [json.loads(row[0]) for row in rows]
        superseded = {item["supersedesRef"] for item in findings if item.get("supersedesRef")}
        return [item for item in findings if item["findingId"] not in superseded]

    def get_pending_decision(self, pending_decision_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM pending_decisions WHERE pending_decision_id=?", (pending_decision_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_pending_decision(self, decision: dict) -> None:
        self._conn.execute(
            "UPDATE pending_decisions SET status=?, entity_json=? WHERE pending_decision_id=?",
            (decision["status"], json.dumps(decision, ensure_ascii=False, separators=(",", ":")), decision["pendingDecisionId"]),
        )

    def insert_assessment(self, assessment: dict) -> None:
        self._conn.execute(
            "INSERT INTO assessments(assessment_id,scan_id,object_id,rule_id,rule_version,entity_json) VALUES(?,?,?,?,?,?)",
            (assessment["assessmentId"], assessment["scanId"], assessment["objectRef"], assessment["rule"]["ruleId"], assessment["rule"]["version"], json.dumps(assessment, ensure_ascii=False, separators=(",", ":"))),
        )
        scan = self._conn.execute("SELECT run_id FROM scans WHERE scan_id=?", (assessment["scanId"],)).fetchone()
        if scan:
            self._append_runtime_event_raw({
                "eventId": f"event-{uuid.uuid4().hex}", "scanId": assessment["scanId"], "runId": scan["run_id"],
                "sequence": self._next_event_sequence(assessment["scanId"]), "occurredAt": assessment.get("decidedAt") or self._now(),
                "monotonicOffsetMs": self._monotonic_offset_ms(assessment["scanId"]), "source": "host", "category": "decision",
                "name": "assessment.committed", "phase": "finish", "severity": "info", "outcome": "succeeded",
                "summary": f"Assessment committed: {assessment['result']}", "correlation": {
                    "operationId": assessment["commitOperationRef"], "objectRef": assessment["objectRef"],
                    "assessmentRef": assessment["assessmentId"], "findingRefs": assessment["findingRefs"],
                    "evidenceRefs": assessment["evidenceRefs"], "caseRef": assessment["caseRefs"][0],
                }, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"},
                "attributes": {"result": assessment["result"], "coverageComplete": assessment["coverage"]["complete"]},
                "durationMs": 0,
            })

    def get_assessment(self, assessment_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM assessments WHERE assessment_id=?", (assessment_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_assessment(self, assessment: dict) -> None:
        self._conn.execute(
            "UPDATE assessments SET entity_json=? WHERE assessment_id=?",
            (json.dumps(assessment, ensure_ascii=False, separators=(",", ":")), assessment["assessmentId"]),
        )

    def get_assessment_for_object_rule(self, scan_id: str, object_id: str, rule: dict) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM assessments WHERE scan_id=? AND object_id=? AND rule_id=? AND rule_version=?", (scan_id, object_id, rule["ruleId"], rule["version"])).fetchone()
        return json.loads(row[0]) if row else None

    def insert_issue(self, issue: dict) -> None:
        self._conn.execute(
            "INSERT INTO issues(issue_id,scan_id,assessment_id,object_id,entity_json) VALUES(?,?,?,?,?)",
            (issue["issueId"], issue["scanId"], issue["assessmentRef"], issue["objectRef"], json.dumps(issue, ensure_ascii=False, separators=(",", ":"))),
        )
        scan = self._conn.execute("SELECT run_id FROM scans WHERE scan_id=?", (issue["scanId"],)).fetchone()
        if scan:
            self._append_runtime_event_raw({
                "eventId": f"event-{uuid.uuid4().hex}", "scanId": issue["scanId"], "runId": scan["run_id"],
                "sequence": self._next_event_sequence(issue["scanId"]), "occurredAt": issue.get("createdAt") or self._now(),
                "monotonicOffsetMs": self._monotonic_offset_ms(issue["scanId"]), "source": "host", "category": "decision",
                "name": "issue.created", "phase": "instant", "severity": "info", "outcome": "succeeded",
                "summary": "Issue projection created from committed Assessment", "correlation": {
                    "assessmentRef": issue["assessmentRef"], "objectRef": issue["objectRef"], "evidenceRefs": issue["evidenceRefs"],
                }, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": {},
            })

    def get_issue(self, issue_id: str) -> dict | None:
        row = self._conn.execute("SELECT entity_json FROM issues WHERE issue_id=?", (issue_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_issue(self, issue: dict) -> None:
        self._conn.execute(
            "UPDATE issues SET entity_json=? WHERE issue_id=?",
            (json.dumps(issue, ensure_ascii=False, separators=(",", ":")), issue["issueId"]),
        )

    def list_entities(self, table: str, scan_id: str | None = None) -> list[dict]:
        allowed = {"page_states", "entrypoints", "page_candidates", "audit_objects", "object_verifications", "reverse_cases", "action_attempts", "request_observations", "evidence", "screenshots", "dimension_findings", "pending_decisions", "assessments", "issues"}
        if table not in allowed:
            raise ValueError("Reading an unknown entity table is not allowed")
        if scan_id is None:
            rows = self._conn.execute(f"SELECT entity_json FROM {table} ORDER BY rowid").fetchall()
        else:
            rows = self._conn.execute(f"SELECT entity_json FROM {table} WHERE scan_id=? ORDER BY rowid", (scan_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_operations(self, scan_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM operations WHERE scan_id=? ORDER BY rowid", (scan_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_runtime_events(self, scan_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT event_json FROM runtime_events WHERE scan_id=? ORDER BY sequence", (scan_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_runtime_event(self, event: dict) -> dict:
        """Persist a trusted component event under the Host-owned sequence."""
        normalized = dict(event)
        normalized["eventId"] = normalized.get("eventId") or f"event-{uuid.uuid4().hex}"
        normalized["sequence"] = self._next_event_sequence(normalized["scanId"])
        normalized["occurredAt"] = normalized.get("occurredAt") or self._now()
        normalized["monotonicOffsetMs"] = self._monotonic_offset_ms(normalized["scanId"])
        self._append_runtime_event_raw(normalized)
        return normalized

    def ensure_integrity_event(self, scan: dict) -> None:
        existing = self._conn.execute("SELECT 1 FROM runtime_events WHERE scan_id=? AND json_extract(event_json, '$.name')='integrity.validation.completed'", (scan["scanId"],)).fetchone()
        if existing:
            return
        self._append_runtime_event(scan, "integrity", "integrity.validation.completed", "instant", "warning", "succeeded", "Runtime event integrity validation completed with declared limitations")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _append_agent_decision_events(self, op: dict) -> None:
        reason = op.get("decisionReason")
        turn_id = op.get("agentTurnId")
        if not isinstance(reason, str) or not reason.strip() or not turn_id:
            return
        scan_id = op["scanId"]
        scan = self._conn.execute("SELECT run_id FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
        if not scan:
            return
        correlation = {"agentTurnId": turn_id, "requestId": op["requestId"], "operationId": op["operationId"]}
        event = {
            "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan_id, "runId": scan["run_id"],
            "sequence": self._next_event_sequence(scan_id), "occurredAt": op.get("acceptedAt") or self._now(),
            "monotonicOffsetMs": self._monotonic_offset_ms(scan_id), "source": "agent", "category": "decision",
            "name": "agent.decision.recorded", "phase": "instant", "severity": "info", "outcome": "succeeded",
            "summary": self._sanitize_summary(reason), "correlation": correlation,
            "privacy": {"classification": "internal", "sanitizationStatus": "sanitized"},
            "attributes": {"tool": op["tool"]},
        }
        self._append_runtime_event_raw(event)
        telemetry = op.get("modelTelemetry") or {}
        if telemetry.get("durationMs") is not None:
            model_event = {
                "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan_id, "runId": scan["run_id"],
                "sequence": self._next_event_sequence(scan_id), "occurredAt": op.get("acceptedAt") or self._now(),
                "monotonicOffsetMs": self._monotonic_offset_ms(scan_id), "durationMs": telemetry["durationMs"],
                "source": "model", "category": "model", "name": "model.call.finished", "phase": "finish",
                "severity": "info", "outcome": "succeeded", "summary": "Model produced a public tool decision",
                "correlation": correlation, "privacy": {"classification": "sensitive_metadata", "sanitizationStatus": "not_required"},
                "attributes": {"retryCount": telemetry.get("retryCount", 0)},
            }
            self._append_runtime_event_raw(model_event)

    @staticmethod
    def _sanitize_summary(value: str) -> str:
        sanitized = " ".join(value.strip().split())[:1024]
        sanitized = re.sub(r"(?i)(password|passwd|token|secret|cookie|authorization)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", sanitized)
        return sanitized or "Agent recorded a tool decision"

    def _append_operation_event(self, op: dict, phase: str) -> None:
        scan_id = op.get("scanId") or op.get("scan_id")
        scan = self._conn.execute("SELECT run_id FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
        if not scan:
            return
        status = op.get("status")
        if phase == "start":
            outcome, severity, summary = "started", "info", f"Operation started: {op.get('tool', 'unknown')}"
        else:
            outcome = {"succeeded": "succeeded", "rejected": "rejected", "failed_known": "failed", "result_unknown": "unknown"}.get(status, "unknown")
            severity = "info" if outcome == "succeeded" else ("warning" if outcome == "rejected" else "error")
            summary = f"Operation finished: {op.get('tool', 'unknown')} ({status})"
        correlation = {"requestId": op.get("requestId") or op.get("request_id"), "operationId": op.get("operationId") or op.get("operation_id")}
        correlation = {key: value for key, value in correlation.items() if value}
        event = {
            "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan_id, "runId": scan["run_id"],
            "sequence": self._next_event_sequence(scan_id),
            "occurredAt": op.get("acceptedAt") if phase == "start" else (op.get("endedAt") or self._now()),
            "monotonicOffsetMs": self._monotonic_offset_ms(scan_id), "source": "host", "category": "tool", "name": "operation.started" if phase == "start" else "operation.finished",
            "phase": phase, "severity": severity, "outcome": outcome, "summary": summary[:1024],
            "correlation": correlation, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": {"tool": op.get("tool", "unknown"), "status": status or "unknown"},
        }
        if phase == "finish":
            event["durationMs"] = max(0, int(op.get("durationMs") or 0))
        self._append_runtime_event_raw(event)
        kind = op.get("operationKind") or op.get("operation_kind")
        if kind in {"browser_action", "recovery"}:
            category = "browser" if kind == "browser_action" else "recovery"
            phase_event = dict(event)
            phase_event.update({
                "eventId": f"event-{uuid.uuid4().hex}", "sequence": self._next_event_sequence(scan_id),
                "source": category, "category": category,
                "name": f"{category}.operation.started" if phase == "start" else f"{category}.operation.finished",
                "summary": f"{category.replace('_', ' ').title()} operation {phase}: {op.get('tool', 'unknown')}",
            })
            self._append_runtime_event_raw(phase_event)
        if phase == "finish" and op.get("tool") == "perform_action" and outcome == "succeeded":
            gate_event = {
                "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan_id, "runId": scan["run_id"],
                "sequence": self._next_event_sequence(scan_id), "occurredAt": op.get("endedAt") or self._now(),
                "monotonicOffsetMs": self._monotonic_offset_ms(scan_id), "source": "host", "category": "safety",
                "name": "host.gate.passed", "phase": "instant", "severity": "info", "outcome": "succeeded",
                "summary": "Host safety gate allowed browser action", "correlation": correlation,
                "privacy": {"classification": "internal", "sanitizationStatus": "not_required"},
                "attributes": {"tool": op.get("tool", "unknown"), "status": status or "unknown"},
            }
            self._append_runtime_event_raw(gate_event)
        if phase == "finish" and outcome in {"rejected", "failed", "unknown"}:
            code = op.get("errorCode") or op.get("error_code") or "OPERATION_FAILED"
            gate_event = {
                "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan_id, "runId": scan["run_id"],
                "sequence": self._next_event_sequence(scan_id), "occurredAt": op.get("endedAt") or self._now(),
                "monotonicOffsetMs": self._monotonic_offset_ms(scan_id), "source": "host", "category": "safety",
                "name": "host.gate.rejected", "phase": "instant", "severity": severity,
                "outcome": "blocked" if outcome != "unknown" else "unknown", "summary": f"Host gate stopped operation: {code}",
                "correlation": correlation, "privacy": {"classification": "internal", "sanitizationStatus": "not_required"},
                "attributes": {"tool": op.get("tool", "unknown"), "errorCode": code},
            }
            self._append_runtime_event_raw(gate_event)

    def _append_runtime_event(self, scan: dict, category: str, name: str, phase: str, severity: str, outcome: str, summary: str) -> None:
        event = {
            "eventId": f"event-{uuid.uuid4().hex}", "scanId": scan["scanId"], "runId": scan["runId"],
            "sequence": self._next_event_sequence(scan["scanId"]), "occurredAt": self._now(), "monotonicOffsetMs": self._monotonic_offset_ms(scan["scanId"]),
            "source": "host", "category": category, "name": name, "phase": phase, "severity": severity,
            "outcome": outcome, "summary": summary[:1024], "privacy": {"classification": "internal", "sanitizationStatus": "not_required"}, "attributes": {},
        }
        if phase == "finish":
            event["durationMs"] = 0
        self._append_runtime_event_raw(event)

    def _next_event_sequence(self, scan_id: str) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM runtime_events WHERE scan_id=?", (scan_id,)).fetchone()
        return int(row[0])

    def _monotonic_offset_ms(self, scan_id: str) -> int:
        row = self._conn.execute("SELECT started_monotonic_ns FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
        return max(0, int((time.monotonic_ns() - row[0]) / 1_000_000)) if row and row[0] else 0

    def _append_runtime_event_raw(self, event: dict) -> None:
        self._conn.execute("INSERT INTO runtime_events(event_id,scan_id,run_id,sequence,event_json) VALUES(?,?,?,?,?)", (event["eventId"], event["scanId"], event["runId"], event["sequence"], json.dumps(event, ensure_ascii=False, separators=(",", ":"))))

    def insert_bootstrap_key(self, key: str, operation_id: str, digest: str) -> None:
        self._conn.execute("INSERT INTO bootstrap_idempotency(idempotency_key,operation_id,request_digest) VALUES(?,?,?)", (key, operation_id, digest))

    def close(self) -> None:
        self._conn.close()
