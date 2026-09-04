from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from assayer_host.lifecycle_transaction import (
    LifecycleCommandError,
    LifecycleJournalStore,
    PluginLifecycleTransaction,
)
from assayer_host.release_lifecycle import PluginInstallation, PluginLifecyclePlanner
from assayer_host.resources import default_schema_root


CURRENT = PluginInstallation(
    "assayer@release-010", "0.1.0", True, True, True, True, True,
)
TARGET = PluginInstallation(
    "assayer@release-011", "0.1.1", False, False, True, True, True,
)


class FakeLifecycleClient:
    def __init__(self):
        self.states = {
            CURRENT.selector: CURRENT,
            TARGET.selector: TARGET,
        }
        self.calls = []
        self.fail_add = set()
        self.fail_remove = set()
        self.unhealthy = set()

    def snapshot(self, selector):
        self.calls.append(("snapshot", selector))
        return self.states[selector]

    def add(self, selector):
        self.calls.append(("add", selector))
        if selector in self.fail_add:
            raise LifecycleCommandError("CODEX_PLUGIN_ADD_FAILED", "Codex could not install the selected plugin release.")
        state = self.states[selector]
        self.states[selector] = PluginInstallation(
            state.selector, state.version, True, True, True,
            state.release_verified, state.immutable_release,
        )

    def remove(self, selector):
        self.calls.append(("remove", selector))
        if selector in self.fail_remove:
            raise LifecycleCommandError("CODEX_PLUGIN_REMOVE_FAILED", "Codex could not remove the selected plugin release.")
        state = self.states[selector]
        self.states[selector] = PluginInstallation(
            state.selector, state.version, False, False, state.available,
            state.release_verified, state.immutable_release,
        )

    def verify_installation(self, installation):
        self.calls.append(("verify", installation.selector))
        return installation.selector not in self.unhealthy


class FailingJournalStore(LifecycleJournalStore):
    def save(self, journal):
        raise OSError("private path must not be reflected")


class DelayedFailingJournalStore(LifecycleJournalStore):
    def __init__(self, root, fail_on_call):
        super().__init__(root)
        self.calls = 0
        self.fail_on_call = fail_on_call

    def save(self, journal):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise OSError("private post-mutation journal path")
        return super().save(journal)


