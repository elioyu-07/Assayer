"""Shared kernel conformance checks across independent plugin implementations."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform import (
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformKernel,
    WorkItem,
    inspect_plugin_lifecycle,
    inspect_plugin_registration,
    load_plugin_manifest,
    validate_candidate_evidence_graph_projection,
)
from tests.helpers import config_quality_registration
from tests.helpers.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)


class RecordPlugin:
    manifest = load_plugin_manifest({
        "pluginId": "test.record-quality",
        "version": "1.0.0",
        "platformApiVersion": "1.0.0",
        "compatibility": {
            "protocolMinVersion": "1.2.0",
            "protocolMaxVersion": "1.2.0",
            "sdkMinVersion": "0.1.2",
            "sdkMaxVersion": "0.1.2",
        },
        "domains": ["record-quality"],
        "subjectKinds": ["record"],
        "checks": [{
            "checkId": "REC-001",
            "version": "1.0.0",
            "subjectKinds": ["record"],
            "dimensions": ["present"],
            "decisionStates": ["issue_found", "scanned_no_issue", "needs_review"],
            "requiredEvidenceKinds": ["record"],
            "requiredCapabilities": ["record_read"],
            "capabilityMissingOutcome": "needs_review",
            "invalidationSignals": ["source_digest"],
        }],
        "executionProfile": {
            "discoverBatching": "forbidden",
            "inspectBatching": "forbidden",
            "decisionBatching": "forbidden",
            "parallelism": "forbidden",
            "cacheReuse": "forbidden",
        },
    })

    def discover(self, scope, context):
        del context
        return tuple(
            WorkItem(
                f"record-{index}",
                "record",
                f"source-{index}",
                str(bool(value)),
                {"present": bool(value)},
            )
            for index, value in enumerate(scope)
        )

    def inspect(self, work_items, check, context):
        del context
        packets = []
        for item in work_items:
            evidence_id = f"evidence-{item.work_item_id}"
            status = "satisfied" if item.metadata["present"] else "violated"
            evidence = EvidenceRecord(
                evidence_id,
                item.work_item_id,
                check.check_id,
                check.version,
                "record",
                item.identity,
                {"present": item.metadata["present"]},
            )
            dimension = DimensionObservation(
                "present",
                ("Record presence was observed.",),
                (evidence_id,),
                status,
            )
            packets.append(InvestigationPacket(
                item,
                check.check_id,
                check.version,
                (dimension,),
                (evidence,),
                "not_required",
            ))
        return tuple(packets)


class RecordDecisionProvider:
    def decide(self, packets, check, context):
        del context
        proposals = []
        for packet in packets:
            observation = packet.dimensions[0]
            result = (
                "scanned_no_issue"
                if observation.candidate_status == "satisfied"
                else "issue_found"
            )
            finding = Finding(
                "present",
                observation.candidate_status,
                observation.observations[0],
            )
            proposals.append(DecisionProposal(
                packet.work_item.work_item_id,
                check.check_id,
                check.version,
                result,
                (finding,),
                "Record quality evaluated.",
            ))
        return tuple(proposals)


class CrossPluginConformanceTest(unittest.TestCase):
    def test_interactive_registration_without_domain_result_contract_is_rejected(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(),
        )

        report = inspect_plugin_registration(registration)

        self.assertIn(
            "PLUGIN_DOMAIN_RESULT_CONTRACT_REQUIRED",
            {issue.code for issue in report.issues},
        )

    def test_generated_common_review_registration_needs_no_domain_contract(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            result_features=frozenset({"common_review"}),
            domain_result_contracts=(),
        )

        report = inspect_plugin_registration(registration)

        self.assertNotIn(
            "PLUGIN_DOMAIN_RESULT_CONTRACT_REQUIRED",
            {issue.code for issue in report.issues},
        )

    def test_generic_lifecycle_gate_accepts_a_registered_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            registration = config_quality_registration()
            report = inspect_plugin_lifecycle(
                registration,
                {"files": [{"path": str(path)}]},
                "CFG-001",
                PlatformContext("generic-gate", frozenset({"structured_read"})),
                decision_provider=ConfigurationDecisionProvider(),
                review_builder=lambda result: result.decisions,
            )

        self.assertTrue(report.passed, report.as_dict())

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

    def test_independent_plugins_share_completed_and_issue_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            scenarios = (
                (
                    ConfigQualityPlugin(),
                    str(path),
                    "CFG-001",
                    ConfigurationDecisionProvider(),
                    "structured_read",
                ),
                (
                    RecordPlugin(),
                    ["available", ""],
                    "REC-001",
                    RecordDecisionProvider(),
                    "record_read",
                ),
            )
            for plugin, scope, check_id, provider, capability in scenarios:
                with self.subTest(plugin=plugin.manifest.plugin_id):
                    result = PlatformKernel().run(
                        plugin,
                        scope,
                        check_id,
                        provider,
                        PlatformContext("run", frozenset({capability})),
                    )
                    self.assertEqual(result.status, "completed")
                    self.assertTrue(result.decisions)
                    self.assertTrue(all(
                        decision.result in {"scanned_no_issue", "issue_found"}
                        for decision in result.decisions
                    ))

    def test_independent_plugins_share_capability_failure_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            scenarios = (
                (
                    ConfigQualityPlugin(),
                    str(path),
                    "CFG-001",
                    ConfigurationDecisionProvider(),
                ),
                (
                    RecordPlugin(),
                    ["available"],
                    "REC-001",
                    RecordDecisionProvider(),
                ),
            )
            for plugin, scope, check_id, provider in scenarios:
                with self.subTest(plugin=plugin.manifest.plugin_id):
                    result = PlatformKernel().run(
                        plugin,
                        scope,
                        check_id,
                        provider,
                        PlatformContext("run", frozenset()),
                    )
                    self.assertEqual(result.status, "partial")
                    self.assertEqual(result.decisions[0].result, "needs_review")


if __name__ == "__main__":
    unittest.main()
