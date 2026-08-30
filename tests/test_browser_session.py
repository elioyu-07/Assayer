import threading
import time
import unittest

from assayer_host import BrowserProfile, BrowserSession, BrowserSessionFailure, HostError, ScanSessionRegistry


class FakeBackend:
    def __init__(self):
        self.launched = []
        self.closed = []

    def launch(self, profile):
        handle = f"context-{len(self.launched) + 1}"
        self.launched.append((handle, profile))
        return handle

    def close(self, handle):
        self.closed.append(handle)


class FailingCloseBackend(FakeBackend):
    def close(self, handle):
        self.closed.append(handle)
        raise RuntimeError("injected close failure")


class BrowserSessionTest(unittest.TestCase):
    def test_profile_rejects_unallowlisted_driver_options(self):
        with self.assertRaises(ValueError):
            BrowserProfile.from_mapping({"executable_path": "/tmp/chromium"})
        with self.assertRaises(ValueError):
            BrowserProfile.from_mapping({"browser": "firefox"})
        with self.assertRaises(ValueError):
            BrowserProfile.from_mapping({"viewport_width": 100})
        with self.assertRaises(ValueError):
            BrowserProfile.from_mapping({"headless": 1})

    def test_session_lifecycle_is_explicit_and_backend_is_required(self):
        session = BrowserSession("scan-1")
        with self.assertRaisesRegex(RuntimeError, "backend"):
            session.open()
        self.assertEqual(session.state, "failed")
        with self.assertRaises(RuntimeError):
            session.open()

    def test_open_is_idempotent_and_close_releases_backend_once(self):
        backend = FakeBackend()
        session = BrowserSession("scan-1", BrowserProfile(headless=False), backend=backend)
        first = session.open()
        self.assertEqual(session.open(), first)
        self.assertEqual(len(backend.launched), 1)
        self.assertEqual(session.run_serial(lambda handle: handle), first)
        session.close(); session.close()
        self.assertEqual(backend.closed, [first])
        self.assertEqual(session.state, "closed")

    def test_run_serial_never_overlaps_callbacks(self):
        backend = FakeBackend()
        session = BrowserSession("scan-1", backend=backend)
        session.open()
        active = 0
        maximum = 0
        guard = threading.Lock()

        def callback(_):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with guard:
                active -= 1

        threads = [threading.Thread(target=lambda: session.run_serial(callback)) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(maximum, 1)

    def test_callback_failure_poison_session(self):
        backend = FakeBackend()
        session = BrowserSession("scan-1", backend=backend)
        session.open()
        with self.assertRaises(BrowserSessionFailure) as caught:
            session.run_serial(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertNotIn("boom", caught.exception.message)
        self.assertEqual(session.state, "failed")
        with self.assertRaises(RuntimeError):
            session.run_serial(lambda _: None)

    def test_deliberate_host_rejection_does_not_poison_session(self):
        session = BrowserSession("scan-1", backend=FakeBackend()); session.open()
        with self.assertRaises(HostError):
            session.run_serial(lambda _: (_ for _ in ()).throw(HostError("NAVIGATION_BLOCKED", "blocked")))
        self.assertEqual(session.state, "open")
        session.close()

    def test_registry_enforces_exclusive_ownership_and_terminal_release(self):
        registry = ScanSessionRegistry()
        first = BrowserSession("scan-1", backend=FakeBackend())
        registry.register("scan-1", first)
        with self.assertRaises(RuntimeError):
            registry.register("scan-1", BrowserSession("scan-1", backend=FakeBackend()))
        with self.assertRaises(ValueError):
            registry.release("scan-1", "auditing")
        first.open()
        registry.release("scan-1", "completed")
        self.assertEqual(len(registry), 0)
        self.assertEqual(first.state, "closed")
        with self.assertRaises(KeyError):
            registry.get("scan-1")

    def test_close_all_releases_each_scan(self):
        registry = ScanSessionRegistry()
        backends = [FakeBackend(), FakeBackend()]
        for index, backend in enumerate(backends):
            session = BrowserSession(f"scan-{index}", backend=backend)
            session.open(); registry.register(f"scan-{index}", session)
        registry.close_all()
        self.assertEqual(len(registry), 0)
        self.assertEqual([backend.closed for backend in backends], [["context-1"], ["context-1"]])

    def test_close_all_attempts_every_session_when_one_close_fails(self):
        registry = ScanSessionRegistry()
        backends = [FailingCloseBackend(), FakeBackend()]
        sessions = []
        for index, backend in enumerate(backends):
            session = BrowserSession(f"scan-{index}", backend=backend); session.open()
            registry.register(f"scan-{index}", session); sessions.append(session)
        with self.assertRaisesRegex(RuntimeError, "close failure"):
            registry.close_all()
        self.assertEqual(len(registry), 0)
        self.assertEqual([backend.closed for backend in backends], [["context-1"], ["context-1"]])
        self.assertEqual([session.state for session in sessions], ["closed", "closed"])


if __name__ == "__main__":
    unittest.main()
