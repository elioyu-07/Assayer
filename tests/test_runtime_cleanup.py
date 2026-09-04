from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assayer_host.runtime_cleanup import PrivateRuntimeCleaner


TRANSACTION_ID = "lifecycle-" + "c" * 32
VERSION = "0.1.0+codex.release010"
SELECTOR = "assayer@release-010"


def uninstall_transaction(*, installed=False, status="completed", operation="uninstall"):
    installation = {
        "selector": SELECTOR,
        "version": VERSION,
        "installed": True,
        "enabled": True,
        "available": True,
        "releaseVerified": True,
        "immutableRelease": True,
    }
    return {
        "schemaVersion": "1.0.0",
        "transactionId": TRANSACTION_ID,
        "planDigest": "d" * 64,
        "operation": operation,
        "status": status,
        "current": installation,
        "target": None,
        "events": [],
        "finalInstallation": {
            **installation,
            "installed": installed,
            "enabled": installed,
        },
        "result": {
            "code": "LIFECYCLE_CHANGE_COMPLETED",
            "message": "The accepted plugin lifecycle change completed and passed terminal verification.",
        },
    }


def create_runtime(cache: Path, *, identity=True) -> Path:
    runtime = cache / "assayer" / f"runtime-{VERSION}"
    runtime.mkdir(parents=True)
    if identity:
        (runtime / "runtime-identity.json").write_text(json.dumps({
            "schemaVersion": "1.0.0",
            "pluginVersion": VERSION,
            "runtimeVersion": "0.1.0",
        }), encoding="utf-8")
    (runtime / "payload.txt").write_text("runtime", encoding="utf-8")
    return runtime


class PrivateRuntimeCleanupTests(unittest.TestCase):
    def test_filesystem_root_is_never_an_allowed_cache_root(self):
        with self.assertRaises(ValueError):
            PrivateRuntimeCleaner(Path("/"))

    def test_completed_uninstall_removes_only_matching_owned_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache)
            sibling = cache / "assayer" / "runtime-0.0.9"
            sibling.mkdir()
            result = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(uninstall_transaction())
            sibling_exists = sibling.exists()
            runtime_exists = runtime.exists()
        self.assertEqual(result["status"], "removed")
        self.assertTrue(result["deleted"])
        self.assertFalse(runtime_exists)
        self.assertTrue(sibling_exists)
        self.assertNotIn(directory, json.dumps(result))

    def test_non_terminal_or_non_uninstall_proof_cannot_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache)
            cleaner = PrivateRuntimeCleaner(cache)
            running = cleaner.cleanup_after_uninstall(
                uninstall_transaction(status="running"),
            )
            upgrade = cleaner.cleanup_after_uninstall(
                uninstall_transaction(operation="upgrade"),
            )
            still_exists = runtime.exists()
        self.assertEqual(running["status"], "rejected")
        self.assertEqual(upgrade["status"], "rejected")
        self.assertTrue(still_exists)

    def test_catalog_must_prove_selector_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache)
            result = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(
                uninstall_transaction(installed=True),
            )
            still_exists = runtime.exists()
        self.assertEqual(result["result"]["code"], "UNINSTALL_NOT_VERIFIED")
        self.assertTrue(still_exists)

    def test_missing_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache, identity=False)
            result = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(uninstall_transaction())
            still_exists = runtime.exists()
        self.assertEqual(result["result"]["code"], "RUNTIME_OWNERSHIP_UNVERIFIED")
        self.assertTrue(still_exists)

    def test_runtime_symlink_never_deletes_external_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external:
            cache = Path(directory)
            target = Path(external)
            (target / "important.txt").write_text("keep", encoding="utf-8")
            runtime = cache / "assayer" / f"runtime-{VERSION}"
            runtime.parent.mkdir(parents=True)
            runtime.symlink_to(target, target_is_directory=True)
            result = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(uninstall_transaction())
            external_exists = (target / "important.txt").exists()
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(external_exists)

    def test_absent_runtime_is_an_idempotent_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result = PrivateRuntimeCleaner(directory).cleanup_after_uninstall(
                uninstall_transaction(),
            )
        self.assertEqual(result["status"], "already_absent")
        self.assertFalse(result["retryable"])

    def test_failed_delete_leaves_owned_quarantine_for_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache)
            cleaner = PrivateRuntimeCleaner(cache)
            with patch("assayer_host.runtime_cleanup.shutil.rmtree", side_effect=OSError):
                first = cleaner.cleanup_after_uninstall(uninstall_transaction())
            quarantine = cache / "assayer" / f".runtime-cleanup-{TRANSACTION_ID}"
            first_state = (runtime.exists(), quarantine.exists())
            second = cleaner.cleanup_after_uninstall(uninstall_transaction())
            final_state = quarantine.exists()
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first_state, (False, True))
        self.assertEqual(second["status"], "removed")
        self.assertFalse(final_state)

    def test_ambiguous_active_and_quarantined_roots_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            runtime = create_runtime(cache)
            quarantine = cache / "assayer" / f".runtime-cleanup-{TRANSACTION_ID}"
            quarantine.mkdir()
            result = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(uninstall_transaction())
            states = (runtime.exists(), quarantine.exists())
        self.assertEqual(result["result"]["code"], "RUNTIME_CLEANUP_AMBIGUOUS")
        self.assertEqual(states, (True, True))


if __name__ == "__main__":
    unittest.main()
