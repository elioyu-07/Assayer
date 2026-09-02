import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assayer_host.browser_runtime import BrowserHostRuntime


class BrowserRuntimePersistenceTest(unittest.TestCase):
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
            self.assertIs(runtime.platform_ledger_store._store, runtime.core._store)
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
