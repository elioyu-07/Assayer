import ast
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


if __name__ == "__main__":
    unittest.main()
