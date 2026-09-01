import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from assayer_host import cli


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
        self.assertIn("outputDir=auto", command[-1])

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


if __name__ == "__main__":
    unittest.main()
