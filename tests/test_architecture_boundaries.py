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

    def test_retired_v1_report_pipeline_surface_is_absent(self):
        import assayer_host

        self.assertFalse((ROOT / "src" / "assayer_host" / "reporting.py").exists())
        self.assertFalse((ROOT / "src" / "assayer_host" / "observability.py").exists())
        self.assertFalse((ROOT / "tests" / "test_observability_runtime.py").exists())
        self.assertEqual([], sorted((ROOT / "examples").glob("*-ledger.json")))
        self.assertNotIn("DerivedReportBuilder", assayer_host.__all__)

    def test_retired_common_review_decision_surface_is_absent(self):
        self.assertFalse(
            (ROOT / "src" / "assayer_platform" / "common_review_decision.py").exists()
        )
        self.assertFalse((ROOT / "tests" / "test_common_review_decision.py").exists())

    def test_retired_common_review_decision_assembly_is_rejected(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "assayer_platform").mkdir(parents=True)
            source = root / "src" / "assayer_platform" / "kept.py"
            source.write_text(
                "def assemble_common_review_decisions():\n    return ()\n", encoding="utf-8",
            )
            violations = _repository_contract_violations(root)
            self.assertTrue(
                any("assemble_common_review_decisions" in item for item in violations),
                violations,
            )

    def test_retired_common_review_decision_module_path_is_rejected(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src" / "assayer_platform" / "common_review_decision.py"
            path.parent.mkdir(parents=True)
            path.write_text("X = 1\n", encoding="utf-8")
            violations = _repository_contract_violations(root)
            self.assertTrue(
                any("common_review_decision.py" in item for item in violations), violations,
            )

    def test_retired_v1_result_mapping_name_is_rejected(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "assayer_platform").mkdir(parents=True)
            source = root / "src" / "assayer_platform" / "compiler.py"
            source.write_text(
                'CASE = {"expectedDecision": "scanned_no_issue"}\n', encoding="utf-8",
            )
            violations = _repository_contract_violations(root)
            self.assertTrue(
                any("expectedDecision" in item for item in violations), violations,
            )

    def test_retired_schema_surface_is_absent(self):
        from scripts.check_architecture_boundaries import RETIRED_SCHEMA_NAMES

        for name in RETIRED_SCHEMA_NAMES:
            self.assertFalse((ROOT / "schemas" / name).exists(), name)
        for name in ("platform-ledger.schema.json", "canonical-result.schema.json"):
            self.assertTrue((ROOT / "schemas" / name).is_file(), name)


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
            (root / "src" / "assayer_host").mkdir(parents=True, exist_ok=True)
            (root / "src" / "assayer_host" / "loader.py").write_text(
                'SCHEMA = "protocol.schema.json"\n', encoding="utf-8",
            )
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

    def test_every_distribution_wheel_declaration_is_checked_and_verified(self):
        from scripts.check_architecture_boundaries import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "packages" / "assayer-platform"
            platform.mkdir(parents=True)
            pyproject = platform / "pyproject.toml"
            pyproject.write_text(
                '[tool.setuptools.data-files]\n'
                '"share/assayer/schemas" = ["../../schemas/*.json"]\n',
                encoding="utf-8",
            )
            reasons = [violation.reason for violation in find_violations(root)]
            self.assertTrue(any("disappears" in reason for reason in reasons), reasons)

            (root / "schemas").mkdir()
            (root / "schemas" / "common.schema.json").write_text("{}", encoding="utf-8")
            (root / "src" / "assayer_host").mkdir(parents=True, exist_ok=True)
            (root / "src" / "assayer_host" / "loader.py").write_text(
                'SCHEMA = "common.schema.json"\n', encoding="utf-8",
            )
            self.assertEqual((), find_violations(root))

            pyproject.write_text(
                '[tool.setuptools.data-files]\n'
                '"share/assayer/schemas" = ["../../schemas/*.json"]\n'
                '"share/assayer/schemas/protocol" = ["../../schemas/protocol/*.json"]\n',
                encoding="utf-8",
            )
            reasons = [violation.reason for violation in find_violations(root)]
            self.assertTrue(any("retired protocol" in reason for reason in reasons), reasons)

    def test_orphan_schema_is_rejected_without_a_live_reader(self):
        from scripts.check_architecture_boundaries import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir(parents=True)
            (root / "schemas" / "orphan.schema.json").write_text("{}", encoding="utf-8")
            reasons = [violation.reason for violation in find_violations(root)]
            self.assertTrue(any("without a live reader" in reason for reason in reasons), reasons)

            (root / "src" / "assayer_host").mkdir(parents=True)
            (root / "src" / "assayer_host" / "loader.py").write_text(
                'SCHEMA = "orphan.schema.json"\n', encoding="utf-8",
            )
            self.assertEqual((), find_violations(root))

    def test_plugin_source_reference_keeps_a_schema_live(self):
        from scripts.check_architecture_boundaries import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir(parents=True)
            (root / "schemas" / "plugin-visible.schema.json").write_text("{}", encoding="utf-8")
            reasons = [violation.reason for violation in find_violations(root)]
            self.assertTrue(any("without a live reader" in reason for reason in reasons), reasons)

            (root / "plugins" / "demo" / "src").mkdir(parents=True)
            (root / "plugins" / "demo" / "src" / "loader.py").write_text(
                'SCHEMA = "plugin-visible.schema.json"\n', encoding="utf-8",
            )
            self.assertEqual((), find_violations(root))

    def test_retired_schema_name_cannot_return_even_with_a_reader(self):
        from scripts.check_architecture_boundaries import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir(parents=True)
            (root / "schemas" / "audit-ledger.schema.json").write_text("{}", encoding="utf-8")
            (root / "src" / "assayer_host").mkdir(parents=True)
            (root / "src" / "assayer_host" / "loader.py").write_text(
                'SCHEMA = "audit-ledger.schema.json"\n', encoding="utf-8",
            )
            reasons = [violation.reason for violation in find_violations(root)]
            self.assertTrue(any("retired v1 or legacy-plugin schema" in reason for reason in reasons), reasons)

    def test_retired_v1_ledger_artifact_name_is_rejected_in_shipped_python(self):
        from scripts.check_architecture_boundaries import _repository_contract_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "assayer_host").mkdir(parents=True)
            source = root / "src" / "assayer_host" / "retired.py"
            source.write_text('LEDGER = "audit-ledger.json"\n', encoding="utf-8")
            violations = _repository_contract_violations(root)
            self.assertTrue(any("audit-ledger.json" in item for item in violations), violations)


if __name__ == "__main__":
    unittest.main()
