import ast
import os
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT / "src" / "assayer_plugin_sdk"

FORBIDDEN_ROOTS = ("assayer_platform", "assayer_host")


def _compiled_frontend_project() -> dict:
    from assayer_platform.simple_plugin_compiler import compile_simple_plugin

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "generated"
        compile_simple_plugin(
            ROOT / "plugins" / "frontend-audit",
            output,
            distribution_name="assayer-plugin-frontend-audit",
        )
        return tomllib.loads(
            (output / "pyproject.toml").read_text(encoding="utf-8")
        )


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


class PluginSdkDistributionTest(unittest.TestCase):
    def test_sdk_never_imports_the_platform_or_host(self) -> None:
        offenders = {}
        for path in SDK_ROOT.glob("*.py"):
            forbidden = _imported_roots(path) & set(FORBIDDEN_ROOTS)
            if forbidden:
                offenders[path.name] = sorted(forbidden)
        self.assertEqual({}, offenders)

    def test_sdk_owns_one_self_contained_schema_root(self) -> None:
        from assayer_plugin_sdk.resources import schema_root

        expected = (ROOT / "src" / "assayer_plugin_sdk" / "schemas").resolve()
        self.assertEqual(expected, schema_root())
        names = (
            "common.schema.json",
            "plugin-manifest.schema.json",
            "capability-provider.schema.json",
            "evidence-claim.schema.json",
            "actionable-result.schema.json",
            "evaluation-corpus.schema.json",
        )
        for name in names:
            self.assertTrue((expected / name).is_file(), name)
            self.assertFalse((ROOT / "schemas" / name).exists(), name)
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("ASSAYER_SDK_SCHEMA_ROOT")
            os.environ["ASSAYER_SDK_SCHEMA_ROOT"] = directory
            try:
                self.assertEqual(Path(directory).resolve(), schema_root())
            finally:
                if previous is None:
                    del os.environ["ASSAYER_SDK_SCHEMA_ROOT"]
                else:
                    os.environ["ASSAYER_SDK_SCHEMA_ROOT"] = previous

    def test_platform_schema_store_composes_sdk_and_platform_owners(self) -> None:
        from assayer_platform.registry import schema_path, schema_store

        store = schema_store(ROOT / "schemas")
        self.assertIn("common.schema.json", store)
        self.assertIn("platform-ledger.schema.json", store)
        self.assertEqual(
            SDK_ROOT / "schemas" / "common.schema.json",
            schema_path("common.schema.json", ROOT / "schemas"),
        )
        self.assertEqual(
            ROOT / "schemas" / "platform-ledger.schema.json",
            schema_path("platform-ledger.schema.json", ROOT / "schemas"),
        )

    def test_platform_schema_store_rejects_sdk_shadowing(self) -> None:
        from assayer_platform import PlatformContractError
        from assayer_platform.registry import schema_store

        with tempfile.TemporaryDirectory() as directory:
            shadow = Path(directory) / "common.schema.json"
            shadow.write_bytes((SDK_ROOT / "schemas" / shadow.name).read_bytes())
            with self.assertRaises(PlatformContractError) as rejected:
                schema_store(Path(directory))
        self.assertEqual("SCHEMA_RESOURCE_CONFLICT", rejected.exception.code)

    def test_schema_root_fails_closed_for_a_missing_override(self) -> None:
        from assayer_plugin_sdk.plugin_sdk import PluginContractError
        from assayer_plugin_sdk.resources import schema_root

        previous = os.environ.get("ASSAYER_SDK_SCHEMA_ROOT")
        os.environ["ASSAYER_SDK_SCHEMA_ROOT"] = "/nonexistent/assayer-plugin-sdk"
        try:
            with self.assertRaises(PluginContractError):
                schema_root()
        finally:
            if previous is None:
                del os.environ["ASSAYER_SDK_SCHEMA_ROOT"]
            else:
                os.environ["ASSAYER_SDK_SCHEMA_ROOT"] = previous

    def test_split_distribution_configs_are_sdk_only(self) -> None:
        sdk = tomllib.loads(
            (ROOT / "packages" / "assayer-plugin-sdk" / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual("assayer-plugin-sdk", sdk["project"]["name"])
        self.assertEqual(["assayer_plugin_sdk"], sdk["tool"]["setuptools"]["packages"])
        self.assertIn(
            "schemas/*.json",
            sdk["tool"]["setuptools"]["package-data"]["assayer_plugin_sdk"],
        )

        plugin = _compiled_frontend_project()
        dependencies = plugin["project"]["dependencies"]
        self.assertTrue(any(dep.startswith("assayer-plugin-sdk") for dep in dependencies))
        self.assertFalse(
            any("assayer-platform" in dep or "assayer-host" in dep for dep in dependencies)
        )

        provider = tomllib.loads(
            (ROOT / "packages" / "assayer-provider-markdown" / "pyproject.toml").read_text(encoding="utf-8")
        )
        provider_dependencies = provider["project"]["dependencies"]
        self.assertTrue(any(dep.startswith("assayer-plugin-sdk") for dep in provider_dependencies))
        self.assertFalse(
            any("assayer-platform" in dep or "assayer-host" in dep for dep in provider_dependencies)
        )

        browser_provider = tomllib.loads(
            (ROOT / "packages" / "assayer-provider-browser" / "pyproject.toml").read_text(encoding="utf-8")
        )
        browser_dependencies = browser_provider["project"]["dependencies"]
        self.assertEqual(["assayer-plugin-sdk==0.1.2"], browser_dependencies)
        self.assertIn(
            "descriptor.json",
            browser_provider["tool"]["setuptools"]["package-data"][
                "assayer_browser_provider"
            ],
        )

    def test_split_distribution_dag_and_entry_point_ownership(self) -> None:
        def config(name: str) -> dict:
            return tomllib.loads(
                (ROOT / "packages" / name / "pyproject.toml").read_text(encoding="utf-8")
            )

        expected_packages = {
            "assayer-plugin-sdk": ["assayer_plugin_sdk"],
            "assayer-agent": ["assayer_agent"],
            "assayer-provider-markdown": ["assayer_document_navigation"],
            "assayer-provider-browser": ["assayer_browser_provider"],
            "assayer-platform": ["assayer_platform", "assayer_host"],
        }
        for name, packages in expected_packages.items():
            self.assertEqual(
                packages, config(name)["tool"]["setuptools"]["packages"], name,
            )

        platform = config("assayer-platform")
        self.assertTrue(
            any(dep.startswith("assayer-plugin-sdk") for dep in platform["project"]["dependencies"])
        )
        entry_points = platform["project"].get("entry-points", {})
        self.assertNotIn("assayer.plugins", entry_points)
        self.assertNotIn("assayer.providers", entry_points)

        agent = config("assayer-agent")
        self.assertEqual(["assayer-plugin-sdk==0.1.2"], agent["project"]["dependencies"])
        self.assertNotIn("assayer.plugins", agent["project"].get("entry-points", {}))
        self.assertNotIn("assayer.providers", agent["project"].get("entry-points", {}))

        sdk_entry_points = config("assayer-plugin-sdk")["project"].get("entry-points", {})
        self.assertNotIn("assayer.plugins", sdk_entry_points)
        self.assertNotIn("assayer.providers", sdk_entry_points)

        self.assertIn(
            "assayer.frontend-audit",
            _compiled_frontend_project()["project"]["entry-points"]["assayer.plugins"],
        )
        self.assertIn(
            "markdown",
            config("assayer-provider-markdown")["project"]["entry-points"]["assayer.providers"],
        )
        self.assertIn(
            "browser",
            config("assayer-provider-browser")["project"]["entry-points"]["assayer.providers"],
        )


if __name__ == "__main__":
    unittest.main()
