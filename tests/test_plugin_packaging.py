from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


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
        server = config["mcpServers"]["assayer"]
        self.assertEqual(server["command"], "./scripts/launch_assayer_mcp")
        self.assertEqual(server["cwd"], ".")
        self.assertNotIn(str(ROOT), json.dumps(config))
        self.assertTrue((PLUGIN / "scripts" / "launch_assayer_mcp").is_file())
        self.assertTrue((PLUGIN / "skills" / "assayer-audit" / "SKILL.md").is_file())

    def test_launcher_validates_full_plugin_version_but_installs_base_wheel_version(self):
        launcher = (PLUGIN / "scripts" / "launch_assayer_mcp").read_text()
        self.assertIn('"$WHEEL_DIR" "$PLUGIN_VERSION"', launcher)
        self.assertNotIn('"$WHEEL_DIR" "$BASE_VERSION" <<', launcher)
        self.assertIn('"assayer[browser,mcp]==$BASE_VERSION"', launcher)
        self.assertIn('export ASSAYER_PLUGIN_VERSION="$PLUGIN_VERSION"', launcher)
        self.assertIn('print(version("assayer"))', launcher)
        self.assertIn('if [ "$RUNTIME_VERSION" != "$BASE_VERSION" ]', launcher)
        self.assertIn('export ASSAYER_RUNTIME_VERSION="$RUNTIME_VERSION"', launcher)
        self.assertIn("export ASSAYER_BUNDLE_VERIFIED=1", launcher)
        self.assertIn('"$RUNTIME_ROOT/runtime-identity.json" "$PLUGIN_VERSION" "$RUNTIME_VERSION"', launcher)
        self.assertIn('temporary.replace(destination)', launcher)

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

    def test_distribution_includes_builtin_plugin_manifest(self):
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        package_data = setuptools["package-data"]
        self.assertIn("manifest.json", package_data["assayer_platform.builtin_plugins.config_quality"])
        self.assertIn("manifest.json", package_data["assayer_platform.builtin_plugins.frontend_audit"])
        self.assertIn("manifest.json", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertIn("recognition.json", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertIn("authority.md", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertIn("self-check-checklist.md", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertIn("spec-template.md", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertIn("nfr-catalog.md", package_data["assayer_platform.builtin_plugins.spec_quality"])
        self.assertTrue((ROOT / "src" / "assayer_platform" / "builtin_plugins" / "config_quality" / "manifest.json").is_file())
        self.assertTrue((ROOT / "src" / "assayer_platform" / "builtin_plugins" / "frontend_audit" / "manifest.json").is_file())
        self.assertTrue((ROOT / "src" / "assayer_platform" / "builtin_plugins" / "spec_quality" / "manifest.json").is_file())

    def test_bundle_builder_runs_plugin_conformance_before_wheel_packaging(self):
        source = (ROOT / "scripts" / "build_plugin_bundle.py").read_text()
        validation = source.index("_validate_builtin_plugin_releases()", source.index("def build("))
        wheel = source.index('"wheel"', source.index("def build("))
        self.assertLess(validation, wheel)


if __name__ == "__main__":
    unittest.main()
