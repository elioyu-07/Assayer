import json
import hashlib
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_f_host import (BrowserHostRuntime, BrowserProfile, BrowserSession, BrowserSessionFailure, CredentialVault,
                          HostCore, HostError, LoginResult, LoginSecret,
                          PlaywrightBrowserBackend,
                          create_recoverable_browser_adapter_bundle)


PAGE = b"""<!doctype html><html lang='zh-CN'><head><title>Orders</title></head>
<body><main><h1>Orders</h1><form role='search' aria-label='Order filters'>
<label>Order number <input name='order-number'></label>
<button type='button'>Query</button><button type='reset'>Reset</button>
</form></main></body></html>"""

WEBSOCKET_PAGE = b"""<!doctype html><html lang='zh-CN'><head><title>WebSocket Orders</title></head>
<body><main><form role='search' aria-label='Order filters'><button type='button'>Query</button></form></main>
<script>window.auditSocket = new WebSocket(`ws://${location.host}/hmr`);</script></body></html>"""

HASH_PAGE = b"""<!doctype html><html lang='zh-CN'><head><title>Hash Orders</title></head>
<body><main><form role='search' aria-label='Hash filters'><button type='button'>Query</button></form></main></body></html>"""

TAB_PAGE = b"""<!doctype html><html><head><title>Tab App</title></head><body>
<nav><a class='lease-tabs__item active' onclick='show(0)'>Overview</a><a class='lease-tabs__item' onclick='show(1)'>Details</a></nav>
<main id='content'>Loading</main><script>
const tabs=[...document.querySelectorAll('.lease-tabs__item')];
function show(index){tabs.forEach((tab,i)=>tab.classList.toggle('active',i===index));if(index===1){fetch('/tab-data').then(r=>r.text()).then(v=>content.innerHTML=`<form role="search" aria-label="Detail filters"><label>${v}<input></label><button type="button">Query</button></form>`);}}
fetch('/startup-data').then(r=>r.text()).then(v=>content.textContent=v);
</script></body></html>"""


def action_page(path):
    if "action-route-page" in path:
        method = "GET"
        endpoint = ""
        onclick = "location.href='/action-route-target';"
    elif "action-cross" in path:
        method = "GET"
        endpoint = f"http://localhost:{SiteHandler.server_port}/action-cross"
        onclick = f"fetch('{endpoint}', {{method:'{method}'}}).catch(() => {{}});"
    else:
        method = "POST" if "action-post" in path else "GET"
        endpoint = "/action-post" if method == "POST" else "/action-get"
        onclick = f"fetch('{endpoint}', {{method:'{method}'}}).catch(() => {{}});"
    return f"""<!doctype html><html><head><title>Action Orders</title></head>
<body><main><form role='search' aria-label='Order filters'>
<button type='button' aria-expanded='false' onclick=\"{onclick} this.setAttribute('aria-expanded', this.getAttribute('aria-expanded') === 'true' ? 'false' : 'true')\">Run action</button>
</form></main></body></html>""".encode()


class SiteHandler(BaseHTTPRequestHandler):
    get_actions = 0
    post_actions = 0
    cross_actions = 0
    websocket_handshakes = 0
    startup_reads = 0
    tab_reads = 0
    server_port = 0

    def do_GET(self):
        if self.path == "/slow-page":
            time.sleep(0.5)
        if self.path in {"/action-get-page", "/action-post-page", "/action-cross-page", "/action-route-page"}:
            body = action_page(self.path)
        elif self.path == "/action-route-target":
            body = action_page("/action-route-target")
        elif self.path == "/action-get":
            type(self).get_actions += 1
            body = b"ok"
        elif self.path == "/action-cross":
            type(self).cross_actions += 1
            body = b"cross"
        elif self.path == "/websocket-page":
            body = WEBSOCKET_PAGE
        elif self.path == "/hash-app":
            body = HASH_PAGE
        elif self.path == "/tab-app":
            body = TAB_PAGE
        elif self.path == "/startup-data":
            type(self).startup_reads += 1
            body = b"Loaded overview data"
        elif self.path == "/tab-data":
            type(self).tab_reads += 1
            body = b"Loaded detail data"
        elif self.path == "/hmr":
            type(self).websocket_handshakes += 1
            body = b"websocket must be intercepted"
        else:
            body = PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if self.path == "/action-post":
            type(self).post_actions += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return None


