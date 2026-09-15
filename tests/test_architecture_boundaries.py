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
            ("assayer_frontend_audit", "from assayer_host.core import HostCore\n", "must not depend on Host"),
            ("assayer_platform", "import playwright\n", "browser implementation"),
            (
                "assayer_frontend_audit",
                "from assayer_platform.canonical_result import build_canonical_result\n",
                "delivery or lifecycle",
            ),
            (
                "assayer_browser_provider",
                "from assayer_host.browser_runtime import BrowserHostRuntime\n",
                "must not depend on Host",
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

    def test_split_distribution_source_roots_are_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = (
                root / "packages" / "assayer-provider-browser" / "src"
                / "assayer_browser_provider" / "fixture.py"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                "from assayer_host.browser_runtime import BrowserHostRuntime\n",
                encoding="utf-8",
            )

            violations = find_violations(root)

        self.assertEqual(len(violations), 1)
        self.assertIn("must not depend on Host", violations[0].reason)


if __name__ == "__main__":
    unittest.main()
