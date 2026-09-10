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

    def test_plugin_facing_validators_use_the_sdk_schema_root(self) -> None:
        sdk = ROOT / "src" / "assayer_plugin_sdk"
        for name in ("actionable_result.py", "evidence_claim.py", "evaluation.py"):
            text = (sdk / name).read_text(encoding="utf-8")
            self.assertNotIn("_schema_root", text, name)
            self.assertIn("assayer_plugin_sdk.resources", text, name)


if __name__ == "__main__":
    unittest.main()
