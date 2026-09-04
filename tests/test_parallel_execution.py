from __future__ import annotations

import json
import threading
import time
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    ParallelExecutionPlanner,
    CapabilityProfile,
    PlatformContext,
    PlatformKernel,
    PlatformRunner,
    PluginRegistration,
    PluginRegistry,
    ProviderFact,
    ProviderRegistration,
    ProviderRegistry,
    ProviderResponse,
    WorkItem,
    load_plugin_manifest,
    load_provider_descriptor,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


MANIFEST = load_plugin_manifest({
    "pluginId": "fixture.parallel-plugin",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
    "domains": ["fixture"],
    "subjectKinds": ["fixture_item"],
    "checks": [{
        "checkId": "PAR-001",
        "version": "1.0.0",
        "subjectKinds": ["fixture_item"],
        "dimensions": ["observed"],
        "decisionStates": ["scanned_no_issue", "needs_review"],
        "requiredEvidenceKinds": ["structured"],
        "requiredCapabilities": ["structured_read"],
        "capabilityMissingOutcome": "blocked",
        "invalidationSignals": ["source_digest"],
    }],
    "executionProfile": {
        "discoverBatching": "forbidden",
        "inspectBatching": "forbidden",
        "decisionBatching": "allowed",
        "parallelism": "allowed",
        "cacheReuse": "forbidden",
        "checkpoint": "required",
        "maxBatchSize": 2,
        "ordering": "independent",
        "failureSplitting": "forbidden",
    },
})


