import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from assayer_host import cli
from assayer_host.readiness import ReadinessCheck, ReadinessReport
from assayer_platform import PluginRegistry
from tests.helpers import config_quality_registration


class CliTest(unittest.TestCase):
    @staticmethod
    def _doctor_report(*, ready: bool) -> ReadinessReport:
        runtime = ReadinessCheck(
            "private_runtime",
            "ok" if ready else "missing",
            True,
            "runtime ready" if ready else "runtime missing",
            None if ready else "Run: assayer doctor --fix",
        )
        return ReadinessReport(
            None,
            "unknown",
            (
                ReadinessCheck("assayer_bundle", "ok", True, "bundle ready"),
                runtime,
            ),
        )

    def test_doctor_fix_prepares_runtime_and_rechecks_readiness(self):
        before = self._doctor_report(ready=False)
        after = self._doctor_report(ready=True)
        output = io.StringIO()
        with patch("assayer_host.cli.collect_readiness", side_effect=[before, after]) as collect, \
             patch("assayer_host.cli._prepare_private_runtime", return_value=(True, None)) as prepare, \
             redirect_stdout(output):
            result = cli.main(["doctor", "--fix", "--plugin-root", "/tmp/assayer-plugin"])

        self.assertEqual(result, 0)
        prepare.assert_called_once_with(Path("/tmp/assayer-plugin").resolve())
        self.assertEqual(collect.call_count, 2)
        self.assertIn("private_runtime: ok", output.getvalue())

    def test_doctor_fix_does_not_prepare_an_already_ready_runtime(self):
        output = io.StringIO()
        with patch("assayer_host.cli.collect_readiness", return_value=self._doctor_report(ready=True)), \
             patch("assayer_host.cli._prepare_private_runtime") as prepare, \
             redirect_stdout(output):
            result = cli.main(["doctor", "--fix", "--plugin-root", "/tmp/assayer-plugin"])

        self.assertEqual(result, 0)
        prepare.assert_not_called()

    def test_doctor_fix_reports_a_stable_bounded_failure(self):
        output = io.StringIO()
        report = self._doctor_report(ready=False)
        with patch("assayer_host.cli.collect_readiness", return_value=report), \
             patch(
                 "assayer_host.cli._prepare_private_runtime",
                 return_value=(False, "ASSAYER_RUNTIME_INSTALL_FAILED: bundled dependencies failed"),
             ), redirect_stdout(output):
            result = cli.main([
                "doctor", "--fix", "--json", "--plugin-root", "/tmp/assayer-plugin",
            ])

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(payload["repair"]["code"], "RUNTIME_PREPARE_FAILED")
        self.assertEqual(payload["repair"]["status"], "failed")
        self.assertNotIn("Traceback", output.getvalue())

    def test_runtime_preparer_runs_the_explicit_script_without_a_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preparer = root / "scripts" / "prepare_assayer_runtime"
            preparer.parent.mkdir()
            preparer.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            completed = Mock(returncode=0, stdout="", stderr="")
            with patch(
                "assayer_host.cli.collect_readiness",
                side_effect=[
                    self._doctor_report(ready=False),
                    self._doctor_report(ready=True),
                ],
            ), patch(
                "assayer_host.cli.subprocess.run", return_value=completed,
            ) as run, redirect_stdout(io.StringIO()):
                result = cli.main([
                    "doctor", "--fix", "--json", "--plugin-root", str(root),
                ])

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.args[0], [str(preparer.resolve())])
        self.assertEqual(run.call_args.kwargs["timeout"], 600.0)
        self.assertNotIn("env", run.call_args.kwargs)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_doctor_resolves_the_latest_installed_codex_plugin_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            older = codex_home / "plugins/cache/personal/assayer/0.1.0"
            newer = codex_home / "plugins/cache/personal/assayer/0.1.1"
            for root in (older, newer):
                manifest = root / ".codex-plugin/plugin.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"name":"assayer","version":"0.1.0"}', encoding="utf-8")
            older.touch()
            newer.touch()
            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False), \
                 patch("assayer_host.cli.Path.home", return_value=Path(directory) / "home"), \
                 patch("assayer_host.cli.collect_readiness", return_value=self._doctor_report(ready=True)) as collect, \
                 redirect_stdout(io.StringIO()):
                cli.main(["doctor", "--json"])

        self.assertEqual(collect.call_args.kwargs["plugin_root"], newer.resolve())

    def test_doctor_prefers_personal_install_over_a_newer_test_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            personal = codex_home / "plugins/cache/personal/assayer/0.1.0"
            test_profile = codex_home / "plugins/cache/assayer-clean/assayer/9.9.9"
            for root in (personal, test_profile):
                manifest = root / ".codex-plugin/plugin.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"name":"assayer","version":"0.1.0"}', encoding="utf-8")
            test_profile.touch()
            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False), \
                 patch("assayer_host.cli.Path.home", return_value=Path(directory) / "home"), \
                 patch("assayer_host.cli.collect_readiness", return_value=self._doctor_report(ready=True)) as collect, \
                 redirect_stdout(io.StringIO()):
                cli.main(["doctor", "--json"])

        self.assertEqual(collect.call_args.kwargs["plugin_root"], personal.resolve())

    def test_explicit_plugin_root_environment_wins_over_codex_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = Path(directory) / "explicit"
            with patch.dict("os.environ", {"ASSAYER_PLUGIN_ROOT": str(explicit)}, clear=False), \
                 patch("assayer_host.cli.collect_readiness", return_value=self._doctor_report(ready=True)) as collect, \
                 redirect_stdout(io.StringIO()):
                cli.main(["doctor", "--json"])
        self.assertEqual(collect.call_args.kwargs["plugin_root"], explicit.resolve())

    def test_default_help_only_advertises_public_first_use_commands(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            cli.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = output.getvalue()
        self.assertIn("{audit,doctor}", help_text)
        self.assertIn("audit", help_text)
        self.assertIn("doctor", help_text)
        self.assertNotIn("smoke", help_text)
        self.assertNotIn("serve", help_text)
        self.assertNotIn("plugins", help_text)

    def test_hidden_advanced_command_remains_callable(self):
        runtime = Mock()
        runtime.smoke.return_value = {"status": "ok"}
        with patch("assayer_host.cli.BrowserHostRuntime", return_value=runtime), \
             redirect_stdout(io.StringIO()):
            result = cli.main(["smoke", "https://test.example.com"])
        self.assertEqual(result, 0)
        runtime.smoke.assert_called_once()

    def test_plugin_verify_runs_the_single_deterministic_pipeline(self):
        report = {
            "schemaVersion": "1.0.0", "status": "passed",
            "wheel": "/tmp/verified/plugin.whl", "sha256": "a" * 64,
        }
        output = io.StringIO()
        with patch("assayer_host.cli.verify_simple_plugin", return_value=report) as verify, \
             redirect_stdout(output):
            result = cli.main([
                "plugin", "verify", "./policy",
                "--output-dir", "./verified",
            ])

        self.assertEqual(result, 0)
        verify.assert_called_once_with(
            "./policy", output_dir="./verified",
            build_timeout_seconds=120,
            install_timeout_seconds=120,
            fixture_timeout_seconds=300,
        )
        self.assertEqual(json.loads(output.getvalue()), report)

    def test_audit_launches_codex_with_dynamic_mcp_and_never_calls_smoke(self):
        completed = Mock(returncode=7)
        with patch("assayer_host.cli.shutil.which", return_value="/usr/local/bin/codex"), \
             patch("assayer_host.cli.subprocess.run", return_value=completed) as run, \
             patch("assayer_host.cli.BrowserHostRuntime") as browser_runtime:
            result = cli.main(["audit", "https://test.example.com", "--output-root", "/tmp/assayer-output"])
        self.assertEqual(result, 7)
        browser_runtime.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/local/bin/codex", "exec"])
        self.assertIn("--approve-for-me", command)
        self.assertIn("mcp_servers.assayer.command", " ".join(command))
        self.assertIn("--mcp", " ".join(command))
        self.assertIn("$assayer-audit", command[-1])
        self.assertIn("https://test.example.com", command[-1])
        self.assertNotIn("outputDir=auto", command[-1])
        self.assertNotIn("protocolVersion", command[-1])

    def test_markdown_audit_routes_to_installed_ass_spec_without_browser_language(self):
        completed = Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "spec.md"
            target.write_text("# Spec\n", encoding="utf-8")
            with patch("assayer_host.cli.shutil.which", return_value="/usr/local/bin/codex"), \
                 patch("assayer_host.cli.subprocess.run", return_value=completed) as run:
                result = cli.main(["audit", str(target)])

        self.assertEqual(result, 0)
        prompt = run.call_args.args[0][-1]
        self.assertIn("$assayer-plugin", prompt)
        self.assertIn("ass-spec", prompt)
        self.assertIn(str(target.resolve()), prompt)
        self.assertIn("do not start Chromium", prompt)
        self.assertNotIn("$assayer-audit", prompt)
        self.assertNotIn("web URL", prompt)
        command = run.call_args.args[0]
        rendered = " ".join(command)
        self.assertIn("--approve-for-me", command)
        self.assertIn("mcp_servers.assayer.command", rendered)
        self.assertIn("assayer-mcp", rendered)
        self.assertIn("--store", rendered)
        self.assertNotIn("assayer_host.transport", rendered)
        self.assertNotIn("--mcp", rendered)

    def test_file_audit_accepts_an_explicit_domain_plugin(self):
        completed = Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "policy.yaml"
            target.write_text("enabled: true\n", encoding="utf-8")
            with patch("assayer_host.cli.shutil.which", return_value="/usr/local/bin/codex"), \
                 patch("assayer_host.cli.subprocess.run", return_value=completed) as run:
                result = cli.main([
                    "audit", str(target), "--plugin", "policy-review",
                ])

        self.assertEqual(result, 0)
        prompt = run.call_args.args[0][-1]
        self.assertIn("$assayer-plugin", prompt)
        self.assertIn("installed policy-review plugin", prompt)

    def test_audit_fails_explicitly_when_codex_is_unavailable(self):
        output = io.StringIO()
        with patch("assayer_host.cli.shutil.which", return_value=None), redirect_stdout(output), \
             patch("assayer_host.cli.BrowserHostRuntime") as browser_runtime:
            result = cli.main(["audit", "https://test.example.com"])
        self.assertEqual(result, 2)
        self.assertIn("AGENT_RUNTIME_UNAVAILABLE", output.getvalue())
        self.assertIn("will not fall back to smoke", output.getvalue())
        browser_runtime.assert_not_called()

    def test_smoke_retains_deterministic_runtime_under_explicit_name(self):
        runtime = Mock()
        runtime.smoke.return_value = {"status": "ok"}
        output = io.StringIO()
        with patch("assayer_host.cli.BrowserHostRuntime", return_value=runtime), redirect_stdout(output):
            result = cli.main(["smoke", "https://test.example.com", "--output-dir", "/tmp/smoke"])
        self.assertEqual(result, 0)
        runtime.smoke.assert_called_once_with("https://test.example.com")
        runtime.close.assert_called_once()

    def test_plugins_list_reports_registered_platform_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()

            with redirect_stdout(output):
                result = cli.main(["plugins", "list", "--json", "--store", directory])

            self.assertEqual(result, 0)
            catalog = __import__("json").loads(output.getvalue())["plugins"]
            self.assertEqual(
                [item["pluginId"] for item in catalog],
                ["assayer.frontend-audit"],
            )
            self.assertEqual(
                catalog[0]["checks"],
                [{"checkId": "FUA-10", "version": "1.0.0"}],
            )
            self.assertEqual(catalog[0]["platformApiVersion"], "1.0.0")
            self.assertEqual(catalog[0]["capabilities"], ["browser_snapshot"])
            self.assertEqual(catalog[0]["executionModes"], ["interactive"])
            self.assertEqual(catalog[0]["scopeSchema"]["required"], ["url"])
            self.assertFalse(catalog[0]["supportsCommit"])

    def test_registered_non_browser_plugin_runs_through_generic_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "settings.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            scope = json.dumps({
                "files": [{
                    "path": str(source),
                    "requiredKeys": ["enabled"],
                    "expectedTypes": {"enabled": "boolean"},
                }]
            })
            output = io.StringIO()
            registry = PluginRegistry((config_quality_registration(),))

            with redirect_stdout(output), \
                 patch("assayer_host.plugin_lifecycle_ops.installed_plugin_registry", return_value=registry), \
                 patch("assayer_host.cli.BrowserHostRuntime") as browser_runtime:
                result = cli.main([
                    "plugins", "run", "--plugin", "test.config-quality",
                    "--check", "CFG-001", "--scope-json", scope,
                    "--output-root", str(root / "output"),
                ])

            payload = json.loads(output.getvalue())
            run_root = Path(payload["outputDir"])
            self.assertEqual(result, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["decisions"], ["scanned_no_issue"])
            self.assertTrue((run_root / f"{payload['runId']}.platform-ledger.json").is_file())
            self.assertTrue((run_root / f"{payload['runId']}.platform-summary.json").is_file())
            browser_runtime.assert_not_called()

    def test_generic_plugin_cli_rejects_unknown_plugin_without_browser(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("assayer_host.cli.BrowserHostRuntime") as browser_runtime:
            result = cli.main([
                "plugins", "run", "--plugin", "missing.plugin",
                "--check", "CFG-001", "--scope-json", "{}",
            ])
        self.assertEqual(result, 2)
        self.assertIn("UNKNOWN_PLUGIN", output.getvalue())
        browser_runtime.assert_not_called()

    def test_generic_plugin_cli_rejects_interactive_plugin_without_browser(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("assayer_host.cli.BrowserHostRuntime") as browser_runtime:
            result = cli.main([
                "plugins", "run", "--plugin", "assayer.frontend-audit",
                "--check", "FUA-10", "--scope-json", "{}",
            ])
        self.assertEqual(result, 2)
        self.assertIn("PLUGIN_EXECUTION_MODE_UNSUPPORTED", output.getvalue())
        browser_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
