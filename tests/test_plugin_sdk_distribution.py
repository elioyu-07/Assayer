import ast
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT / "src" / "assayer_plugin_sdk"

FORBIDDEN_ROOTS = ("assayer_platform", "assayer_host")


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

    def test_sdk_exposes_the_plugin_facing_contract(self) -> None:
        import assayer_plugin_sdk as sdk

        for name in (
            "PLATFORM_API_VERSION",
            "HOST_PROTOCOL_VERSION",
            "HOST_SDK_VERSION",
            "DomainResultContract",
            "PluginCompatibility",
            "negotiate_plugin_compatibility",
            "WorkItem",
            "InvestigationPacket",
            "Finding",
            "DecisionProposal",
            "ReviewCheckpoint",
            "CommitReceipt",
            "CheckContract",
            "PluginManifest",
            "PlatformContext",
            "EvidenceRecord",
            "CapabilityAccess",
            "ProviderCollectionResult",
            "ExecutionProfile",
            "PlatformContractError",
            "PluginContractError",
            "EvidenceHandle",
            "EvidenceHandleRegistry",
            "to_json_value",
            "validate_entity_id",
        ):
            self.assertTrue(hasattr(sdk, name), name)

    def test_sdk_contract_identity_is_shared_with_the_platform_shim(self) -> None:
        import assayer_plugin_sdk as sdk
        import assayer_platform as platform

        self.assertIs(sdk.PlatformContractError, platform.PlatformContractError)
        self.assertIs(sdk.DomainResultContract, platform.DomainResultContract)

    def test_sdk_resolves_its_own_schema_root(self) -> None:
        from assayer_plugin_sdk.resources import schema_root

        self.assertTrue((schema_root() / "common.schema.json").is_file())
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

    def test_sdk_schema_root_is_self_contained(self) -> None:
        from assayer_plugin_sdk.resources import schema_root

        expected = (ROOT / "src" / "assayer_plugin_sdk" / "schemas").resolve()
        self.assertEqual(expected, schema_root())
        for name in (
            "common.schema.json",
            "plugin-manifest.schema.json",
            "capability-provider.schema.json",
            "evidence-claim.schema.json",
            "actionable-result.schema.json",
            "evaluation-corpus.schema.json",
        ):
            self.assertTrue((expected / name).is_file(), name)

    def test_sdk_schema_copies_do_not_drift_from_the_repo(self) -> None:
        sdk = ROOT / "src" / "assayer_plugin_sdk" / "schemas"
        repo = ROOT / "schemas"
        for name in (
            "common.schema.json",
            "plugin-manifest.schema.json",
            "capability-provider.schema.json",
            "evidence-claim.schema.json",
            "actionable-result.schema.json",
            "evaluation-corpus.schema.json",
        ):
            self.assertEqual(
                (repo / name).read_bytes(), (sdk / name).read_bytes(), name,
            )

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

    def test_plugin_facing_validators_use_the_sdk_schema_root(self) -> None:
        sdk = ROOT / "src" / "assayer_plugin_sdk"
        for name in ("actionable_result.py", "evidence_claim.py", "evaluation.py"):
            text = (sdk / name).read_text(encoding="utf-8")
            self.assertNotIn("_schema_root", text, name)
            self.assertIn("assayer_plugin_sdk.resources", text, name)

    def test_in_repo_plugin_depends_only_on_the_sdk(self) -> None:
        plugin_root = ROOT / "src" / "assayer_frontend_audit"
        for path in plugin_root.glob("*.py"):
            self.assertNotIn(
                "assayer_platform", path.read_text(encoding="utf-8"), path.name,
            )

    def test_split_distribution_configs_are_sdk_only(self) -> None:
        import tomllib

        sdk = tomllib.loads(
            (ROOT / "packages" / "assayer-plugin-sdk" / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual("assayer-plugin-sdk", sdk["project"]["name"])
        self.assertEqual(["assayer_plugin_sdk"], sdk["tool"]["setuptools"]["packages"])
        self.assertIn(
            "schemas/*.json",
            sdk["tool"]["setuptools"]["package-data"]["assayer_plugin_sdk"],
        )

        plugin = tomllib.loads(
            (ROOT / "packages" / "assayer-plugin-frontend-audit" / "pyproject.toml").read_text(encoding="utf-8")
        )
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

    def test_split_distribution_dag_and_entry_point_ownership(self) -> None:
        import tomllib

        def config(name: str) -> dict:
            return tomllib.loads(
                (ROOT / "packages" / name / "pyproject.toml").read_text(encoding="utf-8")
            )

        expected_packages = {
            "assayer-plugin-sdk": ["assayer_plugin_sdk"],
            "assayer-plugin-frontend-audit": ["assayer_frontend_audit"],
            "assayer-provider-markdown": ["assayer_document_navigation"],
            "assayer-platform": ["assayer_platform", "assayer_host", "assayer_agent"],
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

        sdk_entry_points = config("assayer-plugin-sdk")["project"].get("entry-points", {})
        self.assertNotIn("assayer.plugins", sdk_entry_points)
        self.assertNotIn("assayer.providers", sdk_entry_points)

        self.assertIn(
            "assayer.frontend-audit",
            config("assayer-plugin-frontend-audit")["project"]["entry-points"]["assayer.plugins"],
        )
        self.assertIn(
            "markdown",
            config("assayer-provider-markdown")["project"]["entry-points"]["assayer.providers"],
        )


if __name__ == "__main__":
    unittest.main()
