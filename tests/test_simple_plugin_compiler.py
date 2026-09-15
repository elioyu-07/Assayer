"""Tests for deterministic Simple SDK artifact compilation."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from assayer_platform.conformance import inspect_plugin_package
from assayer_platform.simple_plugin_compiler import compile_simple_plugin
from assayer_platform.yaml_subset import loads_yaml_subset
from assayer_plugin_sdk.contract import PlatformContractError
from assayer_plugin_sdk.manifest import load_plugin_manifest


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


class SimplePluginCompilerTests(unittest.TestCase):
    @staticmethod
    def _minimal_source(root: Path, plugin_source: str) -> Path:
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
        (source / "plugin.py").write_text(plugin_source, encoding="utf-8")
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
            (source / "plugin.py").write_text("""
from assayer_plugin_sdk.simple import Candidate, Document, invariant, policy_plugin

@policy_plugin(
    id="dev.example.spec-quality", version="1.0.0", input="markdown",
    checks="checks.yaml", instructions="semantic-review.md",
)
class SpecQuality:
    @invariant(
        "candidate-has-reason", guidance="A candidate requires a reason.",
        location="/candidates", positive=({"reason": "known"},),
        negative=({"reason": ""},),
    )
    def candidate_has_reason(self, value):
        return bool(value.get("reason"))

    def scan(self, document: Document):
        if document.contains("permission"):
            yield Candidate(
                "PERM-001", "permission", "Denial behavior is missing.",
                document.lines(1, 1), "P2", "Define denial behavior.",
            )
""", encoding="utf-8")
            first = root / "first"
            second = root / "second"

            compiled = compile_simple_plugin(source, first)
            compile_simple_plugin(source, second)

            self.assertEqual(compiled.plugin_id, "dev.example.spec-quality")
            self.assertIn(
                'name = "dev.example.spec-quality"',
                (first / "pyproject.toml").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                {path.relative_to(first).as_posix(): path.read_bytes()
                 for path in first.rglob("*") if path.is_file()},
                {path.relative_to(second).as_posix(): path.read_bytes()
                 for path in second.rglob("*") if path.is_file()},
            )
            package = first / "src" / compiled.package_name
            manifest = load_plugin_manifest(package / "manifest.json")
            self.assertEqual(manifest.version, "1.0.0")
            self.assertEqual(manifest.checks[0].dimensions, ("PERM-001",))
            invariants = json.loads((package / "invariants.json").read_text())
            agent_rules = json.loads((package / "agent-rules.json").read_text())
            self.assertEqual(invariants["invariants"][0]["location"], "/candidates")
            self.assertEqual(agent_rules["rules"][0]["ruleId"], "candidate-has-reason")
            self.assertTrue(inspect_plugin_package(first).passed)

            (package / "author.py").write_text(
                "import os\n", encoding="utf-8",
            )
            changed = inspect_plugin_package(first)
            self.assertFalse(changed.passed)
            self.assertIn(
                "SIMPLE_AUTHOR_CODE_FORBIDDEN",
                {issue.code for issue in changed.issues},
            )

    def test_compiler_can_stamp_a_distribution_name_for_split_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._minimal_source(root, """\
from assayer_plugin_sdk.simple import Document, policy_plugin

@policy_plugin(
    id="dev.example.safe-plugin", version="1.0.0", input="markdown",
    checks="checks.yaml", instructions="semantic-review.md",
)
class SplitPlugin:
    def scan(self, document: Document):
        return ()
""")
            generated = root / "generated"
            compile_simple_plugin(
                source,
                generated,
                distribution_name="assayer-plugin-split-plugin",
            )
            self.assertIn(
                'name = "assayer-plugin-split-plugin"',
                (generated / "pyproject.toml").read_text(encoding="utf-8"),
            )

    def test_forbidden_import_is_rejected_before_author_source_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "executed"
            source = self._minimal_source(root, f'''\
from pathlib import Path
from assayer_plugin_sdk.simple import Document, policy_plugin

Path({str(marker)!r}).write_text("unsafe", encoding="utf-8")

@policy_plugin(
    id="dev.example.safe-plugin", version="1.0.0", input="markdown",
    checks="checks.yaml", instructions="semantic-review.md",
)
class SafePlugin:
    def scan(self, document: Document):
        return ()
''')
            with self.assertRaises(PlatformContractError) as rejected:
                compile_simple_plugin(source, root / "generated")
            self.assertEqual(
                getattr(rejected.exception, "code", None),
                "SIMPLE_AUTHOR_CODE_FORBIDDEN",
            )
            self.assertFalse(marker.exists())

    def test_browser_policy_pack_derives_provider_contract_and_url_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(Path("plugins/frontend-audit"), source)
            generated = root / "generated"

            compiled = compile_simple_plugin(source, generated)

            package = generated / "src" / compiled.package_name
            manifest = load_plugin_manifest(package / "manifest.json")
            check = manifest.checks[0]
            self.assertEqual(check.dimensions, (
                "filter_present", "query_action", "reset_action", "binding_to_list",
            ))
            self.assertEqual(check.required_capabilities, ("browser_snapshot",))
            scope = json.loads((package / "scope.schema.json").read_text())
            self.assertEqual(scope["required"], ["url"])
            registration = (package / "registration.py").read_text()
            self.assertIn("provider_source_capabilities", registration)
            self.assertIn("provider_scope_resolver=_provider_scope", registration)

    def test_filesystem_builtin_and_private_document_access_are_rejected(self):
        unsafe_methods = (
            'open("result.txt", "w")',
            'document._Document__text',
        )
        for ordinal, expression in enumerate(unsafe_methods):
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self._minimal_source(root, f'''\
from assayer_plugin_sdk.simple import Document, policy_plugin

@policy_plugin(
    id="dev.example.safe-plugin", version="1.0.0", input="markdown",
    checks="checks.yaml", instructions="semantic-review.md",
)
class SafePlugin:
    def scan(self, document: Document):
        {expression}
        return ()
''')
                with self.assertRaises(PlatformContractError) as rejected:
                    compile_simple_plugin(source, root / f"generated-{ordinal}")
                self.assertEqual(
                    getattr(rejected.exception, "code", None),
                    "SIMPLE_AUTHOR_CODE_FORBIDDEN",
                )

    def test_executable_annotation_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "annotation-executed"
            source = self._minimal_source(root, f'''\
from assayer_plugin_sdk.simple import policy_plugin

@policy_plugin(
    id="dev.example.safe-plugin", version="1.0.0", input="markdown",
    checks="checks.yaml", instructions="semantic-review.md",
)
class SafePlugin:
    def scan(self, document: open({str(marker)!r}, "w")):
        return ()
''')
            with self.assertRaises(PlatformContractError) as rejected:
                compile_simple_plugin(source, root / "generated")
            self.assertEqual(rejected.exception.code, "SIMPLE_AUTHOR_CODE_FORBIDDEN")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
