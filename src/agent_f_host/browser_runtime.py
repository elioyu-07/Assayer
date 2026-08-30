"""Product runtime assembly for a real URL (B10)."""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse

from .auth import LoginResult
from .browser_readonly import BrowserReadOnlyPageAdapter, PlaywrightBrowserBackend
from .browser_recovery import create_recoverable_browser_adapter_bundle
from .browser_session import BrowserProfile, BrowserSession
from .core import HostCore
from .errors import HostError


class AnonymousBrowserLoginAdapter:
    """Navigate to a public URL; no credential value crosses the Core boundary."""

    def __init__(self, page):
        self._page = page

    def authenticate_anonymous(self, url):
        self._page.navigate(url)
        return LoginResult("succeeded", current_page_state_id="page-bootstrap-001",
                           capabilities=("runtime", "dom", "interaction", "visual"))


class BrowserHostRuntime:
    """Own one real Chromium Session and the HostCore using its adapters."""

    def __init__(self, url: str, output_dir: str | Path, *, profile: BrowserProfile | None = None):
        parsed = urlparse(url)
        try:
            origin = BrowserReadOnlyPageAdapter._origin(parsed)
        except HostError as error:
            raise ValueError("url must be an http(s) URL without credentials") from error
        self.entry_url = url
        self.output_dir = str(Path(output_dir).expanduser().resolve())
        scan_id = f"scan-{uuid.uuid4().hex}"
        self.session = BrowserSession(scan_id, profile or BrowserProfile(), backend=PlaywrightBrowserBackend())
        self.session.open()
        try:
            self.bundle = create_recoverable_browser_adapter_bundle(self.session, allowed_origin=origin)
            self.core = HostCore(
                login_adapter=AnonymousBrowserLoginAdapter(self.bundle.page),
                page_adapter=self.bundle.page, object_identity_adapter=self.bundle.identity,
                action_adapter=self.bundle.action, recovery_adapter=self.bundle.recovery,
                evidence_adapter=self.bundle.evidence,
                scan_id_factory=lambda: scan_id,
            )
        except Exception:
            self.session.close()
            raise
        self._store = self.core._store
        self._bootstrap_key = None

    def handle(self, request: dict) -> dict:
        if request.get("tool") == "start_audit":
            request_input = request.get("input", {})
            requested_url = request_input.get("url")
            if requested_url != self.entry_url:
                raise HostError("INVALID_REQUEST", "start_audit URL 与运行时绑定 URL 不一致")
            if request_input.get("authMode") != "anonymous":
                raise HostError("INVALID_REQUEST", "真实 URL 运行时当前只接受 anonymous 模式")
            requested_output = request_input.get("outputDir")
            if not isinstance(requested_output, str) or str(Path(requested_output).expanduser().resolve()) != self.output_dir:
                raise HostError("INVALID_REQUEST", "start_audit outputDir 与运行时绑定目录不一致")
            if request_input.get("browserProfile") != "default":
                raise HostError("INVALID_REQUEST", "start_audit browserProfile 与运行时配置不一致")
            key = request.get("idempotencyKey")
            if self._bootstrap_key is not None and key != self._bootstrap_key:
                raise HostError("RUN_CONFLICT", "一个 BrowserHostRuntime 只允许一个 Scan")
        response = self.core.handle(request)
        if request.get("tool") == "start_audit":
            self._bootstrap_key = request.get("idempotencyKey")
        if request.get("tool") == "start_audit" and response.get("status") == "ok":
            if response["result"]["scanId"] != self.session.scan_id:
                self.session.close()
                raise HostError("INTERNAL_FAILURE", "Browser Session 与 Scan 绑定失败")
        if request.get("tool") == "complete_audit" and response.get("result", {}).get("scanStatus") in {"completed", "partial", "failed"}:
            self.session.close()
        return response

    def close(self):
        try:
            self.core.close()
        finally:
            self.session.close()

    def probe(self, url: str) -> dict:
        start = self.handle({
            "protocolVersion": "1.0", "requestId": "runtime-start", "agentTurnId": "runtime-turn-1",
            "tool": "start_audit", "idempotencyKey": "runtime-start",
            "input": {"url": url, "ruleRegistryVersion": "1.0.0", "outputDir": self.output_dir,
                      "browserProfile": "default", "authMode": "anonymous"},
        })
        if start.get("status") != "ok":
            return start
        scan = start["result"]
        page = self.handle({
            "protocolVersion": "1.0", "requestId": "runtime-page", "scanId": scan["scanId"], "runId": scan["runId"],
            "agentTurnId": "runtime-turn-2", "tool": "inspect_page", "idempotencyKey": "runtime-page",
            "expectedRunRevision": scan["runRevision"],
            "input": {"pageStateId": scan["currentPageStateId"], "include": ["route", "objects", "safeEntrypoints", "networkSummary"]},
        })
        if page.get("status") != "ok":
            return page
        result = {"scanId": scan["scanId"], "runId": scan["runId"], "runRevision": page["runRevision"],
                  "pageStateId": page["result"]["pageStateId"], "candidateRefs": page["result"]["candidateRefs"],
                  "entrypointRefs": page["result"]["entrypointRefs"], "route": page["result"].get("route"),
                  "title": page["result"].get("title")}
        if page["result"]["candidateRefs"]:
            verified = self.handle({
                "protocolVersion": "1.0", "requestId": "runtime-object", "scanId": scan["scanId"], "runId": scan["runId"],
                "agentTurnId": "runtime-turn-3", "tool": "inspect_object", "idempotencyKey": "runtime-object",
                "expectedRunRevision": page["runRevision"], "input": {"candidateId": page["result"]["candidateRefs"][0]},
            })
            result["objectVerification"] = verified.get("result", verified)
            if verified.get("status") == "ok" and verified.get("result", {}).get("objectId"):
                evidence = self.handle({
                    "protocolVersion": "1.0", "requestId": "runtime-evidence", "scanId": scan["scanId"], "runId": scan["runId"],
                    "agentTurnId": "runtime-turn-4", "tool": "capture_evidence", "idempotencyKey": "runtime-evidence",
                    "expectedRunRevision": verified["runRevision"],
                    "input": {"pageStateId": scan["currentPageStateId"], "objectId": verified["result"]["objectId"], "includeRawVisual": True},
                })
                result["evidence"] = evidence.get("result", evidence)
        return {"protocolVersion": "1.0", "requestId": "runtime-probe", "status": "ok", "result": result,
                "evidenceRefs": [], "diagnosticRefs": []}
