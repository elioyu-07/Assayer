from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_architecture_boundaries import find_violations, scan_file
ROOT = Path(__file__).resolve().parents[1]


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_repository_has_no_unapproved_import_direction_violations(self):
        self.assertEqual(find_violations(ROOT), ())

    def test_forbidden_edges_are_rejected_in_an_isolated_repository(self):
        cases = (
            ("assayer_ordinary_plugin", "from assayer_host.core import HostCore\n", "must not depend on Host"),
            ("assayer_platform", "import playwright\n", "browser implementation"),
            (
                "assayer_ordinary_plugin",
                "from assayer_platform.canonical_result import build_canonical_result\n",
                "delivery or lifecycle",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (package, source, reason) in enumerate(cases):
                with self.subTest(package=package, reason=reason):
                    path = root / "src" / package / f"fixture_{index}.py"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding="utf-8")
                    violations = scan_file(path, root=root)
                    self.assertEqual(len(violations), 1)
                    self.assertIn(reason, violations[0].reason)

    def test_removed_browser_provider_source_roots_are_absent(self):
        self.assertFalse((ROOT / "src" / "assayer_browser_provider").exists())
        self.assertFalse((ROOT / "packages" / "assayer-provider-browser").exists())


if __name__ == "__main__":
    unittest.main()
