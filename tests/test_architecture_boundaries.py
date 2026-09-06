from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_architecture_boundaries import find_violations, scan_file
from assayer_platform import (
    DeliveryObserver, EvidenceCollectionProvider, NavigationProvider,
    ReviewProtocol,
)


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_four_layer_interfaces_are_platform_only_protocols(self):
        for interface in (
            NavigationProvider, EvidenceCollectionProvider,
            ReviewProtocol, DeliveryObserver,
        ):
            self.assertTrue(getattr(interface, "_is_runtime_protocol", False))
            self.assertTrue(getattr(interface, "_is_protocol", False))

    def test_repository_has_no_unapproved_import_direction_violations(self):
        self.assertEqual(find_violations(ROOT), ())

    def test_plugin_to_host_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.py"
            path.write_text("from assayer_host.core import HostCore\n", encoding="utf-8")
            # Place the fixture under the role's source path so the checker
            # exercises the same AST rule as a real plugin file.
            plugin_path = ROOT / "src" / "assayer_frontend_audit" / "_boundary_fixture.py"
            try:
                plugin_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                violations = scan_file(plugin_path)
            finally:
                plugin_path.unlink(missing_ok=True)
            self.assertEqual(len(violations), 1)
            self.assertIn("must not depend on Host", violations[0].reason)

    def test_platform_to_browser_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.py"
            path.write_text("import playwright\n", encoding="utf-8")
            platform_path = ROOT / "src" / "assayer_platform" / "_boundary_fixture.py"
            try:
                platform_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                violations = scan_file(platform_path)
            finally:
                platform_path.unlink(missing_ok=True)
            self.assertEqual(len(violations), 1)
            self.assertIn("browser implementation", violations[0].reason)

    def test_plugin_delivery_import_is_rejected(self):
        plugin_path = ROOT / "src" / "assayer_frontend_audit" / "_boundary_fixture.py"
        try:
            plugin_path.write_text("from assayer_platform.canonical_result import build_canonical_result\n", encoding="utf-8")
            violations = scan_file(plugin_path)
        finally:
            plugin_path.unlink(missing_ok=True)
        self.assertEqual(len(violations), 1)
        self.assertIn("delivery or lifecycle", violations[0].reason)


if __name__ == "__main__":
    unittest.main()