class ParallelFixturePlugin:
    manifest = MANIFEST

    def __init__(self, *, fail_item=None, delay=0.02):
        self.fail_item = fail_item
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def discover(self, scope, context):
        del context
        return tuple(
            WorkItem(
                f"fixture-item-{index}",
                "fixture_item",
                f"fixture-source-{index}",
                f"fixture-state-{index}",
            )
            for index in range(scope["count"])
        )

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        with self.lock:
            self.calls.append(item.work_item_id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if item.work_item_id == self.fail_item:
                raise RuntimeError("The deterministic fixture failed")
            evidence = EvidenceRecord(
                f"evidence-{item.work_item_id}",
                item.work_item_id,
                check.check_id,
                check.version,
                "structured",
                item.identity,
                {"observed": True},
            )
            return (InvestigationPacket(
                item,
                check.check_id,
                check.version,
                (DimensionObservation(
                    "observed",
                    ("The item was observed.",),
                    (evidence.evidence_id,),
                    "satisfied",
                ),),
                (evidence,),
                "not_required",
            ),)
        finally:
            with self.lock:
                self.active -= 1


class FixtureDecisionProvider:
    def decide(self, packets, check, context):
        del context
        return tuple(DecisionProposal(
            packet.work_item.work_item_id,
            check.check_id,
            check.version,
            "scanned_no_issue",
            (Finding("observed", "satisfied", "The item was observed."),),
            "The deterministic item satisfies the Check.",
        ) for packet in packets)


def context(run_id, concurrency=None):
    limits = {} if concurrency is None else {"maxConcurrency": concurrency}
    return PlatformContext(run_id, frozenset({"structured_read"}), limits)


class ParallelExecutionTests(unittest.TestCase):
    def test_planner_is_fail_closed_and_clamps_workers_to_available_work(self):
        planner = ParallelExecutionPlanner()
        profile = MANIFEST.execution_profile
        enabled = planner.plan(profile, {"maxConcurrency": 8}, task_count=3)
        missing = planner.plan(profile, {}, task_count=3)
        one = planner.plan(profile, {"maxConcurrency": 1}, task_count=3)
        forbidden = planner.plan(
            replace(profile, parallelism="forbidden"),
            {"maxConcurrency": 8},
            task_count=3,
        )
        self.assertEqual((enabled.mode, enabled.worker_count), ("parallel", 3))
        self.assertEqual((missing.mode, missing.reason), ("serial", "limit_missing"))
        self.assertEqual((one.mode, one.reason), ("serial", "limit_one"))
        self.assertEqual((forbidden.mode, forbidden.reason), ("serial", "plugin_forbidden"))

    def test_kernel_parallelizes_only_independent_inspection_and_merges_in_order(self):
        plugin = ParallelFixturePlugin()
        result = PlatformKernel().run(
            plugin,
            {"count": 4},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-parallel", 2),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(plugin.max_active, 2)
        self.assertEqual(result.metrics["parallelEnabled"], 1)
        self.assertEqual(result.metrics["parallelWorkers"], 2)
        self.assertEqual(result.metrics["parallelTasks"], 4)
        self.assertGreater(result.metrics["parallelEstimatedWaitSavedMs"], 0)
        expected = [f"fixture-item-{index}" for index in range(4)]
        self.assertEqual(
            [packet.work_item.work_item_id for packet in result.ledger.investigations],
            expected,
        )
        self.assertEqual(
            [decision.work_item_id for decision in result.decisions],
            expected,
        )
        inspect_operations = [
            operation for operation in result.ledger.operations
            if operation.kind == "inspect"
        ]
        self.assertEqual(
            [operation.work_item_id for operation in inspect_operations],
            expected,
        )

    def test_missing_runtime_concurrency_budget_falls_back_to_serial(self):
        plugin = ParallelFixturePlugin()
        result = PlatformKernel().run(
            plugin,
            {"count": 3},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-serial"),
        )
        plan = next(
            event for event in result.ledger.events
            if event.name == "inspection.parallel.planned"
        )
        self.assertEqual(plugin.max_active, 1)
        self.assertEqual(result.metrics["parallelEnabled"], 0)
        self.assertEqual(plan.details["reason"], "limit_missing")

    def test_one_parallel_failure_does_not_retry_or_contaminate_successful_peers(self):
        plugin = ParallelFixturePlugin(fail_item="fixture-item-1")
        result = PlatformKernel().run(
            plugin,
            {"count": 4},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-partial", 3),
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.decisions), 3)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].work_item_id, "fixture-item-1")
        self.assertEqual(sorted(plugin.calls), [f"fixture-item-{index}" for index in range(4)])
        self.assertEqual(len(plugin.calls), 4)

    def test_unsafe_ordering_is_rejected_before_inspection(self):
        plugin = ParallelFixturePlugin(delay=0)
        plugin.manifest = replace(
            MANIFEST,
            execution_profile=replace(MANIFEST.execution_profile, ordering="strict"),
        )
        result = PlatformKernel().run(
            plugin,
            {"count": 3},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-unsafe-ordering", 3),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(plugin.calls, [])
        self.assertEqual(
            {failure.code for failure in result.failures},
            {"PARALLEL_ORDERING_UNSAFE"},
        )

    def test_parallel_and_serial_runs_have_equivalent_evidence_and_decisions(self):
        parallel = PlatformKernel().run(
            ParallelFixturePlugin(delay=0),
            {"count": 4},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-parallel-equivalence", 4),
        )
        serial_plugin = ParallelFixturePlugin(delay=0)
        serial_plugin.manifest = replace(
            MANIFEST,
            execution_profile=replace(MANIFEST.execution_profile, parallelism="forbidden"),
        )
        serial = PlatformKernel().run(
            serial_plugin,
            {"count": 4},
            "PAR-001",
            FixtureDecisionProvider(),
            context("run-serial-equivalence", 4),
        )

        def facts(result):
            return [(
                packet.work_item.work_item_id,
                packet.work_item.identity,
                packet.work_item.state_digest,
                tuple((item.kind, item.source_identity, dict(item.payload)) for item in packet.evidence),
            ) for packet in result.ledger.investigations]

        def decisions(result):
            return [(
                decision.work_item_id,
                decision.result,
                tuple((finding.dimension, finding.status, finding.reason) for finding in decision.findings),
                decision.reason,
            ) for decision in result.decisions]

        self.assertEqual(facts(parallel), facts(serial))
        self.assertEqual(decisions(parallel), decisions(serial))

    def test_public_parallel_plan_schema_accepts_every_planner_result(self):
        schemas = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
        schema = schemas["parallel-execution.schema.json"]
        validator = Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        )
        validator.validate(ParallelExecutionPlanner().plan(
            MANIFEST.execution_profile,
            {"maxConcurrency": 2},
            task_count=4,
        ).as_dict())

    def test_provider_bound_runner_never_exceeds_negotiated_provider_ceiling(self):
        failures = tuple(
            {
                "code": code,
                "retry": "resolve_unknown_first" if code == "result_unknown" else "never",
            }
            for code in (
                "capability_unavailable",
                "authorization_denied",
                "timeout",
                "budget_exceeded",
                "source_changed",
                "stale_state",
                "source_error",
                "result_unknown",
            )
        )
        descriptor = load_provider_descriptor({
            "providerId": "fixture.parallel-provider",
            "version": "1.0.0",
            "platformApiVersion": "1.0.0",
            "capabilities": [{
                "name": "structured_read",
                "version": "1.0.0",
                "accessMode": "read_only",
                "evidenceKinds": ["structured"],
            }],
            "scopeSchema": {"type": "object", "additionalProperties": False},
            "authorization": {"userScopeRequired": True, "secretHandling": "none"},
            "limits": {
                "timeoutMs": 30000,
                "maxBytes": 100000,
                "maxItems": 10,
                "maxConcurrency": 2,
            },
            "failurePolicy": list(failures),
            "algorithmVersions": {
                "sourceIdentity": "1.0.0",
                "stateDigest": "1.0.0",
            },
        })

        class BoundedProvider:
            def __init__(self):
                self.descriptor = descriptor
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def collect(self, request, runtime_context):
                del runtime_context
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.02)
                    return ProviderResponse(
                        request.request_id,
                        request.provider_id,
                        request.provider_version,
                        request.capability,
                        "succeeded",
                        (ProviderFact(
                            "structured",
                            request.source_identity,
                            request.state_digest,
                            {"observed": True},
                        ),),
                    )
                finally:
                    with self.lock:
                        self.active -= 1

        class ProviderBackedPlugin(ParallelFixturePlugin):
            def __init__(self, provider):
                super().__init__(delay=0)
                self.provider = provider

            def inspect(self, work_items, check, runtime_context):
                del runtime_context
                item = work_items[0]
                result = self.provider.collect(item, check, "structured_read")
                evidence = result.evidence[0]
                return (InvestigationPacket(
                    item,
                    check.check_id,
                    check.version,
                    (DimensionObservation(
                        "observed",
                        ("The provider returned a structured fact.",),
                        (evidence.evidence_id,),
                        "satisfied",
                    ),),
                    (evidence,),
                    "not_required",
                ),)

        provider = BoundedProvider()
        plugin_registration = PluginRegistration(
            MANIFEST,
            plugin_factory=lambda runtime=None: ProviderBackedPlugin(runtime),
            decision_provider_factory=lambda runtime=None: FixtureDecisionProvider(),
            capabilities=frozenset({"structured_read"}),
            scope_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["count"],
                "properties": {"count": {"type": "integer", "minimum": 1}},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            result = PlatformRunner(
                PluginRegistry((plugin_registration,)), directory,
            ).run_with_provider(
                plugin_id=MANIFEST.plugin_id,
                check_id="PAR-001",
                scope={"count": 6},
                provider_registry=ProviderRegistry((ProviderRegistration(
                    descriptor,
                    provider_factory=lambda runtime=None: provider,
                ),)),
                provider_scope={},
                platform_profile=CapabilityProfile(
                    frozenset({"structured_read"}), {"maxConcurrency": 4},
                ),
                user_profile=CapabilityProfile(
                    frozenset({"structured_read"}), {"maxConcurrency": 3},
                ),
            )
            bill_path = (
                Path(directory) / result.run_id
                / f"{result.run_id}.platform-performance-bill.json"
            )
            bill = json.loads(bill_path.read_text(encoding="utf-8"))
            self.assertEqual(bill["measurement"]["provider"]["status"], "captured")
            self.assertEqual(bill["measurement"]["provider"]["requestCount"], 6)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["parallelWorkers"], 2)
        self.assertEqual(provider.max_active, 2)


if __name__ == "__main__":
    unittest.main()
