from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_plugin_source_boundaries import (
    LIVE_TOOLS,
    find_platform_bundle_violations,
    find_violations,
)


class PluginSourceBoundaryTests(unittest.TestCase):
    def test_current_ordinary_plugin_is_declaration_only(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(find_violations(root), ())

    def test_python_author_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plugins" / "example"
            plugin.mkdir(parents=True)
            (plugin / "plugin.yaml").write_text("id: example.plugin\n", encoding="utf-8")
            (plugin / "plugin.py").write_text("def scan(document):\n    return []\n", encoding="utf-8")
            violations = find_violations(root)
        messages = tuple(violation.message for violation in violations)
        self.assertTrue(any("undeclared author file" in message for message in messages))
        self.assertTrue(any("forbidden surface" in message for message in messages))


class PlatformBundleTextTests(unittest.TestCase):
    def _bundle(self, root: Path, text: str, *, name: str = "SKILL.md") -> Path:
        skill = root / "plugins" / "assayer" / "skills" / "assayer-example"
        skill.mkdir(parents=True)
        (skill / name).write_text(text, encoding="utf-8")
        return skill

    def test_current_platform_bundle_text_names_only_live_tools(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(find_platform_bundle_violations(root), ())

    def test_retired_tool_name_in_skill_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._bundle(root, "Call `start_plugin_run` to begin.\n")
            violations = find_platform_bundle_violations(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("start_plugin_run", violations[0].message)

    def test_invented_tool_name_in_skill_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._bundle(root, "Call `start_brand_new_run` to begin.\n")
            violations = find_platform_bundle_violations(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("start_brand_new_run", violations[0].message)

    def test_retired_decision_token_in_manifest_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "plugins" / "assayer" / ".codex-plugin"
            manifest.mkdir(parents=True)
            (manifest / "plugin.json").write_text(
                '{"longDescription": "Returns a needs_review verdict."}\n', encoding="utf-8",
            )
            violations = find_platform_bundle_violations(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("needs_review", violations[0].message)

    def test_live_tool_names_and_plain_prose_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._bundle(
                root,
                "Call `start_compiled_run` then `get_compiled_result`; the report may need review.\n",
            )
            self.assertEqual(find_platform_bundle_violations(root), ())

    def test_generated_runtime_text_is_not_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "plugins" / "assayer" / "runtime" / "wheels"
            runtime.mkdir(parents=True)
            (runtime / "README.md").write_text("Call `start_plugin_run`.\n", encoding="utf-8")
            self.assertEqual(find_platform_bundle_violations(root), ())

    def test_frozen_live_tool_list_matches_the_mcp_transports(self):
        from assayer_host.plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport
        from assayer_host.transport import CompiledPlatformMcpToolTransport

        compiled = {item["name"] for item in CompiledPlatformMcpToolTransport(output_root=".").list_tools()}
        lifecycle = {item["name"] for item in PluginLifecycleMcpToolTransport(".").list_tools()}
        self.assertEqual(compiled | lifecycle, set(LIVE_TOOLS))


if __name__ == "__main__":
    unittest.main()
