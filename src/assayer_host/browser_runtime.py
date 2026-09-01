"""Real-browser Host assembly and non-publishable smoke diagnostics."""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse

from .action_safety import ActionSafetyPolicy
from .auth import LoginResult
from .browser_readonly import BrowserReadOnlyPageAdapter, PlaywrightBrowserBackend
from .browser_recovery import create_recoverable_browser_adapter_bundle
from .browser_session import BrowserProfile, BrowserSession
from .core import HostCore
from .errors import HostError
from .evidence import EvidenceSanitizer


class AnonymousBrowserLoginAdapter:
    """Navigate to a public URL; no credential value crosses the Core boundary."""

    def __init__(self, page, guard=None):
        self._page = page
        self._guard = guard

    def authenticate_anonymous(self, url):
        if self._guard is None:
            self._page.navigate(url)
        else:
            with self._guard.operation(
                "bootstrap",
                lambda request: ActionSafetyPolicy().classify_request(
                    request, self._guard.allowed_origin
                ),
            ):
                self._page.navigate(url)
                self._page.settle_readonly()
        return LoginResult(
            "succeeded",
            current_page_state_id="page-bootstrap-001",
            capabilities=("runtime", "dom", "interaction", "visual"),
        )


class BrowserHostRuntime:
    """Own one real Chromium Session and the HostCore using its adapters."""

    def __init__(
        self,
        url: str,
        output_dir: str | Path,
        *,
        profile: BrowserProfile | None = None,
        scan_id: str | None = None,
    ):
        parsed = urlparse(url)
        try:
            origin = BrowserReadOnlyPageAdapter._origin(parsed)
        except HostError as error:
            raise ValueError("url must be an http(s) URL without credentials") from error
        self.entry_url = url
        self.output_dir = str(Path(output_dir).expanduser().resolve())
        scan_id = scan_id or f"scan-{uuid.uuid4().hex}"
        self.session = BrowserSession(
            scan_id, profile or BrowserProfile(), backend=PlaywrightBrowserBackend()
        )
        self.session.open()
        try:
            self.bundle = create_recoverable_browser_adapter_bundle(
                self.session, allowed_origin=origin
            )
            self.core = HostCore(
                login_adapter=AnonymousBrowserLoginAdapter(
                    self.bundle.page, self.bundle.network_guard
                ),
                page_adapter=self.bundle.page,
                object_identity_adapter=self.bundle.identity,
                action_adapter=self.bundle.action,
                recovery_adapter=self.bundle.recovery,
                evidence_adapter=self.bundle.evidence,
                scan_id_factory=lambda: scan_id,
                entrypoint_adapter=self.bundle.entrypoint,
            )
        except Exception:
            self.session.close()
            raise
        self._sanitizer = EvidenceSanitizer()
        self._bootstrap_key = None

    def handle(self, request: dict) -> dict:
        if request.get("tool") == "start_audit":
            request_input = request.get("input", {})
            requested_url = request_input.get("url")
            if requested_url != self.entry_url:
                raise HostError(
                    "INVALID_REQUEST", "start_audit URL does not match the runtime-bound URL"
                )
            if request_input.get("authMode") != "anonymous":
                raise HostError(
                    "INVALID_REQUEST", "The real-URL runtime currently accepts anonymous mode only"
                )
            requested_output = request_input.get("outputDir")
            if (
                not isinstance(requested_output, str)
                or str(Path(requested_output).expanduser().resolve()) != self.output_dir
            ):
                raise HostError(
                    "INVALID_REQUEST", "start_audit outputDir does not match the runtime-bound directory"
                )
            if request_input.get("browserProfile") != "default":
                raise HostError(
                    "INVALID_REQUEST", "start_audit browserProfile does not match the runtime configuration"
                )
            key = request.get("idempotencyKey")
            if self._bootstrap_key is not None and key != self._bootstrap_key:
                raise HostError("RUN_CONFLICT", "A BrowserHostRuntime allows only one Scan")
        response = self.core.handle(request)
        if request.get("tool") == "start_audit":
            self._bootstrap_key = request.get("idempotencyKey")
        if request.get("tool") == "start_audit" and response.get("status") == "ok":
            if response["result"]["scanId"] != self.session.scan_id:
                self.session.close()
                raise HostError("INTERNAL_FAILURE", "Failed to bind the Browser Session to the Scan")
        if request.get("tool") == "complete_audit" and response.get("result", {}).get(
            "scanStatus"
        ) in {"completed", "partial", "failed"}:
            self.session.close()
        return response

    def close(self):
        try:
            self.core.close()
        finally:
            self.session.close()

    def protocol_state(self) -> dict | None:
        """Return the durable Scan state needed by RuntimeRouter supervision."""
        row = self.core._store.get_scan(self.session.scan_id)
        if row is None:
            return None
        return {
            "scanId": row["scan_id"],
            "runId": row["run_id"],
            "runRevision": int(row["run_revision"]),
            "scanStatus": row["status"],
        }

    def read_screenshot(self, scan_id: str, run_id: str, screenshot_ref: str) -> tuple[bytes, str]:
        return self.core.read_screenshot(scan_id, run_id, screenshot_ref)

    def record_runtime_event(self, event: dict) -> dict:
        return self.core.record_runtime_event(event)

    def refresh_observability(self) -> None:
        self.core.refresh_observability()

    def build_completion_input(self, scan_id: str, run_id: str, completion_reason: str | None = None) -> dict:
        return self.core.build_completion_input(scan_id, run_id, completion_reason)

    def fail_scan(self, code: str, message: str) -> dict:
        state = self.protocol_state()
        if state is None:
            raise HostError("UNKNOWN_REFERENCE", "Scan has not been created")
        return self.core.fail_scan(state["scanId"], state["runId"], code, message)

    def smoke(self, url: str) -> dict:
        """Exercise browser-backed Host facts without producing assessments.

        Smoke output is diagnostic-only: it never records DimensionFinding,
        PendingDecision, RuleAssessment, Issue, or a publishable audit ledger.
        """
        start = self.handle(
            {
                "protocolVersion": "1.0",
                "requestId": "runtime-start",
                "agentTurnId": "runtime-turn-1",
                "tool": "start_audit",
                "idempotencyKey": "runtime-start",
                "input": {
                    "url": url,
                    "ruleRegistryVersion": self.core.rule_registry_version,
                    "outputDir": self.output_dir,
                    "browserProfile": "default",
                    "authMode": "anonymous",
                },
            }
        )
        if start.get("status") != "ok":
            return start
        scan = start["result"]
        page = self.handle(
            {
                "protocolVersion": "1.0",
                "requestId": "runtime-page",
                "scanId": scan["scanId"],
                "runId": scan["runId"],
                "agentTurnId": "runtime-turn-2",
                "tool": "inspect_page",
                "idempotencyKey": "runtime-page",
                "expectedRunRevision": scan["runRevision"],
                "input": {
                    "pageStateId": scan["currentPageStateId"],
                    "include": [
                        "route",
                        "visibleText",
                        "objects",
                        "safeEntrypoints",
                        "networkSummary",
                    ],
                },
            }
        )
        if page.get("status") != "ok":
            return page
        result = {
            "scanId": scan["scanId"],
            "runId": scan["runId"],
            "runRevision": page["runRevision"],
            "pageStateId": page["result"]["pageStateId"],
            "candidateRefs": page["result"]["candidateRefs"],
            "entrypointRefs": page["result"]["entrypointRefs"],
            "route": page["result"].get("route"),
            "title": page["result"].get("title"),
            "activeTab": page["result"].get("activeTab"),
            "structureSummary": page["result"].get("structureSummary", {}),
            "networkSummary": page["result"].get("networkSummary", {}),
            "pages": [],
        }
        visited_tabs = set()
        current_page = page
        current_revision = page["runRevision"]
        for _ in range(16):
            active_tab = current_page["result"].get("activeTab")
            if active_tab:
                visited_tabs.add(active_tab)
            candidates = current_page["result"]["candidateRefs"]
            page_result = {
                "pageStateId": current_page["result"]["pageStateId"],
                "route": current_page["result"].get("route"),
                "title": current_page["result"].get("title"),
                "activeTab": active_tab,
                "candidateRefs": candidates,
                "entrypointRefs": current_page["result"]["entrypointRefs"],
                "structureSummary": current_page["result"].get("structureSummary", {}),
                "networkSummary": current_page["result"].get("networkSummary", {}),
                "visibleTextPreview": self._sanitizer.sanitize(
                    current_page["result"].get("visibleText", "")
                )[:2000],
            }
            if candidates:
                suffix = len(result["pages"])
                verified = self.handle(
                    {
                        "protocolVersion": "1.0",
                        "requestId": f"runtime-object-{suffix}",
                        "scanId": scan["scanId"],
                        "runId": scan["runId"],
                        "agentTurnId": "runtime-turn-3",
                        "tool": "inspect_object",
                        "idempotencyKey": f"runtime-object-{suffix}",
                        "expectedRunRevision": current_revision,
                        "input": {"candidateId": candidates[0]},
                    }
                )
                page_result["objectVerification"] = verified.get("result", verified)
                if suffix == 0:
                    result["objectVerification"] = page_result["objectVerification"]
                if verified.get("status") == "ok" and verified.get("result", {}).get(
                    "objectId"
                ):
                    evidence = self.handle(
                        {
                            "protocolVersion": "1.0",
                            "requestId": f"runtime-evidence-{suffix}",
                            "scanId": scan["scanId"],
                            "runId": scan["runId"],
                            "agentTurnId": "runtime-turn-4",
                            "tool": "capture_evidence",
                            "idempotencyKey": f"runtime-evidence-{suffix}",
                            "expectedRunRevision": verified["runRevision"],
                            "input": {
                                "pageStateId": current_page["result"]["pageStateId"],
                                "objectId": verified["result"]["objectId"],
                                "includeRawVisual": True,
                            },
                        }
                    )
                    page_result["evidence"] = evidence.get("result", evidence)
                    if suffix == 0:
                        result["evidence"] = page_result["evidence"]
                    current_revision = evidence["runRevision"]
            result["pages"].append(page_result)
            entries = current_page["result"].get("entrypoints", [])
            tab_entries = [
                entry
                for entry in entries
                if entry.get("kind") == "tab"
                and entry.get("label") not in visited_tabs
                and entry.get("status") != "processed"
            ]
            if not tab_entries:
                break
            next_entry = tab_entries[0]
            explored = self.handle(
                {
                    "protocolVersion": "1.0",
                    "requestId": f"runtime-tab-{len(result['pages'])}",
                    "scanId": scan["scanId"],
                    "runId": scan["runId"],
                    "agentTurnId": "runtime-turn-tabs",
                    "tool": "explore_entrypoint",
                    "idempotencyKey": f"runtime-tab-{len(result['pages'])}",
                    "expectedRunRevision": current_revision,
                    "input": {
                        "pageStateId": current_page["result"]["pageStateId"],
                        "entrypointId": next_entry["entrypointId"],
                    },
                }
            )
            if explored.get("status") != "ok":
                result["explorationError"] = explored.get("error", explored)
                break
            current_page = explored
            current_revision = explored["runRevision"]
            scan = {
                **scan,
                "runRevision": explored["runRevision"],
                "currentPageStateId": explored["result"]["pageStateId"],
            }
        evidence_refs = [
            item["evidence"]["evidenceId"]
            for item in result["pages"]
            if item.get("evidence", {}).get("evidenceId")
        ]
        result["runRevision"] = current_revision
        result["summary"] = {
            "visitedPageStates": len(result["pages"]),
            "tabsDiscovered": max(
                (
                    item.get("structureSummary", {}).get("tabs", 0)
                    for item in result["pages"]
                ),
                default=0,
            ),
            "ruleCandidateCount": sum(
                len(item.get("candidateRefs", [])) for item in result["pages"]
            ),
            "evidenceCount": len(evidence_refs),
            "pagesWithVisibleErrors": sum(
                1
                for item in result["pages"]
                if item.get("structureSummary", {}).get("errorCount", 0) > 0
            ),
            "observedWrites": sum(
                item.get("networkSummary", {}).get("observedWrites", 0)
                for item in result["pages"]
            ),
            "unknownRequests": sum(
                item.get("networkSummary", {}).get("unknownRequests", 0)
                for item in result["pages"]
            ),
        }
        result.update(
            {
                "mode": "host_smoke",
                "publishable": False,
                "assessmentCount": 0,
                "issueCount": 0,
                "diagnosticMessage": (
                    "Host smoke verifies only the browser fact chain; it does not generate Agent semantic decisions or a formal audit ledger"
                ),
            }
        )
        return {
            "protocolVersion": "1.0",
            "requestId": "runtime-smoke",
            "status": "ok",
            "result": result,
            "evidenceRefs": evidence_refs,
            "diagnosticRefs": [],
        }

    def probe(self, url: str) -> dict:
        """Backward-compatible name for the Host-only smoke probe."""
        return self.smoke(url)
