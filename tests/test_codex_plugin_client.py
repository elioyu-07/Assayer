from __future__ import annotations

import json
import subprocess
import tempfile
import unittest

from assayer_host.codex_plugin_client import (
    CodexCommandResult,
    CodexPluginClient,
    ReleaseAttestation,
    SubprocessCodexCommandRunner,
)
from assayer_host.lifecycle_transaction import LifecycleCommandError
from assayer_host.lifecycle_transaction import LifecycleJournalStore, PluginLifecycleTransaction
from assayer_host.release_lifecycle import PluginLifecyclePlanner


CURRENT = "assayer@release-010"
TARGET = "assayer@release-011"


class FakeCodexRunner:
    def __init__(self, payload=None):
        self.payload = payload or {"installed": [], "available": []}
        self.calls = []
        self.responses = []

    def run(self, arguments, *, timeout_seconds):
        self.calls.append((tuple(arguments), timeout_seconds))
        if self.responses:
            return self.responses.pop(0)
        return CodexCommandResult(0, json.dumps(self.payload).encode())


class StatefulCodexRunner:
    def __init__(self):
        self.installed = {CURRENT: {"pluginId": CURRENT, "version": "0.1.0", "enabled": True}}
        self.available = {
            CURRENT: {"pluginId": CURRENT, "version": "0.1.0"},
            TARGET: {"pluginId": TARGET, "version": "0.1.1"},
        }
        self.calls = []

    def run(self, arguments, *, timeout_seconds):
        del timeout_seconds
        command = tuple(arguments)
        self.calls.append(command)
        if command == ("plugin", "list", "--json"):
            payload = {"installed": list(self.installed.values()), "available": list(self.available.values())}
        elif command[:2] == ("plugin", "add") and command[-1] == "--json":
            selector = command[2]
            source = self.available[selector]
            self.installed[selector] = {**source, "enabled": True}
            payload = {"status": "installed", "pluginId": selector}
        elif command[:2] == ("plugin", "remove") and command[-1] == "--json":
            selector = command[2]
            self.installed.pop(selector, None)
            payload = {"status": "removed", "pluginId": selector}
        else:
            return CodexCommandResult(2, b"{}", b"unexpected private command")
        return CodexCommandResult(0, json.dumps(payload).encode())


def attest(selector, version):
    versions = {CURRENT: "0.1.0", TARGET: "0.1.1"}
    expected = versions.get(selector)
    if expected is None or version not in {None, expected}:
        return None
    return ReleaseAttestation(selector, expected, True, True, True)


