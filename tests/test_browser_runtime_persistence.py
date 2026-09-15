import sqlite3
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assayer_host.browser_runtime import (
    BrowserHostRuntime,
    BrowserSnapshotHostRuntime,
    browser_provider_runtime_resolver,
)
from assayer_platform import ProviderRuntimeLease


class _SnapshotPage:
    def __init__(self):
        self.url = "about:blank"
        self.thread_ids = []

    def goto(self, url, **kwargs):
        del kwargs
        self.thread_ids.append(threading.get_ident())
        self.url = url

    def wait_for_timeout(self, milliseconds):
        del milliseconds
        self.thread_ids.append(threading.get_ident())

    def title(self):
        self.thread_ids.append(threading.get_ident())
        return "Orders"

    def content(self):
        self.thread_ids.append(threading.get_ident())
        return "<main>Orders</main>"

    def evaluate(self, expression):
        del expression
        self.thread_ids.append(threading.get_ident())
        return {
            "visibleText": "Orders",
            "entrypoints": [],
            "candidates": [],
            "networkSummary": {"status": "not_observed"},
        }


class _SnapshotContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


class _SnapshotBackend:
    def __init__(self, page):
        self.context = _SnapshotContext(page)
        self.thread_ids = []

    def launch(self, profile):
        del profile
        self.thread_ids.append(threading.get_ident())
        return self.context

    def close(self, handle):
        del handle
        self.thread_ids.append(threading.get_ident())


class BrowserRuntimePersistenceTest(unittest.TestCase):
    def test_snapshot_runtime_keeps_browser_work_on_one_worker_and_closes(self):
        page = _SnapshotPage()
        backend = _SnapshotBackend(page)
        runtime = BrowserSnapshotHostRuntime(
            "https://test.example.com/orders",
            backend=backend,
        )

        snapshot = runtime.observe_snapshot()
        runtime.close()
        runtime.close()

        self.assertEqual(snapshot.url, "https://test.example.com/orders")
        self.assertEqual(
            snapshot.dom_digest,
            hashlib.sha256(b"<main>Orders</main>").hexdigest(),
        )
        self.assertEqual(len(set(backend.thread_ids + page.thread_ids)), 1)
        self.assertEqual(len(backend.thread_ids), 2)

    def test_browser_provider_resolver_is_lazy_and_capability_scoped(self):
        created = []

        class Runtime:
            def __init__(self, url):
                created.append(url)
                self.closed = 0

            def close(self):
                self.closed += 1

        non_browser = SimpleNamespace(required_capabilities=("document_navigation",))
        browser = SimpleNamespace(required_capabilities=("browser_snapshot",))

        self.assertIsNone(browser_provider_runtime_resolver(
            None,
            non_browser,
            {"url": "https://test.example.com/orders"},
            runtime_factory=Runtime,
        ))
        self.assertEqual(created, [])

        lease = browser_provider_runtime_resolver(
            None,
            browser,
            {"url": "https://test.example.com/orders"},
            runtime_factory=Runtime,
        )
        self.assertIsInstance(lease, ProviderRuntimeLease)
        self.assertEqual(created, ["https://test.example.com/orders"])
        runtime = lease.runtime
        lease.close()
        lease.close()
        self.assertEqual(runtime.closed, 1)

    def test_runtime_uses_scan_local_sqlite_for_host_and_platform_ledgers(self):
        session = SimpleNamespace(scan_id="scan-persistent", open=lambda: None, close=lambda: None)
        bundle = SimpleNamespace(
            page=object(), network_guard=None, identity=object(), action=object(),
            recovery=object(), evidence=object(), entrypoint=object(),
        )
        with tempfile.TemporaryDirectory() as directory, \
                patch("assayer_host.browser_runtime.BrowserSession", return_value=session), \
                patch("assayer_host.browser_runtime.create_recoverable_browser_adapter_bundle", return_value=bundle):
            runtime = BrowserHostRuntime(
                "https://test.example.com/app", directory, scan_id="scan-persistent",
            )
            store_path = Path(directory).resolve() / "host-ledger.sqlite3"
            self.assertEqual(runtime.store_path, store_path)
            self.assertTrue(store_path.is_file())
            runtime.close()

            connection = sqlite3.connect(store_path)
            try:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                connection.close()
            self.assertIn("scans", tables)
            self.assertIn("platform_ledgers", tables)

    def test_failed_runtime_initialization_removes_empty_database(self):
        session = SimpleNamespace(scan_id="scan-failed", open=lambda: None, close=lambda: None)
        bundle = SimpleNamespace(page=object(), network_guard=None, identity=object(), action=object(), recovery=object(), evidence=object(), entrypoint=object())
        with tempfile.TemporaryDirectory() as directory, \
                patch("assayer_host.browser_runtime.BrowserSession", return_value=session), \
                patch("assayer_host.browser_runtime.create_recoverable_browser_adapter_bundle", return_value=bundle), \
                patch("assayer_host.browser_runtime.HostCore", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                BrowserHostRuntime("https://test.example.com/app", directory, scan_id="scan-failed")
            self.assertFalse((Path(directory).resolve() / "host-ledger.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
