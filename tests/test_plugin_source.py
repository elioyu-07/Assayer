"""Local plugin repositories are sanitized authoring inputs, not artifacts."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assayer_platform import PlatformContractError
from assayer_platform.plugin_source import source_tree_checksum, stage_plugin_source


class PluginSourceTests(unittest.TestCase):
    def test_digest_and_stage_share_one_sanitized_source_view(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "plugin.yaml").write_text("id: test.plugin\n", encoding="utf-8")
            for relative in (
                ".assayer/verified/old.whl",
                ".git/config",
                ".venv/bin/python",
                "build/output.txt",
                "dist/old.whl",
                "src/plugin/__pycache__/runtime.pyc",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("transient", encoding="utf-8")

            first = source_tree_checksum(source)
            (source / ".venv/bin/python").write_text("changed", encoding="utf-8")
            self.assertEqual(source_tree_checksum(source), first)

            staged = stage_plugin_source(source, root / "staged")
            self.assertEqual((staged / "plugin.yaml").read_text(), "id: test.plugin\n")
            for forbidden in (".assayer", ".git", ".venv", "build", "dist", "__pycache__"):
                self.assertFalse(any(forbidden in path.parts for path in staged.rglob("*")))

            (source / "plugin.yaml").write_text("id: changed.plugin\n", encoding="utf-8")
            self.assertNotEqual(source_tree_checksum(source), first)

    def test_nontransient_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            target = root / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            (source / "linked.txt").symlink_to(target)

            with self.assertRaises(PlatformContractError) as caught:
                source_tree_checksum(source)
            self.assertEqual(caught.exception.code, "PLUGIN_SOURCE_UNSAFE")


if __name__ == "__main__":
    unittest.main()
