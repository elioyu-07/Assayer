from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from assayer_host import HostError
from assayer_host.lifecycle_product import (
    LifecyclePlanStore,
    LifecycleProductController,
    LifecycleProductToolTransport,
)
from assayer_host.lifecycle_transaction import LifecycleJournalStore, PluginLifecycleTransaction
from assayer_host.release_lifecycle import PluginInstallation, PluginLifecyclePlanner


CURRENT = PluginInstallation(
    "assayer@release-010", "0.1.0", True, True, True, True, True,
)
TARGET = PluginInstallation(
    "assayer@release-011", "0.1.1", False, False, True, True, True,
)


def controller_plan():
    return PluginLifecyclePlanner().build(
        "upgrade", CURRENT, target=TARGET, active_run=False,
        release_direction_verified=True,
    )


def blocked_controller_plan():
    return PluginLifecyclePlanner().build(
        "upgrade", CURRENT, target=None, active_run=False,
        release_direction_verified=False,
    )


class FakeClient:
    def __init__(self):
        self.states = {CURRENT.selector: CURRENT, TARGET.selector: TARGET}
        self.mutations = []

    def snapshot(self, selector):
        return self.states[selector]

    def add(self, selector):
        self.mutations.append(("add", selector))
        state = self.states[selector]
        self.states[selector] = PluginInstallation(
            selector, state.version, True, True, True, True, True,
        )

    def remove(self, selector):
        self.mutations.append(("remove", selector))
        state = self.states[selector]
        self.states[selector] = PluginInstallation(
            selector, state.version, False, False, True, True, True,
        )

    def verify_installation(self, installation):
        return installation.installed and installation.enabled


