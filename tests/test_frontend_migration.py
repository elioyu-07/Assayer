"""Deterministic acceptance for the compiler-generated Frontend Policy Pack."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from assayer_browser_provider import browser_registration
from assayer_platform import (
    CapabilityProfile,
    InteractivePluginController,
    PluginRegistry,
    ProviderRegistry,
    inspect_plugin_registration,
)
from assayer_platform.simple_plugin_compiler import compile_simple_plugin
from assayer_plugin_sdk import BrowserSnapshot


class _SnapshotSource:
    def __init__(self) -> None:
        self.snapshot = BrowserSnapshot(
            visible_text="Orders\nFilter\nQuery\nReset",
            entrypoints=(
                {"kind": "safe_action", "intent": "query"},
                {"kind": "safe_action", "intent": "reset"},
            ),
            candidates=(
                {"kind": "filter_region", "label": "Order filters"},
                {"kind": "result_list", "label": "Orders"},
            ),
            network_summary={"status": "not_observed"},
            route="/orders",
            structure_summary={"fields": 1, "tables": 1},
            dom_digest="a" * 64,
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
        )

    def observe_snapshot(self) -> BrowserSnapshot:
        return self.snapshot


def _load_compiled_registration(root: Path):
    generated = root / "generated"
    compile_simple_plugin(Path("plugins/frontend-audit"), generated)
    package = generated / "src" / "assayer_frontend_audit"
    module_name = "generated_frontend_simple"
    spec = importlib.util.spec_from_file_location(
        module_name,
        package / "__init__.py",
        submodule_search_locations=[str(package)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError("compiler did not produce an importable registration")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module_name, module.registration


class FrontendSimpleCompilationTests(unittest.TestCase):
    def test_frontend_source_has_no_runtime_adapter(self):
        package = Path("src/assayer_frontend_audit")
        self.assertFalse((package / "__init__.py").exists())
        self.assertFalse((package / "runtime.py").exists())

    def test_compiled_registration_is_the_only_frontend_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_name, registration = _load_compiled_registration(root)
            try:
                report = inspect_plugin_registration(
                    registration, construct_implementations=True,
                )
                self.assertTrue(report.passed, report.as_dict())
                self.assertEqual(registration.manifest.plugin_id, "assayer.frontend-audit")
                self.assertEqual(registration.result_features, {"common_review"})
                self.assertEqual(registration.domain_result_contracts, ())
            finally:
                sys.modules.pop(module_name, None)

    def test_compiled_registration_runs_against_browser_snapshot_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_name, registration = _load_compiled_registration(root)
            try:
                source = _SnapshotSource()
                controller = InteractivePluginController(
                    PluginRegistry((registration,)),
                    root / "output",
                    provider_registry=ProviderRegistry((browser_registration(),)),
                    provider_runtime=source,
                    platform_profile=CapabilityProfile(frozenset({"browser_snapshot"})),
                    user_profile=CapabilityProfile(frozenset({"browser_snapshot"})),
                )
                try:
                    started = controller.start(
                        plugin_id="assayer.frontend-audit",
                        check_id="FUA-10",
                        scope={"url": source.snapshot.url},
                    )
                    boundary = controller.advance(started["runId"])
                finally:
                    controller.close()
                self.assertEqual(boundary["status"], "awaiting_agent_decision")
                self.assertEqual(
                    boundary["result"]["semanticTask"]["items"][0]["kind"],
                    "dimension",
                )
            finally:
                sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
