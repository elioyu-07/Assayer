from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from assayer_platform import PluginConformanceReport
from assayer_platform.public_surface import (
    PUBLIC_SURFACE,
    PUBLIC_SURFACE_VERSION,
    is_public_module,
    public_symbols,
)
from assayer_platform.surface_conformance import inspect_plugin_surface


class PublicSurfaceTests(unittest.TestCase):
    def test_surface_is_versioned(self) -> None:
        self.assertEqual(PUBLIC_SURFACE_VERSION, "1.1.0")

    def test_every_symbol_resolves_in_its_module(self) -> None:
        for module_name, names in PUBLIC_SURFACE.items():
            module = importlib.import_module(module_name)
            for name in names:
                self.assertTrue(
                    hasattr(module, name),
                    f"{module_name} does not export {name}",
                )

    def test_top_level_registration_and_context_are_public(self) -> None:
        self.assertIn("AgentContractBundle", public_symbols("assayer_platform"))
        self.assertIn("PluginRegistration", public_symbols("assayer_platform"))
        self.assertIn("PlatformContext", public_symbols("assayer_platform"))

    def test_is_public_module(self) -> None:
        self.assertTrue(is_public_module("assayer_platform.contract"))
        self.assertFalse(is_public_module("assayer_platform.kernel"))


class SurfaceConformanceTests(unittest.TestCase):
    def _write(self, tree: dict[str, str]) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel, text in tree.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return str(root)

    def test_empty_source_passes(self) -> None:
        root = self._write({"plugin_mod.py": "x = 1\n"})
        report = inspect_plugin_surface(root)
        self.assertIsInstance(report, PluginConformanceReport)
        self.assertTrue(report.passed)

    def test_public_imports_pass(self) -> None:
        root = self._write({
            "plugin_mod.py": (
                "from assayer_platform import PluginRegistration\n"
                "from assayer_platform.contract import PlatformContext, WorkItem\n"
                "from assayer_platform.registry import load_plugin_manifest\n"
            ),
        })
        self.assertTrue(inspect_plugin_surface(root).passed)

    def test_relative_import_is_ignored(self) -> None:
        root = self._write({
            "plugin_mod.py": "from .runtime import AssSpecPlugin\n",
        })
        self.assertTrue(inspect_plugin_surface(root).passed)

    def test_non_public_module_is_flagged(self) -> None:
        root = self._write({
            "plugin_mod.py": "import assayer_platform.kernel\n",
        })
        report = inspect_plugin_surface(root)
        self.assertFalse(report.passed)
        self.assertTrue(any(
            issue.code == "PLUGIN_IMPORT_MODULE_NOT_PUBLIC" for issue in report.issues
        ))

    def test_non_public_symbol_is_flagged(self) -> None:
        root = self._write({
            "plugin_mod.py": "from assayer_platform.contract import PlatformKernel\n",
        })
        report = inspect_plugin_surface(root)
        self.assertFalse(report.passed)
        self.assertTrue(any(
            issue.code == "PLUGIN_IMPORT_SYMBOL_NOT_PUBLIC" for issue in report.issues
        ))

    def test_wildcard_import_is_flagged(self) -> None:
        root = self._write({
            "plugin_mod.py": "from assayer_platform.contract import *\n",
        })
        report = inspect_plugin_surface(root)
        self.assertFalse(report.passed)
        self.assertTrue(any(
            issue.code == "PLUGIN_IMPORT_WILDCARD" for issue in report.issues
        ))

    def test_missing_root_is_flagged(self) -> None:
        report = inspect_plugin_surface("/nonexistent/path")
        self.assertFalse(report.passed)
        self.assertTrue(any(
            issue.code == "PLUGIN_SURFACE_ROOT_MISSING" for issue in report.issues
        ))

    def test_minimal_fixture_plugin_passes(self) -> None:
        root = Path(__file__).parent / "fixtures" / "plugins" / "minimal" / "src"
        report = inspect_plugin_surface(root)
        self.assertTrue(report.passed, [i.as_dict() for i in report.issues])


if __name__ == "__main__":
    unittest.main()
