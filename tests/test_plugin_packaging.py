from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from assayer_platform.yaml_subset import load_yaml_subset


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "assayer"


class PluginPackagingContractTests(unittest.TestCase):
    def test_plugin_base_version_matches_python_distribution(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(project["version"], manifest["version"].split("+", 1)[0])
        if "+" in manifest["version"]:
            self.assertIn("+codex.", manifest["version"])

    def test_plugin_mcp_uses_only_relocatable_paths(self):
        config = json.loads((PLUGIN / ".mcp.json").read_text())
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text()
        )
        server = config["mcpServers"]["assayer"]
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(server["command"], "./scripts/launch_assayer_mcp")
        self.assertEqual(server["cwd"], ".")
        self.assertNotIn(str(ROOT), json.dumps(config))
        self.assertTrue((PLUGIN / "scripts" / "launch_assayer_mcp").is_file())
        self.assertTrue((PLUGIN / "scripts" / "prepare_assayer_runtime").is_file())
        self.assertTrue((PLUGIN / "skills" / "assayer-audit" / "SKILL.md").is_file())
        self.assertTrue((PLUGIN / "skills" / "assayer-plugin-development" / "SKILL.md").is_file())

    def test_assayer_skill_declares_local_mcp_dependency_for_cli_discovery(self):
        value = load_yaml_subset(
            PLUGIN / "skills" / "assayer-audit" / "agents" / "openai.yaml"
        )
        self.assertEqual(value["dependencies"]["tools"], [{
            "type": "mcp",
            "value": "assayer",
            "description": "Local Assayer stdio MCP server",
            "transport": "stdio",
        }])

    def test_distribution_preserves_protocol_schema_directory(self):
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        data_files = setuptools["data-files"]
        self.assertEqual(data_files["share/assayer/schemas/protocol"], ["schemas/protocol/*.json"])
        self.assertNotIn("share/assayer/rules", data_files)

    def test_platform_only_root_ships_no_plugin_packages(self):
        root = tomllib.loads((ROOT / "pyproject.toml").read_text())
        setuptools = root["tool"]["setuptools"]
        self.assertEqual(
            ["assayer_platform", "assayer_host"],
            setuptools["packages"],
        )
        self.assertNotIn("package-data", setuptools)
        self.assertNotIn("entry-points", root["project"])
        self.assertTrue(
            any(dep.startswith("assayer-plugin-sdk") for dep in root["project"]["dependencies"])
        )
        self.assertTrue((ROOT / "plugins" / "frontend-audit" / "plugin.yaml").is_file())
        self.assertFalse((ROOT / "src" / "assayer_frontend_audit" / "__init__.py").exists())
        self.assertFalse(
            (ROOT / "packages" / "assayer-plugin-frontend-audit" / "pyproject.toml").exists()
        )
        self.assertFalse((ROOT / "src" / "assayer_platform" / "builtin_plugins").exists())

    def test_bundle_declares_every_runtime_extra_it_builds(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        extras = project["optional-dependencies"]
        self.assertNotIn("browser", extras)
        self.assertIn("mcp==1.27.0", extras["mcp"])

    def test_bundle_runtime_preparation_installs_no_concrete_provider(self):
        preparer = (PLUGIN / "scripts" / "prepare_assayer_runtime").read_text()
        self.assertNotIn("assayer-provider-markdown", preparer)
        self.assertNotIn("assayer-provider-browser", preparer)

    def test_bundle_and_runtime_paths_are_not_bound_to_a_domain_plugin(self):
        builder = (ROOT / "scripts" / "build_plugin_bundle.py").read_text()
        preparer = (PLUGIN / "scripts" / "prepare_assayer_runtime").read_text()
        for source in (builder, preparer):
            self.assertNotIn("ass-spec", source)
            self.assertNotIn("frontend-audit", source)
            self.assertNotIn("assayer.frontend-audit", source)

    def test_bundle_compiler_discovers_first_party_declaration_plugins(self):
        from scripts.build_plugin_bundle import _compiled_first_party_plugins

        compiled = _compiled_first_party_plugins()
        self.assertTrue(compiled)
        sources = {
            path.name for path in (ROOT / "plugins").iterdir()
            if path.is_dir() and (path / "plugin.yaml").is_file()
        }
        self.assertEqual(
            {item["file"] for item in compiled},
            {f"{name}.compiled-plugin.json" for name in sources},
        )
        self.assertEqual(len({item["pluginId"] for item in compiled}), len(compiled))


if __name__ == "__main__":
    unittest.main()
