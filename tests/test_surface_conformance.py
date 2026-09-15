from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from assayer_platform import PluginConformanceReport
from assayer_platform.public_surface import (
    PUBLIC_SURFACE,
    PUBLIC_SURFACE_VERSION,
)
from assayer_platform.surface_conformance import inspect_plugin_surface


class PublicSurfaceTests(unittest.TestCase):
    def test_surface_is_versioned_and_every_symbol_resolves(self) -> None:
        self.assertEqual(PUBLIC_SURFACE_VERSION, "1.5.0")
        for module_name, names in PUBLIC_SURFACE.items():
            module = importlib.import_module(module_name)
            for name in names:
                self.assertTrue(
                    hasattr(module, name),
                    f"{module_name} does not export {name}",
                )

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

    def test_public_sdk_and_plugin_local_imports_pass(self) -> None:
        root = self._write({
            "assayer_fixture_plugin/__init__.py": "",
            "assayer_fixture_plugin/runtime.py": (
                "from assayer_plugin_sdk import PluginRegistration, PlatformContext, WorkItem\n"
                "from assayer_plugin_sdk.plugin_sdk import to_json_value, validate_entity_id\n"
                "from assayer_plugin_sdk.manifest import load_plugin_manifest\n"
                "from assayer_fixture_plugin.helpers import value\n"
            ),
            "assayer_fixture_plugin/helpers.py": "value = 1\n",
        })
        report = inspect_plugin_surface(root)
        self.assertIsInstance(report, PluginConformanceReport)
        self.assertTrue(report.passed, [issue.as_dict() for issue in report.issues])

    def test_forbidden_import_shapes_are_classified(self) -> None:
        cases = (
            ("import assayer_platform.kernel\n", "PLUGIN_IMPORT_PLATFORM_IMPLEMENTATION"),
            ("from assayer_platform.contract import PlatformKernel\n", "PLUGIN_IMPORT_PLATFORM_IMPLEMENTATION"),
            ("from assayer_platform.contract import *\n", "PLUGIN_IMPORT_PLATFORM_IMPLEMENTATION"),
            ("from assayer_document_navigation import parse_markdown\n", "PLUGIN_IMPORT_PROVIDER_IMPLEMENTATION"),
        )
        for source, code in cases:
            with self.subTest(code=code, source=source):
                report = inspect_plugin_surface(self._write({"plugin_mod.py": source}))
                self.assertFalse(report.passed)
                self.assertIn(code, {issue.code for issue in report.issues})

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
