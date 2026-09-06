"""Shared kernel conformance checks across independent plugin implementations."""

import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformKernel,
    WorkItem,
    load_plugin_manifest,
    validate_candidate_evidence_graph_projection,
)
from assayer_platform.testing.config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider
from assayer_platform.builtin_plugins.frontend_audit import FrontendAuditPlugin, FrontendDecisionProvider, FrontendLedgerCommitter, ProductFrontendRuntime
from assayer_host import (
    DeterministicEvidenceAdapter,
    DeterministicLoginAdapter,
    DeterministicObjectIdentityAdapter,
    DeterministicPageAdapter,
    DeterministicRecoveryAdapter,
    HostCore,
)
from assayer_host.transport import ProductMcpToolTransport


class RecordPlugin:
    manifest = load_plugin_manifest({
        "pluginId": "test.record-quality", "version": "1.0.0", "platformApiVersion": "1.0.0",
        "domains": ["record-quality"], "subjectKinds": ["record"],
        "checks": [{
            "checkId": "REC-001", "version": "1.0.0", "subjectKinds": ["record"],
            "dimensions": ["present"],
            "decisionStates": ["issue_found", "scanned_no_issue", "needs_review"],
            "requiredEvidenceKinds": ["record"], "requiredCapabilities": ["record_read"],
            "capabilityMissingOutcome": "needs_review", "invalidationSignals": ["source_digest"],
        }],
        "executionProfile": {
            "discoverBatching": "forbidden", "inspectBatching": "forbidden",
            "decisionBatching": "forbidden", "parallelism": "forbidden",
            "cacheReuse": "forbidden", "checkpoint": "required",
        },
    })

    def discover(self, scope, context):
        del context
        return tuple(WorkItem(f"record-{index}", "record", f"source-{index}", str(bool(value)), {"present": bool(value)})
                     for index, value in enumerate(scope))

    def inspect(self, work_items, check, context):
        del context
        packets = []
        for item in work_items:
            evidence_id = f"evidence-{item.work_item_id}"
            status = "satisfied" if item.metadata["present"] else "violated"
            evidence = EvidenceRecord(evidence_id, item.work_item_id, check.check_id, check.version, "record", item.identity, {"present": item.metadata["present"]})
            dimension = DimensionObservation("present", ("Record presence was observed.",), (evidence_id,), status)
            packets.append(InvestigationPacket(item, check.check_id, check.version, (dimension,), (evidence,), "not_required"))
        return tuple(packets)


class RecordDecisionProvider:
    def decide(self, packets, check, context):
        del context
        proposals = []
        for packet in packets:
            observation = packet.dimensions[0]
            result = "scanned_no_issue" if observation.candidate_status == "satisfied" else "issue_found"
            finding = Finding("present", observation.candidate_status, observation.observations[0])
            proposals.append(DecisionProposal(packet.work_item.work_item_id, check.check_id, check.version, result, (finding,), "Record quality evaluated."))
        return tuple(proposals)


