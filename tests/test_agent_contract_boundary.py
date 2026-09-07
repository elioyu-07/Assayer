"""Host enforcement tests for Run-frozen executable Agent contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from assayer_host import HostError, InteractivePlatformMcpToolTransport
from assayer_platform import AgentContractBundle, PluginRegistry
from tests.test_interactive_protocol import (
    FixturePlugin,
    reference_collection_registration,
    registration,
)


class TrackingPlugin(FixturePlugin):
    def __init__(self):
        self.validation_calls = 0
        self.assembly_calls = 0

    def validate_review_checkpoint(
        self, checkpoint, collection_items, prior_checkpoints, packet, check, context,
    ):
        self.validation_calls += 1
        return super().validate_review_checkpoint(
            checkpoint, collection_items, prior_checkpoints, packet, check, context,
        )

    def assemble_review_checkpoints(
        self, checkpoints, finalization, packet, check, context,
    ):
        self.assembly_calls += 1
        return super().assemble_review_checkpoints(
            checkpoints, finalization, packet, check, context,
        )


class RuntimeFailingPlugin(TrackingPlugin):
    def validate_review_checkpoint(
        self, checkpoint, collection_items, prior_checkpoints, packet, check, context,
    ):
        self.validation_calls += 1
        raise RuntimeError("injected plugin validator failure")

    def finalize(self, work_items, investigations, decisions, output_dir, status):
        raise AssertionError("platform-owned partial closeout must skip plugin hooks")


def contract(
    *, version: str = "1.0.0", checkpoint_collection: str = "signals",
    finalization: bool = True,
) -> AgentContractBundle:
    return AgentContractBundle(
        contract_id="dev.assayer.fixture.interactive-review",
        contract_version=version,
        check_id="FIX-INT-001",
        check_version="1.0.0",
        checkpoint_payload_schemas={
            checkpoint_collection: {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["summary"],
                "properties": {"summary": {"type": "string", "minLength": 1}},
            },
        } if checkpoint_collection else {},
        finalization_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}},
        } if finalization else None,
        semantic_instructions_path="semantic-review.md",
        semantic_instructions_sha256=hashlib.sha256(b"fixture review").hexdigest(),
    )


def strict_registration(plugin: TrackingPlugin, bundle: AgentContractBundle):
    return replace(
        registration(),
        plugin_factory=lambda runtime=None: plugin,
        agent_contracts=(bundle,),
    )


class AgentContractHostBoundaryTests(unittest.TestCase):
    def _transport(self, output: Path, plugin: TrackingPlugin, bundle=None):
        registered = strict_registration(plugin, bundle or contract())
        return InteractivePlatformMcpToolTransport(
            output, plugin_registry=PluginRegistry((registered,)),
        )

    @staticmethod
    def _start_and_advance(transport):
        started = transport.call_tool("start_plugin_run", {
            "pluginId": "fixture.interactive-quality",
            "checkId": "FIX-INT-001",
            "scope": {},
        })["structuredContent"]["result"]
        boundary = transport.call_tool("advance_plugin_run", {})[
            "structuredContent"
        ]["result"]
        return started, boundary

    def test_semantic_task_exposes_only_the_selected_frozen_schema(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            started, boundary = self._start_and_advance(transport)

        self.assertEqual(started["result"]["agentContract"], {
            "contractId": bundle.contract_id,
            "contractVersion": bundle.contract_version,
            "contractDigest": bundle.contract_digest,
        })
        task = boundary["result"]["semanticTask"]
        self.assertEqual(task["agentContract"], {
            "contractId": bundle.contract_id,
            "contractVersion": bundle.contract_version,
            "contractDigest": bundle.contract_digest,
            "inputKind": "reviewCheckpoint",
            "schema": bundle.checkpoint_payload_schemas["signals"],
        })

    def test_missing_digest_and_invalid_payload_fail_before_plugin_hook(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            transport = self._transport(output, plugin, bundle)
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            revision = boundary["runRevision"]
            base = {
                "workItemId": task["workItemId"],
                "collectionId": task["collectionId"],
                "itemIds": task["itemIds"],
            }

            with self.assertRaises(HostError) as stale:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {**base, "payload": {"summary": "Reviewed."}},
                })
            self.assertEqual(stale.exception.code, "AGENT_CONTRACT_STALE")

            with self.assertRaises(HostError) as invalid:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        **base,
                        "payload": {"unexpected": True},
                        "contractDigest": bundle.contract_digest,
                    },
                })
            self.assertEqual(invalid.exception.code, "AGENT_CONTRACT_INPUT_INVALID")
            structured = invalid.exception.as_dict()
            self.assertEqual(structured["owner"], "agent_input")
            self.assertEqual(structured["retryDisposition"], "agent_correction")
            self.assertFalse(structured["retryable"])
            self.assertEqual(structured["requiredNextStep"], "correct_agent_input")
            self.assertEqual(structured["contractDigest"], bundle.contract_digest)
            self.assertTrue(structured["requestId"].startswith("request:"))
            self.assertTrue(structured["errors"])
            self.assertEqual(structured["correctionBudget"], {
                "maximumCorrections": 1,
                "correctionsUsed": 0,
                "correctionsRemaining": 1,
                "exhausted": False,
            })
            self.assertEqual(json.loads(str(invalid.exception)), structured)
            self.assertEqual(plugin.validation_calls, 0)

            progress = transport.call_tool("get_plugin_progress", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(progress["runRevision"], revision)
            self.assertEqual(progress["result"]["reviewCheckpoints"], 0)

            accepted = transport.call_tool("advance_plugin_run", {
                "reviewCheckpoint": {
                    **base,
                    "payload": {"summary": "Reviewed."},
                    "contractDigest": bundle.contract_digest,
                },
            })["structuredContent"]["result"]
            self.assertEqual(plugin.validation_calls, 1)
            self.assertGreater(accepted["runRevision"], revision)

    def test_finalization_is_validated_before_plugin_assembly(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            while task["kind"] == "review_evidence_items":
                boundary = transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": task["workItemId"],
                        "collectionId": task["collectionId"],
                        "itemIds": task["itemIds"],
                        "payload": {"summary": "Page reviewed."},
                        "contractDigest": bundle.contract_digest,
                    },
                })["structuredContent"]["result"]
                task = boundary["result"]["semanticTask"]

            self.assertEqual(task["kind"], "finalize_decision")
            self.assertEqual(task["agentContract"]["inputKind"], "finalization")
            decision = {
                "workItemId": task["workItemId"],
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "All evidence pages were reviewed.",
                "contractDigest": bundle.contract_digest,
            }
            with self.assertRaises(HostError) as invalid:
                transport.call_tool("advance_plugin_run", {
                    "decision": {**decision, "finalization": {}},
                })
            self.assertEqual(invalid.exception.code, "AGENT_CONTRACT_INPUT_INVALID")
            self.assertEqual(plugin.assembly_calls, 0)

            finished = transport.call_tool("advance_plugin_run", {
                "decision": {
                    **decision,
                    "finalization": {"summary": "Complete."},
                },
            })["structuredContent"]["result"]
            self.assertEqual(plugin.assembly_calls, 1)
            self.assertEqual(finished["status"], "completed")

    def test_decision_cannot_bypass_required_checkpoint_finalization(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            revision = boundary["runRevision"]

            with self.assertRaises(HostError) as invalid:
                transport.call_tool("advance_plugin_run", {
                    "decision": {
                        "workItemId": task["workItemId"],
                        "result": "scanned_no_issue",
                        "findings": [{
                            "dimension": "present",
                            "status": "satisfied",
                            "reason": "Reviewed.",
                        }],
                        "reason": "Attempted to skip checkpoint review.",
                        "contractDigest": bundle.contract_digest,
                    },
                })

            self.assertEqual(invalid.exception.code, "AGENT_CONTRACT_INPUT_INVALID")
            self.assertEqual(plugin.validation_calls, 0)
            self.assertEqual(plugin.assembly_calls, 0)
            progress = transport.call_tool("get_plugin_progress", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(progress["runRevision"], revision)
            self.assertEqual(progress["result"]["decisionsCommitted"], 0)

    def test_strict_envelope_rejects_multiple_semantic_mutations(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            checkpoint = {
                "workItemId": task["workItemId"],
                "collectionId": task["collectionId"],
                "itemIds": task["itemIds"],
                "payload": {"summary": "Reviewed."},
                "contractDigest": bundle.contract_digest,
            }
            with self.assertRaises(HostError) as invalid:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": checkpoint,
                    "closeout": {"status": "partial"},
                })

            structured = invalid.exception.as_dict()
            self.assertEqual(
                structured["code"], "AGENT_CONTRACT_ENVELOPE_INVALID",
            )
            self.assertEqual(structured["owner"], "agent_input")
            self.assertEqual(structured["errors"][0]["pointer"], "/")
            self.assertEqual(plugin.validation_calls, 0)

    def test_correction_budget_survives_resume_and_exhaustion_closes_partial(self):
        plugin = TrackingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first = self._transport(output, plugin, bundle)
            started, boundary = self._start_and_advance(first)
            task = boundary["result"]["semanticTask"]
            invalid_submission = {
                "reviewCheckpoint": {
                    "workItemId": task["workItemId"],
                    "collectionId": task["collectionId"],
                    "itemIds": task["itemIds"],
                    "payload": {"unexpected": True},
                    "contractDigest": bundle.contract_digest,
                },
            }
            with self.assertRaises(HostError) as initial:
                first.call_tool("advance_plugin_run", invalid_submission)
            self.assertEqual(
                initial.exception.retry_disposition, "agent_correction",
            )
            first.close()

            resumed = self._transport(output, plugin, bundle)
            resumed.call_tool("resume_plugin_run", {"runId": started["runId"]})
            with self.assertRaises(HostError) as exhausted:
                resumed.call_tool("advance_plugin_run", invalid_submission)

            structured = exhausted.exception.as_dict()
            self.assertEqual(
                structured["code"], "AGENT_CORRECTION_BUDGET_EXHAUSTED",
            )
            self.assertEqual(structured["owner"], "agent_input")
            self.assertEqual(structured["retryDisposition"], "none")
            self.assertEqual(structured["requiredNextStep"], "read_terminal_result")
            self.assertEqual(structured["terminalStatus"], "partial")
            self.assertEqual(structured["correctionBudget"], {
                "maximumCorrections": 1,
                "correctionsUsed": 1,
                "correctionsRemaining": 0,
                "exhausted": True,
            })
            self.assertEqual(plugin.validation_calls, 0)
            self.assertEqual(plugin.assembly_calls, 0)

            terminal = resumed.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(terminal["status"], "partial")
            self.assertEqual(terminal["result"]["decisions"], [])
            overview = terminal["result"]["resultOverview"]
            self.assertEqual(overview["needsReviewCount"], 1)
            self.assertEqual(overview["outcomes"]["needs_review"], 0)
            review_page = resumed.call_tool("get_plugin_result", {
                "sectionId": terminal["result"]["reviewItems"]["sectionId"],
            })["structuredContent"]["result"]["result"]
            self.assertEqual(
                review_page["items"][0]["gaps"]["contractBoundary"]["code"],
                "AGENT_CORRECTION_BUDGET_EXHAUSTED",
            )

            ledger = json.loads((
                output / started["runId"]
                / f"{started['runId']}.platform-ledger.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(ledger["status"], "partial")
            self.assertEqual(ledger["decisions"], [])
            self.assertEqual(len(ledger["failures"]), 1)
            rejections = [
                event for event in ledger["events"]
                if event["name"] == "host.request.rejected"
            ]
            self.assertEqual(len(rejections), 2)
            self.assertEqual(
                rejections[0]["details"]["boundaryKey"],
                rejections[1]["details"]["boundaryKey"],
            )

    def test_runtime_collection_without_registered_schema_fails_before_agent_turn(self):
        plugin = TrackingPlugin()
        bundle = contract(checkpoint_collection="other-items")
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            transport.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality",
                "checkId": "FIX-INT-001",
                "scope": {},
            })
            with self.assertRaises(HostError) as mismatch:
                transport.call_tool("advance_plugin_run", {})

            structured = mismatch.exception.as_dict()
            self.assertEqual(structured["owner"], "plugin")
            self.assertEqual(structured["retryDisposition"], "none")
            self.assertEqual(structured["terminalStatus"], "partial")
            terminal = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(terminal["status"], "partial")
            self.assertEqual(terminal["result"]["decisions"], [])

        self.assertEqual(
            mismatch.exception.code,
            "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
        )
        self.assertEqual(plugin.validation_calls, 0)

    def test_unexpected_plugin_failure_has_zero_agent_budget_and_closes_partial(self):
        plugin = RuntimeFailingPlugin()
        bundle = contract()
        with tempfile.TemporaryDirectory() as directory:
            transport = self._transport(Path(directory) / "output", plugin, bundle)
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            with self.assertRaises(HostError) as failed:
                transport.call_tool("advance_plugin_run", {
                    "reviewCheckpoint": {
                        "workItemId": task["workItemId"],
                        "collectionId": task["collectionId"],
                        "itemIds": task["itemIds"],
                        "payload": {"summary": "Schema-valid checkpoint."},
                        "contractDigest": bundle.contract_digest,
                    },
                })

            structured = failed.exception.as_dict()
            self.assertEqual(structured["code"], "PLUGIN_RUNTIME_FAILURE")
            self.assertEqual(structured["owner"], "plugin")
            self.assertEqual(structured["retryDisposition"], "none")
            self.assertFalse(structured["retryable"])
            self.assertEqual(structured["terminalStatus"], "partial")
            self.assertEqual(structured["correctionBudget"]["maximumCorrections"], 0)
            self.assertNotIn("RuntimeError", structured["message"])
            self.assertNotIn("injected", structured["message"])
            self.assertEqual(plugin.validation_calls, 1)
            terminal = transport.call_tool("advance_plugin_run", {})[
                "structuredContent"
            ]["result"]
            self.assertEqual(terminal["status"], "partial")
            self.assertEqual(terminal["result"]["decisions"], [])

    def test_resume_rejects_changed_contract_with_same_plugin_version(self):
        first_bundle = contract(version="1.0.0")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            first = self._transport(output, TrackingPlugin(), first_bundle)
            started = first.call_tool("start_plugin_run", {
                "pluginId": "fixture.interactive-quality",
                "checkId": "FIX-INT-001",
                "scope": {},
            })["structuredContent"]["result"]
            first.close()

            replacement = self._transport(
                output, TrackingPlugin(), contract(version="1.1.0"),
            )
            with self.assertRaises(HostError) as stale:
                replacement.call_tool("resume_plugin_run", {"runId": started["runId"]})

        self.assertEqual(stale.exception.code, "AGENT_CONTRACT_STALE")

    def test_direct_decision_task_requires_the_frozen_digest(self):
        bundle = contract(checkpoint_collection="", finalization=False)
        registered = replace(
            reference_collection_registration(), agent_contracts=(bundle,),
        )
        with tempfile.TemporaryDirectory() as directory:
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output",
                plugin_registry=PluginRegistry((registered,)),
            )
            _, boundary = self._start_and_advance(transport)
            task = boundary["result"]["semanticTask"]
            self.assertEqual(task["kind"], "decide_work_item")
            self.assertEqual(task["agentContract"]["inputKind"], "decision")
            self.assertIn("contractDigest", task["agentContract"]["schema"]["required"])
            self.assertNotIn(
                "finalization", task["agentContract"]["schema"]["properties"],
            )
            self.assertNotIn(
                "reviewCheckpointIds", task["agentContract"]["schema"]["properties"],
            )
            decision = {
                "workItemId": task["workItemId"],
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "present", "status": "satisfied", "reason": "Reviewed.",
                }],
                "reason": "The reference-only evidence was reviewed.",
            }
            with self.assertRaises(HostError) as stale:
                transport.call_tool("advance_plugin_run", {"decision": decision})
            self.assertEqual(stale.exception.code, "AGENT_CONTRACT_STALE")

            finished = transport.call_tool("advance_plugin_run", {
                "decision": {**decision, "contractDigest": bundle.contract_digest},
            })["structuredContent"]["result"]
            self.assertEqual(finished["status"], "completed")

    def test_transport_uses_shared_envelopes_with_optional_contract_digest(self):
        transport = InteractivePlatformMcpToolTransport(
            plugin_registry=PluginRegistry((registration(),)),
        )
        tools = {item["name"]: item["inputSchema"] for item in transport.list_tools()}
        self.assertIn(
            "contractDigest",
            tools["checkpoint_review"]["properties"],
        )
        self.assertIn(
            "contractDigest",
            tools["submit_decisions"]["properties"]["decisions"]
            ["items"]["properties"],
        )


if __name__ == "__main__":
    unittest.main()
