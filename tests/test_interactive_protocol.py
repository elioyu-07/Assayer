import json
import multiprocessing
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from assayer_host import HostError, InteractivePlatformMcpToolTransport
from assayer_platform import (
    CommitReceipt,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PluginRegistration,
    PluginRegistry,
    PlatformContractError,
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
            "structured", item.identity, {
                "present": True,
                "signals": [
                    {"signal_id": "signal:1", "category": "alpha", "value": 1},
                    {"signal_id": "signal:2", "category": "alpha", "value": 2},
                    {"signal_id": "signal:3", "category": "beta", "value": 3},
                ],
            },
        )
        return (InvestigationPacket(
            item, check.check_id, check.version,
            (DimensionObservation("present", ("The fixture is present.",), (evidence.evidence_id,), "satisfied"),),
            (evidence,), "not_required", metadata={"evidenceCollections": [{
                "collectionId": "signals", "evidenceId": evidence.evidence_id,
                "jsonPointer": "/signals", "itemIdField": "signal_id", "groupBy": ["category"],
            }]},
        ),)

    def assemble_review_checkpoints(self, checkpoints, finalization, packet, check, context):
        del packet, check, context
        return {
            "checkpointPayloads": [dict(item.payload) for item in checkpoints],
            "finalization": dict(finalization),
        }

    def validate_review_checkpoint(
        self, checkpoint, collection_items, prior_checkpoints, packet, check, context,
    ):
        del prior_checkpoints, packet, check, context
        if len(collection_items) != len(checkpoint.item_ids):
            raise ValueError("Checkpoint items do not match the declared Evidence coverage")


class ManyFixturePlugin(FixturePlugin):
    def discover(self, scope, context):
        del scope, context
        return tuple(
            WorkItem(f"fixture:{index}", "fixture_item", f"fixture-source-{index}", f"state-{index}")
            for index in range(3)
        )

    def inspect(self, work_items, check, context):
        return tuple(FixturePlugin.inspect(self, (item,), check, context)[0] for item in work_items)


class ReferenceCollectionFixturePlugin(FixturePlugin):
    def inspect(self, work_items, check, context):
        packet = super().inspect(work_items, check, context)[0]
        descriptors = list(packet.metadata["evidenceCollections"])
        descriptors[0] = {**descriptors[0], "reviewRequired": False}
        descriptors.append({
            "collectionId": "signal-reference",
            "evidenceId": packet.evidence[0].evidence_id,
            "jsonPointer": "/signals",
            "itemIdField": "signal_id",
            "groupBy": ["category"],
            "reviewRequired": False,
        })
        return (replace(packet, metadata={"evidenceCollections": descriptors}),)


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


class ReferenceCollectionContractTest(unittest.TestCase):
    def test_reference_collection_is_pageable_but_does_not_block_review(self):
        registry = PluginRegistry((reference_collection_registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            result = inspected["structuredContent"]["result"]["result"]
            work_item_id = result["investigations"][0]["workItem"]["workItemId"]
            index = result["evidenceCollectionIndex"][work_item_id]
            self.assertEqual({item["collectionId"] for item in index}, {"signals", "signal-reference"})
            self.assertTrue(all(item["reviewRequired"] is False for item in index))
            advanced = transport.call_tool("advance_plugin_run", {})
            boundary = advanced["structuredContent"]["result"]["result"]
            self.assertEqual(boundary["semanticTask"]["kind"], "decide_work_item")
            self.assertEqual(boundary["semanticTask"]["investigation"]["evidence"], [])
            self.assertEqual(
                {item["collectionId"] for item in boundary["semanticTask"]["referenceCollectionIndex"]},
                {"signals", "signal-reference"},
            )
            with self.assertRaises(HostError) as error:
                transport.call_tool("checkpoint_review", {
                    "workItemId": work_item_id, "collectionId": "signals",
                    "itemIds": ["signal:1"], "payload": {"summary": "Not reviewable."},
                })
            self.assertEqual(error.exception.code, "EVIDENCE_COLLECTION_NOT_REVIEWABLE")
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id, "result": "scanned_no_issue",
                "findings": [{"dimension": "present", "status": "satisfied", "reason": "Reviewed."}],
                "reason": "Reference context was not a review queue.",
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(finished["structuredContent"]["result"]["status"], "completed")


def registration():
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: FixturePlugin(),
        decision_provider_factory=lambda runtime=None: FixtureProvider(),
        capabilities=frozenset({"fixture_read"}),
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object"},
    )


def reference_collection_registration():
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: ReferenceCollectionFixturePlugin(),
        decision_provider_factory=lambda runtime=None: FixtureProvider(),
        capabilities=frozenset({"fixture_read"}),
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object"},
    )


def many_registration():
    manifest = load_plugin_manifest({
        "pluginId": "fixture.interactive-many",
        "version": "1.0.0", "platformApiVersion": "1.0.0", "domains": ["fixture"],
        "subjectKinds": ["fixture_item"],
        "checks": [{
            "checkId": "FIX-INT-002", "version": "1.0.0", "subjectKinds": ["fixture_item"],
            "dimensions": ["present"], "decisionStates": ["scanned_no_issue", "needs_review"],
            "requiredEvidenceKinds": ["structured"], "requiredCapabilities": ["fixture_read"],
            "capabilityMissingOutcome": "needs_review", "invalidationSignals": ["source_digest"],
        }],
        "executionProfile": {
            "discoverBatching": "allowed", "inspectBatching": "allowed", "decisionBatching": "allowed",
            "parallelism": "forbidden", "cacheReuse": "forbidden", "checkpoint": "required",
            "maxBatchSize": 2,
        },
    })
    ManyFixturePlugin.manifest = manifest
    return PluginRegistration(
        manifest, plugin_factory=lambda runtime=None: ManyFixturePlugin(),
        decision_provider_factory=lambda runtime=None: FixtureProvider(),
        capabilities=frozenset({"fixture_read"}), execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object"},
    )


def adaptive_registration(*, failure_splitting="allowed"):
    manifest = load_plugin_manifest({
        "pluginId": f"fixture.interactive-adaptive-{failure_splitting}",
        "version": "1.0.0", "platformApiVersion": "1.0.0", "domains": ["fixture"],
        "subjectKinds": ["fixture_item"],
        "checks": [{
            "checkId": "FIX-INT-003", "version": "1.0.0", "subjectKinds": ["fixture_item"],
            "dimensions": ["present"], "decisionStates": ["scanned_no_issue", "needs_review"],
            "requiredEvidenceKinds": ["structured"], "requiredCapabilities": ["fixture_read"],
            "capabilityMissingOutcome": "needs_review", "invalidationSignals": ["source_digest"],
        }],
        "executionProfile": {
            "discoverBatching": "allowed", "inspectBatching": "allowed", "decisionBatching": "allowed",
            "parallelism": "forbidden", "cacheReuse": "forbidden", "checkpoint": "required",
            "maxBatchSize": 4, "ordering": "independent", "failureSplitting": failure_splitting,
        },
    })

    class AdaptiveFixturePlugin(FixturePlugin):
        def __init__(self):
            self.batch_sizes = []

        def discover(self, scope, context):
            del scope, context
            return tuple(
                WorkItem(f"adaptive:{index}", "fixture_item", f"source:{index}", f"state:{index}")
                for index in range(6)
            )

        def inspect(self, work_items, check, context):
            self.batch_sizes.append(len(work_items))
            if len(work_items) > 2:
                raise RuntimeError("The fixture provider accepts at most two items")
            return tuple(FixturePlugin.inspect(self, (item,), check, context)[0] for item in work_items)

    AdaptiveFixturePlugin.manifest = manifest
    plugin = AdaptiveFixturePlugin()
    registration = PluginRegistration(
        manifest, plugin_factory=lambda runtime=None: plugin,
        decision_provider_factory=lambda runtime=None: FixtureProvider(),
        capabilities=frozenset({"fixture_read"}), execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object"},
    )
    return registration, plugin


