import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CodexIntegrationConfigTest(unittest.TestCase):
    def test_project_config_does_not_reintroduce_legacy_assayer_mcp(self):
        config_path = ROOT / ".codex" / "config.toml"
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)

        self.assertNotIn("assayer", config.get("mcp_servers", {}))

    def test_plugin_is_the_cli_mcp_source(self):
        manifest = json.loads(
            (ROOT / "plugins" / "assayer" / ".codex-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")



if __name__ == "__main__":
    unittest.main()
