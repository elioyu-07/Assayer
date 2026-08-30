import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_f_host import (BrowserProfile, BrowserSession, CredentialVault,
                          HostCore, LoginResult, LoginSecret,
                          PlaywrightBrowserBackend,
                          create_readonly_browser_adapters)


PAGE = b"""<!doctype html><html lang='zh-CN'><head><title>Orders</title></head>
<body><main><h1>Orders</h1><form role='search' aria-label='Order filters'>
<label>Order number <input name='order-number'></label>
<button type='button'>Query</button><button type='reset'>Reset</button>
</form></main></body></html>"""


class SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

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
            page_adapter, identity_adapter = create_readonly_browser_adapters(session, allowed_origin=origin)
            vault = CredentialVault()
            vault.put("credential-live", LoginSecret("local-user", "local-password"))
            with tempfile.TemporaryDirectory() as output:
                core = HostCore(
                    credential_vault=vault,
                    login_adapter=LocalNavigationLoginAdapter(page_adapter, f"{origin}/orders?ticket=secret"),
                    page_adapter=page_adapter, object_identity_adapter=identity_adapter,
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
                finally:
                    core.close()
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