def _resume_in_child(output_root, run_id, start_event, release_event, results):
    transport = InteractivePlatformMcpToolTransport(
        output_root, plugin_registry=PluginRegistry((registration(),)),
    )
    start_event.wait(5)
    try:
        response = transport.call_tool("resume_plugin_run", {"runId": run_id})
        results.put(("ok", response["structuredContent"]["result"]["runId"]))
        release_event.wait(5)
    except HostError as error:
        results.put(("error", error.code))
    finally:
        transport.close()


class InteractiveProtocolTest(unittest.TestCase):
    def test_list_tools_publishes_plugin_review_payload_contract(self):
        review_schema = {
            "type": "object",
            "properties": {
                "checklist_review": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "status": {"enum": ["PASS", "REWORK", "ESCALATE", "UNVERIFIED"]},
                            "note": {"type": "string"},
                        },
                    },
                },
            },
        }
        reg = PluginRegistration(
            MANIFEST,
            plugin_factory=lambda runtime=None: FixturePlugin(),
            decision_provider_factory=lambda runtime=None: FixtureProvider(),
            capabilities=frozenset({"fixture_read"}),
            execution_modes=frozenset({"interactive"}),
            scope_schema={"type": "object"},
            review_payload_schema=review_schema,
        )
        transport = InteractivePlatformMcpToolTransport(plugin_registry=PluginRegistry((reg,)))
        tools = {item["name"]: item for item in transport.list_tools()}

        checkpoint = tools["checkpoint_review"]
        self.assertIn('"PASS"', checkpoint["description"])
        self.assertIn("fixture.interactive-quality", checkpoint["description"])
        self.assertEqual(
            checkpoint["inputSchema"]["properties"]["payload"]["description"],
            transport._review_payload_contract_text(),
        )

        advance = tools["advance_plugin_run"]
        self.assertEqual(
            advance["inputSchema"]["properties"]["reviewCheckpoint"]["properties"]["payload"]["description"],
            transport._review_payload_contract_text(),
        )

    def test_list_tools_without_review_payload_schema_keeps_opaque_payload(self):
        transport = InteractivePlatformMcpToolTransport(
            plugin_registry=PluginRegistry((registration(),)),
        )
        checkpoint = next(item for item in transport.list_tools() if item["name"] == "checkpoint_review")
        self.assertNotIn("description", checkpoint["inputSchema"]["properties"]["payload"])

    def test_start_and_progress_report_the_resolved_plugin_identity(self):
        transport = InteractivePlatformMcpToolTransport(
            plugin_registry=PluginRegistry((registration(),)),
        )
        started = transport.call_tool("start_plugin_run", {
            "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
        })["structuredContent"]["result"]["result"]
        self.assertEqual(started["plugin"], {
            "pluginId": "fixture.interactive-quality",
            "version": "1.0.0",
            "platformApiVersion": "1.0.0",
        })
        progress = transport.call_tool("get_plugin_progress", {})["structuredContent"]["result"]["result"]
        self.assertEqual(progress["plugin"]["pluginId"], "fixture.interactive-quality")
        self.assertEqual(progress["plugin"]["version"], "1.0.0")
        transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_result_pages_require_a_terminal_run(self):
        transport = InteractivePlatformMcpToolTransport(
            plugin_registry=PluginRegistry((registration(),)),
        )
        with self.assertRaises(HostError) as unavailable:
            transport.call_tool("get_plugin_result", {"sectionId": "result:missing:0001"})
        self.assertEqual(unavailable.exception.code, "RESULT_NOT_AVAILABLE")

    def test_two_processes_cannot_resume_the_same_run_concurrently(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            original = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = original.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            original.call_tool("advance_plugin_run", {})
            original.close()

            context = multiprocessing.get_context("fork")
            start_event = context.Event()
            release_event = context.Event()
            results = context.Queue()
            processes = [context.Process(
                target=_resume_in_child,
                args=(str(output), started["runId"], start_event, release_event, results),
            ) for _ in range(2)]
            for process in processes:
                process.start()
            start_event.set()
            outcomes = [results.get(timeout=5) for _ in processes]
            release_event.set()
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(outcomes), [
                ("error", "RUN_ALREADY_ACTIVE"), ("ok", started["runId"]),
            ])

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
            self.assertEqual(
                started["structuredContent"]["result"]["result"]["workflow"]["requiredNextStep"],
                "discover_work_items",
            )
            discovered = transport.call_tool("discover_work_items", {})
            self.assertEqual(len(discovered["structuredContent"]["result"]["result"]["workItems"]), 1)
            self.assertEqual(
                discovered["structuredContent"]["result"]["result"]["workflow"]["requiredNextStep"],
                "inspect_work_items",
            )
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            item_id = packet["workItem"]["workItemId"]
            self.assertEqual(
                inspected["structuredContent"]["result"]["result"]["workflow"]["state"],
                "awaiting_agent_decision",
            )
            waiting = inspected["structuredContent"]["result"]["result"]["progress"]
            self.assertEqual(waiting["waitingOn"], "agent")
            self.assertEqual(waiting["phase"], "semantic_review")
            self.assertEqual(waiting["completed"]["workItemsInspected"], 1)
            self.assertEqual(waiting["remaining"]["workItemsToDecide"], 1)
            self.assertEqual(waiting["requiredNextStep"], "checkpoint_review")
            self.assertIn("semantic Agent judgment", waiting["message"])
            self.assertIsNotNone(waiting["enteredAt"])
            diary = (
                Path(directory) / "output" / run_id
                / f"{run_id}.platform-run.log"
            ).read_text(encoding="utf-8")
            self.assertIn("Assayer Platform Run Diary", diary)
            self.assertIn("Status: Waiting for Agent decision", diary)
            self.assertIn("Phase: semantic review", diary)
            self.assertIn("1 awaiting decision", diary)
            self.assertIn("Waiting is expected here", diary)
            self.assertIn(
                "Next action: Review the next evidence batch and save a durable checkpoint.",
                diary,
            )
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": item_id, "result": "scanned_no_issue",
                "findings": [{"dimension": "present", "status": "satisfied", "reason": "The fixture is present."}],
                "reason": "The fixture satisfies the Check.",
            }]})
            progress = transport.call_tool("get_plugin_progress", {})
            self.assertEqual(progress["structuredContent"]["result"]["result"]["decisionsCommitted"], 1)
            self.assertEqual(
                progress["structuredContent"]["result"]["result"]["workflow"]["requiredNextStep"],
                "finish_plugin_run",
            )
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(finished["structuredContent"]["result"]["status"], "completed")
            terminal_progress = finished["structuredContent"]["result"]["result"]["progress"]
            self.assertTrue(terminal_progress["terminal"])
            self.assertEqual(terminal_progress["waitingOn"], "none")
            self.assertEqual(transport._controller._runs, {})

    def test_transport_allows_a_second_independent_plugin_run(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            first = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001",
                "scope": {"target": "first"},
            })["structuredContent"]["result"]["runId"]
            partial = transport.call_tool("finish_plugin_run", {"status": "partial"})[
                "structuredContent"
            ]["result"]["result"]["resultOverview"]
            self.assertEqual(partial["status"], "partial")
            self.assertFalse(partial["coverage"]["discoveryComplete"])
            self.assertIn("Scope discovery did not finish", partial["message"])
            second = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001",
                "scope": {"target": "second"},
            })["structuredContent"]["result"]["runId"]
            self.assertNotEqual(first, second)
            progress = transport.call_tool("get_plugin_progress", {})["structuredContent"]["result"]["result"]
            self.assertEqual(progress["discovered"], 0)
            self.assertEqual(progress["inspected"], 0)
            self.assertEqual(progress["decisionsCommitted"], 0)
            transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_inspection_is_paged_by_manifest_batch_size_and_cursor(self):
        registry = PluginRegistry((many_registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
            "pluginId": "fixture.interactive-many", "checkId": "FIX-INT-002", "scope": {},
            })
            discovered = transport.call_tool("discover_work_items", {})
            ids = [item["workItemId"] for item in discovered["structuredContent"]["result"]["result"]["workItems"]]
            first = transport.call_tool("inspect_work_items", {"workItemIds": ids})
            first_result = first["structuredContent"]["result"]["result"]
            self.assertEqual(first_result["inspectedRange"], {"start": 0, "count": 2, "total": 3})
            self.assertIsNotNone(first_result["nextCursor"])
            second = transport.call_tool("inspect_work_items", {
                "workItemIds": ids, "cursor": first_result["nextCursor"],
            })
            second_result = second["structuredContent"]["result"]["result"]
            self.assertEqual(second_result["inspectedRange"], {"start": 2, "count": 1, "total": 3})
            self.assertIsNone(second_result["nextCursor"])
            transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_investigation_can_return_evidence_index_then_expand_selected_payload(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            result = inspected["structuredContent"]["result"]["result"]
            packet = result["investigations"][0]
            self.assertFalse(result["evidenceIncluded"])
            self.assertEqual(packet["evidence"], [])
            evidence_id = packet["evidenceIndex"][0]["evidenceId"]
            expanded = transport.call_tool("expand_investigation", {
                "workItemId": packet["workItem"]["workItemId"], "evidenceIds": [evidence_id],
            })
            expanded_packet = expanded["structuredContent"]["result"]["result"]["investigation"]
            self.assertEqual([item["evidenceId"] for item in expanded_packet["evidence"]], [evidence_id])
            transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_declared_evidence_collection_is_grouped_and_paged_by_the_platform(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            result = inspected["structuredContent"]["result"]["result"]
            packet = result["investigations"][0]
            work_item_id = packet["workItem"]["workItemId"]
            index = result["evidenceCollectionIndex"][work_item_id][0]
            self.assertEqual(index["collectionId"], "signals")
            self.assertEqual(index["itemCount"], 3)
            self.assertEqual(index["groupCount"], 2)

            first = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id, "collectionId": "signals", "pageSize": 2,
            })["structuredContent"]["result"]["result"]
            self.assertEqual(first["itemIds"], ["signal:1", "signal:2"])
            self.assertEqual(first["page"], {"start": 0, "count": 2, "total": 3})
            self.assertIsNotNone(first["nextCursor"])
            alpha = next(group for group in first["groupSummaries"] if group["values"] == {"category": "alpha"})
            self.assertEqual(alpha["itemIds"], ["signal:1", "signal:2"])

            grouped = transport.call_tool("expand_evidence_collection", {
                "workItemId": work_item_id, "collectionId": "signals", "groupKey": alpha["groupKey"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(grouped["itemIds"], ["signal:1", "signal:2"])
            self.assertEqual(grouped["collection"]["selectedItems"], 2)
            transport.call_tool("finish_plugin_run", {"status": "partial"})

    def test_review_pages_checkpoint_incrementally_and_assemble_one_decision(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})
            packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
            work_item_id = packet["workItem"]["workItemId"]
            first = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_id, "collectionId": "signals",
                "itemIds": ["signal:1", "signal:2"],
                "payload": {"summary": "Reviewed alpha signals."},
            })["structuredContent"]["result"]["result"]
            self.assertEqual(first["coverage"]["remainingItems"], 1)
            self.assertEqual(first["workflow"], {
                "state": "awaiting_agent_decision", "phase": "semantic_review", "canFinish": False,
                "requiredNextStep": "checkpoint_review",
                "remaining": {"workItemsToInspect": 0, "workItemsToDecide": 1, "reviewItems": 1, "failures": 0},
            })
            replay = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_id, "collectionId": "signals",
                "itemIds": ["signal:1", "signal:2"],
                "payload": {"summary": "Reviewed alpha signals."},
            })["structuredContent"]["result"]["result"]
            self.assertTrue(replay["replayed"])
            with self.assertRaises(HostError) as incomplete:
                transport.call_tool("submit_decisions", {"decisions": [{
                    "workItemId": work_item_id, "result": "scanned_no_issue",
                    "findings": [{
                        "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                    }],
                    "reason": "Review is not complete yet.",
                    "reviewCheckpointIds": [first["checkpointId"]],
                    "finalization": {"summary": "Incomplete."},
                }]})
            self.assertEqual(incomplete.exception.code, "REVIEW_CHECKPOINT_INCOMPLETE")
            second = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_id, "collectionId": "signals",
                "itemIds": ["signal:3"], "payload": {"summary": "Reviewed beta signal."},
            })["structuredContent"]["result"]["result"]
            self.assertTrue(second["coverage"]["complete"])
            self.assertEqual(second["workflow"]["requiredNextStep"], "submit_decisions")
            with self.assertRaises(HostError) as overlap:
                transport.call_tool("checkpoint_review", {
                    "workItemId": work_item_id, "collectionId": "signals",
                    "itemIds": ["signal:2"], "payload": {"summary": "Conflicting review."},
                })
            self.assertEqual(overlap.exception.code, "REVIEW_CHECKPOINT_CONFLICT")
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id, "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "All signal pages were reviewed.",
                "reviewCheckpointIds": [first["checkpointId"], second["checkpointId"]],
                "finalization": {"summary": "Complete."},
            }]})
            finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
            metrics = finished["structuredContent"]["result"]["result"]["metrics"]
            self.assertEqual(metrics["reviewCheckpoints"], 2)
            self.assertEqual(metrics["reviewItemsCheckpointed"], 3)
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 2)
            self.assertEqual(ledger["decisions"][0]["details"]["finalization"]["summary"], "Complete.")

    def test_checkpointing_requires_plugin_pre_persistence_validation(self):
        class PluginWithoutCheckpointValidation(FixturePlugin):
            validate_review_checkpoint = None

        registration_value = PluginRegistration(
            MANIFEST,
            plugin_factory=lambda runtime=None: PluginWithoutCheckpointValidation(),
            decision_provider_factory=lambda runtime=None: FixtureProvider(),
            capabilities=frozenset({"fixture_read"}),
            execution_modes=frozenset({"interactive"}),
            scope_schema={"type": "object"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=PluginRegistry((registration_value,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("discover_work_items", {})
            packet = transport.call_tool("inspect_work_items", {})[
                "structuredContent"
            ]["result"]["result"]["investigations"][0]
            with self.assertRaises(HostError) as rejected:
                transport.call_tool("checkpoint_review", {
                    "workItemId": packet["workItem"]["workItemId"],
                    "collectionId": "signals", "itemIds": ["signal:1"],
                    "payload": {"summary": "This must not become durable."},
                })
            self.assertEqual(
                rejected.exception.code, "REVIEW_CHECKPOINT_VALIDATION_UNSUPPORTED",
            )
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger["review_checkpoints"], [])

    def test_semantic_decision_retry_replays_without_a_second_committer_call(self):
        class RecordingCommitter:
            def __init__(self):
                self.calls = 0

            def commit(self, proposal, packet, check, context):
                del packet, check, context
                self.calls += 1
                return CommitReceipt(
                    "commit:fixture:stable", proposal.work_item_id,
                    proposal.check_id, proposal.check_version, proposal.result, "durable",
                )

        committer = RecordingCommitter()
        registration_value = PluginRegistration(
            MANIFEST,
            plugin_factory=lambda runtime=None: FixturePlugin(),
            decision_provider_factory=lambda runtime=None: FixtureProvider(),
            committer_factory=lambda runtime=None: committer,
            capabilities=frozenset({"fixture_read"}),
            execution_modes=frozenset({"interactive"}),
            scope_schema={"type": "object"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=PluginRegistry((registration_value,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("discover_work_items", {})
            packet = transport.call_tool("inspect_work_items", {})[
                "structuredContent"
            ]["result"]["result"]["investigations"][0]
            decision = {
                "workItemId": packet["workItem"]["workItemId"],
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied",
                    "reason": "The fixture is present.",
                }],
                "reason": "The fixture satisfies the Check.",
                "details": {"review": "complete"},
            }
            first = transport.call_tool("submit_decisions", {"decisions": [decision]})[
                "structuredContent"
            ]["result"]
            replay = transport.call_tool("submit_decisions", {"decisions": [decision]})[
                "structuredContent"
            ]["result"]
            self.assertEqual(committer.calls, 1)
            self.assertEqual(first["operationId"], replay["operationId"])
            self.assertEqual(first["runRevision"], replay["runRevision"])
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with self.assertRaises(HostError) as conflict:
                transport.call_tool("submit_decisions", {"decisions": [{
                    **decision, "reason": "A conflicting semantic reason.",
                }]})
            self.assertEqual(conflict.exception.code, "COMMIT_CONFLICT")
            self.assertEqual(committer.calls, 1)
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(ledger["decisions"]), 1)
            self.assertEqual(sum(item["kind"] == "commit" for item in ledger["operations"]), 1)

    def test_host_driven_advance_stops_only_for_semantics_then_finishes(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            run_id = started["structuredContent"]["result"]["runId"]

            review = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(review["status"], "awaiting_agent_decision")
            task = review["result"]["semanticTask"]
            self.assertEqual(task["kind"], "review_evidence_items")
            self.assertEqual(task["itemIds"], ["signal:1", "signal:2"])
            self.assertEqual(task["group"]["values"], {"category": "alpha"})
            self.assertFalse(review["result"]["workflow"]["canFinish"])
            self.assertEqual(review["result"]["workflow"]["requiredNextStep"], "advance_plugin_run")

            next_review = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": task["workItemId"], "collectionId": task["collectionId"],
                    "itemIds": task["itemIds"], "payload": {"summary": "Alpha signals reviewed."},
                },
            })["structuredContent"]["result"]
            next_task = next_review["result"]["semanticTask"]
            self.assertEqual(next_task["itemIds"], ["signal:3"])
            self.assertEqual(next_task["group"]["values"], {"category": "beta"})

            finalize = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": next_task["workItemId"], "collectionId": next_task["collectionId"],
                    "itemIds": next_task["itemIds"], "payload": {"summary": "Beta signal reviewed."},
                },
            })["structuredContent"]["result"]
            self.assertEqual(finalize["status"], "awaiting_agent_decision")
            final_task = finalize["result"]["semanticTask"]
            self.assertEqual(final_task["kind"], "finalize_decision")
            self.assertEqual(len(final_task["reviewCheckpointIds"]), 2)

            finished = transport.call_tool("advance_plugin_run", {
                "decision": {
                    "workItemId": task["workItemId"], "result": "scanned_no_issue",
                    "findings": [{
                        "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                    }],
                    "reason": "All signals satisfy the Check.",
                    "finalization": {"summary": "Complete."},
                },
            })["structuredContent"]["result"]
            self.assertEqual(finished["status"], "completed")
            self.assertEqual(finished["result"]["workflow"]["state"], "completed")
            self.assertTrue(finished["result"]["workflow"]["canFinish"])
            overview = finished["result"]["resultOverview"]
            self.assertEqual(overview["conclusionValidity"], "valid")
            self.assertEqual(overview["coverage"], {
                "discoveryComplete": True,
                "discovered": 1,
                "inspected": 1,
                "decided": 1,
                "failed": 0,
                "unprocessed": 0,
                "complete": True,
            })
            self.assertEqual(overview["outcomes"]["scanned_no_issue"], 1)
            self.assertEqual(overview["nextAction"], "No further audit action is required.")
            delivery = finished["result"]["resultDelivery"]
            self.assertEqual(delivery["mode"], "summary_first")
            self.assertTrue(delivery["detailsAvailable"])
            decisions_section = finished["result"]["decisions"]["sectionId"]
            decision_page = transport.call_tool("get_plugin_result", {
                "sectionId": decisions_section, "pageSize": 1,
            })["structuredContent"]["result"]
            self.assertEqual(decision_page["result"]["page"]["total"], 1)
            self.assertEqual(
                decision_page["result"]["items"][0]["reason"],
                "All signals satisfy the Check.",
            )
            self.assertEqual(
                decision_page["result"]["items"][0]["findings"]["present"]["status"],
                "satisfied",
            )
            self.assertTrue(decision_page["result"]["deltaOnly"])
            self.assertIsNone(transport._active_run_id)
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger["workflow"]["state"], "completed")
            self.assertEqual(len(ledger["review_checkpoints"]), 2)

    def test_advance_replays_checkpoint_after_lost_acknowledgement(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            boundary = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            submitted = {
                "reviewCheckpoint": {
                    "workItemId": boundary["workItemId"],
                    "collectionId": boundary["collectionId"],
                    "itemIds": boundary["itemIds"],
                    "payload": {"summary": "Alpha signals reviewed."},
                },
            }

            # Treat this response as lost after the Host has durably accepted
            # the checkpoint, then retry the identical semantic command.
            accepted = transport.call_tool("advance_plugin_run", submitted)[
                "structuredContent"
            ]["result"]
            replay = transport.call_tool("advance_plugin_run", submitted)[
                "structuredContent"
            ]["result"]
            self.assertEqual(accepted["operationId"], replay["operationId"])
            self.assertEqual(accepted["runRevision"], replay["runRevision"])
            self.assertFalse(accepted["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(
                accepted["result"]["semanticTask"]["itemIds"],
                replay["result"]["semanticTask"]["itemIds"],
            )

            synchronized = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(synchronized["runRevision"], accepted["runRevision"])
            self.assertEqual(synchronized["result"]["durableBoundary"], {
                "runRevision": accepted["runRevision"],
                "lastOperationId": accepted["operationId"],
                "requiredNextStep": "advance_plugin_run",
            })
            self.assertEqual(
                synchronized["result"]["semanticTask"]["itemIds"],
                accepted["result"]["semanticTask"]["itemIds"],
            )
            ledger = json.loads(next(output.glob("*/run-*.platform-ledger.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(ledger["review_checkpoints"]), 1)
            self.assertEqual(sum(
                item["kind"] == "review_checkpoint" for item in ledger["operations"]
            ), 1)

    def test_checkpoint_correction_is_append_only_effective_and_idempotent(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            first_task = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            original = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": first_task["workItemId"],
                    "collectionId": first_task["collectionId"],
                    "itemIds": first_task["itemIds"],
                    "payload": {"summary": "Original alpha review."},
                },
            })["structuredContent"]["result"]
            second_task = original["result"]["semanticTask"]
            transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": second_task["workItemId"],
                    "collectionId": second_task["collectionId"],
                    "itemIds": second_task["itemIds"],
                    "payload": {"summary": "Beta review."},
                },
            })
            correction_input = {
                "reviewCheckpoint": {
                    "workItemId": first_task["workItemId"],
                    "collectionId": first_task["collectionId"],
                    "itemIds": first_task["itemIds"],
                    "payload": {"summary": "Corrected alpha review."},
                    "supersedesCheckpointId": original["operationId"],
                },
            }
            corrected = transport.call_tool("advance_plugin_run", correction_input)[
                "structuredContent"
            ]["result"]
            replay = transport.call_tool("advance_plugin_run", correction_input)[
                "structuredContent"
            ]["result"]
            self.assertFalse(corrected["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(corrected["operationId"], replay["operationId"])
            self.assertEqual(corrected["runRevision"], replay["runRevision"])
            final_task = corrected["result"]["semanticTask"]
            self.assertEqual(final_task["kind"], "finalize_decision")
            self.assertEqual(len(final_task["reviewCheckpointIds"]), 2)
            self.assertIn(corrected["operationId"], final_task["reviewCheckpointIds"])
            self.assertNotIn(original["operationId"], final_task["reviewCheckpointIds"])
            terminal = transport.call_tool("advance_plugin_run", {"decision": {
                "workItemId": first_task["workItemId"],
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "All effective checkpoint pages were reviewed.",
                "finalization": {"summary": "Complete."},
            }})["structuredContent"]["result"]
            self.assertEqual(terminal["status"], "completed")
            self.assertEqual(terminal["result"]["metrics"]["reviewCheckpoints"], 2)
            self.assertEqual(terminal["result"]["metrics"]["reviewCheckpointRecords"], 3)
            ledger = json.loads(next(output.glob("*/run-*.platform-ledger.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(ledger["review_checkpoints"]), 3)
            self.assertEqual(
                ledger["decisions"][0]["details"]["checkpointPayloads"],
                [{"summary": "Corrected alpha review."}, {"summary": "Beta review."}],
            )

    def test_checkpoint_correction_rejects_unknown_wrong_scope_and_nonleaf_targets(self):
        many_registration = PluginRegistration(
            MANIFEST,
            plugin_factory=lambda runtime=None: ManyFixturePlugin(),
            decision_provider_factory=lambda runtime=None: FixtureProvider(),
            capabilities=frozenset({"fixture_read"}),
            execution_modes=frozenset({"interactive"}),
            scope_schema={"type": "object"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=PluginRegistry((many_registration,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            discovered = transport.call_tool("discover_work_items", {})[
                "structuredContent"
            ]["result"]["result"]["workItems"]
            work_item_ids = [item["workItemId"] for item in discovered]
            for work_item_id in work_item_ids[:2]:
                transport.call_tool("inspect_work_items", {"workItemIds": [work_item_id]})
            original = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_ids[0], "collectionId": "signals",
                "itemIds": ["signal:1"], "payload": {"summary": "Original."},
            })["structuredContent"]["result"]["result"]

            invalid_requests = (
                ({
                    "workItemId": work_item_ids[0], "collectionId": "signals",
                    "itemIds": ["signal:1"], "payload": {"summary": "Unknown target."},
                    "supersedesCheckpointId": "review:unknown",
                }, "UNKNOWN_REVIEW_CHECKPOINT"),
                ({
                    "workItemId": work_item_ids[1], "collectionId": "signals",
                    "itemIds": ["signal:1"], "payload": {"summary": "Wrong WorkItem."},
                    "supersedesCheckpointId": original["checkpointId"],
                }, "REVIEW_CHECKPOINT_CONFLICT"),
                ({
                    "workItemId": work_item_ids[0], "collectionId": "signals",
                    "itemIds": ["signal:2"], "payload": {"summary": "Wrong item scope."},
                    "supersedesCheckpointId": original["checkpointId"],
                }, "REVIEW_CHECKPOINT_CONFLICT"),
            )
            for request, expected_code in invalid_requests:
                with self.subTest(expected_code=expected_code):
                    with self.assertRaises(HostError) as rejected:
                        transport.call_tool("checkpoint_review", request)
                    self.assertEqual(rejected.exception.code, expected_code)

            corrected = transport.call_tool("checkpoint_review", {
                "workItemId": work_item_ids[0], "collectionId": "signals",
                "itemIds": ["signal:1"], "payload": {"summary": "Corrected."},
                "supersedesCheckpointId": original["checkpointId"],
            })["structuredContent"]["result"]["result"]
            with self.assertRaises(HostError) as nonleaf:
                transport.call_tool("checkpoint_review", {
                    "workItemId": work_item_ids[0], "collectionId": "signals",
                    "itemIds": ["signal:1"], "payload": {"summary": "Invalid branch."},
                    "supersedesCheckpointId": original["checkpointId"],
                })
            self.assertEqual(nonleaf.exception.code, "REVIEW_CHECKPOINT_CONFLICT")
            ledger = json.loads(
                (output / started["runId"] / f"{started['runId']}.platform-ledger.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 2)
            self.assertEqual(
                ledger["review_checkpoints"][1]["supersedes_checkpoint_id"],
                original["checkpointId"],
            )
            self.assertEqual(corrected["coverage"]["reviewedItems"], 1)

    def test_checkpoint_correction_chain_survives_transport_restart(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            first_transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            task = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            original = first_transport.call_tool("advance_plugin_run", {"reviewCheckpoint": {
                "workItemId": task["workItemId"], "collectionId": task["collectionId"],
                "itemIds": task["itemIds"], "payload": {"summary": "Original."},
            }})["structuredContent"]["result"]
            corrected = first_transport.call_tool("advance_plugin_run", {"reviewCheckpoint": {
                "workItemId": task["workItemId"], "collectionId": task["collectionId"],
                "itemIds": task["itemIds"], "payload": {"summary": "Corrected."},
                "supersedesCheckpointId": original["operationId"],
            }})["structuredContent"]["result"]

            first_transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            synchronized = resumed_transport.call_tool("resume_plugin_run", {
                "runId": corrected["runId"],
            })[
                "structuredContent"
            ]["result"]
            self.assertEqual(
                synchronized["result"]["semanticTask"]["itemIds"], ["signal:3"],
            )
            self.assertEqual(synchronized["runRevision"], corrected["runRevision"])
            ledger = json.loads(next(output.glob("*/run-*.platform-ledger.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(ledger["review_checkpoints"]), 2)

    def test_advance_without_input_replays_terminal_acknowledgement(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            task = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            next_task = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": task["workItemId"], "collectionId": task["collectionId"],
                    "itemIds": task["itemIds"], "payload": {"summary": "Alpha reviewed."},
                },
            })["structuredContent"]["result"]["result"]["semanticTask"]
            final_task = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    "workItemId": next_task["workItemId"], "collectionId": next_task["collectionId"],
                    "itemIds": next_task["itemIds"], "payload": {"summary": "Beta reviewed."},
                },
            })["structuredContent"]["result"]["result"]["semanticTask"]
            decision = {
                "workItemId": final_task["workItemId"], "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "All signals satisfy the Check.",
                "finalization": {"summary": "Complete."},
            }
            terminal = transport.call_tool("advance_plugin_run", {"decision": decision})[
                "structuredContent"
            ]["result"]
            replay = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(replay["status"], "completed")
            self.assertEqual(replay["runId"], terminal["runId"])
            self.assertEqual(replay["runRevision"], terminal["runRevision"])
            self.assertEqual(replay["result"], terminal["result"])
            self.assertTrue(replay["replayed"])
            self.assertFalse((Path(directory) / "output" / ".active-plugin-run.json").exists())
            self.assertFalse(
                (Path(directory) / "output" / terminal["runId"] / "platform-resume.json").exists()
            )
            self.assertTrue((Path(directory) / "output" / ".latest-plugin-run.json").is_file())
            resumed_transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            resumed_replay = resumed_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(resumed_replay["status"], "completed")
            self.assertEqual(resumed_replay["runId"], terminal["runId"])
            self.assertEqual(resumed_replay["runRevision"], terminal["runRevision"])
            self.assertEqual(resumed_replay["result"], terminal["result"])
            self.assertTrue(resumed_replay["replayed"])
            decisions_section = resumed_replay["result"]["decisions"]["sectionId"]
            page = resumed_transport.call_tool("get_plugin_result", {
                "sectionId": decisions_section,
            })["structuredContent"]["result"]
            self.assertEqual(page["result"]["page"]["total"], 1)
            with self.assertRaises(HostError) as rejected:
                transport.call_tool("advance_plugin_run", {"decision": decision})
            self.assertEqual(rejected.exception.code, "RUN_TERMINAL")

    def test_result_publication_failure_leaves_a_resumable_running_ledger(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            transport.call_tool("discover_work_items", {})
            transport.call_tool("inspect_work_items", {})
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": "fixture:1", "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "The fixture satisfies the Check.",
                "details": {"review": "complete"},
            }]})
            blocked_result_path = output / started["runId"] / "result-summary.pending.json"
            blocked_result_path.mkdir()
            with self.assertRaises(HostError) as failed:
                transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(failed.exception.code, "RESULT_PUBLICATION_FAILED")
            ledger_path = output / started["runId"] / f"{started['runId']}.platform-ledger.json"
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8"))["status"], "running")
            self.assertTrue((output / started["runId"] / "platform-resume.json").is_file())

            blocked_result_path.rmdir()
            transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            terminal = resumed_transport.call_tool("resume_plugin_run", {
                "runId": started["runId"],
            })[
                "structuredContent"
            ]["result"]
            self.assertEqual(terminal["status"], "completed")
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8"))["status"], "completed")

    def test_terminal_ledger_failure_rolls_back_in_memory_and_retries(self):
        class FailOnceOnTerminal:
            def __init__(self, delegate):
                self.delegate = delegate
                self.root = delegate.root
                self.failed = False

            def save(self, ledger):
                if ledger.status != "running" and not self.failed:
                    self.failed = True
                    raise OSError("injected terminal ledger failure")
                return self.delegate.save(ledger)

            def load(self, run_id):
                return self.delegate.load(run_id)

        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            transport.call_tool("discover_work_items", {})
            transport.call_tool("inspect_work_items", {})
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": "fixture:1", "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "The fixture satisfies the Check.",
                "details": {"review": "complete"},
            }]})
            run = transport._controller._runs[run_id]["run"]
            durable_store = run.store
            run.store = FailOnceOnTerminal(durable_store)

            with self.assertRaises(HostError) as failed:
                transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(failed.exception.code, "PLATFORM_LEDGER_PERSIST_FAILED")
            self.assertEqual(run.status, "running")
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8"))["status"], "running")
            self.assertTrue((output / run_id / "result-summary.pending.json").is_file())
            self.assertFalse((output / run_id / "result-summary.json").exists())

            run.store = durable_store
            terminal = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(terminal["status"], "completed")
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8"))["status"], "completed")

    def test_interrupted_result_promotion_recovers_from_terminal_ledger_and_pending_result(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            transport.call_tool("discover_work_items", {})
            transport.call_tool("inspect_work_items", {})
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": "fixture:1", "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "The fixture satisfies the Check.",
                "details": {"review": "complete"},
            }]})
            result_path = output / run_id / "result-summary.json"
            pending_path = output / run_id / "result-summary.pending.json"
            result_path.mkdir()

            with self.assertRaises(HostError) as failed:
                transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(failed.exception.code, "RESULT_PUBLICATION_FAILED")
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8"))["status"], "completed")
            self.assertTrue(pending_path.is_file())
            self.assertTrue((output / run_id / "platform-owner.json").is_file())

            result_path.rmdir()
            recovered = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(recovered["status"], "completed")
            self.assertTrue(recovered["replayed"])
            self.assertTrue(result_path.is_file())
            self.assertFalse(pending_path.exists())
            self.assertFalse((output / ".active-plugin-run.json").exists())

    def test_new_transport_does_not_claim_existing_run_and_can_start_an_independent_run(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = first_transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            boundary = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]

            second_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            second_transport.list_tools()
            self.assertIsNone(second_transport._active_run_id)
            owner_before = json.loads(
                (output / started["runId"] / "platform-owner.json").read_text(encoding="utf-8")
            )
            second_started = second_transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })[
                "structuredContent"
            ]["result"]
            self.assertNotEqual(second_started["runId"], started["runId"])
            continued = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(continued["runId"], started["runId"])
            self.assertEqual(continued["runRevision"], boundary["runRevision"])
            owner_after = json.loads(
                (output / started["runId"] / "platform-owner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(owner_after, owner_before)

    def test_corrupt_resume_descriptor_fails_closed_without_ledger_mutation(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = first_transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            before = ledger_path.read_bytes()
            (output / run_id / "platform-resume.json").write_text("{invalid", encoding="utf-8")

            first_transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            with self.assertRaises(HostError) as failed:
                resumed_transport.call_tool("resume_plugin_run", {"runId": run_id})
            self.assertEqual(failed.exception.code, "RUN_RESUME_FAILED")
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_terminal_ledger_repairs_latest_pointer_after_interrupted_cleanup(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            run_id = started["runId"]
            descriptor_path = output / run_id / "platform-resume.json"
            original_writer = transport._controller._write_terminal_pointer

            def interrupted_terminal_pointer(run_id):
                del run_id
                raise PlatformContractError(
                    "RESULT_PUBLICATION_FAILED", "Injected terminal pointer interruption",
                )

            transport._controller._write_terminal_pointer = interrupted_terminal_pointer
            with self.assertRaises(HostError) as interrupted:
                transport.call_tool("advance_plugin_run", {"closeout": {"status": "partial"}})
            self.assertEqual(interrupted.exception.code, "RESULT_PUBLICATION_FAILED")
            transport._controller._write_terminal_pointer = original_writer
            self.assertFalse((output / ".latest-plugin-run.json").exists())
            self.assertTrue((output / run_id / "platform-owner.json").is_file())
            self.assertTrue(descriptor_path.is_file())

            transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            replay = resumed_transport.call_tool("resume_plugin_run", {"runId": run_id})[
                "structuredContent"
            ]["result"]
            self.assertEqual(replay["status"], "partial")
            self.assertTrue(replay["replayed"])
            self.assertTrue((output / ".latest-plugin-run.json").is_file())
            self.assertFalse((output / ".active-plugin-run.json").exists())
            self.assertFalse(descriptor_path.exists())

    def test_corrupt_terminal_result_fails_closed_without_rewriting_history(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            terminal = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            run_id = terminal["runId"]
            transport.call_tool("advance_plugin_run", {"closeout": {"status": "partial"}})
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            result_path = output / run_id / "result-summary.json"
            ledger_before = ledger_path.read_bytes()
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["sourceDigest"] = "0" * 64
            result_path.write_text(json.dumps(result), encoding="utf-8")
            corrupt_before = result_path.read_bytes()

            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            with self.assertRaises(HostError) as failed:
                resumed_transport.call_tool("advance_plugin_run", {})
            self.assertEqual(failed.exception.code, "RESULT_PUBLICATION_FAILED")
            self.assertEqual(ledger_path.read_bytes(), ledger_before)
            self.assertEqual(result_path.read_bytes(), corrupt_before)

    def test_explicit_resume_restores_the_unique_durable_semantic_boundary(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = first_transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            boundary = first_transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]["result"]["semanticTask"]
            submission = {
                "reviewCheckpoint": {
                    "workItemId": boundary["workItemId"],
                    "collectionId": boundary["collectionId"],
                    "itemIds": boundary["itemIds"],
                    "payload": {"summary": "Alpha signals reviewed."},
                },
            }
            accepted = first_transport.call_tool("advance_plugin_run", submission)[
                "structuredContent"
            ]["result"]
            expected_next_ids = accepted["result"]["semanticTask"]["itemIds"]

            resumed_transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            with self.assertRaises(HostError) as active_owner:
                resumed_transport.call_tool("resume_plugin_run", {"runId": started["runId"]})
            self.assertEqual(active_owner.exception.code, "RUN_ALREADY_ACTIVE")
            first_transport.close()
            synchronized = resumed_transport.call_tool("resume_plugin_run", {
                "runId": started["runId"],
            })[
                "structuredContent"
            ]["result"]
            self.assertEqual(synchronized["runId"], started["runId"])
            self.assertEqual(synchronized["runRevision"], accepted["runRevision"])
            self.assertEqual(synchronized["result"]["semanticTask"]["itemIds"], expected_next_ids)
            self.assertEqual(
                synchronized["result"]["durableBoundary"]["lastOperationId"],
                accepted["operationId"],
            )

            replay = resumed_transport.call_tool("advance_plugin_run", submission)[
                "structuredContent"
            ]["result"]
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["operationId"], accepted["operationId"])
            self.assertEqual(replay["runRevision"], accepted["runRevision"])
            ledger = json.loads(
                (output / started["runId"] / f"{started['runId']}.platform-ledger.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(ledger["review_checkpoints"]), 1)

    def test_host_driven_advance_supports_explicit_noncompleted_closeout(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            closed = transport.call_tool("advance_plugin_run", {
                "closeout": {
                    "status": "failed",
                    "failures": [{
                        "code": "OPERATOR_STOP", "message": "The operator stopped the Run.",
                    }],
                },
            })["structuredContent"]["result"]
            self.assertEqual(closed["status"], "failed")
            self.assertEqual(closed["result"]["workflow"]["state"], "failed")
            overview = closed["result"]["resultOverview"]
            self.assertEqual(overview["conclusionValidity"], "invalidated")
            self.assertFalse(overview["coverage"]["discoveryComplete"])
            self.assertIn("No formal conclusion", overview["message"])
            self.assertIn("do not use this Run", overview["nextAction"])
            self.assertEqual(closed["result"]["decisions"], [])
            self.assertIsNone(transport._active_run_id)

    def test_needs_review_result_exposes_concrete_gaps_and_next_action(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            transport.call_tool("discover_work_items", {})
            inspected = transport.call_tool("inspect_work_items", {})[
                "structuredContent"
            ]["result"]["result"]
            work_item_id = inspected["investigations"][0]["workItem"]["workItemId"]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id,
                "result": "needs_review",
                "findings": [{
                    "dimension": "present",
                    "status": "unresolved",
                    "reason": "The available source does not identify whether the fixture is active.",
                }],
                "reason": "The fixture activation state cannot be established from current Evidence.",
            }]})
            terminal = transport.call_tool("finish_plugin_run", {"status": "completed"})[
                "structuredContent"
            ]["result"]["result"]
            overview = terminal["resultOverview"]
            self.assertEqual(overview["needsReviewCount"], 1)
            self.assertEqual(overview["outcomes"]["needs_review"], 1)
            self.assertIn("still require review", overview["message"])
            review_page = transport.call_tool("get_plugin_result", {
                "sectionId": terminal["reviewItems"]["sectionId"],
            })["structuredContent"]["result"]["result"]
            review_item = review_page["items"][0]
            self.assertEqual(
                review_item["gaps"]["present"]["reason"],
                "The available source does not identify whether the fixture is active.",
            )
            self.assertIn("present", review_item["nextAction"])

    def test_failed_result_invalidates_and_hides_previously_committed_decisions(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=registry)
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })["structuredContent"]["result"]
            transport.call_tool("discover_work_items", {})
            work_item_id = transport.call_tool("inspect_work_items", {})[
                "structuredContent"
            ]["result"]["result"]["investigations"][0]["workItem"]["workItemId"]
            transport.call_tool("submit_decisions", {"decisions": [{
                "workItemId": work_item_id,
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "The fixture is present.",
                }],
                "reason": "The current Evidence satisfies the Check.",
            }]})
            terminal = transport.call_tool("finish_plugin_run", {
                "status": "failed",
                "failures": [{
                    "code": "INTEGRITY_LOST",
                    "message": "Terminal integrity could not be established.",
                }],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(terminal["decisions"], [])
            self.assertEqual(terminal["reviewItems"], [])
            self.assertEqual(terminal["resultOverview"]["invalidatedDecisionCount"], 1)
            self.assertEqual(sum(terminal["resultOverview"]["outcomes"].values()), 0)
            stored_result = json.loads(
                (output / started["runId"] / "result-summary.json").read_text(encoding="utf-8")
            )["result"]
            self.assertEqual(stored_result["decisions"], [])
            ledger = json.loads(
                (
                    output / started["runId"]
                    / f"{started['runId']}.platform-ledger.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(len(ledger["decisions"]), 1)

    def test_host_driven_advance_rejects_closeout_mixed_with_semantic_input(self):
        registry = PluginRegistry((registration(),))
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=registry,
            )
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality", "checkId": "FIX-INT-001", "scope": {},
            })
            with self.assertRaises(HostError) as conflict:
                transport.call_tool("advance_plugin_run", {
                    "closeout": {"status": "partial"},
                    "decision": {
                        "workItemId": "fixture:1", "result": "needs_review",
                        "findings": [{
                            "dimension": "present", "status": "unresolved", "reason": "Unknown.",
                        }],
                        "reason": "The review is incomplete.",
                    },
                })
            self.assertEqual(conflict.exception.code, "WORKFLOW_INPUT_CONFLICT")
            transport.call_tool("advance_plugin_run", {"closeout": {"status": "partial"}})

    def test_safe_batch_split_learns_a_smaller_size_for_later_pages(self):
        registration_value, plugin = adaptive_registration()
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=PluginRegistry((registration_value,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": registration_value.manifest.plugin_id,
                "checkId": "FIX-INT-003", "scope": {},
            })["structuredContent"]["result"]
            discovered = transport.call_tool("discover_work_items", {})
            ids = [item["workItemId"] for item in discovered["structuredContent"]["result"]["result"]["workItems"]]
            first = transport.call_tool("inspect_work_items", {
                "workItemIds": ids,
            })["structuredContent"]["result"]["result"]
            self.assertEqual(len(first["investigations"]), 4)
            self.assertEqual(first["inspectionFailures"], [])
            self.assertEqual(first["batching"], {
                "selected": 4, "attempts": 3, "splits": 1,
                "effectiveBatchSize": 2, "adapted": True,
            })
            second = transport.call_tool("inspect_work_items", {
                "workItemIds": ids, "cursor": first["nextCursor"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(len(second["investigations"]), 2)
            self.assertIsNone(second["nextCursor"])
            self.assertEqual(plugin.batch_sizes, [4, 2, 2, 2])
            progress = transport.call_tool("get_plugin_progress", {})[
                "structuredContent"
            ]["result"]["result"]
            self.assertEqual(progress["inspectionBatching"], {
                "attempts": 4, "splits": 1, "failures": 0, "effectiveBatchSize": 2,
            })
            finished = transport.call_tool("finish_plugin_run", {"status": "partial"})
            metrics = finished["structuredContent"]["result"]["result"]["metrics"]
            self.assertEqual(metrics["inspectionBatches"], 4)
            self.assertEqual(metrics["batchSplits"], 1)
            self.assertEqual(metrics["inspectBatchSize"], 2)

    def test_batch_failure_is_not_retried_without_explicit_safe_split_contract(self):
        registration_value, plugin = adaptive_registration(failure_splitting="forbidden")
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=PluginRegistry((registration_value,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": registration_value.manifest.plugin_id,
                "checkId": "FIX-INT-003", "scope": {},
            })["structuredContent"]["result"]
            discovered = transport.call_tool("discover_work_items", {})
            ids = [item["workItemId"] for item in discovered["structuredContent"]["result"]["result"]["workItems"]]
            inspected = transport.call_tool("inspect_work_items", {
                "workItemIds": ids,
            })["structuredContent"]["result"]["result"]
            self.assertEqual(inspected["investigations"], [])
            self.assertEqual(len(inspected["inspectionFailures"]), 4)
            self.assertEqual(inspected["batching"]["splits"], 0)
            self.assertEqual(plugin.batch_sizes, [4])
            with self.assertRaises(HostError) as completion:
                transport.call_tool("finish_plugin_run", {"status": "completed"})
            self.assertEqual(completion.exception.code, "UNRESOLVED_FAILURE")
            finished = transport.call_tool("finish_plugin_run", {"status": "failed"})
            terminal_result = finished["structuredContent"]["result"]["result"]
            self.assertTrue(terminal_result["progress"]["terminal"])
            self.assertEqual(terminal_result["progress"]["state"], "failed")
            failure_section = terminal_result["failures"]
            failures = transport.call_tool("get_plugin_result", {
                "sectionId": failure_section["sectionId"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(failures["page"]["total"], 4)
            diary = (
                Path(directory) / "output" / started["runId"]
                / f"{started['runId']}.platform-run.log"
            ).read_text(encoding="utf-8")
            self.assertIn("Status: Failed", diary)
            self.assertIn("Failures: 4", diary)
            self.assertIn("INSPECTION_FAILED", diary)
            self.assertIn("Next action: No further action is required.", diary)

    def test_tool_catalog_contains_only_domain_neutral_names(self):
        transport = InteractivePlatformMcpToolTransport(plugin_registry=PluginRegistry((registration(),)))
        tools = transport.list_tools()
        self.assertEqual(
            [item["name"] for item in tools],
            ["start_plugin_run", "resume_plugin_run", "advance_plugin_run", "get_plugin_result", "discover_work_items", "inspect_work_items", "expand_investigation",
             "expand_evidence_collection", "checkpoint_review", "submit_decisions", "recover_work_item",
             "get_plugin_progress", "finish_plugin_run"],
        )
        for item in tools:
            if item["name"] != "resume_plugin_run":
                self.assertNotIn("runId", item["inputSchema"].get("properties", {}))


if __name__ == "__main__":
    unittest.main()