class LifecycleTransactionTests(unittest.TestCase):
    def setUp(self):
        self.plan = PluginLifecyclePlanner().build(
            "upgrade", CURRENT, target=TARGET, release_direction_verified=True,
        )

    @staticmethod
    def _direction(operation, current, target):
        return operation == "upgrade" and current.version == "0.1.0" and target.version == "0.1.1"

    def _transaction(self, directory, client, **kwargs):
        return PluginLifecycleTransaction(
            client, LifecycleJournalStore(directory),
            direction_verifier=self._direction,
            **kwargs,
        )

    def assert_valid(self, journal):
        schema = json.loads((default_schema_root() / "plugin-lifecycle-transaction.schema.json").read_text())
        Draft202012Validator(schema).validate(journal)

    def test_confirmation_is_required_before_catalog_or_mutation_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=False,
                transaction_id="lifecycle-00000000000000000000000000000001",
            )
        self.assertEqual(journal["status"], "confirmation_required")
        self.assertEqual(client.calls, [])
        self.assert_valid(journal)

    def test_successful_upgrade_is_verified_and_journaled(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000002",
            )
            persisted = json.loads((
                Path(directory) / journal["transactionId"] / "lifecycle-transaction.json"
            ).read_text())
        self.assertEqual(journal["status"], "completed")
        mutations = [call for call in client.calls if call[0] in {"add", "remove"}]
        self.assertEqual(mutations, [
            ("remove", CURRENT.selector),
            ("add", TARGET.selector),
        ])
        self.assertEqual(journal, persisted)
        self.assertEqual(journal["finalInstallation"]["version"], "0.1.1")
        self.assert_valid(journal)

    def test_stale_plan_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            client.states[TARGET.selector] = PluginInstallation(
                TARGET.selector, "0.1.2", False, False, True, True, True,
            )
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000003",
            )
        self.assertEqual(journal["status"], "rejected")
        self.assertEqual(journal["result"]["code"], "STALE_LIFECYCLE_PLAN")
        self.assertEqual([call for call in client.calls if call[0] in {"add", "remove"}], [])
        self.assert_valid(journal)

    def test_target_install_failure_restores_previous_release(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            client.fail_add.add(TARGET.selector)
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000004",
            )
        self.assertEqual(journal["status"], "rolled_back")
        self.assertTrue(client.states[CURRENT.selector].installed)
        self.assertFalse(client.states[TARGET.selector].installed)
        self.assertIn(("add", CURRENT.selector), client.calls)
        self.assertEqual(journal["result"]["code"], "LIFECYCLE_CHANGE_ROLLED_BACK")
        self.assert_valid(journal)

    def test_target_health_failure_removes_target_and_restores_previous_release(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            client.unhealthy.add(TARGET.selector)
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000005",
            )
        self.assertEqual(journal["status"], "rolled_back")
        self.assertTrue(client.states[CURRENT.selector].installed)
        self.assertFalse(client.states[TARGET.selector].installed)
        mutations = [call for call in client.calls if call[0] in {"add", "remove"}]
        self.assertEqual(mutations, [
            ("remove", CURRENT.selector),
            ("add", TARGET.selector),
            ("remove", TARGET.selector),
            ("add", CURRENT.selector),
        ])
        self.assert_valid(journal)

    def test_failed_compensation_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            client.fail_add.update({TARGET.selector, CURRENT.selector})
            journal = self._transaction(directory, client).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000006",
            )
        self.assertEqual(journal["status"], "failed")
        self.assertEqual(journal["result"]["code"], "LIFECYCLE_COMPENSATION_FAILED")
        self.assertFalse(journal["finalInstallation"]["installed"])
        self.assert_valid(journal)

    def test_active_run_makes_accepted_plan_stale_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            journal = self._transaction(
                directory, client, active_run_probe=lambda: True,
            ).execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000007",
            )
        self.assertEqual(journal["status"], "rejected")
        self.assertEqual(journal["result"]["code"], "STALE_LIFECYCLE_PLAN")
        self.assertEqual([call for call in client.calls if call[0] in {"add", "remove"}], [])
        self.assert_valid(journal)

    def test_journal_failure_before_mutation_aborts_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            transaction = PluginLifecycleTransaction(
                client, FailingJournalStore(directory),
                direction_verifier=self._direction,
            )
            journal = transaction.execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000008",
            )
        self.assertEqual(journal["status"], "failed")
        self.assertEqual(journal["result"]["code"], "LIFECYCLE_JOURNAL_UNAVAILABLE")
        self.assertEqual([call for call in client.calls if call[0] in {"add", "remove"}], [])
        self.assertNotIn("private path", json.dumps(journal))
        self.assert_valid(journal)

    def test_journal_failure_after_current_removal_triggers_compensation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            transaction = PluginLifecycleTransaction(
                client, DelayedFailingJournalStore(directory, fail_on_call=2),
                direction_verifier=self._direction,
            )
            journal = transaction.execute(
                self.plan, confirmed=True,
                transaction_id="lifecycle-0000000000000000000000000000000b",
            )
        self.assertEqual(journal["status"], "rolled_back")
        self.assertTrue(client.states[CURRENT.selector].installed)
        self.assertFalse(client.states[TARGET.selector].installed)
        self.assertNotIn("private post-mutation", json.dumps(journal))
        self.assert_valid(journal)

    def test_uninstall_is_verified_and_can_compensate(self):
        uninstall = PluginLifecyclePlanner().build("uninstall", CURRENT)
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            transaction = PluginLifecycleTransaction(
                client, LifecycleJournalStore(directory),
            )
            journal = transaction.execute(
                uninstall, confirmed=True,
                transaction_id="lifecycle-00000000000000000000000000000009",
            )
        self.assertEqual(journal["status"], "completed")
        self.assertFalse(journal["finalInstallation"]["installed"])
        self.assert_valid(journal)

    def test_plan_content_cannot_be_changed_after_acceptance(self):
        modified = deepcopy(self.plan)
        modified["nextAction"] = "Different accepted text."
        with tempfile.TemporaryDirectory() as directory:
            client = FakeLifecycleClient()
            journal = self._transaction(directory, client).execute(
                modified, confirmed=True,
                transaction_id="lifecycle-0000000000000000000000000000000a",
            )
        self.assertEqual(journal["status"], "rejected")
        self.assertEqual(journal["result"]["code"], "STALE_LIFECYCLE_PLAN")
        self.assert_valid(journal)


if __name__ == "__main__":
    unittest.main()
