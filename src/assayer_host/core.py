from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, RefResolver

from .auth import CredentialVault, LoginAdapter, LoginCoordinator, UnavailableLoginAdapter
from .browser_session import BrowserSessionFailure
from .errors import HostError
from .page import EntrypointAdapter, ReadOnlyPageAdapter, UnavailableEntrypointAdapter, UnavailablePageAdapter
from .object_identity import ObjectIdentityAdapter, UnavailableObjectIdentityAdapter
from .action_safety import (ActionExecution, ActionSafetyPolicy,
                            SafeActionAdapter, UnavailableActionAdapter)
from .recovery import RECOVERY_DIMENSIONS, RecoveryAdapter, RecoveryAttempt, RecoveryCheck, UnavailableRecoveryAdapter
from .evidence import EvidenceAdapter, EvidenceSanitizer, UnavailableEvidenceAdapter, is_page_observation
from .reporting import DerivedReportBuilder
from .observability import render_observability
from .resources import default_rules_root, default_schema_root
from .store import SQLiteStore


TOOL_KINDS = {
    "start_audit": "bootstrap", "get_rule_contract": "read", "get_audit_progress": "read",
    "inspect_page": "read", "explore_entrypoint": "browser_action", "inspect_object": "read",
    "begin_case": "lifecycle", "perform_action": "browser_action", "restore_case": "recovery",
    "inspect_source": "read", "observe_page": "read", "capture_evidence": "read", "record_findings": "finding_record", "prepare_decision": "decision_preparation",
    "commit_decision": "decision_commit", "complete_audit": "lifecycle",
}