class CodexPluginClientTests(unittest.TestCase):
    def client(self, runner, health=lambda installation: True):
        return CodexPluginClient(
            runner, release_attestation=attest, health_verifier=health,
            read_timeout_seconds=7, mutation_timeout_seconds=11,
        )

    def test_snapshot_reads_exact_installed_selector_and_attestation(self):
        runner = FakeCodexRunner({
            "installed": [{
                "pluginId": CURRENT, "version": "0.1.0",
                "installed": True, "enabled": True,
                "source": {"path": "/private/not-public"},
            }],
            "available": [],
        })
        state = self.client(runner).snapshot(CURRENT)
        self.assertTrue(state.installed)
        self.assertTrue(state.enabled)
        self.assertTrue(state.available)
        self.assertTrue(state.release_verified)
        self.assertTrue(state.immutable_release)
        self.assertEqual(runner.calls, [(('plugin', 'list', '--json'), 7)])
        self.assertNotIn("private", json.dumps(state.as_dict()))

    def test_available_target_is_not_treated_as_installed(self):
        runner = FakeCodexRunner({
            "installed": [],
            "available": [{"pluginId": TARGET, "version": "0.1.1"}],
        })
        state = self.client(runner).snapshot(TARGET)
        self.assertFalse(state.installed)
        self.assertFalse(state.enabled)
        self.assertTrue(state.available)
        self.assertTrue(state.release_verified)

    def test_missing_attestation_fails_closed(self):
        runner = FakeCodexRunner({
            "installed": [{"pluginId": "assayer@unknown", "version": "0.9.0", "enabled": True}],
            "available": [],
        })
        client = CodexPluginClient(
            runner, release_attestation=lambda selector, version: None,
            health_verifier=lambda installation: True,
        )
        state = client.snapshot("assayer@unknown")
        self.assertTrue(state.installed)
        self.assertFalse(state.release_verified)
        self.assertFalse(state.immutable_release)
        self.assertFalse(client.verify_installation(state))

    def test_add_and_remove_use_only_fixed_codex_json_commands(self):
        runner = FakeCodexRunner()
        client = self.client(runner)
        client.add(TARGET)
        client.remove(CURRENT)
        self.assertEqual(runner.calls, [
            (("plugin", "add", TARGET, "--json"), 11),
            (("plugin", "remove", CURRENT, "--json"), 11),
        ])

    def test_unsafe_selector_never_reaches_runner(self):
        runner = FakeCodexRunner()
        client = self.client(runner)
        with self.assertRaises(ValueError):
            client.add("../../private")
        self.assertEqual(runner.calls, [])

    def test_nonzero_command_hides_stderr_and_stdout(self):
        runner = FakeCodexRunner()
        runner.responses.append(CodexCommandResult(
            1, b'{"secret":"stdout-token"}', b"stderr-token /private/path",
        ))
        with self.assertRaises(LifecycleCommandError) as error:
            self.client(runner).add(TARGET)
        self.assertEqual(error.exception.code, "CODEX_PLUGIN_ADD_FAILED")
        self.assertNotIn("token", error.exception.message)
        self.assertNotIn("private", error.exception.message)

    def test_invalid_and_oversized_json_are_rejected(self):
        runner = FakeCodexRunner()
        runner.responses.extend([
            CodexCommandResult(0, b"not-json"),
            CodexCommandResult(0, b"{" + b"x" * (2 * 1024 * 1024) + b"}"),
        ])
        client = self.client(runner)
        with self.assertRaises(LifecycleCommandError) as invalid:
            client.snapshot(CURRENT)
        self.assertEqual(invalid.exception.code, "CODEX_PLUGIN_OUTPUT_INVALID")
        with self.assertRaises(LifecycleCommandError) as oversized:
            client.snapshot(CURRENT)
        self.assertEqual(oversized.exception.code, "CODEX_PLUGIN_OUTPUT_TOO_LARGE")

    def test_duplicate_selector_is_rejected_as_ambiguous(self):
        entry = {"pluginId": CURRENT, "version": "0.1.0", "enabled": True}
        runner = FakeCodexRunner({"installed": [entry, entry], "available": []})
        with self.assertRaises(LifecycleCommandError) as error:
            self.client(runner).snapshot(CURRENT)
        self.assertEqual(error.exception.code, "CODEX_PLUGIN_CATALOG_AMBIGUOUS")

    def test_missing_catalog_lists_fail_closed(self):
        runner = FakeCodexRunner({"installed": []})
        with self.assertRaises(LifecycleCommandError) as error:
            self.client(runner).snapshot(CURRENT)
        self.assertEqual(error.exception.code, "CODEX_PLUGIN_CATALOG_INVALID")

    def test_verify_installation_rechecks_catalog_and_health(self):
        runner = FakeCodexRunner({
            "installed": [{"pluginId": CURRENT, "version": "0.1.0", "enabled": True}],
            "available": [],
        })
        seen = []
        client = self.client(runner, health=lambda installation: seen.append(installation.selector) or True)
        state = client.snapshot(CURRENT)
        self.assertTrue(client.verify_installation(state))
        self.assertEqual(seen, [CURRENT])
        self.assertEqual(len(runner.calls), 2)

    def test_health_verifier_exception_fails_closed(self):
        runner = FakeCodexRunner({
            "installed": [{"pluginId": CURRENT, "version": "0.1.0", "enabled": True}],
            "available": [],
        })

        def broken(installation):
            raise RuntimeError("secret health details")

        client = self.client(runner, health=broken)
        state = client.snapshot(CURRENT)
        self.assertFalse(client.verify_installation(state))

    def test_subprocess_runner_uses_no_shell_and_bounded_timeout(self):
        recorded = {}
        original = subprocess.run

        def fake_run(command, **kwargs):
            recorded["command"] = command
            recorded.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=b"{}", stderr=b"")

        subprocess.run = fake_run
        try:
            result = SubprocessCodexCommandRunner(
                executable="codex-test", environment={"TEST": "1"},
            ).run(("plugin", "list", "--json"), timeout_seconds=9)
        finally:
            subprocess.run = original
        self.assertEqual(result.return_code, 0)
        self.assertEqual(recorded["command"], ["codex-test", "plugin", "list", "--json"])
        self.assertNotIn("shell", recorded)
        self.assertEqual(recorded["timeout"], 9)
        self.assertEqual(recorded["env"], {"TEST": "1"})
        self.assertIs(recorded["stdin"], subprocess.DEVNULL)

    def test_subprocess_timeout_returns_only_safe_error(self):
        original = subprocess.run

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("secret-command", 1, output=b"token")

        subprocess.run = timeout
        try:
            with self.assertRaises(LifecycleCommandError) as error:
                SubprocessCodexCommandRunner().run(
                    ("plugin", "list", "--json"), timeout_seconds=1,
                )
        finally:
            subprocess.run = original
        self.assertEqual(error.exception.code, "CODEX_PLUGIN_COMMAND_TIMEOUT")
        self.assertNotIn("secret", error.exception.message)

    def test_concrete_adapter_closes_transaction_against_stateful_codex_contract(self):
        runner = StatefulCodexRunner()
        client = self.client(runner)
        current = client.snapshot(CURRENT)
        target = client.snapshot(TARGET)
        plan = PluginLifecyclePlanner().build(
            "upgrade", current, target=target, release_direction_verified=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            transaction = PluginLifecycleTransaction(
                client, LifecycleJournalStore(directory),
                direction_verifier=lambda operation, old, new: (
                    operation == "upgrade" and old.version == "0.1.0" and new.version == "0.1.1"
                ),
            )
            journal = transaction.execute(
                plan, confirmed=True,
                transaction_id="lifecycle-0000000000000000000000000000000c",
            )
        self.assertEqual(journal["status"], "completed")
        self.assertNotIn(CURRENT, runner.installed)
        self.assertIn(TARGET, runner.installed)
        mutations = [call for call in runner.calls if call[:2] != ("plugin", "list")]
        self.assertEqual(mutations, [
            ("plugin", "remove", CURRENT, "--json"),
            ("plugin", "add", TARGET, "--json"),
        ])


if __name__ == "__main__":
    unittest.main()
