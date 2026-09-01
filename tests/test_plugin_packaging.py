from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "assayer"


class PluginPackagingContractTests(unittest.TestCase):
    def test_plugin_and_python_distribution_versions_match(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(project["version"], manifest["version"])

    def test_plugin_mcp_uses_only_relocatable_paths(self):
        config = json.loads((PLUGIN / ".mcp.json").read_text())
        server = config["mcpServers"]["assayer"]
        self.assertEqual(server["command"], "./scripts/launch_assayer_mcp")
        self.assertEqual(server["cwd"], ".")
        self.assertNotIn(str(ROOT), json.dumps(config))
        self.assertTrue((PLUGIN / "scripts" / "launch_assayer_mcp").is_file())
        self.assertTrue((PLUGIN / "skills" / "assayer-audit" / "SKILL.md").is_file())

    def test_assayer_skill_declares_local_mcp_dependency_for_cli_discovery(self):
        # Keep the packaging gate dependency-light; the plugin validator owns
        # full YAML parsing and this test only checks the stable contract keys.
        text = (PLUGIN / "skills" / "assayer-audit" / "agents" / "openai.yaml").read_text()
        self.assertIn("dependencies:", text)
        self.assertIn("type: \"mcp\"", text)
        self.assertIn("value: \"assayer\"", text)
        self.assertIn("transport: \"stdio\"", text)

    def test_distribution_preserves_protocol_schema_directory(self):
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        data_files = setuptools["data-files"]
        self.assertEqual(data_files["share/assayer/schemas/protocol"], ["schemas/protocol/*.json"])
        self.assertIn("rules/registry.json", data_files["share/assayer/rules"])


if __name__ == "__main__":
    unittest.main()
