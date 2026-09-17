"""Tests for deterministic declaration-only plugin compilation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_platform.yaml_subset import loads_yaml_subset
from assayer_plugin_sdk.contract import PlatformContractError


class YamlSubsetTests(unittest.TestCase):
    def test_nested_domain_declaration_is_parsed_without_executable_yaml(self):
        value = loads_yaml_subset("""
checks:
  - id: PERM-001
    title: Permission behavior
    terms: [denied, unauthorized, "data scope"]
""")
        self.assertEqual(value["checks"][0]["id"], "PERM-001")
        self.assertEqual(
            value["checks"][0]["terms"],
            ["denied", "unauthorized", "data scope"],
        )


class DeclarationCompilerTests(unittest.TestCase):
    def test_deleted_simple_author_module_is_not_available(self):
        self.assertIsNone(importlib.util.find_spec("assayer_plugin_sdk.simple"))

    @staticmethod
    def _minimal_source(root: Path, plugin_source: str) -> Path:
        del plugin_source
        source = root / "source"
        source.mkdir()
        (source / "plugin.yaml").write_text("""
id: dev.example.safe-plugin
name: Safe plugin
description: Checks a Markdown document.
version: 1.0.0
input: markdown
checks: checks.yaml
instructions: semantic-review.md
""", encoding="utf-8")
        (source / "checks.yaml").write_text("""
checks:
  - id: SAFE-001
    title: Safe check
    description: Checks deterministic author execution.
    applicability: Markdown documents.
    default_severity: P2
    recommendation: Keep author code deterministic.
    unknown_when: The document is unavailable.
""", encoding="utf-8")
        (source / "semantic-review.md").write_text(
            "# Review\nUse frozen support.\n", encoding="utf-8",
        )
        cases = source / "cases"
        cases.mkdir()
        (cases / "ready.yaml").write_text("""
input: ready.md
expect:
  candidate_rules: []
  final: ready
""", encoding="utf-8")
        (cases / "ready.md").write_text("# Ready\n", encoding="utf-8")
        return source

    def test_compiler_generates_all_mechanical_contracts_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "plugin.yaml").write_text("""
id: dev.example.spec-quality
name: Spec quality
description: Reviews Markdown specifications.
version: 1.0.0
input: markdown
checks: checks.yaml
instructions: semantic-review.md
""", encoding="utf-8")
            (source / "checks.yaml").write_text("""
checks:
  - id: PERM-001
    title: Permission denial behavior
    description: Permission requirements define denial responses.
    applicability: Documents that define protected operations.
    default_severity: P2
    recommendation: Define roles and denial responses.
    unknown_when: Referenced authority is unavailable.
""", encoding="utf-8")
            (source / "semantic-review.md").write_text(
                "# Review\nConfirm only with frozen support.\n", encoding="utf-8",
            )
            cases = source / "cases"
            cases.mkdir()
            (cases / "ready.yaml").write_text("""
input: ready.md
expect:
  candidate_rules: []
  final: ready
""", encoding="utf-8")
            (cases / "ready.md").write_text("# Overview\npermission\n", encoding="utf-8")
            first = root / "first"
            second = root / "second"

            compiled = compile_plugin_contract(source, first)
            compile_plugin_contract(source, second)

            self.assertEqual(compiled.plugin_id, "dev.example.spec-quality")
            self.assertEqual(
                {path.relative_to(first).as_posix(): path.read_bytes()
                 for path in first.rglob("*") if path.is_file()},
                {path.relative_to(second).as_posix(): path.read_bytes()
                 for path in second.rglob("*") if path.is_file()},
            )
            contract = load_compiled_plugin_contract(first)
            self.assertEqual(contract.version, "1.0.0")
            self.assertEqual(contract.payload["checks"][0]["dimensions"], ("PERM-001",))
            self.assertEqual(compiled.artifacts, ("compiled-plugin.json",))
            self.assertEqual(tuple(first.rglob("*.py")), ())

    def test_compiler_does_not_generate_a_python_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._minimal_source(root, "")
            generated = root / "generated"
            compile_plugin_contract(source, generated)
            self.assertTrue((generated / "compiled-plugin.json").is_file())
            self.assertFalse((generated / "pyproject.toml").exists())

    def test_forbidden_import_is_rejected_before_author_source_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "executed"
            source = self._minimal_source(root, f'''\
from pathlib import Path

Path({str(marker)!r}).write_text("unsafe", encoding="utf-8")
''')
            (source / "plugin.py").write_text("unsafe", encoding="utf-8")
            with self.assertRaises(PlatformContractError) as rejected:
                compile_plugin_contract(source, root / "generated")
            self.assertEqual(
                getattr(rejected.exception, "code", None),
                "ORDINARY_PLUGIN_PYTHON_FORBIDDEN",
            )
            self.assertFalse(marker.exists())

    def test_browser_policy_pack_derives_provider_contract_and_url_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(Path("plugins/frontend-audit"), source)
            generated = root / "generated"

            compiled = compile_plugin_contract(source, generated)

            contract = load_compiled_plugin_contract(generated)
            check = contract.payload["checks"][0]
            self.assertEqual(check["dimensions"], (
                "filter_present", "query_action", "reset_action", "binding_to_list",
            ))
            scope = contract.payload["input"]["scopeSchema"]
            self.assertEqual(scope["required"], ("url",))
            self.assertEqual(contract.input_kind, "browser_snapshot")
            self.assertEqual(tuple(generated.rglob("*.py")), ())

    def test_filesystem_builtin_and_private_document_access_are_rejected(self):
        unsafe_methods = (
            'open("result.txt", "w")',
            'document._Document__text',
        )
        for ordinal, expression in enumerate(unsafe_methods):
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self._minimal_source(root, expression)
                (source / "plugin.py").write_text(expression, encoding="utf-8")
                with self.assertRaises(PlatformContractError) as rejected:
                    compile_plugin_contract(source, root / f"generated-{ordinal}")
                self.assertEqual(
                    getattr(rejected.exception, "code", None),
                    "ORDINARY_PLUGIN_PYTHON_FORBIDDEN",
                )

    def test_executable_annotation_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "annotation-executed"
            source = self._minimal_source(root, f'open({str(marker)!r}, "w")')
            (source / "plugin.py").write_text("def scan(document):\n    return ()\n", encoding="utf-8")
            with self.assertRaises(PlatformContractError) as rejected:
                compile_plugin_contract(source, root / "generated")
            self.assertEqual(rejected.exception.code, "ORDINARY_PLUGIN_PYTHON_FORBIDDEN")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
