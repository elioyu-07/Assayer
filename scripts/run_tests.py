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
SPEC_PLUGIN_SRC = ROOT / "plugins" / "spec-quality" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SPEC_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(SPEC_PLUGIN_SRC))
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
existing_pythonpath = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (str(SRC), str(SPEC_PLUGIN_SRC), existing_pythonpath) if part
)

BROWSER_TEST_PREFIX = "test_browser_playwright."
MCP_SDK_TEST_ID = "test_transport.TransportTest.test_optional_fastmcp_server_registers_single_argument_tools"
MIN_BROWSER_TESTS = 25


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
        if not test.id().removeprefix("tests.").startswith(BROWSER_TEST_PREFIX)
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
            "install with: python -m pip install -e '.[test]'"
        )

    normalized_ids = [test.id().removeprefix("tests.") for test in tests]
    browser_count = sum(test_id.startswith(BROWSER_TEST_PREFIX) for test_id in normalized_ids)
    if browser_count < MIN_BROWSER_TESTS:
        raise RuntimeError(
            f"full verification discovered only {browser_count} real-browser tests; "
            f"expected at least {MIN_BROWSER_TESTS}"
        )
    if MCP_SDK_TEST_ID not in normalized_ids:
        raise RuntimeError("full verification did not discover the real MCP SDK test")

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium", headless=True, timeout=30_000)
            try:
                page = browser.new_page()
                page.set_content("<title>Assayer full verification</title>")
                if page.title() != "Assayer full verification":
                    raise RuntimeError("Chromium preflight returned an unexpected page title")
            finally:
                browser.close()
    except Exception as error:
        raise RuntimeError(
            "full verification cannot launch Playwright Chromium; "
            "install it with: python -m playwright install chromium"
        ) from error


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
