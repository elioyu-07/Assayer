from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_host.transport import CompiledPlatformMcpToolTransport
from assayer_platform.compiled_plugin_lifecycle import CompiledPluginLifecycleManager
from assayer_platform.declaration_compiler import compile_plugin_contract
from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.provider_registry import ProviderRegistry
from assayer_document_navigation import markdown_registration
from assayer_platform.contract import CapabilityProfile


FIXTURE = Path(__file__).parent / "fixtures" / "plugins" / "policy-pack"


def _result(response: dict) -> dict:
    return response["structuredContent"]["result"]


class CompiledTransportTests(unittest.TestCase):
    def _transport(self, root: Path) -> CompiledPlatformMcpToolTransport:
        artifact = root / "artifact"
        compile_plugin_contract(FIXTURE, artifact)
        CompiledPluginLifecycleManager(
            PluginInstallationStore(root / "store"),
        ).install(artifact / "compiled-plugin.json")
        return CompiledPlatformMcpToolTransport(
            root / "runs", store_root=root / "store",
            provider_registry=ProviderRegistry(),
        )

    def test_markdown_pages_review_and_result_survive_transport_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._transport(root).close()
            document = root / "input.md"
            document.write_text("# Overview\n\nFirst.\n\nSecond.\n", encoding="utf-8")
            transport = CompiledPlatformMcpToolTransport(
                root / "runs", store_root=root / "store",
                provider_registry=ProviderRegistry([markdown_registration()]),
                platform_profile=CapabilityProfile(frozenset({"document_navigation"}), limits={"maxItems": 1}),
            )
            self.addCleanup(transport.close)

            def call(name, arguments):
                response = transport.call_tool(name, arguments)
                self.assertFalse(response["isError"], response)
                return _result(response)

            run_id = call("start_compiled_run", {
                "pluginId": "test.policy-pack", "checkId": "POLICY-001",
                "scope": {"files": [str(document)]},
            })["runId"]
            call("bind_provider", {"runId": run_id})
            self.assertEqual(len(call("discover_sources", {"runId": run_id})["workItems"]), 1)
            collected = call("collect_evidence", {"runId": run_id})
            self.assertEqual(len(collected["collections"]), 3)
            plan = call("plan_review_batches", {"runId": run_id, "maxBatchItems": 1})["coverage"]
            self.assertEqual(len(plan["atoms"]), 3)
            self.assertEqual(len(plan["batches"]), 3)
            atoms = {a["atomId"]: a for a in plan["atoms"]}
            first, second = plan["atoms"][:2]
            rejected = _result(transport.call_tool("submit_review_batch", {
                "runId": run_id, "batchId": plan["batches"][0]["batchId"],
                "decisions": [{"atomId": first["atomId"], "state": "satisfied", "evidenceRefs": second["payload"]["evidenceRefs"]}],
            }))
            self.assertEqual(rejected["error"]["code"], "INVALID_REVIEW_DECISION")
            for batch in plan["batches"]:
                call("submit_review_batch", {
                    "runId": run_id, "batchId": batch["batchId"],
                    "decisions": [{"atomId": aid, "state": "satisfied", "rationale": "Reviewed the frozen source element.", "evidenceRefs": atoms[aid]["payload"]["evidenceRefs"]} for aid in batch["atomIds"]],
                })
            result = call("finalize_compiled_run", {"runId": run_id})
            self.assertEqual(call("get_compiled_result", {"runId": run_id}), result)
            transport.close()
            restarted = CompiledPlatformMcpToolTransport(root / "runs", store_root=root / "store", provider_registry=ProviderRegistry())
            self.addCleanup(restarted.close)
            restored = _result(restarted.call_tool("get_compiled_result", {"runId": run_id}))
            self.assertEqual(restored, result)
            self.assertEqual(len(restored["coverage"]["verdicts"]), 3)

    def test_only_compiled_execution_tools_are_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory))
            names = {item["name"] for item in transport.list_tools()}

        self.assertEqual(names, {
            "list_compiled_plugins", "start_compiled_run", "bind_provider",
            "discover_sources", "collect_evidence", "plan_review_batches",
            "submit_review_batch", "finalize_compiled_run", "get_compiled_result",
        })
        self.assertTrue(names.isdisjoint({"run_plugin", "start_plugin_run", "advance_plugin_run"}))

    def test_provider_required_run_fails_closed_without_installed_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "input.md"
            document.write_text("# Delivery\n\nNo overview is present.\n", encoding="utf-8")
            transport = self._transport(root)
            run_id = _result(transport.call_tool("start_compiled_run", {
                "pluginId": "test.policy-pack",
                "checkId": "POLICY-001",
                "scope": {"files": [{"path": str(document)}]},
            }))["runId"]

            result = _result(transport.call_tool("bind_provider", {"runId": run_id}))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "PROVIDER_NOT_FOUND")

    def test_unknown_compiled_plugin_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory))
            result = _result(transport.call_tool("start_compiled_run", {
                "pluginId": "missing.plugin", "checkId": "MISSING-001", "scope": {},
            }))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "PLUGIN_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