class CrossPluginConformanceTest(unittest.TestCase):
    def test_config_plugin_can_publish_platform_evidence_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            plugin = ConfigQualityPlugin()
            context = PlatformContext("run", frozenset({"structured_read"}))
            item = plugin.discover(str(path), context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            graph = packet.evidence[0].payload["candidateGraph"]
            validate_candidate_evidence_graph_projection(graph)
            self.assertEqual(graph["candidateCount"], 3)
            self.assertTrue(graph["coverageComplete"])

    def test_frontend_adapter_maps_runtime_packets_without_kernel_browser_types(self):
        class Runtime:
            def discover_work_items(self, scope, context):
                del scope, context
                return [WorkItem("object-1", "frontend_object", "object-identity", "runtime-state", {"label": "filter"})]

            def inspect_work_items(self, work_items, check, context):
                del context
                item = work_items[0]
                evidence = tuple(EvidenceRecord(f"evidence-{kind}", item.work_item_id, check.check_id, check.version, kind, item.identity, {"observed": True}) for kind in ("runtime_dom", "runtime_visual"))
                dimensions = tuple(DimensionObservation(name, ("The runtime evidence supports this dimension.",), (evidence[0].evidence_id, evidence[1].evidence_id), "satisfied") for name in check.dimensions)
                return [InvestigationPacket(item, check.check_id, check.version, dimensions, evidence, "restored")]

        plugin = FrontendAuditPlugin(Runtime())
        result = PlatformKernel().run(plugin, {"url": "https://example.test"}, "FUA-10", FrontendDecisionProvider(), PlatformContext("run", frozenset({"structured_read", "visual_read"})))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.decisions[0].result, "scanned_no_issue")

    def test_legacy_product_facade_is_translated_to_platform_work_item_and_packet(self):
        class ProductCaller:
            def call_tool(self, name, arguments):
                if name == "discover_scope":
                    return {"structuredContent": {"status": "ok", "result": {
                        "currentPageStateId": "page-1",
                        "candidates": [{"candidateId": "candidate-1", "status": "pending", "kind": "filter_region", "label": "Orders", "potentialRules": [{"ruleId": "FUA-10", "version": "1.1.0"}]}],
                    }}}
                if name != "investigate_object":
                    raise AssertionError(f"Unexpected product tool: {name}")
                return {"structuredContent": {"status": "ok", "result": {
                    "observation": {"observationScope": "viewport", "logicalLists": []},
                    "evidence": {"payloadType": "json"}, "evidenceRefs": ["legacy-evidence"],
                    "caseId": "case-1",
                    "recovery": {"finalStatus": "restored"}, "readyForDecision": True,
                }}}

        runtime = ProductFrontendRuntime(ProductCaller())
        plugin = FrontendAuditPlugin(runtime)
        context = PlatformContext("run", frozenset({"structured_read", "visual_read"}))
        result = PlatformKernel().run(plugin, {"url": "https://example.test"}, "FUA-10", FrontendDecisionProvider(), context)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.decisions[0].result, "needs_review")
        self.assertEqual(result.decisions[0].work_item_id, "frontend:candidate-1")

    def test_actual_deterministic_frontend_facade_reaches_platform_kernel(self):
        core = HostCore(
            login_adapter=DeterministicLoginAdapter(),
            page_adapter=DeterministicPageAdapter(),
            object_identity_adapter=DeterministicObjectIdentityAdapter(),
            evidence_adapter=DeterministicEvidenceAdapter(),
            recovery_adapter=DeterministicRecoveryAdapter(),
        )
        caller = ProductMcpToolTransport(core)
        try:
            caller.call_tool("start_audit", {"url": "https://test.example.com"})
            plugin = FrontendAuditPlugin(ProductFrontendRuntime(caller))
            result = PlatformKernel().run(
                plugin, {}, "FUA-10", FrontendDecisionProvider(),
                PlatformContext("platform-run", frozenset({"structured_read", "visual_read"})),
                committer=FrontendLedgerCommitter(caller),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.decisions[0].result, "needs_review")
            self.assertEqual(result.metrics["inspectionBatches"], 1)
            self.assertEqual(result.metrics["durableCommits"], 1)
            self.assertEqual(result.receipts[0].durability, "durable")
            self.assertEqual(result.ledger.decision_authority, "platform")
            self.assertEqual(result.receipts[0].authority, "platform")
            self.assertTrue(result.receipts[0].metadata["hostAssessmentId"])
            self.assertNotEqual(result.receipts[0].commit_id, result.receipts[0].metadata["hostAssessmentId"])
        finally:
            caller.close()

    def test_independent_plugins_share_completed_and_issue_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            scenarios = (
                (ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), "structured_read"),
                (RecordPlugin(), ["available", ""], "REC-001", RecordDecisionProvider(), "record_read"),
            )
            for plugin, scope, check_id, provider, capability in scenarios:
                with self.subTest(plugin=plugin.manifest.plugin_id):
                    result = PlatformKernel().run(plugin, scope, check_id, provider, PlatformContext("run", frozenset({capability})))
                    self.assertEqual(result.status, "completed")
                    self.assertTrue(result.decisions)
                    self.assertTrue(all(decision.result in {"scanned_no_issue", "issue_found"} for decision in result.decisions))

    def test_independent_plugins_share_capability_failure_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{}', encoding="utf-8")
            scenarios = (
                (ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider()),
                (RecordPlugin(), ["available"], "REC-001", RecordDecisionProvider()),
            )
            for plugin, scope, check_id, provider in scenarios:
                with self.subTest(plugin=plugin.manifest.plugin_id):
                    result = PlatformKernel().run(plugin, scope, check_id, provider, PlatformContext("run", frozenset()))
                    self.assertEqual(result.status, "partial")
                    self.assertEqual(result.decisions[0].result, "needs_review")


if __name__ == "__main__":
    unittest.main()