class HostCore:
    """Protocol boundary with durable Scan/Operation state and fail-closed adapters."""

    def __init__(self, schema_root: str | Path | None = None, *, store: SQLiteStore | None = None,
                 credential_vault: CredentialVault | None = None, login_adapter: LoginAdapter | None = None,
                 page_adapter: ReadOnlyPageAdapter | None = None, object_identity_adapter: ObjectIdentityAdapter | None = None,
                 action_adapter: SafeActionAdapter | None = None, action_policy: ActionSafetyPolicy | None = None,
                 recovery_adapter: RecoveryAdapter | None = None, evidence_adapter: EvidenceAdapter | None = None,
                 evidence_sanitizer: EvidenceSanitizer | None = None, report_builder: DerivedReportBuilder | None = None,
                 login_coordinator: LoginCoordinator | None = None,
                 scan_id_factory: Callable[[], str] | None = None,
                 entrypoint_adapter: EntrypointAdapter | None = None):
        root = Path(schema_root).resolve() if schema_root else default_schema_root()
        schemas = {}
        for path in root.rglob("*.schema.json"):
            data = json.loads(path.read_text())
            schemas[data["$id"]] = data
            schemas[path.name] = data
        envelope = schemas["envelope.schema.json"]
        self._envelope_validator = Draft202012Validator(envelope, resolver=RefResolver(envelope["$id"], envelope, store=schemas))
        contracts = schemas["tool-contracts.schema.json"]
        def contract_validator(name):
            wrapper = {"$schema":"https://json-schema.org/draft/2020-12/schema", "$ref":f"{contracts['$id']}#/$defs/{name}"}
            return Draft202012Validator(wrapper, resolver=RefResolver.from_schema(wrapper, store=schemas))
        self._contract_validator = contract_validator
        def entity_validator(name):
            schema = schemas[name]
            return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))
        self._page_state_validator = entity_validator("page-state.schema.json")
        self._entrypoint_validator = entity_validator("entrypoint.schema.json")
        self._candidate_validator = entity_validator("page-candidate.schema.json")
        self._audit_object_validator = entity_validator("audit-object.schema.json")
        self._object_verification_validator = entity_validator("object-verification.schema.json")
        self._case_validator = entity_validator("reverse-case.schema.json")
        self._action_attempt_validator = entity_validator("action-attempt.schema.json")
        self._request_observation_validator = entity_validator("request-observation.schema.json")
        self._evidence_validator = entity_validator("evidence.schema.json")
        self._screenshot_validator = entity_validator("screenshot.schema.json")
        self._finding_validator = entity_validator("dimension-finding.schema.json")
        self._pending_decision_validator = entity_validator("pending-decision.schema.json")
        self._assessment_validator = entity_validator("rule-assessment.schema.json")
        self._issue_validator = entity_validator("issue.schema.json")
        self._scan_run_validator = entity_validator("scan-run.schema.json")
        self._ledger_validator = entity_validator("audit-ledger.schema.json")
        self._runtime_event_validator = entity_validator("runtime-event.schema.json")
        self._observability_manifest_validator = entity_validator("observability-manifest.schema.json")
        self._derived_output_validators = {"issues.json": entity_validator("derived-issues.schema.json"), "page-element-judgement.json": entity_validator("page-element-judgement.schema.json"), "run-diagnostics.json": entity_validator("run-diagnostics.schema.json")}
        self._store = store or SQLiteStore()
        self._credential_vault = credential_vault or CredentialVault()
        self._login_adapter = login_adapter or UnavailableLoginAdapter()
        self._login_coordinator = login_coordinator or LoginCoordinator()
        self._scan_id_factory = scan_id_factory or (lambda: self._new_id("scan"))
        self._entrypoint_adapter = entrypoint_adapter or UnavailableEntrypointAdapter()
        self._page_adapter = page_adapter or UnavailablePageAdapter()
        self._object_identity_adapter = object_identity_adapter or UnavailableObjectIdentityAdapter()
        self._action_adapter = action_adapter or UnavailableActionAdapter()
        self._action_policy = action_policy or ActionSafetyPolicy()
        self._recovery_adapter = recovery_adapter or UnavailableRecoveryAdapter()
        self._evidence_adapter = evidence_adapter or UnavailableEvidenceAdapter()
        self._evidence_sanitizer = evidence_sanitizer or EvidenceSanitizer()
        self._report_builder = report_builder or DerivedReportBuilder()
        registry_path = default_rules_root() / "registry.json" if schema_root is None else root.parent / "rules" / "registry.json"
        self._rules_root = registry_path.parent.resolve()
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {"registryVersion":"0.0.0","digest":"0"*64}
        self._rule_registry = registry
        self._rule_registry_version = registry["registryVersion"]
        self._rule_registry_digest = registry["digest"]
        self._rules_by_kind = {}
        self._rules = {}
        for rule in registry.get("rules", []):
            if rule.get("status") != "enabled":
                continue
            self._rules[(rule["ruleId"], rule["version"])] = rule
            for kind in rule["objectKinds"]:
                self._rules_by_kind.setdefault(kind, []).append({"ruleId":rule["ruleId"],"version":rule["version"]})
        self._recover_interrupted_operations()

    @property
    def credential_vault(self) -> CredentialVault:
        return self._credential_vault

    @property
    def rule_registry_version(self) -> str:
        return self._rule_registry_version

    @property
    def frozen_rules(self) -> tuple[dict, ...]:
        return tuple(
            {"ruleId": rule["ruleId"], "version": rule["version"],
             "objectKinds": tuple(rule.get("objectKinds", ())),
             "coverageDimensions": tuple(rule.get("coverageDimensions", ()))}
            for rule in self._rule_registry.get("rules", ())
            if rule.get("status") == "enabled"
        )

    def read_screenshot(self, scan_id: str, run_id: str, screenshot_ref: str) -> tuple[bytes, str]:
        """Return verified screenshot bytes to the trusted transport adapter.

        This is not an Agent tool.  It lets MCP attach the exact immutable
        pixels referenced by an ``observe_page`` response without putting
        base64 data into the protocol ledger or JSON transport.
        """
        row = self._store.get_scan_by_run(scan_id, run_id)
        screenshot = self._store.get_screenshot(screenshot_ref)
        if row is None or screenshot is None or screenshot.get("scanId") != scan_id:
            raise HostError("UNKNOWN_REFERENCE", "Screenshot does not belong to the current Scan")
        if screenshot.get("status") != "captured" or screenshot.get("imageType") not in {"png", "jpeg", "webp"}:
            raise HostError("SCREENSHOT_NOT_CAPTURED", "Page-observation screenshot cannot be read")
        scan = self._scan_from_row(row)
        try:
            content = self._read_screenshot(scan["outputDir"], screenshot)
        except ValueError as error:
            raise HostError("INTEGRITY_CHECK_FAILED", "Page-observation screenshot integrity check failed") from error
        return content, f"image/{screenshot['imageType']}"

    def record_runtime_event(self, event: dict) -> dict:
        """Persist a sanitized runtime event emitted by a trusted supervisor."""
        if not isinstance(event, dict):
            raise ValueError("runtime event must be an object")
        allowed = {"scanId", "runId", "source", "category", "name", "phase", "severity", "outcome", "summary", "correlation", "privacy", "attributes", "durationMs", "occurredAt"}
        value = {key: event[key] for key in allowed if key in event}
        value.setdefault("eventId", self._new_id("event"))
        scan = self._store.get_scan_by_run(value.get("scanId", ""), value.get("runId", ""))
        if not scan:
            raise HostError("UNKNOWN_REFERENCE", "RuntimeEvent has an invalid Scan/Run reference")
        value["scanId"], value["runId"] = scan["scan_id"], scan["run_id"]
        value.setdefault("occurredAt", self._now())
        value.setdefault("monotonicOffsetMs", 0)
        value.setdefault("privacy", {"classification": "internal", "sanitizationStatus": "sanitized"})
        value.setdefault("attributes", {})
        self._validate_entity(self._runtime_event_validator, {**value, "sequence": 1}, "RuntimeEvent")
        with self._store.transaction():
            return self._store.append_runtime_event(value)

    def refresh_observability(self) -> None:
        """Finalize runtime artifacts after terminal Router/transport events."""
        row = self._store._conn.execute("SELECT * FROM scans ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            return
        scan = self._scan_from_row(dict(row))
        with self._store.transaction():
            self._store.ensure_integrity_event(scan)
        events = self._store.list_runtime_events(scan["scanId"])
        for event in events:
            self._validate_entity(self._runtime_event_validator, event, "RuntimeEvent")
        stream, manifest_bytes, manifest = render_observability(
            scan, events, self._store.list_operations(scan["scanId"]),
            self._store.list_entities("assessments", scan["scanId"]),
        )
        self._validate_entity(self._observability_manifest_validator, manifest, "ObservabilityManifest")
        self._replace_observability_artifacts(scan["outputDir"], stream, manifest_bytes)

    def _recover_interrupted_operations(self):
        interrupted = self._store.get_interrupted_operations()
        for row in interrupted:
            scan = self._scan_from_row(self._store.get_scan(row["scan_id"]))
            case = self._store.get_case(row.get("case_ref")) if row.get("case_ref") else None
            scan["runRevision"] += 1
            scan["status"] = "failed"
            row.update({"status": "result_unknown", "error_code": "REQUEST_RESULT_UNKNOWN", "error_message": "Host restarted during the operation; action or recovery dispatch/completion cannot be proven"})
            if case:
                case.update({"status": "invalidated", "endedAt": self._now(), "restoreReason": {"code": "HOST_RESTART_INTERRUPTED", "message": "Host restart interrupted the action or recovery"}})
            with self._store.transaction():
                self._store.update_scan(scan)
                self._store.update_operation(row)
                if case:
                    self._store.update_case(case)

    def fail_scan(self, scan_id: str, run_id: str, code: str, message: str) -> dict:
        """Fail an active Scan after its supervising Agent lease is lost.

        This is an internal runtime-supervision API, not an Agent tool.  It
        invalidates publishable conclusions and active lifecycle entities
        before the browser Context is closed.
        """
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
            raise ValueError("supervision failure code must be an uppercase error code")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("supervision failure message must be nonempty")
        row = self._store.get_scan_by_run(scan_id, run_id)
        if row is None:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist")
        scan = self._scan_from_row(row)
        if scan["status"] in {"completed", "partial", "failed"}:
            return {"scanId": scan_id, "runId": run_id, "runRevision": scan["runRevision"],
                    "scanStatus": scan["status"], "failureEventId": None}

        event_id = self._new_id("operation")
        ended_at = self._now()
        reason = {"code": code, "message": self._evidence_sanitizer.sanitize(message.strip())}
        accepted_revision = scan["runRevision"]
        scan["runRevision"] += 1
        scan["status"] = "failed"
        supervision_operation = {
            "operationId": event_id, "scanId": scan_id, "requestId": self._new_id("request"),
            "tool": "agent_supervision", "operationKind": "lifecycle",
            "idempotencyKey": f"agent-supervision:{event_id}",
            "requestDigest": self._digest({"code": code, "message": reason["message"]}),
            "status": "failed_known", "acceptedAtRevision": accepted_revision,
            "errorCode": code, "errorMessage": reason["message"],
        }

        cases = self._store.list_entities("reverse_cases", scan_id)
        pending_decisions = self._store.list_entities("pending_decisions", scan_id)
        assessments = self._store.list_entities("assessments", scan_id)
        issues = self._store.list_entities("issues", scan_id)
        operations = self._store.list_operations(scan_id)
        changed_operation_ids = set()
        active_case_states = {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared", "restoring"}
        for case in cases:
            if case.get("status") in active_case_states:
                case.update({"status": "invalidated", "endedAt": ended_at, "restoreReason": reason})
        for pending in pending_decisions:
            if pending.get("status") == "pending":
                pending["status"] = "invalidated"
        for assessment in assessments:
            if assessment.get("conclusionValidity") == "valid":
                assessment["conclusionValidity"] = "invalidated"
                assessment["invalidatedBy"] = list(dict.fromkeys(
                    list(assessment.get("invalidatedBy", [])) + [event_id]
                ))
        for issue in issues:
            if issue.get("conclusionValidity") == "valid":
                issue["conclusionValidity"] = "invalidated"
                issue["invalidatedBy"] = list(dict.fromkeys(
                    list(issue.get("invalidatedBy", [])) + [event_id]
                ))
        for operation in operations:
            if operation.get("status") == "running":
                unknown = operation.get("operation_kind") in {"browser_action", "recovery"}
                changed_operation_ids.add(operation["operation_id"])
                operation.update({
                    "status": "result_unknown" if unknown else "failed_known",
                    "error_code": "REQUEST_RESULT_UNKNOWN" if unknown else code,
                    "error_message": "Browser action result cannot be proven after supervised termination" if unknown else reason["message"],
                })

        for case in cases:
            if case.get("status") == "invalidated" and case.get("restoreReason") == reason:
                self._validate_entity(self._case_validator, case, "ReverseCase")
        for pending in pending_decisions:
            if pending.get("status") == "invalidated":
                self._validate_entity(self._pending_decision_validator, pending, "PendingDecision")
        for assessment in assessments:
            if event_id in assessment.get("invalidatedBy", []):
                self._validate_entity(self._assessment_validator, assessment, "RuleAssessment")
        for issue in issues:
            if event_id in issue.get("invalidatedBy", []):
                self._validate_entity(self._issue_validator, issue, "Issue")

        with self._store.transaction():
            self._store.update_scan(scan)
            self._store.insert_operation(supervision_operation)
            for case in cases:
                if case.get("status") == "invalidated" and case.get("restoreReason") == reason:
                    self._store.update_case(case)
            for pending in pending_decisions:
                if pending.get("status") == "invalidated":
                    self._store.update_pending_decision(pending)
            for assessment in assessments:
                if event_id in assessment.get("invalidatedBy", []):
                    self._store.update_assessment(assessment)
            for issue in issues:
                if event_id in issue.get("invalidatedBy", []):
                    self._store.update_issue(issue)
            for operation in operations:
                if operation.get("operation_id") in changed_operation_ids:
                    self._store.update_operation(operation)
        return {"scanId": scan_id, "runId": run_id, "runRevision": scan["runRevision"],
                "scanStatus": "failed", "failureEventId": event_id, "reason": reason}

    def handle(self, request: dict) -> dict:
        self._validate_envelope(request)
        tool = request["tool"]
        self._validate_tool_input(tool, request["input"])
        if tool == "start_audit":
            return self._start_audit(request)
        scan = self._require_scan(request)
        if tool == "get_operation":
            return self._get_operation(request, scan)
        existing = self._store.get_by_idempotency(scan["scanId"], request["idempotencyKey"])
        if existing:
            self._assert_same_digest(existing, self._request_digest(request), "Idempotency key maps to a different request digest")
            if existing["status"] == "running":
                if tool == "inspect_page": return self._inspect_page(request, scan, existing)
                if tool == "explore_entrypoint": return self._explore_entrypoint(request, scan, existing)
                if tool == "inspect_object": return self._inspect_object(request, scan, existing)
                if tool == "get_rule_contract": return self._get_rule_contract(request, scan, existing)
                if tool == "get_audit_progress": return self._get_audit_progress(request, scan, existing)
                if tool == "begin_case": return self._begin_case(request, scan, existing)
                if tool == "observe_page": return self._observe_page(request, scan, existing)
                if tool == "capture_evidence": return self._capture_evidence(request, scan, existing)
                if tool == "record_findings": return self._record_findings(request, scan, existing)
                if tool == "prepare_decision": return self._prepare_decision(request, scan, existing)
                if tool == "commit_decision": return self._commit_decision(request, scan, existing)
                if tool == "complete_audit": return self._complete_audit(request, scan, existing)
            return self._operation_response(request, scan, existing)
        if scan["status"] in {"completed", "partial", "failed"} and (tool != "complete_audit" or scan["status"] == "completed"):
            raise HostError("RUN_TERMINAL", "Scan has reached a terminal state")
        implemented = tool in {"get_rule_contract", "get_audit_progress", "inspect_page", "explore_entrypoint", "inspect_object", "begin_case", "perform_action", "restore_case", "observe_page", "capture_evidence", "record_findings", "prepare_decision", "commit_decision", "complete_audit"}
        operation, repeated = self._accept_operation(request, scan, implemented=implemented)
        if repeated:
            if operation["status"] == "running" and tool == "inspect_page":
                return self._inspect_page(request, scan, operation)
            if operation["status"] == "running" and tool == "explore_entrypoint":
                return self._explore_entrypoint(request, scan, operation)
            if operation["status"] == "running" and tool == "inspect_object":
                return self._inspect_object(request, scan, operation)
            if operation["status"] == "running" and tool == "get_rule_contract":
                return self._get_rule_contract(request, scan, operation)
            if operation["status"] == "running" and tool == "get_audit_progress":
                return self._get_audit_progress(request, scan, operation)
            if operation["status"] == "running" and tool == "begin_case":
                return self._begin_case(request, scan, operation)
            if operation["status"] == "running" and tool == "observe_page":
                return self._observe_page(request, scan, operation)
            if operation["status"] == "running" and tool == "capture_evidence":
                return self._capture_evidence(request, scan, operation)
            if operation["status"] == "running" and tool == "record_findings":
                return self._record_findings(request, scan, operation)
            if operation["status"] == "running" and tool == "prepare_decision":
                return self._prepare_decision(request, scan, operation)
            if operation["status"] == "running" and tool == "commit_decision":
                return self._commit_decision(request, scan, operation)
            if operation["status"] == "running" and tool == "complete_audit":
                return self._complete_audit(request, scan, operation)
            return self._operation_response(request, scan, operation)
        if tool == "inspect_page":
            return self._inspect_page(request, scan, operation)
        if tool == "get_rule_contract":
            return self._get_rule_contract(request, scan, operation)
        if tool == "get_audit_progress":
            return self._get_audit_progress(request, scan, operation)
        if tool == "explore_entrypoint":
            return self._explore_entrypoint(request, scan, operation)
        if tool == "inspect_object":
            return self._inspect_object(request, scan, operation)
        if tool == "begin_case":
            return self._begin_case(request, scan, operation)
        if tool == "perform_action":
            return self._perform_action(request, scan, operation)
        if tool == "restore_case":
            return self._restore_case(request, scan, operation)
        if tool == "observe_page":
            return self._observe_page(request, scan, operation)
        if tool == "capture_evidence":
            return self._capture_evidence(request, scan, operation)
        if tool == "record_findings":
            return self._record_findings(request, scan, operation)
        if tool == "prepare_decision":
            return self._prepare_decision(request, scan, operation)
        if tool == "commit_decision":
            return self._commit_decision(request, scan, operation)
        if tool == "complete_audit":
            return self._complete_audit(request, scan, operation)
        error = HostError("INTERNAL_FAILURE", f"Tool {tool} is not connected to a Host adapter")
        return self._response(request, scan, "rejected", error=error, operation=operation)

    def _start_audit(self, request: dict) -> dict:
        if request["input"]["ruleRegistryVersion"] != self._rule_registry_version:
            raise HostError("INVALID_REQUEST", "Requested rule registry version does not match the current Host version", next_step="refresh_rule_registry")
        entry_url = self._safe_entry_url(request["input"]["url"])
        digest = self._request_digest(request)
        with self._store.transaction():
            existing = self._store.get_bootstrap(request["idempotencyKey"])
            if existing:
                self._assert_same_digest(existing, digest, "Bootstrap idempotency key maps to a different request digest")
                scan = self._scan_from_row(self._store.get_scan(existing["scan_id"]))
                return self._operation_response(request, scan, existing)
            scan = {
                "scanId": self._scan_id_factory(), "runId": self._new_id("run"), "runRevision": 0,
                "status": "authenticating", "loginStatus": "pending", "createdAt": self._now(),
                "ruleRegistryDigest": self._rule_registry_digest, "currentPageStateId": None, "capabilitiesJson": "[]",
                "outputDir": request["input"]["outputDir"], "entryUrl": entry_url,
            }
            operation = self._new_operation(request, scan["scanId"], "bootstrap", digest, "running", 0)
            self._store.insert_scan(scan)
            self._store.insert_operation(operation)
            self._store.insert_bootstrap_key(request["idempotencyKey"], operation["operationId"], digest)

        auth_mode = request["input"].get("authMode", "credential")
        if auth_mode == "anonymous":
            outcome = self._login_coordinator.authenticate_anonymous(request["input"]["url"], self._login_adapter)
        else:
            secret = self._credential_vault.consume(request["input"].get("credentialHandle", ""))
            if secret is None:
                return self._finish_bootstrap_failure(request, scan, operation, "CREDENTIAL_CHANNEL_FAILED", "Credential handle does not exist, has expired, or has been consumed")
            outcome = self._login_coordinator.authenticate(request["input"]["url"], secret, self._login_adapter)
        if outcome.status != "succeeded":
            return self._finish_bootstrap_failure(request, scan, operation, outcome.error_code or "LOGIN_FAILED", outcome.reason or "Login failed")
        login = outcome.result

        scan.update({"runRevision": 1, "status": "exploring", "loginStatus": "succeeded",
                     "currentPageStateId": login.current_page_state_id,
                     "capabilitiesJson": json.dumps(list(login.capabilities), separators=(",", ":"))})
        operation["status"] = "succeeded"
        with self._store.transaction():
            self._store.update_operation(operation)
            self._store.update_scan(scan)
        return self._response(request, scan, "ok", result=self._start_result(scan), operation=operation)

    def _finish_bootstrap_failure(self, request, scan, operation, code, message):
        scan.update({"runRevision": 1, "status": "failed", "loginStatus": "failed"})
        operation.update({"status": "failed_known", "errorCode": code, "errorMessage": message})
        with self._store.transaction():
            self._store.update_operation(operation)
            self._store.update_scan(scan)
        return self._response(request, scan, "failed", error=HostError(code, message), operation=operation)

    @staticmethod
    def _safe_entry_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise HostError("INVALID_REQUEST", "Entry URL must be an HTTP(S) URL without embedded credentials")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        try:
            port = parsed.port
        except ValueError as error:
            raise HostError("INVALID_REQUEST", "Entry URL port is invalid") from error
        netloc = f"{host}:{port}" if port is not None else host
        base = f"{parsed.scheme}://{netloc}{parsed.path or '/'}"
        fragment_path = urlparse(parsed.fragment).path if parsed.fragment.startswith("/") else ""
        return f"{base}#{fragment_path}" if fragment_path else base

    def _start_result(self, scan: dict) -> dict:
        result = {"scanId":scan["scanId"],"runId":scan["runId"],"loginStatus":scan["loginStatus"],
                  "ruleRegistryDigest":scan["ruleRegistryDigest"],"capabilities":json.loads(scan["capabilitiesJson"]),
                  "runRevision":scan["runRevision"],
                  "frozenRules":[{"ruleId":rule["ruleId"],"version":rule["version"],
                                  "objectKinds":list(rule.get("objectKinds", [])),
                                  "coverageDimensions":list(rule.get("coverageDimensions", []))}
                                 for rule in self._rule_registry.get("rules", []) if rule.get("status") == "enabled"]}
        if scan.get("currentPageStateId"):
            result["currentPageStateId"] = scan["currentPageStateId"]
        return result

    def _get_operation(self, request: dict, scan: dict) -> dict:
        op = self._store.get_operation(request["input"]["operationId"])
        if not op or op["scan_id"] != scan["scanId"]:
            raise HostError("UNKNOWN_REFERENCE", "Operation does not exist in the current Scan")
        result = {"operationId":op["operation_id"],"status":op["status"],"requestDigest":op["request_digest"]}
        if op.get("result_json"):
            result["result"] = json.loads(op["result_json"])
        return self._response(request, scan, "ok", result=result)

    def _get_rule_contract(self, request: dict, scan: dict, operation: dict) -> dict:
        rule_ref = request["input"]["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not rule or scan.get("ruleRegistryDigest") != self._rule_registry_digest:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Rule is not a frozen enabled rule for the current Scan")
        path = (self._rules_root.parent / rule["document"]).resolve()
        try:
            if self._rules_root not in path.parents or not path.is_file():
                raise ValueError("Rule document path is invalid")
            content = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except (OSError, ValueError) as error:
            return self._finish_operation_failure(request, scan, operation, "RULE_CONTRACT_UNAVAILABLE", str(error))
        if digest != rule.get("contentDigest"):
            return self._finish_operation_failure(request, scan, operation, "RULE_CONTRACT_INTEGRITY_FAILED", "Rule document digest does not match the frozen registry")
        result = {"operationId": operation.get("operation_id") or operation["operationId"], "runRevision": scan["runRevision"],
                  "rule": dict(rule), "content": content, "contentDigest": digest, "registryDigest": scan["ruleRegistryDigest"]}
        operation.update({"status":"succeeded", "resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _get_audit_progress(self, request: dict, scan: dict, operation: dict) -> dict:
        pages = self._store.list_entities("page_states", scan["scanId"])
        objects = self._store.list_entities("audit_objects", scan["scanId"])
        cases = self._store.list_entities("reverse_cases", scan["scanId"])
        pending = self._store.list_entities("pending_decisions", scan["scanId"])
        grouped, _ = self._entrypoint_partitions(scan["scanId"])
        active_statuses = {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared", "restoring"}
        investigations = []
        for target in objects:
            for rule_ref in target.get("potentialRules", []):
                rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
                if not rule:
                    continue
                latest = self._store.get_latest_findings(scan["scanId"], target["objectId"], rule_ref)
                coverage, effective = self._finding_coverage(scan["scanId"], target["objectId"], rule_ref)
                effective_ids = {item["findingId"] for item in effective}
                investigations.append({"objectRef":target["objectId"], "rule":rule_ref, **coverage,
                                       "latestFindingRefs":[item["findingId"] for item in latest],
                                       "stagedFindingRefs":[item["findingId"] for item in latest if item["findingId"] not in effective_ids]})
        result = {"operationId":operation.get("operation_id") or operation["operationId"], "runRevision":scan["runRevision"],
                  "scanStatus":scan["status"], "currentPageStateId":scan["currentPageStateId"],
                  "visitedPageStateRefs":[item["pageStateId"] for item in pages], "entrypoints":grouped,
                  "objects":[{"objectId":item["objectId"], "status":item["status"], "potentialRules":item.get("potentialRules", []), "assessmentRefs":item.get("assessmentRefs", [])} for item in objects],
                  "activeCases":[{"caseId":item["caseId"], "objectRef":item["objectRef"], "rule":item["rule"], "status":item["status"]} for item in cases if item.get("status") in active_statuses],
                  "investigations":investigations,
                  "pendingDecisionRefs":[item["pendingDecisionId"] for item in pending if item.get("status") == "pending"]}
        operation.update({"status":"succeeded", "resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _inspect_page(self, request: dict, scan: dict, operation: dict) -> dict:
        page_state_id = request["input"]["pageStateId"]
        if page_state_id != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PageState is not the active page state for the current Scan")
        stored = self._store.get_page_state(page_state_id)
        if stored:
            entrypoints = self._store.get_entrypoints(page_state_id)
            candidates = self._store.get_candidates(page_state_id)
            inspection = self._store.get_page_inspection(page_state_id) or {}
        else:
            try:
                observed = self._page_adapter.observe(page_state_id)
                stored, inspection, entrypoints, candidates = self._materialize_page(scan, page_state_id, observed)
                self._validate_entity(self._page_state_validator, stored, "PageState")
                for item in entrypoints:
                    self._validate_entity(self._entrypoint_validator, item, "Entrypoint")
                for item in candidates:
                    self._validate_entity(self._candidate_validator, item, "PageCandidate")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            except Exception:
                return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Read-only page adapter failed")
            with self._store.transaction():
                self._store.insert_page_inspection(stored, inspection, entrypoints, candidates)
        result = self._inspect_page_result(request, scan, operation, stored, inspection, entrypoints, candidates)
        operation.update({"status":"succeeded","resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _materialize_page(self, scan, page_state_id, observed, parent_page_state_id=None):
        revision = scan["runRevision"]
        candidates = []
        candidate_by_locator_digest = {}
        for item in observed.candidates:
            rules = self._rules_by_kind.get(item.kind, [])
            if not rules:
                continue
            candidate = {"candidateId":self._stable_id("candidate", page_state_id, item.locator_material),
                         "scanId":scan["scanId"],"pageStateRef":page_state_id,"kind":item.kind,"label":item.label,
                         "role":item.role,"locatorDigest":self._digest(item.locator_material),"potentialRules":rules,
                         "observedAtRevision":revision}
            candidates.append(candidate)
            candidate_by_locator_digest[candidate["locatorDigest"]] = candidate["candidateId"]
        entrypoints = []
        embedded = []
        for index, item in enumerate(observed.entrypoints):
            entrypoint_id = self._stable_id("entrypoint", page_state_id, str(index), item.kind, item.label)
            reason = {"code":item.reason_code,"message":item.reason_message}
            identity_material = item.logical_identity_material
            if not identity_material:
                identity_material = json.dumps(
                    [observed.origin, observed.route, item.kind, " ".join(item.label.split()), item.host_locator_id or ""],
                    ensure_ascii=False, separators=(",", ":"),
                )
            entrypoint = {"entrypointId":entrypoint_id,"scanId":scan["scanId"],"pageStateRef":page_state_id,
                          "kind":item.kind,"label":item.label,"status":item.status,"discoveredAtRevision":revision,
                          "identity":{"algorithmVersion":"1.0.0","materialDigest":self._digest([observed.origin, item.kind, identity_material])},
                          "reason":reason}
            if item.host_locator_id:
                entrypoint["hostLocatorId"] = item.host_locator_id
            if item.target_locator_material:
                candidate_ref = candidate_by_locator_digest.get(self._digest(item.target_locator_material))
                if candidate_ref:
                    entrypoint["candidateRef"] = candidate_ref
            entrypoints.append(entrypoint)
            embedded_item = {"entrypointId":entrypoint_id,"label":item.label,"intent":item.intent,
                             "status":item.status,"reason":reason}
            if entrypoint.get("candidateRef"):
                embedded_item["candidateRef"] = entrypoint["candidateRef"]
            embedded.append(embedded_item)
        page = {"pageStateId":page_state_id,"scanId":scan["scanId"],"url":observed.url,"origin":observed.origin,
                "route":observed.route,"title":observed.title,"stateKind":observed.state_kind,"observedAtRevision":revision,
                "observedAt":self._now(),"domDigest":self._digest(observed.dom_material),
                "identity":{"algorithmVersion":"1.0.0","materialDigest":self._digest(observed.identity_material)},
                "safeEntrypoints":embedded,"objectRefs":[]}
        if parent_page_state_id:
            page["parentPageStateId"] = parent_page_state_id
        inspection = {"visibleText":observed.visible_text,"networkSummary":observed.network_summary or {},
                      "structureSummary":observed.structure_summary or {},"activeTab":observed.active_tab}
        return page, inspection, entrypoints, candidates

    def _inspect_page_result(self, request, scan, operation, page, inspection, entrypoints, candidates):
        include = set(request["input"]["include"])
        result = {"operationId":operation.get("operation_id") or operation["operationId"],"runRevision":scan["runRevision"],
                  "pageStateId":page["pageStateId"],"candidateRefs":[x["candidateId"] for x in candidates],
                  "entrypointRefs":[x["entrypointId"] for x in entrypoints],
                  "entrypoints":[{"entrypointId":x["entrypointId"],"kind":x["kind"],"label":x["label"],"status":x["status"]}
                                 for x in entrypoints]}
        if "route" in include:
            result.update({"route":page.get("route", ""),"title":page.get("title", "")})
        if "visibleText" in include:
            result["visibleText"] = inspection.get("visibleText", "")
        if "networkSummary" in include:
            result["networkSummary"] = inspection.get("networkSummary", {})
        if inspection.get("structureSummary") is not None:
            result["structureSummary"] = inspection.get("structureSummary", {})
        if inspection.get("activeTab"):
            result["activeTab"] = inspection["activeTab"]
        return result

    def _explore_entrypoint(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        previous_id = data["pageStateId"]
        if previous_id != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Entrypoint source is not the current active PageState")
        barrier = self._navigation_decision_barrier(scan, previous_id)
        if barrier is not None:
            code, message = barrier
            return self._finish_operation_failure(
                request, scan, operation, code, message,
            )
        entrypoint = next((item for item in self._store.get_entrypoints(previous_id)
                           if item["entrypointId"] == data["entrypointId"]), None)
        if not entrypoint or entrypoint.get("scanId") != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Entrypoint does not exist on the current page")
        operation_id = operation.get("operation_id") or operation["operationId"]
        try:
            execution = self._entrypoint_adapter.explore(entrypoint, self._store.get_page_state(previous_id), operation_id)
        except BrowserSessionFailure:
            return self._finish_operation_failure(request, scan, operation, "BROWSER_SESSION_FAILED", "Browser Session failed during entrypoint exploration")
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Entrypoint exploration adapter failed")
        if execution.status == "unavailable":
            return self._finish_operation_failure(request, scan, operation, "ACTION_ADAPTER_UNAVAILABLE", execution.diagnostic or "Entrypoint exploration is unavailable")
        if execution.status != "succeeded":
            code = "REQUEST_BLOCKED" if execution.status == "request_blocked" and not execution.page_changed else "BROWSER_SESSION_FAILED"
            return self._finish_operation_failure(request, scan, operation, code, execution.diagnostic or "Entrypoint exploration result cannot be proven")
        next_id = self._new_id("page")
        scan["runRevision"] += 1
        scan["currentPageStateId"] = next_id
        try:
            observed = self._page_adapter.observe(next_id)
            if observed.active_tab != entrypoint.get("label"):
                raise HostError("BROWSER_SESSION_FAILED", "Active entrypoint does not match the target after the tab switch")
            page, inspection, entrypoints, candidates = self._materialize_page(scan, next_id, observed, previous_id)
            self._validate_entity(self._page_state_validator, page, "PageState")
            for item in entrypoints:
                self._validate_entity(self._entrypoint_validator, item, "Entrypoint")
            for item in candidates:
                self._validate_entity(self._candidate_validator, item, "PageCandidate")
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "BROWSER_SESSION_FAILED", "A trusted PageState could not be established after switching entrypoints")
        request_observations = []
        for index, network in enumerate(execution.requests):
            decision = self._action_policy.classify_request(network, page.get("origin", ""))
            parsed = urlparse(network.url)
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else parsed.path
            item = {"observationId":self._stable_id("request", operation_id, str(index)),"scanId":scan["scanId"],
                    "operationId":operation_id,"method":network.method.upper(),"url":safe_url,
                    "transport":network.transport,"sent":network.sent,"outcome":decision.outcome,
                    "code":decision.code,"reason":decision.reason}
            self._validate_entity(self._request_observation_validator, item, "RequestObservation")
            request_observations.append(item)
        result = {"operationId":operation_id,"runRevision":scan["runRevision"],"entrypointId":entrypoint["entrypointId"],
                  "previousPageStateId":previous_id,"pageStateId":next_id,
                  "candidateRefs":[item["candidateId"] for item in candidates],
                  "entrypointRefs":[item["entrypointId"] for item in entrypoints],
                  "entrypoints":[{"entrypointId":item["entrypointId"],"kind":item["kind"],"label":item["label"],"status":item["status"]}
                                 for item in entrypoints],"route":page.get("route", ""),
                  "title":page.get("title", ""),"structureSummary":inspection.get("structureSummary", {}),
                  "networkSummary":inspection.get("networkSummary", {}),"visibleText":inspection.get("visibleText", "")}
        if inspection.get("activeTab"):
            result["activeTab"] = inspection["activeTab"]
        operation.update({"status":"succeeded","resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.insert_page_inspection(page, inspection, entrypoints, candidates)
            for item in request_observations:
                self._store.insert_request_observation(item)
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _navigation_decision_barrier(self, scan: dict, page_state_id: str) -> tuple[str, str] | None:
        """Prevent leaving a page with an investigation that has not closed.

        Object and Evidence references are intentionally bound to a PageState.
        Once a Case/Finding has been started, navigating away before the
        decision is committed would make the inevitable prepare/commit call
        stale.  Enforce the ordering at the Host boundary so the model gets a
        deterministic next step instead of discovering it several calls later.
        """
        objects = self._store.list_entities("audit_objects", scan["scanId"])
        cases = self._store.list_entities("reverse_cases", scan["scanId"])
        pending = self._store.list_entities("pending_decisions", scan["scanId"])
        object_ids = {item["objectId"] for item in objects if item.get("pageStateRef") == page_state_id}
        if not object_ids:
            return None
        if any(item.get("status") == "pending" and item.get("objectRef") in object_ids for item in pending):
            return "DECISION_REQUIRED", "The current page has an uncommitted PendingDecision; complete the current object decision first"
        active_states = {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared", "restoring"}
        if any(item.get("objectRef") in object_ids and item.get("status") in active_states for item in cases):
            return "CASE_ACTIVE", "The current page has an unrestored Case; restore it and complete the current object decision first"
        assessed = {
            (item.get("objectRef"), item.get("rule", {}).get("ruleId"), item.get("rule", {}).get("version"))
            for item in self._store.list_entities("assessments", scan["scanId"])
        }
        for case in cases:
            if case.get("objectRef") not in object_ids or case.get("status") != "completed":
                continue
            if case.get("recovery", {}).get("finalStatus") != "restored":
                return "CASE_ACTIVE", "The current page Case recovery state is unconfirmed; the page cannot be switched"
            rule = case.get("rule", {})
            key = (case.get("objectRef"), rule.get("ruleId"), rule.get("version"))
            if key not in assessed:
                return "DECISION_REQUIRED", "The current page Case is complete but its decision is uncommitted; finish record_findings→prepare_decision→commit_decision first"
        return None

    def build_completion_input(self, scan_id: str, run_id: str, completion_reason: str | None = None) -> dict:
        """Build a completion payload from the durable ledger.

        This is an internal assembly API used by the product MCP facade.  The
        model no longer copies page, entrypoint, object, or result-count
        references from a possibly stale progress snapshot.
        """
        row = self._store.get_scan_by_run(scan_id, run_id)
        if row is None:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist")
        scan = self._scan_from_row(row)
        pages = self._store.list_entities("page_states", scan_id)
        objects = self._store.list_entities("audit_objects", scan_id)
        assessments = self._store.list_entities("assessments", scan_id)
        partitions, skip_reasons = self._entrypoint_partitions(scan_id)
        processed_entrypoints = partitions["processed"]
        skipped_entrypoints = [
            {"entrypointId": ref, "reason": skip_reasons[ref]}
            for ref in partitions["skipped"]
        ]
        unprocessed_entrypoints = partitions["unprocessed"]
        processed_objects = []
        for item in objects:
            if item.get("status") == "decided" and len(item.get("assessmentRefs", [])) >= len(item.get("potentialRules", [])):
                processed_objects.append(item["objectId"])
        by_rule = {}
        for assessment in assessments:
            rule = assessment.get("rule", {})
            key = (rule.get("ruleId"), rule.get("version"))
            by_rule.setdefault(key, []).append(assessment)
        summaries = []
        for rule in self._rules.values():
            key = (rule["ruleId"], rule["version"])
            found = by_rule.get(key, [])
            counts = {result: sum(1 for item in found if item.get("result") == result)
                      for result in ("issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise")}
            summaries.append({
                "rule": {"ruleId": rule["ruleId"], "version": rule["version"]},
                "assessmentCount": len(found),
                "resultCounts": {key: value for key, value in counts.items() if value},
                "coverageComplete": bool(found) and all(item.get("coverage", {}).get("complete") for item in found),
            })
        reason = (completion_reason or "Host completed the audit from the current durable ledger; unfinished work remains partial.").strip()
        return {
            "visitedPageStateRefs": [item["pageStateId"] for item in pages],
            "processedObjectRefs": processed_objects,
            "processedEntrypointRefs": processed_entrypoints,
            "skippedEntrypoints": skipped_entrypoints,
            "ruleSummaries": summaries,
            "unprocessedEntrypointRefs": unprocessed_entrypoints,
            "completionReason": reason,
        }

    def _entrypoint_partitions(self, scan_id: str) -> tuple[dict[str, list[str]], dict[str, dict]]:
        """Resolve physical Entrypoints through their stable logical identity.

        PageState is immutable, so a persistent navigation shell is observed as
        a new set of physical Entrypoints after every tab switch. Coverage is
        nevertheless about logical destinations. A completed member therefore
        closes its equivalent observations, while unrelated/new identities
        remain unprocessed. Candidate-backed safe entries close only after the
        corresponding audit object has a complete formal decision.
        """
        entrypoints = self._store.list_entities("entrypoints", scan_id)
        explored = set()
        for op in self._store.list_operations(scan_id):
            if op.get("tool") != "explore_entrypoint" or op.get("status") != "succeeded" or not op.get("result_json"):
                continue
            try:
                explored.add(json.loads(op["result_json"]).get("entrypointId"))
            except (TypeError, json.JSONDecodeError):
                continue

        decided_objects = {
            item["objectId"] for item in self._store.list_entities("audit_objects", scan_id)
            if item.get("status") == "decided"
            and len(item.get("assessmentRefs", [])) >= len(item.get("potentialRules", []))
        }
        decided_candidates = {
            item.get("sourceRef")
            for item in self._store.list_entities("object_verifications", scan_id)
            if item.get("sourceKind") == "candidate" and item.get("outcome") == "matched"
            and item.get("objectRef") in decided_objects
        }

        by_identity = {}
        for item in entrypoints:
            digest = (item.get("identity") or {}).get("materialDigest") or item["entrypointId"]
            by_identity.setdefault(digest, []).append(item)

        partitions = {"processed": [], "skipped": [], "unprocessed": []}
        skip_reasons = {}
        for members in by_identity.values():
            directly_processed = {
                item["entrypointId"] for item in members
                if item["entrypointId"] in explored or item.get("status") == "processed"
                or item.get("candidateRef") in decided_candidates
            }
            skipped_members = [item for item in members if item.get("status") == "skipped"]
            if directly_processed:
                status = "processed"
            elif skipped_members:
                status = "skipped"
            else:
                status = "unprocessed"
            for item in members:
                ref = item["entrypointId"]
                partitions[status].append(ref)
                if status == "skipped":
                    source = next((member for member in skipped_members if (member.get("reason") or {}).get("message")), None)
                    skip_reasons[ref] = (source or {}).get("reason") or {"code": "SKIPPED", "message": "Equivalent logical entrypoint was explicitly skipped"}
        for refs in partitions.values():
            refs.sort()
        return partitions, skip_reasons

    def _inspect_object(self, request: dict, scan: dict, operation: dict) -> dict:
        source_kind = "candidate" if "candidateId" in request["input"] else "audit_object"
        source_ref = request["input"].get("candidateId") or request["input"].get("objectId")
        source = self._store.get_candidate(source_ref) if source_kind == "candidate" else self._store.get_audit_object(source_ref)
        if not source or source["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Candidate or AuditObject does not exist in the current Scan")
        if source["pageStateRef"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Object source page is not the current active PageState")
        page = self._store.get_page_state(source["pageStateRef"])
        if not page:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Object source PageState does not exist")
        try:
            outcome = self._object_identity_adapter.verify_candidate(source, page) if source_kind == "candidate" else self._object_identity_adapter.rebind_object(source, page)
            self._validate_identity_outcome(outcome)
            verification, audit_object, replacement = self._materialize_object_verification(scan, source_kind, source, outcome, operation)
            self._validate_entity(self._object_verification_validator, verification, "ObjectVerification")
            if audit_object:
                self._validate_entity(self._audit_object_validator, audit_object, "AuditObject")
            if replacement:
                self._validate_entity(self._candidate_validator, replacement, "PageCandidate")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Object identity adapter failed")
        result = {"operationId":operation.get("operation_id") or operation["operationId"],"runRevision":scan["runRevision"],
                  "verificationRef":verification["verificationId"],"rebindStatus":verification["outcome"],
                  "candidateCount":verification["candidateCount"],"matchedDimensions":verification["matchedDimensions"],
                  "changedDimensions":verification["changedDimensions"],"evidenceRefs":[]}
        if verification.get("objectRef"):
            result["objectId"] = verification["objectRef"]
            result["objectKind"] = audit_object["kind"]
            result["potentialRules"] = audit_object["potentialRules"]
            result["controls"] = audit_object.get("controls", [])
            result["lists"] = audit_object.get("lists", [])
        if replacement:
            result["replacementCandidateRef"] = replacement["candidateId"]
        operation.update({"status":"succeeded","resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            if replacement:
                self._store.insert_candidate(replacement)
            self._store.insert_object_verification(verification, audit_object)
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _begin_case(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        obj = self._store.get_audit_object(data["objectId"])
        if not obj or obj["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "AuditObject does not exist in the current Scan")
        if obj["pageStateRef"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Object is not a verified object on the current active page")
        if obj.get("status") not in {"eligible", "investigating"} or obj.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "The object's current state does not allow Case creation")
        rule = data["rule"]
        if rule not in obj.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Rule does not belong to the object's frozen rule set")
        if (rule["ruleId"], rule["version"]) not in self._rules:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Rule version is not in the Host registry")
        required = set(self._rules[(rule["ruleId"], rule["version"])].get("requiredCapabilities", []))
        capabilities = set(json.loads(scan["capabilitiesJson"]))
        if not required.issubset(capabilities):
            return self._finish_operation_failure(request, scan, operation, "CAPABILITY_MISSING", "The current Scan lacks the capabilities required by this rule")
        active = self._store.get_active_case(scan["scanId"], obj["objectId"], rule)
        if active:
            return self._finish_operation_failure(request, scan, operation, "CASE_ALREADY_ACTIVE", "An active Case already exists for this object and rule")
        case = {"caseId": self._new_id("case"), "scanId": scan["scanId"], "objectRef": obj["objectId"],
                "rule": rule, "kind": data["kind"], "purpose": data["purpose"], "beginRevision": scan["runRevision"] + 1,
                "plannedAt": self._now(), "plannedCoverageDimensions": data["plannedCoverageDimensions"],
                "syntheticInputs": data.get("syntheticInputs", []), "status": "planned", "operationRefs": [operation.get("operation_id") or operation["operationId"]],
                "actions": [], "beforePageStateRef": scan["currentPageStateId"],
                "recovery": {"policy": "targeted_then_refresh", "baselinePageStateRef": scan["currentPageStateId"], "finalStatus": "not_started", "attempts": []}}
        try:
            self._validate_entity(self._case_validator, case, "ReverseCase")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        capture_baseline = getattr(self._recovery_adapter, "capture_baseline", None)
        if callable(capture_baseline):
            try:
                capture_baseline(case, obj, self._store.get_page_state(scan["currentPageStateId"]))
            except Exception:
                return self._finish_operation_failure(request, scan, operation, "RECOVERY_BASELINE_UNAVAILABLE", "Case recovery baseline could not be established")
        scan["runRevision"] += 1
        obj["status"] = "investigating"
        operation["caseRef"] = case["caseId"]
        result = {"caseId": case["caseId"], "baselinePageStateRef": case["beforePageStateRef"], "status": "planned", "runRevision": scan["runRevision"]}
        operation.update({"status": "succeeded", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        inserted = False
        with self._store.transaction():
            inserted = self._store.insert_case(case)
            if inserted:
                self._store.update_audit_object(obj)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        if not inserted:
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            operation.pop("caseRef", None)
            operation.pop("resultJson", None)
            return self._finish_operation_failure(request, scan, operation, "CASE_ALREADY_ACTIVE", "An active Case already exists for this object and rule")
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _perform_action(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        case = self._store.get_case(data["caseId"])
        obj = self._store.get_audit_object(data["objectId"])
        if not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != data["objectId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case does not exist in the current Scan or is not bound to the target object")
        if case.get("status") not in {"planned", "executing", "safety_check", "evidence_captured", "decision_prepared"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "The current Case state does not allow actions")
        if not obj or obj["scanId"] != scan["scanId"] or obj.get("status") not in {"eligible", "investigating"} or obj.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Target object is not verified in the current Scan")
        if data["pageStateId"] != scan.get("currentPageStateId") or obj["pageStateRef"] != data["pageStateId"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Action page is not the current active PageState")
        try:
            self._validate_action_parameters(data, obj)
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        decision = self._action_policy.action_decision(data["type"], data["intent"], data["parameters"])
        action_id = self._new_id("action")
        action = {"actionId": action_id, "scanId": scan["scanId"], "caseId": case["caseId"], "operationId": operation.get("operation_id") or operation["operationId"],
                  "type": data["type"], "targetObjectRef": obj["objectId"], "intent": data["intent"], "parameters": self._sanitized_parameters(data["parameters"]),
                  "atRunRevision": scan["runRevision"], "safetyOutcome": "blocked" if decision.outcome == "blocked" else "not_attempted"}
        if decision.outcome == "blocked":
            action["blockReason"] = {"code": decision.code, "message": decision.reason}
            try:
                self._validate_entity(self._action_attempt_validator, action, "ActionAttempt")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            operation.update({"status": "failed_known", "errorCode": decision.code, "errorMessage": decision.reason, "resultJson": json.dumps({"runRevision": scan["runRevision"], "beforePageStateRef": data["pageStateId"], "afterPageStateRef": data["pageStateId"], "resultStatus": "rejected", "actionId": action_id, "safetyOutcome": "blocked"}, ensure_ascii=False, separators=(",", ":"))})
            case["actions"].append(self._case_action(action, data["pageStateId"]))
            case["operationRefs"].append(action["operationId"])
            self._validate_entity(self._case_validator, case, "ReverseCase")
            with self._store.transaction():
                self._store.update_case(case)
                self._store.insert_action_attempt(action); self._store.update_operation(operation)
            return self._response(request, scan, "rejected", error=HostError(decision.code, decision.reason), operation=operation)
        page = self._store.get_page_state(data["pageStateId"])
        if not page:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PageState does not exist")
        try:
            action_for_adapter = dict(data)
            action_for_adapter["operationId"] = operation.get("operation_id") or operation["operationId"]
            execution = self._action_adapter.execute(action_for_adapter, obj, page, lambda req: self._action_policy.classify_request(req, page.get("origin", "")))
        except Exception:
            execution = ActionExecution(status="result_unknown", diagnostic="Action adapter raised an exception")
        if execution.status not in {"succeeded", "request_blocked", "result_unknown", "persistent_write_observed", "unavailable"}:
            execution = ActionExecution(status="result_unknown", requests=execution.requests, diagnostic="Action adapter returned an unknown status")
        requests = []
        request_decisions = []
        for idx, req in enumerate(execution.requests):
            request_decision = self._action_policy.classify_request(req, page.get("origin", ""))
            request_decisions.append(request_decision)
            parsed = urlparse(req.url)
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else parsed.path
            requests.append({"observationId": self._stable_id("request", operation.get("operation_id") or operation["operationId"], str(idx)), "scanId": scan["scanId"], "operationId": operation.get("operation_id") or operation["operationId"],
                              "method": req.method.upper(), "url": safe_url, "transport": req.transport, "sent": req.sent,
                              "outcome": request_decision.outcome, "code": request_decision.code, "reason": request_decision.reason})
        if execution.status == "succeeded":
            if any(item.outcome == "already_sent" for item in request_decisions):
                execution = ActionExecution(status="persistent_write_observed", requests=execution.requests, diagnostic="Request was sent before the interceptor took control", interaction=execution.interaction)
            elif any(item.outcome == "unknown" for item in request_decisions):
                execution = ActionExecution(status="result_unknown", requests=execution.requests, diagnostic="Request cannot be proven unsent", interaction=execution.interaction)
            elif any(item.outcome == "blocked" for item in request_decisions):
                execution = ActionExecution(status="request_blocked", requests=execution.requests, diagnostic="Request was blocked by Host before sending", local_state_changed=True, interaction=execution.interaction)
        action["requestObservationRefs"] = [x["observationId"] for x in requests]
        if execution.status == "unavailable":
            return self._finish_operation_failure(request, scan, operation, "ACTION_ADAPTER_UNAVAILABLE", execution.diagnostic or "Browser action adapter is not configured")
        if execution.status == "persistent_write_observed":
            scan["runRevision"] += 1; scan["status"] = "failed"
        elif execution.status == "succeeded":
            scan["runRevision"] += 1
        elif execution.status == "result_unknown":
            scan["runRevision"] += 1
        elif execution.status == "request_blocked" and execution.local_state_changed:
            scan["runRevision"] += 1
        action["atRunRevision"] = scan["runRevision"]
        interaction_evidence = None
        if execution.interaction is not None:
            try:
                interaction_evidence = self._materialize_interaction_evidence(
                    scan, obj, case, operation, execution.interaction,
                    [item["observationId"] for item in requests],
                )
                action["resultEvidenceRefs"] = [interaction_evidence["evidenceId"]]
            except (HostError, ValueError):
                execution = ActionExecution(status="result_unknown", requests=execution.requests,
                                            diagnostic="Interaction Evidence could not be persisted safely", local_state_changed=True)
        result_status = "succeeded" if execution.status == "succeeded" else ("result_unknown" if execution.status in {"result_unknown", "persistent_write_observed"} else "rejected")
        after = data["pageStateId"]
        action["safetyOutcome"] = "allowed" if execution.status == "succeeded" else "blocked"
        if execution.status != "succeeded":
            action["blockReason"] = {"code": "REQUEST_RESULT_UNKNOWN" if result_status == "result_unknown" else "REQUEST_BLOCKED", "message": execution.diagnostic or "Action did not complete"}
        try:
            self._validate_entity(self._action_attempt_validator, action, "ActionAttempt")
            for item in requests:
                self._validate_entity(self._request_observation_validator, item, "RequestObservation")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        operation_status = "succeeded" if result_status == "succeeded" else ("failed_known" if execution.status == "persistent_write_observed" else ("result_unknown" if result_status == "result_unknown" else "failed_known"))
        operation.update({"status": operation_status, "errorCode": None if result_status == "succeeded" else action["blockReason"]["code"], "errorMessage": None if result_status == "succeeded" else action["blockReason"]["message"]})
        if execution.status == "persistent_write_observed":
            operation.update({"errorCode": "PERSISTENT_WRITE_OBSERVED", "errorMessage": "A potential persistent-write request was observed leaving the browser"})
        result = {"runRevision": scan["runRevision"], "beforePageStateRef": data["pageStateId"], "afterPageStateRef": after, "resultStatus": result_status, "actionId": action_id, "safetyOutcome": action["safetyOutcome"], "requestObservationRefs": [x["observationId"] for x in requests]}
        if interaction_evidence is not None:
            result["interactionEvidenceRef"] = interaction_evidence["evidenceId"]
        operation["resultJson"] = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        case["operationRefs"].append(action["operationId"])
        case["actions"].append(self._case_action(action, data["pageStateId"]))
        if interaction_evidence is not None:
            case["evidenceRefs"] = list(dict.fromkeys(case.get("evidenceRefs", []) + [interaction_evidence["evidenceId"]]))
        case["startedAt"] = case.get("startedAt") or self._now()
        case["afterPageStateRef"] = after
        if execution.status == "succeeded":
            case["status"] = "executing"
        elif execution.status in {"request_blocked", "result_unknown"}:
            case["status"] = "restoring"
        elif execution.status == "persistent_write_observed":
            case.update({"status": "invalidated", "endedAt": self._now(),
                         "restoreReason": {"code": "PERSISTENT_WRITE_OBSERVED", "message": "A potential persistent-write request left the browser"}})
        self._validate_entity(self._case_validator, case, "ReverseCase")
        with self._store.transaction():
            self._store.update_case(case)
            for item in requests: self._store.insert_request_observation(item)
            if interaction_evidence is not None: self._store.insert_evidence(interaction_evidence)
            self._store.insert_action_attempt(action)
            if execution.status == "succeeded":
                self._store.update_scan(scan)
            elif execution.status == "result_unknown":
                self._store.update_scan(scan)
            elif execution.status == "persistent_write_observed":
                self._store.update_scan(scan)
            elif execution.status == "request_blocked" and execution.local_state_changed:
                self._store.update_scan(scan)
            self._store.update_operation(operation)
        if execution.status == "persistent_write_observed":
            return self._response(request, scan, "failed", error=HostError("PERSISTENT_WRITE_OBSERVED", "A potential persistent-write request was observed leaving the browser"), operation=operation)
        if result_status == "result_unknown":
            return self._response(request, scan, "rejected", error=HostError("REQUEST_RESULT_UNKNOWN", operation["errorMessage"]), operation=operation)
        if result_status == "rejected":
            return self._response(request, scan, "rejected", error=HostError(operation["errorCode"], operation["errorMessage"]), operation=operation)
        return self._response(request, scan, "ok", result=result, operation=operation)

    def _restore_case(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        case = self._store.get_case(data["caseId"])
        target = self._store.get_audit_object(data["objectId"])
        if not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != data["objectId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case does not exist in the current Scan or is not bound to the target object")
        if case.get("status") in {"completed", "restore_failed", "invalidated"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Case is already closed and cannot be recovered again")
        if data["pageStateId"] != scan.get("currentPageStateId"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Recovery input is not the current active PageState")
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Recovery target object does not exist")
        case["status"] = "restoring"
        case["startedAt"] = case.get("startedAt") or self._now()
        page = self._store.get_page_state(data["pageStateId"])
        attempts = []
        try:
            adapter_case = dict(case)
            adapter_case["_hostOperationId"] = operation.get("operation_id") or operation["operationId"]
            first = self._recovery_adapter.restore(adapter_case, target, page, "targeted_inverse")
            self._validate_recovery_attempt(first, "targeted_inverse")
        except Exception:
            first = self._unknown_recovery_attempt("targeted_inverse", "Recovery adapter failed")
        attempts.append(self._recovery_attempt_dict(first))
        final = first
        if first.outcome != "restored" or not self._all_checks_match(first):
            try:
                adapter_case = dict(case)
                adapter_case["_hostOperationId"] = operation.get("operation_id") or operation["operationId"]
                second = self._recovery_adapter.restore(adapter_case, target, page, "refresh_replay")
                self._validate_recovery_attempt(second, "refresh_replay")
            except Exception:
                second = self._unknown_recovery_attempt("refresh_replay", "Refresh-replay adapter failed")
            attempts.append(self._recovery_attempt_dict(second))
            final = second
        clean = final.outcome == "restored" and self._all_checks_match(final)
        if clean:
            final_status, case_status = "restored", "completed"
            target["status"] = "eligible"
        else:
            final_status, case_status = ("uncertain" if final.outcome == "uncertain" else "failed"), "restore_failed"
            case["restoreReason"] = {"code": "CASE_NOT_RESTORED", "message": final.reason or "Not all recovery checks passed"}
            target.update({"status": "blocked", "blockedReason": {"code": "CASE_NOT_RESTORED", "message": "Case did not cross the recovery barrier"}})
            if self._has_pollution_uncertainty(final):
                scan["status"] = "failed"
            elif scan["status"] not in {"failed", "completed"}:
                scan["status"] = "partial"
        scan["runRevision"] += 1
        case.update({"status": case_status, "endedAt": self._now(), "afterPageStateRef": case.get("beforePageStateRef"),
                     "recovery": {"policy": "targeted_then_refresh", "baselinePageStateRef": case.get("beforePageStateRef"), "finalStatus": final_status, "attempts": attempts}})
        if clean:
            case.pop("restoreReason", None)
        try:
            self._validate_entity(self._case_validator, case, "ReverseCase")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        result = {"caseId": case["caseId"], "finalStatus": final_status, "runRevision": scan["runRevision"]}
        operation.update({"caseRef": case["caseId"], "status": "succeeded" if clean else "failed_known", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        if not clean:
            operation.update({"errorCode": "CASE_NOT_RESTORED", "errorMessage": "Case did not cross the recovery barrier"})
        with self._store.transaction():
            self._store.update_case(case)
            self._store.update_audit_object(target)
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        if clean:
            return self._response(request, scan, "ok", result=result, operation=operation)
        return self._response(request, scan, "failed" if scan["status"] == "failed" else "rejected", result=result,
                              error=HostError("CASE_NOT_RESTORED", "Case did not cross the recovery barrier"), operation=operation)

    def _observe_page(self, request: dict, scan: dict, operation: dict) -> dict:
        """Capture the model-facing browser observation without making a decision.

        The observation contains a structured page context and a viewport
        screenshot.  It is deliberately separate from ``capture_evidence``:
        this is a transient model input, while ordinary evidence remains the
        audit ledger's minimal structured fact.
        """
        data = request["input"]
        page = self._store.get_page_state(data["pageStateId"])
        target = self._store.get_audit_object(data["objectId"])
        case = self._store.get_case(data["caseId"]) if data.get("caseId") else None
        if not target or target.get("scanId") != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Page-observation target does not exist in the current Scan")
        if data["pageStateId"] != scan.get("currentPageStateId") or not page or page.get("scanId") != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Page-observation page is not the current active PageState")
        if target.get("pageStateRef") != data["pageStateId"] or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Page-observation target is not a uniquely verified object on the current page")
        if target.get("status") not in {"eligible", "investigating"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Object state does not allow page observation")
        if case and (case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"]):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case does not exist or is not bound to the observation target")
        try:
            observer = getattr(self._evidence_adapter, "observe_page", None)
            if not callable(observer):
                raise HostError("CAPABILITY_MISSING", "Current Evidence adapter does not support page observation")
            captured = observer(page, target, case)
            payload = self._evidence_sanitizer.sanitize(captured.payload)
            if captured.kind != "runtime_visual" or captured.raw_visual is None:
                raise ValueError("Page observation must produce both structured context and a Raw Visual")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        except ValueError as error:
            return self._finish_operation_failure(request, scan, operation, "SANITIZATION_FAILED", str(error))
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Page-observation adapter failed")
        revision = scan["runRevision"] + 1
        captured_at = self._now()
        operation_id = operation.get("operation_id") or operation["operationId"]
        evidence_id = self._stable_id("evidence", operation_id)
        screenshot, screenshot_path = self._materialize_raw_visual(scan, page, target, case, captured, operation_id, captured_at, revision)
        evidence = {"evidenceId": evidence_id, "scanId": scan["scanId"], "pageStateRef": page["pageStateId"], "objectRef": target["objectId"],
                    "kind": captured.kind, "capturedAt": captured_at, "capturedAtRevision": revision,
                    "collectorVersion": captured.collector_version, "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
                    "normalizationAlgorithmVersion": "1.0.0", "sanitized": True,
                    "payload": {"payloadType": captured.payload_type, "content": payload}}
        if case:
            evidence["caseRef"] = case["caseId"]
        if captured.source_binding is not None:
            evidence["sourceBinding"] = self._evidence_sanitizer.sanitize(captured.source_binding)
        evidence["screenshotRefs"] = [screenshot["screenshotId"]]
        digest_material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
        evidence["integrityDigest"] = self._digest(digest_material)
        try:
            self._validate_entity(self._evidence_validator, evidence, "Evidence")
            self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        scan["runRevision"] = revision
        if case:
            case["status"] = "evidence_captured"
            case.setdefault("evidenceRefs", []).append(evidence_id)
            case["operationRefs"].append(operation_id)
            self._validate_entity(self._case_validator, case, "ReverseCase")
        result = {"operationId": operation_id, "runRevision": revision, "evidenceId": evidence_id,
                  "screenshotRef": screenshot["screenshotId"],
                  "observation": {"kind": captured.kind, "payload": {"payloadType": captured.payload_type, "content": payload},
                                  "visual": {"status": screenshot["status"], "sanitizationStatus": screenshot["sanitizationStatus"]}}}
        if evidence.get("sourceBinding"):
            result["observation"]["sourceBinding"] = evidence["sourceBinding"]
        operation.update({"caseRef": case["caseId"] if case else operation.get("caseRef"), "status": "succeeded",
                          "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        created_file = False
        try:
            if screenshot.get("status") == "captured":
                created_file = self._write_screenshot(scan["outputDir"], screenshot["path"], captured.raw_visual.image_bytes, screenshot["digest"])
            with self._store.transaction():
                self._store.insert_evidence(evidence, screenshot)
                if case:
                    self._store.update_case(case)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            if created_file and screenshot_path and screenshot_path.exists():
                screenshot_path.unlink()
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Page-observation Evidence persistence failed")
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=[evidence_id])

    def _capture_evidence(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        page = self._store.get_page_state(data["pageStateId"])
        target = self._store.get_audit_object(data["objectId"])
        case = self._store.get_case(data["caseId"]) if data.get("caseId") else None
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence target does not exist in the current Scan")
        if data["pageStateId"] != scan.get("currentPageStateId") or not page or page["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Evidence page is not the current active PageState")
        if target["pageStateRef"] != data["pageStateId"] or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence target is not a uniquely verified object on the current page")
        if target.get("status") not in {"eligible", "investigating"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Object state does not allow formal Evidence capture")
        if data.get("caseId") and (not case or case["scanId"] != scan["scanId"] or case.get("objectRef") != target["objectId"]):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case does not exist or is not bound to the Evidence target")
        if case and case.get("status") not in {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "The current Case state does not allow Evidence capture")
        try:
            captured = self._evidence_adapter.capture(page, target, case, data.get("includeRawVisual", False))
            payload = self._evidence_sanitizer.sanitize(captured.payload)
            if data.get("includeRawVisual", False) and captured.kind != "runtime_visual":
                raise ValueError("Raw Visual must produce runtime_visual Evidence")
            if not data.get("includeRawVisual", False) and captured.kind == "runtime_visual":
                raise ValueError("runtime_visual Evidence must explicitly request a Raw Visual")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        except ValueError as error:
            return self._finish_operation_failure(request, scan, operation, "SANITIZATION_FAILED", str(error))
        except Exception:
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Evidence adapter failed")
        revision = scan["runRevision"] + 1
        captured_at = self._now()
        operation_id = operation.get("operation_id") or operation["operationId"]
        evidence_id = self._stable_id("evidence", operation_id)
        screenshot = None
        screenshot_path = None
        if data.get("includeRawVisual", False):
            screenshot, screenshot_path = self._materialize_raw_visual(scan, page, target, case, captured, operation_id, captured_at, revision)
        evidence = {"evidenceId": evidence_id, "scanId": scan["scanId"], "pageStateRef": page["pageStateId"], "objectRef": target["objectId"],
                    "kind": captured.kind, "capturedAt": captured_at, "capturedAtRevision": revision,
                    "collectorVersion": captured.collector_version, "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
                    "normalizationAlgorithmVersion": "1.0.0", "sanitized": True,
                    "payload": {"payloadType": captured.payload_type, "content": payload}}
        if case:
            evidence["caseRef"] = case["caseId"]
        if captured.source_binding is not None:
            evidence["sourceBinding"] = self._evidence_sanitizer.sanitize(captured.source_binding)
        if screenshot:
            evidence["screenshotRefs"] = [screenshot["screenshotId"]]
        digest_material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
        evidence["integrityDigest"] = self._digest(digest_material)
        try:
            self._validate_entity(self._evidence_validator, evidence, "Evidence")
            if screenshot:
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        scan["runRevision"] = revision
        if case:
            case["status"] = "evidence_captured"
            case.setdefault("evidenceRefs", []).append(evidence_id)
            case["operationRefs"].append(operation_id)
            self._validate_entity(self._case_validator, case, "ReverseCase")
        result = {"evidenceId": evidence_id, "runRevision": revision,
                  "evidence": {"kind": evidence["kind"], "payload": evidence["payload"]}}
        if evidence.get("sourceBinding"):
            result["evidence"]["sourceBinding"] = evidence["sourceBinding"]
        if screenshot:
            result["screenshotRef"] = screenshot["screenshotId"]
            result["evidence"]["visual"] = {
                "status": screenshot["status"],
                "sanitizationStatus": screenshot["sanitizationStatus"],
            }
        operation.update({"caseRef": case["caseId"] if case else operation.get("caseRef"), "status": "succeeded",
                          "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        created_file = False
        try:
            if screenshot and screenshot["status"] == "captured":
                created_file = self._write_screenshot(scan["outputDir"], screenshot["path"], captured.raw_visual.image_bytes, screenshot["digest"])
            with self._store.transaction():
                self._store.insert_evidence(evidence, screenshot)
                if case:
                    self._store.update_case(case)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            if created_file and screenshot_path and screenshot_path.exists():
                screenshot_path.unlink()
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Evidence or screenshot persistence failed")
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=[evidence_id])

    def _materialize_raw_visual(self, scan, page, target, case, captured, operation_id, captured_at, revision):
        raw = captured.raw_visual
        screenshot_id = self._stable_id("screenshot", operation_id)
        common = {"screenshotId": screenshot_id, "scanId": scan["scanId"], "pageStateRef": page["pageStateId"], "objectRef": target["objectId"],
                  "kind": "raw_visual", "capturedAt": captured_at, "capturedAtRevision": revision,
                  "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
                  "sanitizationStatus": getattr(raw, "sanitization_status", "sanitized") if raw else "failed"}
        if case:
            common["caseRef"] = case["caseId"]
        target_box = target.get("location", {}).get("boundingBox")
        source_box = (raw.source_bounding_box or raw.bounding_box) if raw else None
        bbox_valid = bool(raw and raw.bounding_box and source_box and target_box and all(source_box.get(key) == target_box.get(key) for key in ("x", "y", "width", "height"))
                          and raw.bounding_box["x"] + raw.bounding_box["width"] <= raw.width
                          and raw.bounding_box["y"] + raw.bounding_box["height"] <= raw.height)
        header_valid = bool(raw and ((raw.image_type == "png" and raw.image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                                     or (raw.image_type == "jpeg" and raw.image_bytes.startswith(b"\xff\xd8\xff"))
                                     or (raw.image_type == "webp" and raw.image_bytes.startswith(b"RIFF") and raw.image_bytes[8:12] == b"WEBP")))
        sanitization_status = getattr(raw, "sanitization_status", "sanitized") if raw else "failed"
        status_consistent = sanitization_status in {"sanitized", "not_performed"} and raw is not None and raw.sanitized == (sanitization_status == "sanitized")
        if raw and raw.status == "captured" and status_consistent and raw.image_bytes and raw.image_type in {"png", "jpeg", "webp"} and raw.width > 0 and raw.height > 0 and bbox_valid and header_valid and raw.annotation:
            extension = "jpg" if raw.image_type == "jpeg" else raw.image_type
            relative = f"screenshots/{screenshot_id}.{extension}"
            common.update({"status": "captured", "path": relative, "digest": hashlib.sha256(raw.image_bytes).hexdigest(), "imageType": raw.image_type,
                           "width": raw.width, "height": raw.height, "problemBoundingBox": raw.bounding_box,
                           "sourceBoundingBox": source_box,
                           "annotation": self._evidence_sanitizer.sanitize(raw.annotation)})
            return common, Path(scan["outputDir"]) / relative
        status = raw.status if raw and raw.status in {"not_located", "ambiguous", "rejected"} else "rejected"
        reason = raw.reason if raw and raw.reason else ("Screenshot sanitization status is invalid" if raw and not status_consistent else "Screenshot format, dimensions, or object-location validation failed")
        common.update({"status": status, "failureReason": {"code": "SANITIZATION_FAILED" if raw and not status_consistent else "SCREENSHOT_NOT_CAPTURED", "message": reason}})
        return common, None

    def _record_findings(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        target = self._store.get_audit_object(data["objectId"])
        rule_ref = data["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not target or target.get("scanId") != scan["scanId"] or not rule or rule_ref not in target.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding object or rule does not belong to the current Scan")
        operation_id = operation.get("operation_id") or operation["operationId"]
        revision = scan["runRevision"] + 1
        findings = []
        dimensions = [item["dimension"] for item in data["findings"]]
        if len(dimensions) != len(set(dimensions)):
            return self._finish_operation_failure(request, scan, operation, "INVALID_REQUEST", "A request cannot write the same dimension more than once")
        latest_ids = {item["findingId"] for item in self._store.get_latest_findings(scan["scanId"], target["objectId"], rule_ref)}
        evidence_refs = set()
        for index, item in enumerate(data["findings"]):
            if item["dimension"] not in rule.get("coverageDimensions", []):
                return self._finish_operation_failure(request, scan, operation, "INVALID_REQUEST", "Finding dimension does not belong to the frozen rule coverage contract")
            for case_ref in item["caseRefs"]:
                case = self._store.get_case(case_ref)
                if not case or case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"] or case.get("rule") != rule_ref:
                    return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding Case reference is not bound to the current object and rule")
            for evidence_ref in item["evidenceRefs"]:
                evidence = self._store.get_evidence(evidence_ref)
                if not evidence or evidence.get("scanId") != scan["scanId"] or evidence.get("objectRef") != target["objectId"]:
                    return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding Evidence reference is not bound to the current object")
                if evidence.get("caseRef") and evidence["caseRef"] not in item["caseRefs"]:
                    return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding Evidence/Case reference closure is incomplete")
                evidence_refs.add(evidence_ref)
            if item.get("supersedesRef"):
                old = self._store.get_dimension_finding(item["supersedesRef"])
                if not old or old.get("scanId") != scan["scanId"] or old.get("objectRef") != target["objectId"] or old.get("rule") != rule_ref or old.get("dimension") != item["dimension"] or old["findingId"] not in latest_ids:
                    return self._finish_operation_failure(request, scan, operation, "INVALID_SUPERSEDES_REFERENCE", "Only the latest Finding for the same object, rule, and dimension may be superseded")
            finding = {"findingId":self._stable_id("finding", operation_id, str(index)), "scanId":scan["scanId"], "recordOperationRef":operation_id,
                       "objectRef":target["objectId"], "rule":rule_ref, "dimension":item["dimension"], "status":item["status"],
                       "reasonText":self._evidence_sanitizer.sanitize(item["reasonText"]), "evidenceRefs":list(item["evidenceRefs"]),
                       "caseRefs":list(item["caseRefs"]), "createdAt":self._now(), "createdAtRevision":revision}
            if item.get("supersedesRef"):
                finding["supersedesRef"] = item["supersedesRef"]
            try:
                self._validate_entity(self._finding_validator, finding, "DimensionFinding")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            findings.append(finding)
        result = {"findingRefs":[item["findingId"] for item in findings], "runRevision":revision}
        operation.update({"status":"succeeded", "resultJson":json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        scan["runRevision"] = revision
        with self._store.transaction():
            for finding in findings:
                self._store.insert_dimension_finding(finding)
            self._store.update_scan(scan)
            self._store.update_operation(operation)
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=sorted(evidence_refs))

    def _finding_coverage(self, scan_id: str, object_id: str, rule_ref: dict, finding_refs: list[str] | None = None) -> tuple[dict, list[dict]]:
        rule = self._rules[(rule_ref["ruleId"], rule_ref["version"])]
        latest = self._store.get_latest_findings(scan_id, object_id, rule_ref)
        if finding_refs is not None:
            selected = set(finding_refs)
            latest = [item for item in latest if item["findingId"] in selected]
        effective = []
        for finding in latest:
            valid = True
            for case_ref in finding["caseRefs"]:
                case = self._store.get_case(case_ref)
                if not case or case.get("status") != "completed" or case.get("recovery", {}).get("finalStatus") != "restored":
                    valid = False
                    break
            if valid:
                effective.append(finding)
        by_dimension = {item["dimension"]: item for item in effective}
        required = list(rule.get("coverageDimensions", []))
        attempted = [dimension for dimension in required if dimension in by_dimension]
        resolved = [dimension for dimension in required if by_dimension.get(dimension, {}).get("status") in {"satisfied", "violated"}]
        unresolved = [dimension for dimension in required if dimension in by_dimension and dimension not in resolved]
        coverage = {"requiredDimensions":required, "attemptedDimensions":attempted, "resolvedDimensions":resolved,
                    "unresolvedDimensions":unresolved, "complete":set(required).issubset(resolved)}
        return coverage, effective

    @staticmethod
    def _check_decision_gate(result: str, coverage: dict, findings: list[dict], rule: dict) -> tuple[str, str] | None:
        gate = rule.get("decisionGates", {}).get(result)
        if not gate:
            return "RULE_CONTRACT_UNAVAILABLE", "Frozen rule lacks a machine decision gate for the current result"
        by_dimension = {item["dimension"]: item["status"] for item in findings}
        allowed = set(gate.get("allRequiredStatuses", []))
        if allowed and any(by_dimension.get(dimension) not in allowed for dimension in coverage["requiredDimensions"]):
            return "COVERAGE_INCOMPLETE", "Finding statuses do not satisfy the rule's all-dimensions machine gate"
        minimum = gate.get("anyStatusMinimum")
        if minimum and sum(item["status"] in set(minimum["statuses"]) for item in findings) < minimum["minimum"]:
            return "EVIDENCE_INSUFFICIENT", "Finding statuses do not satisfy the rule's minimum status-count gate"
        if len(findings) < gate.get("minimumFindings", 0):
            return "EVIDENCE_INSUFFICIENT", "Finding count does not satisfy the rule's minimum gate"
        return None

    def _prepare_decision(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        target = self._store.get_audit_object(data["objectId"])
        if not target or target["scanId"] != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Decision object does not exist in the current Scan")
        if target.get("pageStateRef") != scan.get("currentPageStateId") or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Decision object is not a uniquely verified object on the current page")
        if target.get("status") not in {"eligible", "investigating"}:
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Object state does not allow decision preparation")
        rule_ref = data["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not rule or rule_ref not in target.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Rule does not belong to the object's frozen rule set")

        evidence = []
        for evidence_ref in data["evidenceRefs"]:
            item = self._store.get_evidence(evidence_ref)
            if not item or item.get("scanId") != scan["scanId"] or item.get("objectRef") != target["objectId"] or item.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence does not exist or is not bound to the current Scan, page, and object")
            if item.get("caseRef") and item["caseRef"] not in data["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence Case is not included in the current decision")
            evidence.append(item)

        cases = []
        for case_ref in data["caseRefs"]:
            case = self._store.get_case(case_ref)
            if not case or case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"] or case.get("rule") != rule_ref:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case does not exist or is not bound to the current object and rule")
            if case.get("status") != "completed" or case.get("recovery", {}).get("finalStatus") != "restored":
                return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "Referenced Case has not completed the recovery barrier")
            cases.append(case)

        latest_all = self._store.get_latest_findings(scan["scanId"], target["objectId"], rule_ref)
        latest_by_id = {item["findingId"]: item for item in latest_all}
        findings = []
        for finding_ref in data["findingRefs"]:
            finding = self._store.get_dimension_finding(finding_ref)
            if not finding or finding.get("scanId") != scan["scanId"] or finding.get("objectRef") != target["objectId"] or finding.get("rule") != rule_ref or finding_ref not in latest_by_id:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Decision may reference only the latest Finding for the current object, rule, and dimension")
            if not set(finding["evidenceRefs"]).issubset(data["evidenceRefs"]) or not set(finding["caseRefs"]).issubset(data["caseRefs"]):
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding Evidence/Case is not included in the decision reference closure")
            if any(self._store.get_case(case_ref).get("status") != "completed" or self._store.get_case(case_ref).get("recovery", {}).get("finalStatus") != "restored" for case_ref in finding["caseRefs"]):
                return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "Finding is still staged or its Case is invalid")
            findings.append(finding)
        coverage, effective_findings = self._finding_coverage(scan["scanId"], target["objectId"], rule_ref, data["findingRefs"])
        if {item["findingId"] for item in effective_findings} != set(data["findingRefs"]):
            return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "Not all Findings crossed the recovery barrier")
        gate_error = self._check_decision_gate(data["result"], coverage, findings, rule)
        if gate_error:
            return self._finish_operation_failure(request, scan, operation, *gate_error)
        # A bare DOM count, unclassified list reference, or ordinary screenshot
        # cannot prove that control/list ownership was actually observed.  Make
        # the aligned viewport observation a Host gate rather than a soft Agent
        # instruction.
        if (
            data["result"] == "needs_review"
            and data.get("blocker", {}).get("code") == "BINDING_UNRESOLVED"
            and not any(is_page_observation(item) for item in evidence)
        ):
            return self._finish_operation_failure(
                request, scan, operation, "EVIDENCE_INSUFFICIENT",
                "List-binding review must first reference viewport and logical-list Evidence from observe_page",
            )
        if data["result"] == "issue_found" and not evidence:
            return self._finish_operation_failure(request, scan, operation, "EVIDENCE_INSUFFICIENT", "An issue conclusion requires at least one valid Evidence item")
        if data.get("severity") and data["severity"] not in rule.get("allowedSeverities", []):
            return self._finish_operation_failure(request, scan, operation, "INVALID_REQUEST", "Issue severity is outside the range allowed by the rule")

        operation_id = operation.get("operation_id") or operation["operationId"]
        pending_id = self._stable_id("pending", operation_id)
        revision = scan["runRevision"] + 1
        screenshot = None
        screenshot_bytes = None
        screenshot_path = None
        if data["result"] == "issue_found":
            raw_ref = data.get("rawVisualRef")
            raw = self._store.get_screenshot(raw_ref) if raw_ref else None
            evidence_links_raw = any(raw_ref in item.get("screenshotRefs", []) for item in evidence)
            if not raw or raw.get("kind") != "raw_visual" or raw.get("status") != "captured" or not evidence_links_raw:
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "Issue conclusion must reference a captured Raw Visual in Evidence")
            if raw.get("scanId") != scan["scanId"] or raw.get("objectRef") != target["objectId"] or raw.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Raw Visual is not bound to the current Scan, page, and object")
            if raw.get("caseRef") and raw["caseRef"] not in data["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Raw Visual Case is not included in the current decision")
            if raw.get("sanitizationStatus") != "sanitized":
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_SANITIZATION_REQUIRED", "Formal issue_found requires a sanitized IssueScreenshot; automatic pixel sanitization was not performed on the current image")
            try:
                screenshot_bytes = self._read_screenshot(scan["outputDir"], raw)
                screenshot = self._materialize_issue_screenshot(scan, raw, operation_id, revision)
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
                screenshot_path = Path(scan["outputDir"]) / screenshot["path"]
            except (OSError, ValueError, HostError) as error:
                message = error.message if isinstance(error, HostError) else str(error)
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", message)

        applicable = data["result"] != "not_applicable"
        pending = {"pendingDecisionId": pending_id, "scanId": scan["scanId"], "preparationOperationRef": operation_id,
                   "objectRef": target["objectId"], "rule": rule_ref, "applicable": applicable, "result": data["result"],
                   "coverage": coverage, "findingRefs":list(data["findingRefs"]), "evidenceRefs": list(data["evidenceRefs"]), "caseRefs": list(data["caseRefs"]),
                   "reasonText": self._evidence_sanitizer.sanitize(data["reasonText"]), "status": "pending",
                   "preparedAt": self._now(), "preparedAtRevision": revision}
        if data["result"] == "needs_review":
            pending["blocker"] = self._evidence_sanitizer.sanitize(data["blocker"])
        if screenshot:
            pending.update({"rawVisualRef": data["rawVisualRef"], "screenshotRef": screenshot["screenshotId"]})
            for field in ("severity", "title", "message", "impact", "recommendation"):
                pending[field] = self._evidence_sanitizer.sanitize(data[field])
        try:
            self._validate_entity(self._pending_decision_validator, pending, "PendingDecision")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        result = {"pendingDecisionId": pending_id, "runRevision": revision}
        if screenshot:
            result["screenshotRef"] = screenshot["screenshotId"]
        operation.update({"status": "succeeded", "resultJson": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        scan["runRevision"] = revision
        created_file = False
        try:
            if screenshot:
                created_file = self._write_screenshot(scan["outputDir"], screenshot["path"], screenshot_bytes, screenshot["digest"])
            with self._store.transaction():
                if screenshot:
                    self._store.insert_screenshot(screenshot)
                self._store.insert_pending_decision(pending)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            if created_file and screenshot_path and screenshot_path.exists():
                screenshot_path.unlink()
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "PendingDecision or issue screenshot persistence failed")
        return self._response(request, scan, "ok", result=result, operation=operation, evidence_refs=data["evidenceRefs"])

    def _commit_decision(self, request: dict, scan: dict, operation: dict) -> dict:
        pending = self._store.get_pending_decision(request["input"]["pendingDecisionId"])
        if not pending or pending.get("scanId") != scan["scanId"]:
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PendingDecision does not exist in the current Scan")
        if pending.get("status") != "pending":
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "PendingDecision is committed or invalid")
        if scan.get("ruleRegistryDigest") != self._rule_registry_digest or pending.get("preparedAtRevision", scan["runRevision"]) > scan["runRevision"]:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Rule registry or PendingDecision revision is stale")
        try:
            self._validate_entity(self._pending_decision_validator, pending, "PendingDecision")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        preparation = self._store.get_operation(pending["preparationOperationRef"])
        if not preparation or preparation.get("scan_id") != scan["scanId"] or preparation.get("tool") != "prepare_decision" or preparation.get("status") != "succeeded":
            return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "PendingDecision preparation Operation reference is invalid")
        target = self._store.get_audit_object(pending["objectRef"])
        rule_ref = pending["rule"]
        rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
        if not target or target.get("scanId") != scan["scanId"] or target.get("pageStateRef") != scan.get("currentPageStateId") or target.get("rebindStatus") != "matched":
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Object identity or page state became stale before commit")
        if target.get("status") not in {"eligible", "investigating"} or not rule or rule_ref not in target.get("potentialRules", []):
            return self._finish_operation_failure(request, scan, operation, "INVALID_LIFECYCLE_TRANSITION", "Object or rule is no longer valid at commit time")
        try:
            self._validate_entity(self._audit_object_validator, target, "AuditObject")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        if self._store.get_assessment_for_object_rule(scan["scanId"], target["objectId"], rule_ref):
            return self._finish_operation_failure(request, scan, operation, "ALREADY_COMMITTED", "A formal decision already exists for this object and rule")
        for case_ref in pending["caseRefs"]:
            case = self._store.get_case(case_ref)
            if not case or case.get("scanId") != scan["scanId"] or case.get("objectRef") != target["objectId"] or case.get("rule") != rule_ref:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Case reference closure is incomplete at commit time")
            if case.get("status") != "completed" or case.get("recovery", {}).get("finalStatus") != "restored":
                pending["status"] = "invalidated"
                with self._store.transaction():
                    self._store.update_pending_decision(pending)
                return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "Case has not crossed the recovery barrier at commit time")
            try:
                self._validate_entity(self._case_validator, case, "ReverseCase")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        findings = []
        latest_ids = {item["findingId"] for item in self._store.get_latest_findings(scan["scanId"], target["objectId"], rule_ref)}
        for finding_ref in pending["findingRefs"]:
            finding = self._store.get_dimension_finding(finding_ref)
            if not finding or finding_ref not in latest_ids or finding.get("scanId") != scan["scanId"] or finding.get("objectRef") != target["objectId"] or finding.get("rule") != rule_ref:
                return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Finding was superseded or its reference became stale before commit")
            if not set(finding["evidenceRefs"]).issubset(pending["evidenceRefs"]) or not set(finding["caseRefs"]).issubset(pending["caseRefs"]):
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Finding reference closure is invalid at commit time")
            findings.append(finding)
        coverage, effective_findings = self._finding_coverage(scan["scanId"], target["objectId"], rule_ref, pending["findingRefs"])
        if {item["findingId"] for item in effective_findings} != set(pending["findingRefs"]):
            return self._finish_operation_failure(request, scan, operation, "CASE_NOT_RESTORED", "Finding recovery barrier is invalid at commit time")
        if coverage != pending.get("coverage"):
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Coverage proof changed before commit")
        gate_error = self._check_decision_gate(pending["result"], coverage, findings, rule)
        if gate_error:
            return self._finish_operation_failure(request, scan, operation, *gate_error)
        evidence_refs = list(pending["evidenceRefs"])
        for evidence_ref in evidence_refs:
            evidence = self._store.get_evidence(evidence_ref)
            if not evidence or evidence.get("scanId") != scan["scanId"] or evidence.get("objectRef") != target["objectId"] or evidence.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence reference is invalid at commit time")
            if evidence.get("caseRef") and evidence["caseRef"] not in pending["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Evidence Case reference closure is incomplete at commit time")
            digest_material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
            if evidence.get("integrityDigest") != self._digest(digest_material):
                return self._finish_operation_failure(request, scan, operation, "EVIDENCE_INTEGRITY_FAILED", "Evidence integrity check failed at commit time")
            try:
                self._validate_entity(self._evidence_validator, evidence, "Evidence")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        assessment_id = self._new_id("assessment")
        revision = scan["runRevision"] + 1
        assessment = {"assessmentId": assessment_id, "scanId": scan["scanId"], "preparationOperationRef": pending["preparationOperationRef"], "commitOperationRef": operation.get("operation_id") or operation["operationId"], "objectRef": target["objectId"], "rule": rule_ref, "applicable": pending["applicable"], "result": pending["result"], "coverage": coverage, "findingRefs":list(pending["findingRefs"]), "evidenceRefs": evidence_refs, "caseRefs": list(pending["caseRefs"]), "reasonText": pending["reasonText"], "conclusionValidity": "valid", "decidedAt": self._now(), "committedAtRevision": revision}
        if pending.get("screenshotRef"):
            screenshot = self._store.get_screenshot(pending["screenshotRef"])
            raw = self._store.get_screenshot(pending.get("rawVisualRef"))
            if not screenshot or screenshot.get("kind") != "issue" or screenshot.get("status") != "captured" or screenshot.get("scanId") != scan["scanId"] or screenshot.get("objectRef") != target["objectId"] or screenshot.get("pageStateRef") != target["pageStateRef"]:
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "Issue screenshot is invalid at commit time")
            if not raw or raw.get("kind") != "raw_visual" or raw.get("status") != "captured" or screenshot.get("rawVisualRef") != raw.get("screenshotId") or screenshot.get("digest") != raw.get("digest"):
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", "Issue screenshot to Raw Visual provenance chain is invalid")
            if screenshot.get("caseRef") and screenshot["caseRef"] not in pending["caseRefs"]:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Issue screenshot Case reference closure is incomplete")
            try:
                self._validate_entity(self._screenshot_validator, screenshot, "Screenshot")
                self._read_screenshot(scan["outputDir"], screenshot)
            except (OSError, ValueError, HostError) as error:
                message = error.message if isinstance(error, HostError) else str(error)
                return self._finish_operation_failure(request, scan, operation, "SCREENSHOT_NOT_CAPTURED", message)
            assessment["screenshotRef"] = pending["screenshotRef"]
        for field in ("severity", "title", "impact", "recommendation"):
            if field in pending:
                assessment[field] = pending[field]
        if pending.get("blocker"):
            assessment["blocker"] = pending["blocker"]
        try:
            self._validate_entity(self._assessment_validator, assessment, "RuleAssessment")
        except HostError as error:
            return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        issue = None
        if pending["result"] == "issue_found":
            issue = {"issueId": self._new_id("issue"), "scanId": scan["scanId"], "assessmentRef": assessment_id, "objectRef": target["objectId"], "rule": rule_ref, "result": "issue_found", "evidenceRefs": evidence_refs, "screenshotRef": pending["screenshotRef"], "severity": pending["severity"], "title": pending["title"], "message": pending["message"], "impact": pending["impact"], "recommendation": pending["recommendation"], "conclusionValidity": "valid", "createdAt": self._now(), "createdAtRevision": revision}
            try:
                self._validate_entity(self._issue_validator, issue, "Issue")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
        target.setdefault("assessmentRefs", []).append(assessment_id)
        target["status"] = "decided" if len(target["assessmentRefs"]) >= len(target.get("potentialRules", [])) else "eligible"
        self._validate_entity(self._audit_object_validator, target, "AuditObject")
        pending["status"] = "committed"
        operation_result = {"assessmentId": assessment_id, "runRevision": revision}
        if issue:
            operation_result["issueId"] = issue["issueId"]
        operation.update({"status": "succeeded", "resultJson": json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))})
        scan["runRevision"] = revision
        try:
            with self._store.transaction():
                self._store.insert_assessment(assessment)
                if issue:
                    self._store.insert_issue(issue)
                self._store.update_audit_object(target)
                self._store.update_pending_decision(pending)
                self._store.update_scan(scan)
                self._store.update_operation(operation)
        except Exception:
            operation.pop("resultJson", None)
            scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            return self._finish_operation_failure(request, scan, operation, "INTERNAL_FAILURE", "Atomic persistence of the formal decision failed")
        return self._response(request, scan, "ok", result=operation_result, operation=operation, evidence_refs=evidence_refs)

    def _complete_audit(self, request: dict, scan: dict, operation: dict) -> dict:
        data = request["input"]
        if (Path(scan["outputDir"]) / "audit-ledger.json").exists():
            return self._finish_operation_failure(request, scan, operation, "ALREADY_COMPLETED", "The current Scan already has an audit ledger")
        if scan.get("loginStatus") != "succeeded":
            return self._finish_operation_failure(request, scan, operation, "LOGIN_FAILED", "Audit cannot complete because login did not succeed")
        if scan.get("ruleRegistryDigest") != self._rule_registry_digest:
            return self._finish_operation_failure(request, scan, operation, "STALE_STATE", "Scan frozen-rule digest does not match the current Host registry")
        if any(item.get("status") == "pending" for item in self._store.list_entities("pending_decisions", scan["scanId"])):
            return self._finish_operation_failure(request, scan, operation, "DECISION_PENDING", "An uncommitted PendingDecision remains")
        operation_id = operation.get("operation_id") or operation["operationId"]
        if any(item["operation_id"] != operation_id and item["status"] == "running" for item in self._store.list_operations(scan["scanId"])):
            return self._finish_operation_failure(request, scan, operation, "OPERATION_IN_PROGRESS", "An unclosed Operation remains")
        cases = self._store.list_entities("reverse_cases", scan["scanId"])
        if any(item.get("status") in {"planned", "safety_check", "executing", "evidence_captured", "decision_prepared", "restoring"} for item in cases):
            return self._finish_operation_failure(request, scan, operation, "CASE_ACTIVE", "An unclosed Case remains")
        page_states = self._store.list_entities("page_states", scan["scanId"]); entrypoints = self._store.list_entities("entrypoints", scan["scanId"])
        objects = self._store.list_entities("audit_objects", scan["scanId"]); assessments = self._store.list_entities("assessments", scan["scanId"]); issues = self._store.list_entities("issues", scan["scanId"])
        findings = self._store.list_entities("dimension_findings", scan["scanId"]); evidence_items = self._store.list_entities("evidence", scan["scanId"])
        scan_id = scan["scanId"]
        if any(item.get("scanId") != scan_id for item in page_states + entrypoints + objects + cases + findings + evidence_items + assessments + issues):
            return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Ledger contains cross-Scan entities")
        page_ids = {item["pageStateId"] for item in page_states}; object_ids = {item["objectId"] for item in objects}; entry_ids = {item["entrypointId"] for item in entrypoints}
        case_ids = {item["caseId"] for item in cases}; evidence_ids = {item["evidenceId"] for item in evidence_items}; finding_ids = {item["findingId"] for item in findings}
        findings_by_id = {item["findingId"]: item for item in findings}
        for finding in findings:
            try:
                self._validate_entity(self._finding_validator, finding, "DimensionFinding")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            rule = self._rules.get((finding["rule"]["ruleId"], finding["rule"]["version"]))
            if finding["objectRef"] not in object_ids or not rule or finding["dimension"] not in rule.get("coverageDimensions", []) or not set(finding["caseRefs"]).issubset(case_ids) or not set(finding["evidenceRefs"]).issubset(evidence_ids):
                return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "DimensionFinding reference closure is incomplete")
            if finding.get("supersedesRef"):
                old = findings_by_id.get(finding["supersedesRef"])
                if not old or any(old[field] != finding[field] for field in ("objectRef", "rule", "dimension")):
                    return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "DimensionFinding supersession chain is incomplete")
        if len(data["visitedPageStateRefs"]) != len(set(data["visitedPageStateRefs"])) or not set(data["visitedPageStateRefs"]).issubset(page_ids):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "visitedPageStateRefs reference closure is incomplete")
        if len(data["processedObjectRefs"]) != len(set(data["processedObjectRefs"])) or not set(data["processedObjectRefs"]).issubset(object_ids):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "processedObjectRefs reference closure is incomplete")
        processed = set(data["processedEntrypointRefs"]); skipped = {item["entrypointId"] for item in data["skippedEntrypoints"]}; unprocessed = set(data["unprocessedEntrypointRefs"])
        if len(skipped) != len(data["skippedEntrypoints"]) or processed | skipped | unprocessed != entry_ids or not processed.isdisjoint(skipped | unprocessed) or not skipped.isdisjoint(unprocessed):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "Entrypoint processed/skipped/unprocessed partition is incomplete")
        if any(not item.get("reason", {}).get("message") for item in data["skippedEntrypoints"]):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "Skipped entrypoint is missing a reason")
        by_rule = {}
        for assessment in assessments:
            try:
                self._validate_entity(self._assessment_validator, assessment, "RuleAssessment")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            if not set(assessment["findingRefs"]).issubset(finding_ids) or any(findings_by_id[ref]["objectRef"] != assessment["objectRef"] or findings_by_id[ref]["rule"] != assessment["rule"] for ref in assessment["findingRefs"]):
                return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Assessment Finding reference closure is incomplete")
            by_rule.setdefault((assessment["rule"]["ruleId"], assessment["rule"]["version"]), []).append(assessment)
        assessment_ids = {item["assessmentId"] for item in assessments}
        issues_by_assessment = {}
        for issue in issues:
            try:
                self._validate_entity(self._issue_validator, issue, "Issue")
            except HostError as error:
                return self._finish_operation_failure(request, scan, operation, error.code, error.message)
            if issue["assessmentRef"] not in assessment_ids:
                return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Issue is not bound to an Assessment in the current Scan")
            issues_by_assessment.setdefault(issue["assessmentRef"], []).append(issue)
        if any((assessment["result"] == "issue_found" and len(issues_by_assessment.get(assessment["assessmentId"], [])) != 1) or (assessment["result"] != "issue_found" and assessment["assessmentId"] in issues_by_assessment) for assessment in assessments):
            return self._finish_operation_failure(request, scan, operation, "LEDGER_REFERENCE_INVALID", "Issue and issue_found Assessment are not one-to-one")
        summaries = []
        summary_keys = [(item["rule"]["ruleId"], item["rule"]["version"]) for item in data["ruleSummaries"]]
        if len(summary_keys) != len(set(summary_keys)) or set(summary_keys) != set(self._rules):
            return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "Rule summaries must cover exactly the frozen enabled rules")
        for summary in data["ruleSummaries"]:
            rule_ref = summary["rule"]; rule = self._rules.get((rule_ref["ruleId"], rule_ref["version"]))
            if not rule:
                return self._finish_operation_failure(request, scan, operation, "UNKNOWN_REFERENCE", "Rule summary references an unknown rule")
            found = by_rule.get((rule_ref["ruleId"], rule_ref["version"]), [])
            counts = {result: sum(1 for item in found if item["result"] == result) for result in ("issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise")}
            provided_counts = summary.get("resultCounts", {})
            if summary["assessmentCount"] != len(found) or any(provided_counts.get(key, 0) != value for key, value in counts.items()):
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "Rule summary count does not match formal Assessments")
            complete = bool(found) and all(item["coverage"].get("complete") for item in found)
            if summary["coverageComplete"] != complete:
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "Rule summary coverage status is inconsistent")
            summaries.append({"rule": rule_ref, "assessmentCount": len(found), "resultCounts": {k: v for k, v in counts.items() if v}, "coverageComplete": complete})
        incomplete_objects = object_ids - set(data["processedObjectRefs"])
        incomplete_pages = page_ids - set(data["visitedPageStateRefs"])
        for object_ref in data["processedObjectRefs"]:
            target = next(item for item in objects if item["objectId"] == object_ref)
            if target.get("status") != "decided" or len(target.get("assessmentRefs", [])) < len(target.get("potentialRules", [])):
                return self._finish_operation_failure(request, scan, operation, "COVERAGE_INVALID", "processedObjectRefs contains an object without all required rule decisions")
        incomplete_rules = any(not item["coverageComplete"] for item in summaries)
        status = "failed" if scan["status"] == "failed" else ("partial" if unprocessed or incomplete_objects or incomplete_pages or incomplete_rules else "completed")
        proof = {"visitedPageStateRefs": list(data["visitedPageStateRefs"]), "processedObjectRefs": list(data["processedObjectRefs"]), "processedEntrypointRefs": list(data["processedEntrypointRefs"]), "skippedEntrypoints": list(data["skippedEntrypoints"]), "ruleSummaries": summaries, "unprocessedEntrypointRefs": list(data["unprocessedEntrypointRefs"]), "completionReason": data["completionReason"]}
        terminal = {"code": "COVERAGE_COMPLETE" if status == "completed" else ("SCAN_FAILED" if status == "failed" else "COVERAGE_PARTIAL"), "message": data["completionReason"]}
        revision = scan["runRevision"] + 1
        scan.update({"status": status, "runRevision": revision})
        operation_result = {"scanStatus": status, "conclusionsValid": status != "failed", "runRevision": revision}
        operation.update({"status": "succeeded", "resultJson": json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))})
        with self._store.transaction():
            self._store.update_operation(operation); self._store.update_scan(scan)
        try:
            artifact_paths = self._export_ledger(scan, proof, summaries, terminal, status)
            operation_result.update({"ledgerPath": "audit-ledger.json", "artifactPaths": artifact_paths})
            operation["resultJson"] = json.dumps(operation_result, ensure_ascii=False, separators=(",", ":"))
            with self._store.transaction():
                self._store.update_operation(operation)
        except Exception as error:
            scan["status"] = "failed"; operation.update({"status": "failed_known", "errorCode": "LEDGER_EXPORT_FAILED", "errorMessage": str(error)}); operation.pop("resultJson", None)
            with self._store.transaction(): self._store.update_scan(scan); self._store.update_operation(operation)
            return self._response(request, scan, "failed", error=HostError("LEDGER_EXPORT_FAILED", str(error)), operation=operation)
        return self._response(request, scan, "ok", result=operation_result, operation=operation)

    def _export_ledger(self, scan: dict, proof: dict, summaries: list[dict], terminal: dict, status: str) -> list[str]:
        ended_at = self._now(); entry_url = scan.get("entryUrl") or "https://unknown.invalid"; parsed = urlparse(entry_url)
        scan_entity = {"scanId": scan["scanId"], "runId": scan["runId"], "protocolVersion": "1.0", "skillVersion": "1.0.0", "runRevision": scan["runRevision"], "algorithms": {"pageIdentity": "1.0.0", "objectIdentity": "1.0.0", "recoveryPolicy": "1.0.0", "sanitizationPolicy": "1.0.0", "normalization": "1.0.0"}, "status": status, "auditMode": "runtime_only", "entryUrl": entry_url, "allowedOrigins": [f"{parsed.scheme}://{parsed.netloc}"], "startedAt": scan["createdAt"], "endedAt": ended_at, "loginStatus": scan["loginStatus"], "ruleRegistryDigest": scan["ruleRegistryDigest"], "frozenRules": [{"ruleId": r["ruleId"], "version": r["version"]} for r in self._rule_registry.get("rules", []) if r.get("status") == "enabled"], "capabilities": json.loads(scan["capabilitiesJson"]), "coverageProof": proof, "terminalReason": terminal, "conclusionsValid": status != "failed"}
        self._validate_entity(self._scan_run_validator, scan_entity, "ScanRun")
        operations = []
        for row in self._store.list_operations(scan["scanId"]):
            accepted_at = row.get("accepted_at") or scan["createdAt"]
            item = {"operationId": row["operation_id"], "scanId": row["scan_id"], "requestId": row["request_id"], "tool": row["tool"], "operationKind": row["operation_kind"], "idempotencyKey": row["idempotency_key"], "requestDigest": row["request_digest"], "status": row["status"], "acceptedAt": accepted_at, "acceptedAtRevision": row["accepted_at_revision"]}
            if row.get("case_ref"): item["caseRef"] = row["case_ref"]
            if row.get("agent_turn_id"): item["agentTurnId"] = row["agent_turn_id"]
            if row.get("decision_reason"): item["decisionReason"] = row["decision_reason"]
            if row.get("model_duration_ms") is not None or row.get("model_retry_count") is not None:
                item["modelTelemetry"] = {"durationMs": row.get("model_duration_ms") or 0, "retryCount": row.get("model_retry_count") or 0}
            if row["status"] in {"succeeded", "rejected", "failed_known", "result_unknown"}:
                item["endedAt"] = row.get("ended_at") or ended_at
                if row.get("duration_ms") is not None:
                    item["durationMs"] = max(0, int(row["duration_ms"]))
            if row["status"] in {"rejected", "failed_known", "result_unknown"}: item["reason"] = {"code": row.get("error_code") or "OPERATION_FAILED", "message": row.get("error_message") or "Operation did not produce a successful result"}
            operations.append(item)
        entrypoints = self._store.list_entities("entrypoints", scan["scanId"]); processed = set(proof["processedEntrypointRefs"]); unprocessed = set(proof["unprocessedEntrypointRefs"]); skipped = {item["entrypointId"]: item["reason"] for item in proof["skippedEntrypoints"]}
        for entrypoint in entrypoints:
            ref = entrypoint["entrypointId"]
            if ref in processed: entrypoint["status"] = "processed"
            elif ref in skipped: entrypoint.update({"status": "skipped", "reason": skipped[ref]})
            elif ref in unprocessed: entrypoint["status"] = "unprocessed"
        objects = self._store.list_entities("audit_objects", scan["scanId"]); refs_by_page = {}
        for target in objects: refs_by_page.setdefault(target["pageStateRef"], []).append(target["objectId"])
        pages = self._store.list_entities("page_states", scan["scanId"])
        for page in pages:
            page["objectRefs"] = sorted(refs_by_page.get(page["pageStateId"], []))
            for embedded in page.get("safeEntrypoints", []):
                ref = embedded["entrypointId"]
                if ref in processed: embedded["status"] = "processed"
                elif ref in skipped: embedded.update({"status": "skipped", "reason": skipped[ref]})
                elif ref in unprocessed: embedded["status"] = "unprocessed"
        ledger = {"schemaVersion": "1.0.0", "createdAt": ended_at, "scan": scan_entity, "ruleRegistry": self._rule_registry, "pageStates": pages, "entrypoints": entrypoints, "objects": objects, "operations": operations, "dimensionFindings":self._store.list_entities("dimension_findings", scan["scanId"]), "assessments": self._store.list_entities("assessments", scan["scanId"]), "cases": self._store.list_entities("reverse_cases", scan["scanId"]), "evidence": self._store.list_entities("evidence", scan["scanId"]), "screenshots": self._store.list_entities("screenshots", scan["scanId"]), "issues": self._store.list_entities("issues", scan["scanId"])}
        self._validate_entity(self._ledger_validator, ledger, "AuditLedger")
        artifacts = {"audit-ledger.json": (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
        derived = self._report_builder.render(ledger)
        for name, validator in self._derived_output_validators.items():
            if name not in derived: raise ValueError(f"Missing derived JSON artifact: {name}")
            self._validate_entity(validator, json.loads(derived[name]), name)
        artifacts.update(derived)
        with self._store.transaction():
            self._store.ensure_integrity_event(scan)
        runtime_events = self._store.list_runtime_events(scan["scanId"])
        for event in runtime_events:
            self._validate_entity(self._runtime_event_validator, event, "RuntimeEvent")
        event_stream, manifest_bytes, manifest = render_observability(
            scan, runtime_events, self._store.list_operations(scan["scanId"]), ledger["assessments"]
        )
        self._validate_entity(self._observability_manifest_validator, manifest, "ObservabilityManifest")
        artifacts.update({"runtime-events.jsonl": event_stream, "observability-manifest.json": manifest_bytes})
        self._publish_artifacts(scan["outputDir"], artifacts)
        return sorted(artifacts)

    @staticmethod
    def _publish_artifacts(output_dir: str, artifacts: dict[str, bytes]) -> None:
        root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
        targets = {}
        for relative, content in artifacts.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path.name != relative:
                raise ValueError("Derived artifact path is invalid")
            target = root / path; targets[target] = content
            if target.exists() and target.read_bytes() != content:
                raise ValueError(f"Derived artifact content conflict: {relative}")
        created = []
        try:
            for target, content in targets.items():
                if target.exists(): continue
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}-", dir=root)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
                    os.link(temporary, target); created.append(target)
                finally:
                    temporary.unlink(missing_ok=True)
        except Exception:
            for target in created: target.unlink(missing_ok=True)
            raise

    @staticmethod
    def _replace_observability_artifacts(output_dir: str, stream: bytes, manifest: bytes) -> None:
        root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
        for name, content in (("runtime-events.jsonl", stream), ("observability-manifest.json", manifest)):
            target = root / name
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}-", dir=root)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content); output.flush(); os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    def _materialize_issue_screenshot(self, scan: dict, raw: dict, operation_id: str, revision: int) -> dict:
        screenshot_id = self._stable_id("screenshot", operation_id, "issue")
        extension = "jpg" if raw["imageType"] == "jpeg" else raw["imageType"]
        issue = {key: raw[key] for key in ("scanId", "pageStateRef", "objectRef", "imageType", "width", "height", "problemBoundingBox", "sourceBoundingBox", "annotation", "sanitizationPolicyVersion", "sanitizationStatus")}
        if raw.get("caseRef"):
            issue["caseRef"] = raw["caseRef"]
        issue.update({"screenshotId": screenshot_id, "kind": "issue", "status": "captured", "rawVisualRef": raw["screenshotId"],
                      "path": f"screenshots/{screenshot_id}.{extension}", "digest": raw["digest"],
                      "capturedAt": self._now(), "capturedAtRevision": revision})
        return issue

    @staticmethod
    def _read_screenshot(output_dir: str, screenshot: dict) -> bytes:
        root = Path(output_dir).resolve()
        relative = Path(screenshot["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Screenshot path escapes outputDir")
        path = (root / relative).resolve()
        if path == root or root not in path.parents or not path.is_file():
            raise ValueError("Raw Visual file does not exist in outputDir")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != screenshot["digest"]:
            raise ValueError("Raw Visual file integrity check failed")
        return content

    @staticmethod
    def _write_screenshot(output_dir: str, relative_path: str, image_bytes: bytes, expected_digest: str):
        if not output_dir:
            raise ValueError("Scan has no configured outputDir")
        target = Path(output_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest:
                raise ValueError("Screenshot file with the same name has conflicting content")
            return False
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(image_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    @staticmethod
    def _validate_recovery_attempt(attempt: RecoveryAttempt, method: str):
        if attempt.method != method or attempt.outcome not in {"restored", "uncertain", "failed"}:
            raise ValueError("Recovery adapter returned an unknown status")
        if not attempt.checks:
            raise ValueError("Recovery result is missing checks")
        if any(check.dimension not in {"url_route", "page_layer", "active_tab", "overlay_state", "control_state", "object_identity", "pending_requests", "write_request", "local_visual"} or check.outcome not in {"match", "mismatch", "unknown"} for check in attempt.checks):
            raise ValueError("Recovery check is invalid")
        dimensions = [check.dimension for check in attempt.checks]
        if len(dimensions) != len(set(dimensions)) or not set(RECOVERY_DIMENSIONS[:-1]).issubset(dimensions):
            raise ValueError("Recovery result is missing required dimensions or contains duplicates")
        outcomes = {check.outcome for check in attempt.checks}
        if attempt.outcome == "restored" and outcomes != {"match"}:
            raise ValueError("restored cannot contain unknown or mismatch checks")
        if attempt.outcome == "uncertain" and ("unknown" not in outcomes or "mismatch" in outcomes):
            raise ValueError("uncertain must contain an unknown check and cannot contain mismatch")
        if attempt.outcome == "failed" and not attempt.reason:
            raise ValueError("failed must include a reason")

    @staticmethod
    def _all_checks_match(attempt: RecoveryAttempt) -> bool:
        return bool(attempt.checks) and all(check.outcome == "match" for check in attempt.checks)

    @staticmethod
    def _has_unknown(attempt: RecoveryAttempt) -> bool:
        return any(check.outcome == "unknown" for check in attempt.checks)

    @staticmethod
    def _has_pollution_uncertainty(attempt: RecoveryAttempt) -> bool:
        return any(check.dimension in {"pending_requests", "write_request"} and check.outcome != "match" for check in attempt.checks)

    @staticmethod
    def _recovery_attempt_dict(attempt: RecoveryAttempt) -> dict:
        item = {"method": attempt.method, "outcome": attempt.outcome,
                "checks": [{"dimension": check.dimension, "outcome": check.outcome, "expected": check.expected, "observed": check.observed} for check in attempt.checks]}
        if attempt.reason:
            item["reason"] = {"code": "RECOVERY_FAILED", "message": attempt.reason}
        return item

    @staticmethod
    def _unknown_recovery_attempt(method: str, reason: str) -> RecoveryAttempt:
        return RecoveryAttempt(method, "failed", tuple(RecoveryCheck(dimension, "unknown") for dimension in RECOVERY_DIMENSIONS), reason)

    def _materialize_object_verification(self, scan, source_kind, source, outcome, operation):
        object_id = source.get("objectId") if source_kind == "audit_object" else None
        audit_object = None
        replacement = None
        if outcome.status == "matched":
            match = outcome.match
            object_id = object_id or self._stable_id("object", scan["scanId"], source["candidateId"])
            audit_object = {"objectId":object_id,"scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],
                            "kind":source["kind"],"status":"eligible","runtimeObserved":True,
                            "identity":{"hostLocatorId":match.host_locator_id,"fingerprint":self._digest(match.identity_material),
                                        "algorithmVersion":"1.0.0","role":match.role,"accessibleName":match.accessible_name,
                                        "visibleText":match.visible_text},"rebindStatus":"matched","lastVerifiedAtRevision":scan["runRevision"],
                            "location":{"boundingBox":{"x":match.x,"y":match.y,"width":match.width,"height":match.height},
                                        "visible":True,"viewportWidth":match.viewport_width,"viewportHeight":match.viewport_height},
                            "potentialRules":source["potentialRules"],"assessmentRefs":[],"observedAt":self._now()}
            audit_object["controls"] = [dict(item) for item in match.controls]
            audit_object["lists"] = [dict(item) for item in match.lists]
        elif source_kind == "audit_object":
            audit_object = dict(source)
            audit_object.update({"status":"blocked","rebindStatus":outcome.status,
                                 "blockedReason":{"code":f"OBJECT_{outcome.status.upper()}","message":"Object could not be rebound reliably."},
                                 "lastVerifiedAtRevision":scan["runRevision"]})
        if outcome.status == "changed":
            match = outcome.match
            replacement = {"candidateId":self._stable_id("candidate", source["pageStateRef"], match.identity_material),
                           "scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],"kind":source["kind"],
                           "label":match.accessible_name or source.get("label", source["kind"]),"role":match.role,
                           "locatorDigest":self._digest(match.identity_material),"potentialRules":source["potentialRules"],
                           "observedAtRevision":scan["runRevision"]}
        verification = {"verificationId":self._stable_id("verification", operation.get("operation_id") or operation["operationId"]),
                        "scanId":scan["scanId"],"pageStateRef":source["pageStateRef"],"sourceKind":source_kind,
                        "sourceRef":source.get("candidateId") or source.get("objectId"),"outcome":outcome.status,
                        "candidateCount":outcome.candidate_count,"matchedDimensions":list(outcome.matched_dimensions),
                        "excludedReasons":list(outcome.excluded_reasons),"changedDimensions":list(outcome.changed_dimensions),
                        "observedAt":self._now(),"observedAtRevision":scan["runRevision"]}
        if object_id and outcome.status == "matched":
            verification["objectRef"] = object_id
        return verification, audit_object, replacement

    @staticmethod
    def _validate_action_parameters(data: dict, obj: dict) -> None:
        action_type = data["type"]
        interactive = {"input_synthetic_value", "select_synthetic_option", "activate_query", "activate_reset", "restore_value"}
        if action_type not in interactive:
            return
        parameters = data.get("parameters")
        if not isinstance(parameters, dict):
            raise HostError("INVALID_REQUEST", "Interaction action parameters must be an object")
        expected_keys = {"controlRef", "valueClass"} if action_type in {"input_synthetic_value", "select_synthetic_option"} else {"controlRef"}
        if set(parameters) != expected_keys:
            raise HostError("INVALID_REQUEST", "Interaction actions may submit only controlRef and the specified valueClass")
        control_ref = parameters.get("controlRef")
        control = next((item for item in obj.get("controls", ()) if item.get("controlRef") == control_ref), None)
        if not control:
            raise HostError("UNKNOWN_REFERENCE", "controlRef does not belong to the current AuditObject")
        if not control.get("visible") or control.get("disabled"):
            raise HostError("ACTION_BLOCKED", "Target control is hidden or disabled")
        if action_type in {"input_synthetic_value", "restore_value"} and control.get("readOnly"):
            raise HostError("ACTION_BLOCKED", "Target control is read-only and cannot receive text input or value restoration")
        required_semantic = {
            "input_synthetic_value": "filter_input", "select_synthetic_option": "filter_input",
            "activate_query": "query", "activate_reset": "reset", "restore_value": "filter_input",
        }[action_type]
        if control.get("semanticAction") != required_semantic:
            raise HostError("ACTION_BLOCKED", "Action does not match the Host semantics of controlRef")
        allowed_kinds = {
            "input_synthetic_value": {"input", "textarea"}, "select_synthetic_option": {"select"},
            "activate_query": {"input", "button", "other"}, "activate_reset": {"input", "button", "other"},
            "restore_value": {"input", "select", "textarea"},
        }[action_type]
        if control.get("kind") not in allowed_kinds:
            raise HostError("ACTION_BLOCKED", "Action does not match the control type")
        if action_type in {"input_synthetic_value", "select_synthetic_option"} and parameters.get("valueClass") not in {
            "valid", "boundary_low", "boundary_high", "special_characters"
        }:
            raise HostError("ACTION_BLOCKED", "valueClass is not on the Host synthetic-value safety allowlist")

    def _materialize_interaction_evidence(self, scan: dict, obj: dict, case: dict, operation: dict,
                                          interaction: dict, request_refs: list[str]) -> dict:
        operation_id = operation.get("operation_id") or operation["operationId"]
        content = self._evidence_sanitizer.sanitize({**interaction, "requestObservationRefs": request_refs})
        evidence = {
            "evidenceId": self._stable_id("evidence-interaction", operation_id),
            "scanId": scan["scanId"], "pageStateRef": obj["pageStateRef"], "objectRef": obj["objectId"],
            "caseRef": case["caseId"], "kind": "runtime_interaction", "capturedAt": self._now(),
            "capturedAtRevision": scan["runRevision"], "collectorVersion": "1.0.0",
            "sanitizationPolicyVersion": self._evidence_sanitizer.POLICY_VERSION,
            "normalizationAlgorithmVersion": "1.0.0", "sanitized": True,
            "payload": {"payloadType": "json", "content": content},
            "sourceBinding": {"status": "verified", "bindingReason": "The fixed Host interaction probe captured before/after state on the same object reference"},
        }
        material = {key: value for key, value in evidence.items() if key not in {"evidenceId", "capturedAt", "integrityDigest"}}
        evidence["integrityDigest"] = self._digest(material)
        self._validate_entity(self._evidence_validator, evidence, "Evidence")
        return evidence

    @classmethod
    def _sanitized_parameters(cls, value, key=None):
        if isinstance(value, dict):
            return {str(item_key): cls._sanitized_parameters(item, str(item_key)) for item_key, item in value.items()}
        if isinstance(value, list):
            return [cls._sanitized_parameters(item, key) for item in value]
        if isinstance(value, str):
            if key == "controlRef" and value.startswith("control-"):
                return value
            if key == "valueClass" and value in {"valid", "boundary_low", "boundary_high", "special_characters"}:
                return value
            return {"valueClass": "redacted_string", "length": len(value)}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return {"valueClass": "redacted_unknown"}

    @staticmethod
    def _case_action(action: dict, before_page_state_id: str) -> dict:
        item = {key: action[key] for key in ("actionId", "type", "targetObjectRef", "intent", "parameters", "safetyOutcome", "atRunRevision")}
        if action["safetyOutcome"] == "allowed":
            recovery_mode = "inverse" if action["type"] in {"expand", "collapse", "input_synthetic_value", "select_synthetic_option"} else ("noop" if action["type"] in {"focus", "scroll"} else "refresh_only")
            item.update({"beforeStateEvidenceRefs": [before_page_state_id], "recoveryMode": recovery_mode})
            if recovery_mode == "inverse":
                inverse_type = {"expand": "collapse", "collapse": "expand",
                                "input_synthetic_value": "restore_value", "select_synthetic_option": "restore_value"}[action["type"]]
                inverse_parameters = ({"controlRef": action["parameters"]["controlRef"]}
                                      if inverse_type == "restore_value" else {})
                item["inverseAction"] = {"type": inverse_type, "targetObjectRef": action.get("targetObjectRef"), "parameters": inverse_parameters}
        if action.get("blockReason"):
            item["blockReason"] = action["blockReason"]
        if action.get("requestObservationRefs"):
            item["requestObservationRefs"] = list(action["requestObservationRefs"])
        if action.get("resultEvidenceRefs"):
            item["resultEvidenceRefs"] = list(action["resultEvidenceRefs"])
        return item

    @staticmethod
    def _validate_identity_outcome(outcome):
        if outcome.status not in {"matched", "not_found", "ambiguous", "changed"}:
            raise HostError("INTERNAL_FAILURE", "Object identity adapter returned an unknown status")
        if outcome.status == "matched" and (outcome.candidate_count != 1 or not outcome.match or not outcome.matched_dimensions):
            raise HostError("INTERNAL_FAILURE", "matched requires exactly one candidate, complete match material, and match dimensions")
        if outcome.status == "not_found" and (outcome.candidate_count != 0 or outcome.match):
            raise HostError("INTERNAL_FAILURE", "not_found requires zero candidates and no matched object")
        if outcome.status == "ambiguous" and (outcome.candidate_count < 2 or outcome.match):
            raise HostError("INTERNAL_FAILURE", "ambiguous requires at least two candidates and no selected object")
        if outcome.status == "changed" and (outcome.candidate_count < 1 or not outcome.match or not outcome.changed_dimensions):
            raise HostError("INTERNAL_FAILURE", "changed requires a related object and changed dimensions")

    def _finish_operation_failure(self, request, scan, operation, code, message):
        operation.update({"status":"failed_known","errorCode":code,"errorMessage":message})
        terminal = code == "BROWSER_SESSION_FAILED"
        if terminal and scan.get("status") not in {"completed", "partial", "failed"}:
            scan["runRevision"] += 1
            scan["status"] = "failed"
        with self._store.transaction():
            self._store.update_operation(operation)
            if terminal:
                self._store.update_scan(scan)
        return self._response(request, scan, "failed" if terminal else "rejected", error=HostError(code, message), operation=operation)

    def _accept_operation(self, request: dict, scan: dict, *, implemented: bool = False) -> tuple[dict, bool]:
        digest = self._request_digest(request)
        with self._store.transaction():
            existing = self._store.get_by_idempotency(scan["scanId"], request["idempotencyKey"])
            if existing:
                self._assert_same_digest(existing, digest, "Idempotency key maps to a different request digest")
                return existing, True
            current_scan = self._scan_from_row(self._store.get_scan(scan["scanId"]))
            self._check_revision(request, current_scan)
            operation = self._new_operation(request, scan["scanId"], TOOL_KINDS[request["tool"]], digest, "running" if implemented else "rejected", current_scan["runRevision"])
            if not implemented:
                operation.update({"errorCode":"INTERNAL_FAILURE","errorMessage":f"Tool {request['tool']} is not connected to a Host adapter"})
            self._store.insert_operation(operation)
        return operation, False

    def _operation_response(self, request: dict, scan: dict, operation: dict) -> dict:
        status = operation["status"]
        tool = operation.get("tool")
        if status in {"running", "succeeded"} and tool == "start_audit":
            return self._response(request, scan, "ok", result=self._start_result(scan), operation=operation)
        if status == "succeeded" and operation.get("result_json"):
            saved = json.loads(operation["result_json"])
            evidence_refs = [saved["evidenceId"]] if tool in {"observe_page", "capture_evidence"} and saved.get("evidenceId") else []
            if tool == "prepare_decision" and saved.get("pendingDecisionId"):
                pending = self._store.get_pending_decision(saved["pendingDecisionId"])
                evidence_refs = list(pending.get("evidenceRefs", [])) if pending else []
            if tool == "commit_decision" and saved.get("assessmentId"):
                assessment = self._store.get_assessment(saved["assessmentId"])
                evidence_refs = list(assessment.get("evidenceRefs", [])) if assessment else []
            return self._response(request, scan, "ok", result=saved, operation=operation, evidence_refs=evidence_refs)
        code = operation.get("error_code") or operation.get("errorCode") or ("OPERATION_IN_PROGRESS" if status == "running" else "OPERATION_RESULT_UNKNOWN")
        message = operation.get("error_message") or operation.get("errorMessage") or "Operation has not produced a definite result"
        saved_result = operation.get("result_json") or operation.get("resultJson")
        return self._response(request, scan, "failed" if scan["status"] == "failed" else "rejected",
                              result=json.loads(saved_result) if saved_result else None,
                              error=HostError(code, message), operation=operation)

    def _require_scan(self, request: dict) -> dict:
        row = self._store.get_scan_by_run(request["scanId"], request["runId"])
        if not row:
            raise HostError("UNKNOWN_REFERENCE", "Scan or Run does not exist")
        return self._scan_from_row(row)

    @staticmethod
    def _scan_from_row(row: dict) -> dict:
        return {"scanId":row["scan_id"],"runId":row["run_id"],"runRevision":row["run_revision"],
                "status":row["status"],"loginStatus":row["login_status"],"createdAt":row["created_at"],
                "ruleRegistryDigest":row["rule_registry_digest"],"currentPageStateId":row["current_page_state_id"],
                "capabilitiesJson":row["capabilities_json"],"outputDir":row["output_dir"],"entryUrl":row["entry_url"]}

    def _check_revision(self, request: dict, scan: dict) -> None:
        if request["expectedRunRevision"] != scan["runRevision"]:
            raise HostError("STALE_STATE", "expectedRunRevision is stale", retryable=True, next_step="inspect_current_state")

    @staticmethod
    def _assert_same_digest(operation: dict, digest: str, message: str) -> None:
        if (operation.get("request_digest") or operation.get("requestDigest")) != digest:
            raise HostError("IDEMPOTENCY_CONFLICT", message)

    def _validate_envelope(self, request: dict) -> None:
        errors = sorted(self._envelope_validator.iter_errors(request), key=lambda e: list(e.path))
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_request")
        if request["tool"] not in TOOL_KINDS and request["tool"] != "get_operation":
            raise HostError("UNKNOWN_TOOL", f"Unknown tool: {request['tool']}")

    def _validate_tool_input(self, tool: str, value: dict) -> None:
        name = {"start_audit":"startAuditInput","get_rule_contract":"getRuleContractInput","get_audit_progress":"getAuditProgressInput","inspect_page":"inspectPageInput","explore_entrypoint":"exploreEntrypointInput","inspect_object":"inspectObjectInput",
                "begin_case":"beginCaseInput","perform_action":"performActionInput","restore_case":"restoreCaseInput",
                "inspect_source":"inspectSourceInput","observe_page":"observePageInput","capture_evidence":"captureEvidenceInput","record_findings":"recordFindingsInput","prepare_decision":"prepareDecisionInput",
                "commit_decision":"commitDecisionInput","get_operation":"getOperationInput","complete_audit":"completeAuditInput"}[tool]
        errors = sorted(self._contract_validator(name).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INVALID_REQUEST", errors[0].message, next_step="fix_tool_input")

    def _validate_tool_output(self, tool: str, value: dict) -> None:
        name = {"start_audit":"startAuditOutput","get_rule_contract":"getRuleContractOutput","get_audit_progress":"getAuditProgressOutput","inspect_page":"inspectPageOutput","explore_entrypoint":"exploreEntrypointOutput","inspect_object":"inspectObjectOutput",
                "begin_case":"beginCaseOutput","perform_action":"performActionOutput","restore_case":"restoreCaseOutput",
                "inspect_source":"inspectSourceOutput","observe_page":"observePageOutput","capture_evidence":"captureEvidenceOutput","record_findings":"recordFindingsOutput","prepare_decision":"prepareDecisionOutput",
                "commit_decision":"commitDecisionOutput","get_operation":"getOperationOutput","complete_audit":"completeAuditOutput"}[tool]
        errors = sorted(self._contract_validator(name).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INTERNAL_FAILURE", f"Host tool output violates the contract: {errors[0].message}")

    @staticmethod
    def _validate_entity(validator, value, name):
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise HostError("INTERNAL_FAILURE", f"Host-generated {name} violates its Schema: {errors[0].message}")

    @staticmethod
    def _new_operation(request, scan_id, kind, digest, status, revision):
        operation = {"operationId":HostCore._new_id("operation"),"scanId":scan_id,"requestId":request["requestId"],
                "tool":request["tool"],"operationKind":kind,"idempotencyKey":request["idempotencyKey"],
                "requestDigest":digest,"status":status,"acceptedAtRevision":revision}
        if request.get("agentTurnId"):
            operation["agentTurnId"] = request["agentTurnId"]
        if request.get("decisionReason"):
            operation["decisionReason"] = request["decisionReason"]
        if request.get("modelTelemetry"):
            operation["modelTelemetry"] = dict(request["modelTelemetry"])
        if request.get("input", {}).get("caseId"):
            operation["caseRef"] = request["input"]["caseId"]
        return operation

    @staticmethod
    def _digest(value) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _request_digest(self, request: dict) -> str:
        return self._digest({"tool":request["tool"],"input":request["input"]})

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def _stable_id(self, prefix: str, *parts: str) -> str:
        return f"{prefix}-{self._digest(list(parts))[:24]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _response(self, request, scan, status, *, result=None, error=None, operation=None, evidence_refs=None, diagnostic_refs=None):
        response = {"protocolVersion":request["protocolVersion"],"requestId":request["requestId"],"scanId":scan["scanId"],"runId":scan["runId"],"runRevision":scan["runRevision"],"status":status,"evidenceRefs":list(evidence_refs or []),"diagnosticRefs":list(diagnostic_refs or [])}
        if result is not None:
            if operation is not None:
                result.setdefault("operationId", operation.get("operation_id") or operation.get("operationId"))
            self._validate_tool_output(request["tool"], result)
            response["result"] = result
        if error is not None:
            response["error"] = error.as_dict()
        return response

    def close(self) -> None:
        try:
            self._credential_vault.clear_all()
        finally:
            self._store.close()