class LocalNavigationLoginAdapter:
    def __init__(self, page_adapter, url):
        self.page_adapter = page_adapter
        self.url = url

    def authenticate(self, url, secret):
        self.page_adapter.navigate(self.url)
        return LoginResult("succeeded", current_page_state_id="page-live-001",
                           capabilities=("runtime", "dom", "interaction"))


class PlaywrightReadonlyIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Playwright optional dependency is not installed")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
        SiteHandler.server_port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "server"):
            cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_real_chromium_page_flows_through_host_core(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        session = BrowserSession("scan-live", BrowserProfile(), backend=PlaywrightBrowserBackend())
        try:
            try:
                session.open()
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            bundle = create_recoverable_browser_adapter_bundle(session, allowed_origin=origin)
            vault = CredentialVault()
            vault.put("credential-live", LoginSecret("local-user", "local-password"))
            with tempfile.TemporaryDirectory() as output:
                core = HostCore(
                    credential_vault=vault,
                    login_adapter=LocalNavigationLoginAdapter(bundle.page, f"{origin}/orders?ticket=secret"),
                    page_adapter=bundle.page, object_identity_adapter=bundle.identity,
                    evidence_adapter=bundle.evidence,
                )
                try:
                    started = core.handle({
                        "protocolVersion": "1.0", "requestId": "live-start", "agentTurnId": "turn-live-1",
                        "tool": "start_audit", "idempotencyKey": "live-start",
                        "input": {"url": f"{origin}/orders", "ruleRegistryVersion": "1.0.0",
                                  "outputDir": output, "browserProfile": "default",
                                  "credentialHandle": "credential-live"},
                    })["result"]
                    page = core.handle({
                        "protocolVersion": "1.0", "requestId": "live-page", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-live-2", "tool": "inspect_page",
                        "idempotencyKey": "live-page", "expectedRunRevision": 1,
                        "input": {"pageStateId": started["currentPageStateId"], "include": ["route", "objects"]},
                    })["result"]
                    verified = core.handle({
                        "protocolVersion": "1.0", "requestId": "live-object", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-live-3", "tool": "inspect_object",
                        "idempotencyKey": "live-object", "expectedRunRevision": 1,
                        "input": {"candidateId": page["candidateRefs"][0]},
                    })
                    self.assertEqual(page["route"], "/orders")
                    self.assertEqual(verified["result"]["rebindStatus"], "matched")
                    stored = core._store.get_page_state(started["currentPageStateId"])
                    self.assertEqual(stored["url"], f"{origin}/orders")
                    self.assertNotIn("ticket", json.dumps(stored))
                    object_id = verified["result"]["objectId"]
                    evidence = core.handle({
                        "protocolVersion": "1.0", "requestId": "live-evidence", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-live-4", "tool": "capture_evidence",
                        "idempotencyKey": "live-evidence", "expectedRunRevision": 1,
                        "input": {"pageStateId": started["currentPageStateId"], "objectId": object_id,
                                  "includeRawVisual": False},
                    })
                    self.assertEqual(evidence["status"], "ok")
                    persisted = core._store.get_evidence(evidence["result"]["evidenceId"])
                    self.assertEqual(persisted["kind"], "runtime_dom")
                    self.assertNotIn("secret", json.dumps(persisted))
                    self.assertNotIn("ticket", json.dumps(persisted))
                    visual = core.handle({
                        "protocolVersion": "1.0", "requestId": "live-visual", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-live-5", "tool": "capture_evidence",
                        "idempotencyKey": "live-visual", "expectedRunRevision": 2,
                        "input": {"pageStateId": started["currentPageStateId"], "objectId": object_id,
                                  "includeRawVisual": True},
                    })
                    self.assertEqual(visual["status"], "ok")
                    screenshot = core._store.get_screenshot(visual["result"]["screenshotRef"])
                    self.assertEqual(screenshot["status"], "captured")
                    self.assertEqual(screenshot["sanitizationStatus"], "not_performed")
                    self.assertEqual(screenshot["problemBoundingBox"]["x"], 0)
                    self.assertEqual(screenshot["problemBoundingBox"]["y"], 0)
                    self.assertEqual(screenshot["sourceBoundingBox"], core._store.get_audit_object(object_id)["location"]["boundingBox"])
                    image_path = os.path.join(output, screenshot["path"])
                    with open(image_path, "rb") as stream:
                        image = stream.read()
                    self.assertEqual(hashlib.sha256(image).hexdigest(), screenshot["digest"])
                    self.assertEqual(os.stat(image_path).st_mode & 0o777, 0o600)
                finally:
                    core.close()
        finally:
            session.close()

    def test_real_url_runtime_assembles_anonymous_browser_core(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        with tempfile.TemporaryDirectory() as output:
            try:
                runtime = BrowserHostRuntime(f"{origin}/orders", output)
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            try:
                invalid = {
                    "protocolVersion": "1.0", "requestId": "runtime-invalid", "agentTurnId": "runtime-invalid",
                    "tool": "start_audit", "idempotencyKey": "runtime-invalid",
                    "input": {"url": f"{origin}/orders", "ruleRegistryVersion": "1.0.0", "outputDir": output,
                              "browserProfile": "default", "authMode": "credential", "credentialHandle": "credential-invalid"},
                }
                with self.assertRaises(HostError) as caught:
                    runtime.handle(invalid)
                self.assertEqual(caught.exception.code, "INVALID_REQUEST")
                result = runtime.probe(f"{origin}/orders")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["result"]["route"], "/orders")
                self.assertEqual(len(result["result"]["candidateRefs"]), 1)
                self.assertEqual(result["result"]["objectVerification"]["rebindStatus"], "matched")
                screenshot = runtime.core._store.get_screenshot(result["result"]["evidence"]["screenshotRef"])
                self.assertEqual(screenshot["status"], "captured")
                self.assertEqual(screenshot["sanitizationStatus"], "not_performed")
                self.assertEqual(runtime.session.scan_id, result["result"]["scanId"])
            finally:
                runtime.close()
            self.assertEqual(runtime.session.state, "closed")

    def test_real_url_runtime_blocks_websocket_without_navigation_deadlock(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        SiteHandler.websocket_handshakes = 0
        profile = BrowserProfile(navigation_timeout_ms=3_000, operation_timeout_ms=3_000)
        with tempfile.TemporaryDirectory() as output:
            try:
                runtime = BrowserHostRuntime(f"{origin}/websocket-page", output, profile=profile)
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            started_at = time.monotonic()
            try:
                result = runtime.probe(f"{origin}/websocket-page")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["result"]["route"], "/websocket-page")
                self.assertLess(time.monotonic() - started_at, 3.0)
                self.assertEqual(SiteHandler.websocket_handshakes, 0)
                self.assertGreaterEqual(runtime.bundle.network_guard.summary()["blockedRequests"], 1)
            finally:
                runtime.close()

    def test_real_url_runtime_reports_hash_route_without_hash_query(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        url = f"{origin}/hash-app#/lease-mock?token=secret"
        with tempfile.TemporaryDirectory() as output:
            try:
                runtime = BrowserHostRuntime(url, output)
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            try:
                result = runtime.probe(url)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["result"]["route"], "/lease-mock")
                stored = runtime.core._store.get_page_state(result["result"]["pageStateId"])
                self.assertNotIn("token", json.dumps(stored))
                self.assertNotIn("secret", json.dumps(stored))
            finally:
                runtime.close()

    def test_real_url_runtime_loads_readonly_xhr_and_explores_tabs(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        SiteHandler.startup_reads = 0
        SiteHandler.tab_reads = 0
        with tempfile.TemporaryDirectory() as output:
            try:
                runtime = BrowserHostRuntime(f"{origin}/tab-app", output)
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            try:
                result = runtime.probe(f"{origin}/tab-app")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["result"]["summary"]["visitedPageStates"], 2)
                self.assertEqual(result["result"]["summary"]["tabsDiscovered"], 2)
                self.assertEqual(result["result"]["summary"]["evidenceCount"], 1)
                self.assertEqual([page["activeTab"] for page in result["result"]["pages"]], ["Overview", "Details"])
                self.assertIn("Loaded overview data", result["result"]["pages"][0]["visibleTextPreview"])
                self.assertIn("Loaded detail data", result["result"]["pages"][1]["visibleTextPreview"])
                self.assertEqual(SiteHandler.startup_reads, 1)
                self.assertEqual(SiteHandler.tab_reads, 1)
                self.assertEqual(result["result"]["summary"]["observedWrites"], 0)
                self.assertEqual(result["result"]["summary"]["unknownRequests"], 0)
            finally:
                runtime.close()

    def test_real_chromium_navigation_timeout_invalidates_session(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        profile = BrowserProfile(navigation_timeout_ms=100, operation_timeout_ms=100)
        session = BrowserSession("scan-timeout", profile, backend=PlaywrightBrowserBackend())
        try:
            try:
                session.open()
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            bundle = create_recoverable_browser_adapter_bundle(session, allowed_origin=origin)
            with self.assertRaises(BrowserSessionFailure):
                bundle.page.navigate(f"{origin}/slow-page")
            self.assertEqual(session.state, "failed")
        finally:
            session.close()

    def test_real_chromium_context_crash_is_sanitized_and_invalidates_session(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        session = BrowserSession("scan-crash", BrowserProfile(), backend=PlaywrightBrowserBackend())
        try:
            try:
                context = session.open()
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            bundle = create_recoverable_browser_adapter_bundle(session, allowed_origin=origin)
            bundle.page.navigate(f"{origin}/orders")
            bundle.page.observe("page-crash-001")
            context.close()
            with self.assertRaises(BrowserSessionFailure) as caught:
                bundle.page.observe("page-crash-002")
            self.assertEqual(caught.exception.code, "BROWSER_SESSION_FAILED")
            self.assertNotIn("TargetClosed", caught.exception.message)
            self.assertEqual(session.state, "failed")
        finally:
            session.close()

    def _run_action(self, page_name, action_type="expand", restore=False, return_case=False, capture=False, return_evidence=False):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        session = BrowserSession(f"scan-{page_name}", BrowserProfile(), backend=PlaywrightBrowserBackend())
        try:
            try:
                session.open()
            except Exception as error:
                self.skipTest(f"Playwright Chromium is not installed: {type(error).__name__}")
            bundle = create_recoverable_browser_adapter_bundle(session, allowed_origin=origin)
            vault = CredentialVault()
            vault.put(f"credential-{page_name}", LoginSecret("local-user", "local-password"))
            with tempfile.TemporaryDirectory() as output:
                core = HostCore(
                    credential_vault=vault,
                    login_adapter=LocalNavigationLoginAdapter(bundle.page, f"{origin}/{page_name}-page"),
                    page_adapter=bundle.page, object_identity_adapter=bundle.identity,
                    action_adapter=bundle.action, recovery_adapter=bundle.recovery,
                )
                try:
                    started = core.handle({
                        "protocolVersion": "1.0", "requestId": f"{page_name}-start", "agentTurnId": "turn-action-1",
                        "tool": "start_audit", "idempotencyKey": f"{page_name}-start",
                        "input": {"url": f"{origin}/{page_name}-page", "ruleRegistryVersion": "1.0.0",
                                  "outputDir": output, "browserProfile": "default",
                                  "credentialHandle": f"credential-{page_name}"},
                    })["result"]
                    page = core.handle({
                        "protocolVersion": "1.0", "requestId": f"{page_name}-page", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-action-2", "tool": "inspect_page",
                        "idempotencyKey": f"{page_name}-inspect", "expectedRunRevision": 1,
                        "input": {"pageStateId": started["currentPageStateId"], "include": ["objects"]},
                    })["result"]
                    object_id = core.handle({
                        "protocolVersion": "1.0", "requestId": f"{page_name}-object", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-action-3", "tool": "inspect_object",
                        "idempotencyKey": f"{page_name}-object", "expectedRunRevision": 1,
                        "input": {"candidateId": page["candidateRefs"][0]},
                    })["result"]["objectId"]
                    case = core.handle({
                        "protocolVersion": "1.0", "requestId": f"{page_name}-case", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-action-4", "tool": "begin_case",
                        "idempotencyKey": f"{page_name}-case", "expectedRunRevision": 1,
                        "input": {"objectId": object_id, "rule": {"ruleId": "FUA-10", "version": "1.0.0"},
                                  "kind": "observation", "purpose": "验证筛选动作",
                                  "plannedCoverageDimensions": ["filter_present", "query_action", "reset_action", "binding_to_list"]},
                    })["result"]
                    result = core.handle({
                        "protocolVersion": "1.0", "requestId": f"{page_name}-action", "scanId": started["scanId"],
                        "runId": started["runId"], "agentTurnId": "turn-action-5", "tool": "perform_action",
                        "idempotencyKey": f"{page_name}-action", "expectedRunRevision": 2,
                        "input": {"pageStateId": started["currentPageStateId"], "caseId": case["caseId"],
                                  "objectId": object_id, "type": action_type, "intent": "观察筛选区", "parameters": {}},
                    })
                    persisted_evidence = None
                    if capture:
                        result = core.handle({
                            "protocolVersion": "1.0", "requestId": f"{page_name}-evidence", "scanId": started["scanId"],
                            "runId": started["runId"], "agentTurnId": "turn-action-6", "tool": "capture_evidence",
                            "idempotencyKey": f"{page_name}-evidence", "expectedRunRevision": result["runRevision"],
                            "input": {"pageStateId": started["currentPageStateId"], "objectId": object_id,
                                      "caseId": case["caseId"], "includeRawVisual": False},
                        })
                        persisted_evidence = core._store.get_evidence(result["result"]["evidenceId"])
                    if restore:
                        result = core.handle({
                            "protocolVersion": "1.0", "requestId": f"{page_name}-restore", "scanId": started["scanId"],
                            "runId": started["runId"], "agentTurnId": "turn-action-6", "tool": "restore_case",
                            "idempotencyKey": f"{page_name}-restore", "expectedRunRevision": result["runRevision"],
                            "input": {"caseId": case["caseId"], "pageStateId": started["currentPageStateId"],
                                      "objectId": object_id, "fallback": "refresh_and_replay_safe_entrypoints"},
                        })
                    if return_case:
                        return result, core._store.get_case(case["caseId"])
                    if return_evidence:
                        return result, persisted_evidence
                    return result
                finally:
                    core.close()
        finally:
            session.close()

    def test_real_chromium_same_origin_get_action_succeeds_and_rebinds(self):
        SiteHandler.get_actions = 0
        result = self._run_action("action-get")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["resultStatus"], "succeeded")
        self.assertGreaterEqual(SiteHandler.get_actions, 1)
        self.assertEqual(len(result["result"]["requestObservationRefs"]), 1)

    def test_real_chromium_post_action_is_blocked_before_server(self):
        SiteHandler.post_actions = 0
        result = self._run_action("action-post")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "REQUEST_BLOCKED")
        self.assertEqual(SiteHandler.post_actions, 0)

    def test_real_chromium_blocked_write_can_be_restored_without_server_write(self):
        SiteHandler.post_actions = 0
        result = self._run_action("action-post", restore=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["finalStatus"], "restored")
        self.assertEqual(SiteHandler.post_actions, 0)

    def test_real_chromium_cross_origin_get_is_blocked_before_server(self):
        SiteHandler.cross_actions = 0
        result = self._run_action("action-cross")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "REQUEST_BLOCKED")
        self.assertEqual(SiteHandler.cross_actions, 0)

    def test_real_chromium_targeted_inverse_crosses_recovery_barrier(self):
        result, case = self._run_action("action-get", restore=True, return_case=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["finalStatus"], "restored")
        self.assertEqual([item["method"] for item in case["recovery"]["attempts"]], ["targeted_inverse"])
        self.assertEqual(len(case["recovery"]["attempts"][0]["checks"]), 9)
        self.assertTrue(all(item["outcome"] == "match" for item in case["recovery"]["attempts"][0]["checks"]))

    def test_real_chromium_refresh_replay_recovers_route_change(self):
        result, case = self._run_action("action-route", action_type="expand", restore=True, return_case=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["finalStatus"], "restored")
        self.assertEqual([item["method"] for item in case["recovery"]["attempts"]], ["targeted_inverse", "refresh_replay"])

    def test_real_chromium_structured_evidence_binds_case_without_visual(self):
        result, evidence = self._run_action("action-get", capture=True, return_evidence=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(evidence["kind"], "runtime_dom")
        self.assertTrue(evidence.get("caseRef"))
        self.assertNotIn("screenshotRefs", evidence)


if __name__ == "__main__":
    unittest.main()
