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

    def test_retired_runtime_distribution_and_protocol_schemas_are_absent(self):
        self.assertFalse((ROOT / "src" / "assayer_agent").exists())
        self.assertFalse((ROOT / "packages" / "assayer-agent").exists())
        self.assertFalse((ROOT / "schemas" / "protocol").exists())


class RetiredToolVocabularyTest(unittest.TestCase):
    def _repository(self, directory: str) -> Path:
        root = Path(directory)
        (root / "src" / "assayer_host").mkdir(parents=True, exist_ok=True)
        return root

    def test_quoted_retired_tool_names_are_rejected_in_shipped_python(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            source = root / "src" / "assayer_host" / "retired.py"
            source.write_text('LABELS = {"start_audit": "Started an audit."}\n', encoding="utf-8")
            violations = _repository_contract_violations(root)
            self.assertTrue(any("start_audit" in item for item in violations), violations)

    def test_live_method_names_are_not_mistaken_for_retired_tools(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            source = root / "src" / "assayer_host" / "live.py"
            source.write_text(
                "def get_operation(self, operation_id):\n"
                "    return {'tool': 'submit_review_batch', 'id': operation_id}\n",
                encoding="utf-8",
            )
            self.assertEqual((), _repository_contract_violations(root))

    def test_retired_protocol_vocabulary_is_rejected_in_schemas_and_data_files(self):
        from scripts.check_architecture_boundaries import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir(parents=True, exist_ok=True)
            schema = root / "schemas" / "protocol.schema.json"
            schema.write_text(
                '{"enum": ["start_audit", "submit_review_batch"]}\n', encoding="utf-8",
            )
            self.assertTrue(find_violations(root))
            schema.write_text('{"enum": ["satisfied", "violated"]}\n', encoding="utf-8")
            self.assertEqual((), find_violations(root))
            (root / "pyproject.toml").write_text(
                '[tool.setuptools.data-files]\n"share/assayer/schemas/protocol" = ["schemas/protocol/*.json"]\n',
                encoding="utf-8",
            )
            self.assertTrue(find_violations(root))


if __name__ == "__main__":
    unittest.main()
