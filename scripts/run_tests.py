"""Run Assayer's explicit fast or full verification profile.

The fast profile is dependency-light and deliberately excludes tests that
start Chromium or instantiate the optional MCP SDK server.  The full profile
is the release gate: it proves the optional dependencies and Chromium runtime
are usable, runs every test, and treats every skip as a failure.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE_SRCS = tuple(sorted(
    path for path in (ROOT / "packages").glob("*/src") if path.is_dir()
))
for source_root in reversed((SRC, *PACKAGE_SRCS)):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
existing_pythonpath = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (
        str(SRC), *(str(path) for path in PACKAGE_SRCS), existing_pythonpath,
    ) if part
)

MCP_SDK_TEST_ID = "test_transport.TransportTest.test_optional_fastmcp_server_registers_single_argument_tools"
INTEGRATION_TEST_PREFIXES = frozenset({
    "test_browser_playwright.",
    "test_cli_plugin_lifecycle.",
    "test_external_plugin_package.",
    "test_isolated_lifecycle_acceptance.",
    "test_mcp_stdio_integration.",
    "test_plugin_lifecycle_mcp.",
    "test_plugin_verify.",
})
SLOW_TEST_PREFIXES = frozenset({
    "test_plugin_release_gate.PluginReleaseGateTest.test_isolated_install_",
    "test_plugin_release_gate.PluginReleaseGateTest.test_complete_release_gate_builds_",
    "test_plugin_release_gate.PluginReleaseGateTest.test_strict_",
    "test_provider_release_gate.ProviderReleaseGateTests.test_isolated_install_",
})
REQUIRED_BROWSER_TEST_IDS = frozenset({
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_page_flows_through_host_core",
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_failures_invalidate_session",
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_network_policy_blocks_unsafe_requests",
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_recovery_crosses_required_barriers",
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_interaction_evidence_is_bound_to_real_state",
    "test_browser_playwright.PlaywrightReadonlyIntegrationTest."
    "test_real_chromium_typed_controls_enforce_readonly_and_restore",
})


def iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def discover_tests() -> list[unittest.TestCase]:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    return list(iter_tests(suite))


def fast_tests(tests: list[unittest.TestCase]) -> unittest.TestSuite:
    selected = [
        test for test in tests
        if not any(
            test.id().removeprefix("tests.").startswith(prefix)
            for prefix in INTEGRATION_TEST_PREFIXES
        )
        and not any(
            test.id().removeprefix("tests.").startswith(prefix)
            for prefix in SLOW_TEST_PREFIXES
        )
        and test.id().removeprefix("tests.") != MCP_SDK_TEST_ID
    ]
    return unittest.TestSuite(selected)


def preflight_full(tests: list[unittest.TestCase]) -> None:
    missing = []
    for module in ("jsonschema", "playwright.sync_api", "mcp"):
        try:
            available = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            available = False
        if not available:
            missing.append(module)
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"full verification dependencies are missing: {names}; "
            "install the Python dependencies with: python -m pip install -e '.[test]' "
            "and Playwright with: pip install playwright"
        )

    normalized_ids = [test.id().removeprefix("tests.") for test in tests]
    missing_browser_behaviors = REQUIRED_BROWSER_TEST_IDS.difference(normalized_ids)
    if missing_browser_behaviors:
        raise RuntimeError(
            "full verification is missing required real-browser behaviors: "
            + ", ".join(sorted(missing_browser_behaviors))
        )
    if MCP_SDK_TEST_ID not in normalized_ids:
        raise RuntimeError("full verification did not discover the real MCP SDK test")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Assayer verification profiles")
    parser.add_argument("profile", choices=("fast", "full"))
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    tests = discover_tests()
    if args.profile == "full":
        try:
            preflight_full(tests)
        except RuntimeError as error:
            print(f"C07.2 full verification preflight failed: {error}", file=sys.stderr)
            return 2
        suite = unittest.TestSuite(tests)
    else:
        suite = fast_tests(tests)

    runner = unittest.TextTestRunner(verbosity=1 if args.quiet else 2)
    result = runner.run(suite)
    if args.profile == "full" and result.skipped:
        print("C07.2 full verification forbids skipped tests:", file=sys.stderr)
        for test, reason in result.skipped:
            print(f"- {test.id()}: {reason}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