class LifecycleProductTests(unittest.TestCase):
    def _controller(self, directory, client, clock, *, authorizer=None, active=lambda: False):
        store = LifecyclePlanStore(Path(directory) / "plans.sqlite3", clock=clock)

        def transaction_factory():
            return PluginLifecycleTransaction(
                client,
                LifecycleJournalStore(Path(directory) / "transactions"),
                active_run_probe=active,
                direction_verifier=lambda operation, current, target: (
                    operation == "upgrade" and current.version == "0.1.0" and target.version == "0.1.1"
                ),
            )

        journal_store = LifecycleJournalStore(Path(directory) / "transactions")

        return LifecycleProductController(
            client, store, transaction_factory,
            current_selector=CURRENT.selector,
            target_resolver=lambda operation, current: TARGET if operation == "upgrade" else None,
            direction_verifier=lambda operation, current, target: (
                operation == "upgrade" and current.version == "0.1.0" and target.version == "0.1.1"
            ),
            active_run_probe=active,
            external_authorizer=authorizer,
            transaction_loader=journal_store.load,
            token_ttl_seconds=60,
        ), store

    def test_plan_issues_bounded_token_only_for_ready_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            client = FakeClient()
            controller, store = self._controller(directory, client, lambda: now[0])
            result = controller.plan("upgrade")
            record = store.read(result["planToken"])
        self.assertEqual(result["phase"], "awaiting_confirmation")
        self.assertRegex(result["planToken"], r"^lifecycle-token-[0-9a-f]{64}$")
        self.assertEqual(result["expiresAt"], 1060)
        self.assertEqual(record["state"], "awaiting_confirmation")

    def test_raw_token_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, _ = self._controller(directory, client, lambda: 1000)
            result = controller.plan("upgrade")
            database = sqlite3.connect(Path(directory) / "plans.sqlite3")
            row = database.execute("SELECT token_digest FROM lifecycle_plan_token").fetchone()
            database.close()
            raw = (Path(directory) / "plans.sqlite3").read_bytes()
        self.assertNotEqual(row[0], result["planToken"])
        self.assertNotIn(result["planToken"].encode(), raw)

    def test_confirmation_without_external_authorization_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, store = self._controller(directory, client, lambda: 1000)
            planned = controller.plan("upgrade")
            result = controller.execute(planned["planToken"], confirmed=True)
            record = store.read(planned["planToken"])
        self.assertEqual(result["phase"], "authorization_required")
        self.assertEqual(client.mutations, [])
        self.assertEqual(record["state"], "awaiting_confirmation")

    def test_external_authorizer_failure_fails_closed_without_claiming_token(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()

            def unavailable(reference, plan):
                raise RuntimeError("private authorization failure")

            controller, store = self._controller(
                directory, client, lambda: 1000, authorizer=unavailable,
            )
            planned = controller.plan("upgrade")
            result = controller.execute(planned["planToken"], confirmed=True)
            record = store.read(planned["planToken"])
        self.assertEqual(result["phase"], "authorization_required")
        self.assertEqual(record["state"], "awaiting_confirmation")
        self.assertEqual(client.mutations, [])

    def test_external_authorization_receives_reference_not_bearer_token(self):
        seen = []
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, _ = self._controller(
                directory, client, lambda: 1000,
                authorizer=lambda reference, plan: seen.append((reference, plan["operation"])) or True,
            )
            planned = controller.plan("upgrade")
            result = controller.execute(planned["planToken"], confirmed=True)
        self.assertEqual(result["phase"], "terminal")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["transaction"]["status"], "completed")
        self.assertEqual(seen[0][1], "upgrade")
        self.assertNotEqual(seen[0][0], planned["planToken"])

    def test_terminal_retry_replays_without_authorization_or_mutation(self):
        authorizations = []
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, _ = self._controller(
                directory, client, lambda: 1000,
                authorizer=lambda reference, plan: authorizations.append(reference) or True,
            )
            planned = controller.plan("upgrade")
            first = controller.execute(planned["planToken"], confirmed=True)
            mutations = list(client.mutations)
            second = controller.execute(planned["planToken"], confirmed=True)
        self.assertEqual(first["transaction"], second["transaction"])
        self.assertTrue(second["replayed"])
        self.assertEqual(client.mutations, mutations)
        self.assertEqual(len(authorizations), 1)

    def test_expired_token_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            client = FakeClient()
            controller, _ = self._controller(
                directory, client, lambda: now[0],
                authorizer=lambda reference, plan: True,
            )
            planned = controller.plan("upgrade")
            now[0] = planned["expiresAt"]
            result = controller.execute(planned["planToken"], confirmed=True)
        self.assertEqual(result["phase"], "expired")
        self.assertEqual(client.mutations, [])

    def test_plan_blocked_by_active_run_has_no_token(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, _ = self._controller(
                directory, client, lambda: 1000, active=lambda: True,
            )
            result = controller.plan("upgrade")
        self.assertEqual(result["phase"], "planned")
        self.assertEqual(result["plan"]["status"], "blocked")
        self.assertIsNone(result["planToken"])

    def test_concurrent_or_interrupted_claim_returns_result_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, store = self._controller(
                directory, client, lambda: 1000,
                authorizer=lambda reference, plan: True,
            )
            planned = controller.plan("upgrade")
            claim = store.claim(planned["planToken"])
            self.assertEqual(claim["claimStatus"], "claimed")
            result = controller.execute(planned["planToken"], confirmed=True)
        self.assertEqual(result["phase"], "result_unknown")
        self.assertEqual(client.mutations, [])

    def test_two_store_instances_cannot_claim_the_same_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plans.sqlite3"
            first = LifecyclePlanStore(path, clock=lambda: 1000)
            second = LifecyclePlanStore(path, clock=lambda: 1000)
            token = first.issue(controller_plan(), ttl_seconds=60)["planToken"]
            barrier = threading.Barrier(2)
            results = []

            def claim(store):
                barrier.wait()
                results.append(store.claim(token)["claimStatus"])

            workers = [threading.Thread(target=claim, args=(store,)) for store in (first, second)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        self.assertEqual(sorted(results), ["claimed", "executing"])

    def test_executing_token_repairs_from_terminal_transaction_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, store = self._controller(
                directory, client, lambda: 1000,
                authorizer=lambda reference, plan: True,
            )
            planned = controller.plan("upgrade")
            claim = store.claim(planned["planToken"])
            transaction_id = controller._transaction_id(planned["planToken"])
            transaction = controller._transaction_factory().execute(
                claim["plan"], confirmed=True, transaction_id=transaction_id,
            )
            result = controller.execute(planned["planToken"], confirmed=True)
        self.assertEqual(result["phase"], "terminal")
        self.assertTrue(result["replayed"])
        self.assertEqual(result["transaction"], transaction)

    def test_invalid_token_is_rejected_before_database_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            controller, _ = self._controller(directory, client, lambda: 1000)
            with self.assertRaises(ValueError):
                controller.execute("../../private", confirmed=True)

    def test_tool_transport_separates_plan_and_destructive_execute_metadata(self):
        class RecordingController:
            def __init__(self):
                self.calls = []

            def plan(self, operation):
                self.calls.append(("plan", operation))
                return {
                    "schemaVersion": "1.0.0", "phase": "planned",
                    "plan": blocked_controller_plan(), "planToken": None, "expiresAt": None,
                }

            def execute(self, token, *, confirmed):
                self.calls.append(("execute", token, confirmed))
                return {
                    "schemaVersion": "1.0.0", "phase": "authorization_required",
                    "replayed": False,
                    "result": {
                        "code": "EXTERNAL_AUTHORIZATION_REQUIRED",
                        "message": "Trusted external authorization is required.",
                    },
                }

        controller = RecordingController()
        transport = LifecycleProductToolTransport(controller)
        tools = {item["name"]: item for item in transport.list_tools()}
        self.assertFalse(tools["plan_plugin_change"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["execute_plugin_change"]["annotations"]["destructiveHint"])
        token = "lifecycle-token-" + "a" * 64
        planned = transport.call_tool("plan_plugin_change", {"operation": "upgrade"})
        executed = transport.call_tool(
            "execute_plugin_change", {"planToken": token, "confirmed": True},
        )
        self.assertEqual(planned["structuredContent"]["result"]["phase"], "planned")
        self.assertEqual(executed["structuredContent"]["result"]["phase"], "authorization_required")
        self.assertEqual(controller.calls, [("plan", "upgrade"), ("execute", token, True)])

    def test_tool_transport_rejects_false_confirmation_and_extra_fields(self):
        transport = LifecycleProductToolTransport(object())
        with self.assertRaises(HostError) as false_confirmation:
            transport.call_tool("execute_plugin_change", {
                "planToken": "lifecycle-token-" + "a" * 64,
                "confirmed": False,
            })
        self.assertEqual(false_confirmation.exception.code, "INVALID_REQUEST")
        with self.assertRaises(HostError) as extra:
            transport.call_tool("plan_plugin_change", {
                "operation": "upgrade", "command": "arbitrary",
            })
        self.assertEqual(extra.exception.code, "INVALID_REQUEST")

    def test_tool_transport_rejects_controller_output_outside_product_schema(self):
        class InvalidController:
            def plan(self, operation):
                return {"phase": "planned", "privatePath": "/private/release"}

        transport = LifecycleProductToolTransport(InvalidController())
        with self.assertRaises(HostError) as error:
            transport.call_tool("plan_plugin_change", {"operation": "upgrade"})
        self.assertEqual(error.exception.code, "INTERNAL_FAILURE")
        self.assertNotIn("private", error.exception.message)


if __name__ == "__main__":
    unittest.main()
