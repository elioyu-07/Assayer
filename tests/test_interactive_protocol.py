import tempfile
import unittest
from pathlib import Path

from assayer_host import InteractivePlatformMcpToolTransport
from assayer_platform import (
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PluginRegistration,
    PluginRegistry,
    PlatformContext,
    WorkItem,
    DecisionProposal,
    load_plugin_manifest,
)


MANIFEST = load_plugin_manifest({
    "pluginId": "fixture.interactive-quality",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
    "domains": ["fixture"],
    "subjectKinds": ["fixture_item"],
    "checks": [{
        "checkId": "FIX-INT-001", "version": "1.0.0",
        "subjectKinds": ["fixture_item"], "dimensions": ["present"],
        "decisionStates": ["scanned_no_issue", "needs_review"],
        "requiredEvidenceKinds": ["structured"],
        "requiredCapabilities": ["fixture_read"],
        "capabilityMissingOutcome": "needs_review",
        "invalidationSignals": ["source_digest"],
    }],
    "executionProfile": {
        "discoverBatching": "forbidden", "inspectBatching": "forbidden",
        "decisionBatching": "forbidden", "parallelism": "forbidden",
        "cacheReuse": "forbidden", "checkpoint": "required",
    },
})


class FixturePlugin:
    manifest = MANIFEST

    def discover(self, scope, context):
        del scope, context
        return (WorkItem("fixture:1", "fixture_item", "fixture-source", "state-1"),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        evidence = EvidenceRecord(
            "evidence:fixture:1", item.work_item_id, check.check_id, check.version,
            "structured", item.identity, {"present": True},
        )
        return (InvestigationPacket(
            item, check.check_id, check.version,
            (DimensionObservation("present", ("The fixture is present.",), (evidence.evidence_id,), "satisfied"),),
            (evidence,), "not_required",
        ),)


class FixtureProvider:
    def decide(self, packets, check, context):
        del context
        packet = packets[0]
        return (DecisionProposal(
            packet.work_item.work_item_id, check.check_id, check.version,
            "scanned_no_issue",
            (Finding("present", "satisfied", "The fixture is present."),),
            "The fixture satisfies the Check.",
        ),)


def registration():
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: FixturePlugin(),
        decision_provider_factory=lambda runtime=None: FixtureProvider(),
        capabilities=frozenset({"fixture_read"}),
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object"},
    )


class InteractiveProtocolTest(unittest.TestCase):
    def test_controller_runs_domain_neutral_lifecycle(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality",
                "checkId": "FIX-INT-001", "scope": {"target": "fixture"},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            self.assertEqual(started["structuredContent"]["result"]["status"], "started")
            discovered = transport.call_tool("discover_work_items", {})
            self.assertEqual(len(discovered["structuredContent"]["result"]["result"]["workItems"]), 1)
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            item_id = packet["workItem"]["workItemId"]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": item_id, "result": "scanned_no_issue",
                "findings": [{"dimension": "present", "status": "satisfied", "reason": "The fixture is present."}],
                "reason": "The fixture satisfies the Check.",
            }]})
            progress = transport.call_tool("get_plugin_progress", {})
            self.assertEqual(progress["structuredContent"]["result"]["result"]["decisionsCommitted"], 1)
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(finished["structuredContent"]["result"]["status"], "completed")

    def test_tool_catalog_contains_only_domain_neutral_names(self):
        transport = InteractivePlatformMcpToolTransport(plugin_registry=PluginRegistry((registration(),)))
        tools = transport.list_tools()
        self.assertEqual(
            [item["name"] for item in tools],
            ["start_plugin_run", "discover_work_items", "inspect_work_items", "submit_decisions",
             "recover_work_item", "get_plugin_progress", "finish_plugin_run"],
        )
        for item in tools:
            self.assertNotIn("runId", item["inputSchema"].get("properties", {}))


if __name__ == "__main__":
    unittest.main()
