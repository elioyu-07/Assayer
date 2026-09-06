import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from assayer_host import cli
from assayer_platform import PluginRegistry
from tests.helpers import config_quality_registration


class CliTest(unittest.TestCase):
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
        self.assertIn("mcp_servers.assayer.command", " ".join(command))
        self.assertIn("--mcp", " ".join(command))
        self.assertIn("$assayer-audit", command[-1])
        self.assertIn("https://test.example.com", command[-1])
        self.assertNotIn("outputDir=auto", command[-1])
        self.assertNotIn("protocolVersion", command[-1])

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
        output = io.StringIO()

        with redirect_stdout(output):
            result = cli.main(["plugins", "list", "--json"])

        self.assertEqual(result, 0)
        catalog = __import__("json").loads(output.getvalue())["plugins"]
        self.assertEqual(
            [item["pluginId"] for item in catalog],
            ["assayer.frontend-audit"],
        )
        self.assertEqual(catalog[0]["checks"], [{"checkId": "FUA-10", "version": "1.1.0"}])
        self.assertEqual(catalog[0]["platformApiVersion"], "1.0.0")
        self.assertIn("visual_read", catalog[0]["capabilities"])
        self.assertEqual(catalog[0]["executionModes"], ["interactive"])
        self.assertEqual(catalog[0]["scopeSchema"]["required"], ["url"])
        self.assertTrue(catalog[0]["supportsCommit"])

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
                 patch("assayer_host.cli.installed_plugin_registry", return_value=registry), \
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
