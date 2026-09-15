"""Tests for the platform-envelope-free SDK v2 domain contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform import (
    CommitReceipt,
    DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION,
    DomainResultContract,
    PlatformContractError,
    InteractivePluginController,
    inspect_plugin_registration,
    PluginRegistry,
)
from assayer_platform.error_policy import boundary_error_policy
from assayer_host import HostError, InteractivePlatformMcpToolTransport
from tests.helpers import ConfigQualityPlugin, config_quality_registration


INSTRUCTIONS_SHA256 = hashlib.sha256(b"domain instructions").hexdigest()


def domain_contract(**overrides) -> DomainResultContract:
    values = {
        "contract_id": "dev.assayer.fixture.domain-result",
        "contract_version": "1.0.0",
        "check_id": "CFG-001",
        "check_version": "1.0.0",
        "result_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["decisions"],
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["candidate_id", "status"],
                        "properties": {
                            "candidate_id": {"type": "string"},
                            "status": {"enum": ["CONFIRMED", "REJECTED"]},
                        },
                    },
                },
            },
        },
        "semantic_instructions_path": "semantic-review.md",
        "semantic_instructions_sha256": INSTRUCTIONS_SHA256,
    }
    values.update(overrides)
    return DomainResultContract(**values)


class DomainResultContractTests(unittest.TestCase):
    def test_host_bounds_large_domain_evidence_without_plugin_pagination(self):
        """Large packets stay in the Host ledger, not in one Agent payload."""
        class LargeEvidencePlugin(ConfigQualityPlugin):
            def inspect(self, work_items, check, context):
                packets = super().inspect(work_items, check, context)
                packet = packets[0]
                evidence = packet.evidence[0]
                payload = dict(evidence.payload)
                payload["sourceChunks"] = [
                    {
                        "source_chunk_id": f"source:fixture:{index}",
                        "excerpt": "x" * 1800,
                    }
                    for index in range(220)
                ]
                payload["navigation"] = [
                    {"title": f"Section {index}", "content": "y" * 900}
                    for index in range(180)
                ]
                payload["dimensionEvidence"] = [
                    {"dimension": "value_types", "mapping": "z" * 700}
                    for _ in range(300)
                ]
                return [replace(
                    packet,
                    evidence=(replace(evidence, payload=payload),),
                )]

        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: LargeEvidencePlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(),),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            pending = controller.advance(controller.active_run_id)
            task = pending["result"]["semanticTask"]
            encoded = json.dumps(task, ensure_ascii=False).encode("utf-8")
            self.assertLessEqual(len(encoded), 24 * 1024)
            serialized = encoded.decode("utf-8")
            self.assertNotIn("source_chunk_id", serialized)
            self.assertNotIn("sourceDigest", serialized)
            self.assertIn("truncated", serialized)
            paging = task["agentView"]["evidencePaging"]
            self.assertEqual(paging["pageSize"], 4)
            self.assertEqual(paging["total"], 220)
            self.assertIsNotNone(paging["nextCursor"])
            expanded = controller.expand_semantic_evidence(
                controller.active_run_id,
                cursor=paging["nextCursor"], page_size=20,
            )
            self.assertEqual(expanded["status"], "semantic_evidence_expanded")
            self.assertEqual(expanded["result"]["page"]["count"], 20)
            self.assertTrue(all(
                item["evidenceRef"].startswith("R")
                for item in expanded["result"]["evidence"]
            ))

    def test_domain_input_failures_are_owned_by_agent_with_one_explicit_correction(self):
        for code in ("DOMAIN_RESULT_INVALID", "DOMAIN_EVIDENCE_REFERENCE_INVALID"):
            policy = boundary_error_policy(code)
            self.assertEqual(policy.owner, "agent_input")
            self.assertEqual(policy.retry_disposition, "agent_correction")
            self.assertEqual(policy.required_next_step, "correct_domain_result")

    def test_domain_result_correction_budget_is_bound_without_agent_platform_ids(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": "scanned_no_issue",
                    "findings": [{
                        "dimension": name, "status": "satisfied", "reason": "ok",
                    } for name in ("parseable", "required_keys", "value_types")],
                    "reason": result["reason"],
                }

        registered = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(result_schema={
                "type": "object", "additionalProperties": False,
                "required": ["reason"],
                "properties": {"reason": {"type": "string", "minLength": 1}},
            }),),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output",
                plugin_registry=PluginRegistry((registered,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "test.config-quality", "checkId": "CFG-001",
                "scope": {"files": [{"path": str(source)}]},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("advance_plugin_run", {})
            with self.assertRaises(HostError) as first:
                transport.call_tool("advance_plugin_run", {"domainResult": {}})
            self.assertEqual(first.exception.code, "DOMAIN_RESULT_INVALID")
            self.assertEqual(first.exception.owner, "agent_input")
            self.assertEqual(first.exception.retry_disposition, "agent_correction")
            self.assertIsNone(first.exception.contract_digest)
            self.assertEqual(first.exception.correction_budget["correctionsRemaining"], 1)
            with self.assertRaises(HostError) as second:
                transport.call_tool("advance_plugin_run", {"domainResult": {}})
            self.assertEqual(second.exception.code, "AGENT_CORRECTION_BUDGET_EXHAUSTED")
            self.assertEqual(second.exception.terminal_status, "partial")
            self.assertIn("Original validation errors:", second.exception.message)
            self.assertIn("/reason", second.exception.message)
            coverage = json.loads((
                Path(directory) / "output" / run_id
                / f"{run_id}.review-coverage.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(coverage["terminalStatus"], "partial")
            self.assertEqual(coverage["entries"][0]["status"], "blocked")

    def test_digest_is_deterministic_and_schema_is_detached(self):
        source = domain_contract().result_schema
        first = domain_contract(result_schema=source)
        source["properties"]["extra"] = {"type": "string"}
        second = domain_contract()

        self.assertEqual(first.contract_digest, second.contract_digest)
        self.assertRegex(first.contract_digest, r"^sha256:[a-f0-9]{64}$")
        self.assertEqual(
            first.as_dict()["inputKind"], "domainResult",
        )
        self.assertEqual(
            DOMAIN_RESULT_CONTRACT_CANONICALIZATION_VERSION, "1.0.0",
        )

    def test_result_shape_is_derived_from_the_executable_schema(self):
        contract = domain_contract(result_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["review"],
            "properties": {
                "review": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["cross_document_review", "document_context"],
                    "properties": {
                        "cross_document_review": {"type": "string"},
                        "document_context": {"type": "string"},
                    },
                },
            },
        })
        self.assertEqual(contract.result_shape["requiredPaths"], [
            "/review", "/review/cross_document_review", "/review/document_context",
        ])
        self.assertEqual(contract.result_shape["allowedPaths"], [
            "/review", "/review/cross_document_review", "/review/document_context",
        ])
        self.assertTrue(contract.result_shape["strict"])

    def test_platform_envelope_fields_are_rejected_even_when_nested(self):
        with self.assertRaises(PlatformContractError) as rejected:
            domain_contract(result_schema={
                "type": "object",
                "properties": {
                    "decisions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"taskDigest": {"type": "string"}},
                        },
                    },
                },
            })
        self.assertEqual(rejected.exception.code, "INVALID_DOMAIN_RESULT_CONTRACT")

    def test_check_identity_fields_are_host_owned(self):
        with self.assertRaises(PlatformContractError) as rejected:
            domain_contract(result_schema={
                "type": "object",
                "properties": {"checkId": {"type": "string"}},
            })
        self.assertEqual(rejected.exception.code, "INVALID_DOMAIN_RESULT_CONTRACT")

    def test_semantic_rule_ids_are_unique_and_instructions_are_required(self):
        with self.assertRaises(PlatformContractError) as rejected:
            domain_contract(semantic_rules=[
                {"ruleId": "CFG-RULE", "instruction": "Do this."},
                {"ruleId": "CFG-RULE", "instruction": "Do that."},
            ])
        self.assertEqual(rejected.exception.code, "INVALID_DOMAIN_RESULT_CONTRACT")

    def test_registration_gate_validates_domain_contract_coverage(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(),),
        )
        report = inspect_plugin_registration(registration)
        self.assertTrue(report.passed, report.as_dict())

    def test_missing_domain_mapper_fails_before_run_state_is_created(self):
        class NoMapperPlugin(ConfigQualityPlugin):
            map_domain_result = None

        registered = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: NoMapperPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(result_schema={
                "type": "object",
                "required": ["reason"],
                "properties": {"reason": {"type": "string"}},
            }),),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registered,)), Path(directory) / "output",
            )
            with self.assertRaises(PlatformContractError) as rejected:
                controller.start(
                    plugin_id="test.config-quality", check_id="CFG-001",
                    scope={"files": [{"path": str(source)}]},
                )
            self.assertEqual(rejected.exception.code, "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH")
            self.assertIsNone(controller.active_run_id)
            self.assertEqual(list((Path(directory) / "output").iterdir()), [])

    def test_registration_gate_rejects_unknown_check(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(check_id="UNKNOWN"),),
        )
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_DOMAIN_RESULT_CONTRACT_CHECK_UNKNOWN", codes)
        self.assertIn("PLUGIN_DOMAIN_RESULT_CONTRACT_CHECK_COVERAGE_INCOMPLETE", codes)

    def test_host_binds_domain_result_to_the_current_task_without_platform_fields(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del context
                return {
                    "result": result["result"],
                    "findings": result["findings"],
                    "reason": result["reason"],
                    "details": {"source": "domain-result-test"},
                }

        contract = domain_contract(result_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["result", "findings", "reason"],
            "properties": {
                "result": {"enum": ["scanned_no_issue", "needs_review"]},
                "reason": {"type": "string", "minLength": 1},
                "findings": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["dimension", "status", "reason"],
                        "properties": {
                            "dimension": {"type": "string"},
                            "status": {"enum": ["satisfied", "unresolved"]},
                            "reason": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)),
                Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            pending = controller.advance(controller.active_run_id)
            task = pending["result"]["semanticTask"]
            progress = controller.progress(controller.active_run_id)
            self.assertEqual(progress["status"], "running")
            self.assertIn("pendingCandidateIds", progress["result"]["evidenceGraph"])
            self.assertEqual(task["kind"], "domain_review")
            self.assertNotIn("workItemId", task)
            self.assertNotIn("taskDigest", task)
            self.assertIn("domainContract", task)
            self.assertNotIn("agentContract", task)
            self.assertIn("semanticInstructions", task["domainContract"])
            self.assertEqual(
                task["domainContract"]["semanticInstructions"]["uri"],
                "assayer://plugins/test.config-quality/checks/CFG-001/1.0.0/semantic-instructions",
            )
            self.assertEqual(
                task["domainContract"]["resultShape"]["requiredPaths"],
                ["/findings", "/findings/*/dimension", "/findings/*/reason", "/findings/*/status", "/reason", "/result"],
            )
            self.assertNotIn("evidenceHandles", task)
            self.assertNotIn("investigation", task)
            self.assertIn("agentView", task)
            self.assertTrue(all(
                item["evidenceRef"].startswith("R")
                for item in task["agentView"]["evidence"]
            ))
            serialized_task = json.dumps(task)
            for forbidden in (
                "workItemId", "taskDigest", "contractDigest",
                "evidenceId", "sourceDigest", "source_chunk_id", "document_path",
            ):
                self.assertNotIn(forbidden, serialized_task)
            result = controller.advance(
                controller.active_run_id,
                domain_result={
                    "result": "scanned_no_issue",
                    "reason": "All dimensions are satisfied.",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                },
            )
            self.assertEqual(result["status"], "completed")
            bill_path = (
                Path(directory) / "output" / result["runId"]
                / f"{result['runId']}.platform-performance-bill.json"
            )
            bill = json.loads(bill_path.read_text(encoding="utf-8"))
            self.assertEqual(bill["measurement"]["semanticTask"]["status"], "captured")
            self.assertEqual(bill["measurement"]["agentWait"]["status"], "captured")
            self.assertLessEqual(bill["source"]["semanticTaskBytes"], 24 * 1024)

    def test_host_resolves_task_handle_into_decision_details(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": result["result"],
                    "findings": result["findings"],
                    "reason": result["reason"],
                }

        contract = domain_contract(result_schema={
            "type": "object", "additionalProperties": False,
            "required": ["result", "findings", "reason", "supportedBy"],
            "properties": {
                "result": {"enum": ["scanned_no_issue"]},
                "reason": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "status": {"enum": ["satisfied"]},
                        "reason": {"type": "string", "minLength": 1},
                    },
                }},
                "supportedBy": {"type": "array", "items": {"type": "string"}},
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            pending = controller.advance(controller.active_run_id)
            task = pending["result"]["semanticTask"]
            self.assertNotIn("investigation", task)
            self.assertNotIn("evidenceHandles", task)
            self.assertEqual(task["agentView"]["evidence"][0]["evidenceRef"], "R1")
            result = controller.advance(
                controller.active_run_id,
                domain_result={
                    "result": "scanned_no_issue",
                    "reason": "All dimensions are satisfied.",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                    "supportedBy": ["R1"],
                },
            )
            self.assertEqual(result["status"], "completed")
            ledger_path = Path(directory) / "output" / controller.terminal_run_id / f"{controller.terminal_run_id}.platform-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            evidence_id = ledger["investigations"][0]["evidence"][0]["evidence_id"]
            self.assertEqual(ledger["decisions"][0]["details"]["evidenceRefs"], [evidence_id])

    def test_successful_interactive_transport_is_billed_but_rejected_input_is_not(self):
        """Transport timing follows successful boundaries, never Agent correction failures."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            registration = replace(
                config_quality_registration(),
                execution_modes=frozenset({"interactive"}),
                domain_result_contracts=(domain_contract(result_schema={
                    "type": "object", "additionalProperties": False,
                    "required": ["result", "findings", "reason"],
                    "properties": {
                        "result": {"const": "scanned_no_issue"},
                        "reason": {"type": "string", "minLength": 1},
                        "findings": {"type": "array", "minItems": 1, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["dimension", "status", "reason"],
                            "properties": {
                                "dimension": {"type": "string"},
                                "status": {"const": "satisfied"},
                                "reason": {"type": "string", "minLength": 1},
                            },
                        }},
                    },
                }),),
            )
            transport = InteractivePlatformMcpToolTransport(
                Path(directory) / "output", plugin_registry=PluginRegistry((registration,)),
            )
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "test.config-quality", "checkId": "CFG-001",
                "scope": {"files": [{"path": str(source)}]},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            transport.call_tool("advance_plugin_run", {})
            ledger_path = Path(directory) / "output" / run_id / f"{run_id}.platform-ledger.json"
            before = json.loads(ledger_path.read_text(encoding="utf-8"))["metrics"]
            with self.assertRaises(HostError) as rejected:
                transport.call_tool("advance_plugin_run", {"domainResult": {}})
            self.assertEqual(rejected.exception.code, "DOMAIN_RESULT_INVALID")
            after_rejection = json.loads(ledger_path.read_text(encoding="utf-8"))["metrics"]
            self.assertEqual(after_rejection["transportSamples"], before["transportSamples"])
            result = transport.call_tool("advance_plugin_run", {"domainResult": {
                "result": "scanned_no_issue", "reason": "All dimensions are satisfied.",
                "findings": [
                    {"dimension": name, "status": "satisfied", "reason": "ok"}
                    for name in ("parseable", "required_keys", "value_types")
                ],
            }})
            self.assertEqual(result["structuredContent"]["result"]["status"], "completed")
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            metrics = ledger["metrics"]
            # Only successful advance boundaries are timed; start itself does
            # not force another synchronous ledger write.
            self.assertGreaterEqual(metrics["transportSamples"], 2)
            self.assertGreater(metrics["transportMs"], 0)
            self.assertEqual(metrics["agentWaitSamples"], 1)
            bill = json.loads(
                (Path(directory) / "output" / run_id / f"{run_id}.platform-performance-bill.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(bill["measurement"]["transport"]["status"], "captured")
            self.assertEqual(bill["measurement"]["agentWait"]["status"], "captured")

    def test_resume_preserves_semantic_and_transport_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = []
            for index in (1, 2):
                source = Path(directory) / f"config-{index}.json"
                source.write_text(json.dumps({"enabled": True, "index": index}), encoding="utf-8")
                sources.append({"path": str(source)})
            registration = replace(
                config_quality_registration(),
                execution_modes=frozenset({"interactive"}),
                domain_result_contracts=(domain_contract(result_schema={
                    "type": "object", "additionalProperties": False,
                    "required": ["result", "findings", "reason"],
                    "properties": {
                        "result": {"const": "scanned_no_issue"},
                        "reason": {"type": "string", "minLength": 1},
                        "findings": {"type": "array", "minItems": 1, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["dimension", "status", "reason"],
                            "properties": {
                                "dimension": {"type": "string"},
                                "status": {"const": "satisfied"},
                                "reason": {"type": "string", "minLength": 1},
                            },
                        }},
                    },
                }),),
            )
            output = Path(directory) / "output"
            transport = InteractivePlatformMcpToolTransport(output, plugin_registry=PluginRegistry((registration,)))
            started = transport.call_tool("start_plugin_run", {
                "pluginId": "test.config-quality", "checkId": "CFG-001",
                "scope": {"files": sources},
            })
            run_id = started["structuredContent"]["result"]["runId"]
            first = transport.call_tool("advance_plugin_run", {})
            self.assertEqual(first["structuredContent"]["result"]["status"], "awaiting_agent_decision")
            base = {
                "result": "scanned_no_issue", "reason": "Satisfied.",
                "findings": [
                    {"dimension": name, "status": "satisfied", "reason": "ok"}
                    for name in ("parseable", "required_keys", "value_types")
                ],
            }
            second = transport.call_tool("advance_plugin_run", {"domainResult": base})
            self.assertEqual(second["structuredContent"]["result"]["status"], "awaiting_agent_decision")
            coverage_path = output / run_id / f"{run_id}.review-coverage.json"
            self.assertTrue(coverage_path.is_file())
            first_coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(first_coverage["batches"]), 2)
            self.assertEqual(first_coverage["batches"][0]["status"], "accepted")
            self.assertEqual(first_coverage["batches"][1]["status"], "offered")
            transport.close()
            resumed_transport = InteractivePlatformMcpToolTransport(
                output, plugin_registry=PluginRegistry((registration,)),
            )
            resumed = resumed_transport.call_tool("resume_plugin_run", {"runId": run_id})
            self.assertEqual(resumed["structuredContent"]["result"]["status"], "awaiting_agent_decision")
            resumed_coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(resumed_coverage["batches"]), 2)
            ledger_path = output / run_id / f"{run_id}.platform-ledger.json"
            metrics = json.loads(ledger_path.read_text(encoding="utf-8"))["metrics"]
            # Resume reconstructs and republishes the active semantic boundary,
            # so the durable counter includes that fresh Host compilation.
            self.assertGreaterEqual(metrics["semanticTaskBuilds"], 2)
            self.assertGreater(metrics["semanticTaskBytes"], 0)
            self.assertEqual(metrics["agentWaitSamples"], 1)
            # The resume call reconstructs the boundary internally; only the
            # two successful advance requests so far have crossed a timed
            # semantic boundary.
            self.assertGreaterEqual(metrics["transportSamples"], 2)
            resumed_transport.call_tool("advance_plugin_run", {"domainResult": base})
            final_coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(final_coverage["batches"]), 2)
            self.assertEqual(final_coverage["terminalStatus"], "completed")
            self.assertTrue(all(
                batch["status"] == "terminal" for batch in final_coverage["batches"]
            ))

    def test_coverage_replays_after_commit_failure_and_process_resume(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del result, packet, check, context
                return {
                    "result": "scanned_no_issue",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                    "reason": "Satisfied.",
                }

        class FlakyCommitter:
            attempts = 0

            def commit(self, proposal, packet, check, context):
                del packet, check, context
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("temporary commit failure")
                return CommitReceipt(
                    f"commit:{proposal.work_item_id}",
                    proposal.work_item_id,
                    proposal.check_id,
                    proposal.check_version,
                    proposal.result,
                    "memory",
                )

        committer = FlakyCommitter()
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            committer_factory=lambda _runtime=None: committer,
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(domain_contract(result_schema={
                "type": "object", "additionalProperties": False,
                "required": ["reason"],
                "properties": {"reason": {"type": "string", "minLength": 1}},
            }),),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            output = Path(directory) / "output"
            run_id = "run:coverage-crash"
            controller = InteractivePluginController(
                PluginRegistry((registration,)), output,
            )
            controller.start(
                plugin_id="test.config-quality",
                check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
                run_id=run_id,
            )
            controller.advance(run_id)
            with self.assertRaises(PlatformContractError) as failed:
                controller.advance(run_id, domain_result={"reason": "Satisfied."})
            self.assertEqual(failed.exception.code, "COMMIT_FAILED")
            coverage_path = output / run_id / f"{run_id}.review-coverage.json"
            before_resume = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(before_resume["verdicts"]), 1)
            self.assertEqual(before_resume["batches"][0]["status"], "accepted")
            controller.close()

            resumed = InteractivePluginController(
                PluginRegistry((registration,)), output,
            )
            boundary = resumed.resume(run_id)
            self.assertEqual(boundary["status"], "awaiting_agent_decision")
            terminal = resumed.advance(run_id, domain_result={"reason": "Satisfied."})
            self.assertEqual(terminal["status"], "completed")
            after_resume = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_resume["verdicts"]), 1)
            self.assertEqual(after_resume["terminalStatus"], "completed")
            self.assertEqual(committer.attempts, 2)

    def test_later_task_does_not_reuse_an_earlier_handle(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": result["result"], "findings": result["findings"],
                    "reason": result["reason"],
                }

        contract = domain_contract(result_schema={
            "type": "object", "additionalProperties": False,
            "required": ["result", "findings", "reason", "supportedBy"],
            "properties": {
                "result": {"const": "scanned_no_issue"},
                "reason": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "status": {"const": "satisfied"},
                        "reason": {"type": "string", "minLength": 1},
                    },
                }},
                "supportedBy": {"type": "array", "items": {"type": "string"}},
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            sources = []
            for index, name in enumerate(("one.json", "two.json"), start=1):
                source = Path(directory) / name
                source.write_text(json.dumps({"enabled": True, "index": index}), encoding="utf-8")
                sources.append({"path": str(source)})
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": sources},
            )
            first = controller.advance(controller.active_run_id)
            self.assertEqual(first["result"]["semanticTask"]["agentView"]["evidence"][0]["evidenceRef"], "R1")
            base = {
                "result": "scanned_no_issue", "reason": "Satisfied.",
                "findings": [
                    {"dimension": name, "status": "satisfied", "reason": "ok"}
                    for name in ("parseable", "required_keys", "value_types")
                ],
            }
            second = controller.advance(
                controller.active_run_id,
                domain_result={**base, "supportedBy": ["R1"]},
            )
            self.assertEqual(second["result"]["semanticTask"]["agentView"]["evidence"][0]["evidenceRef"], "R2")
            run_id = controller.active_run_id
            controller.close()
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            resumed = controller.resume(run_id)
            self.assertEqual(
                resumed["result"]["semanticTask"]["agentView"]["evidence"][0]["evidenceRef"],
                "R2",
            )
            with self.assertRaises(PlatformContractError) as rejected:
                controller.advance(
                    run_id,
                    domain_result={**base, "supportedBy": ["R1"]},
                )
            self.assertEqual(rejected.exception.code, "STALE_EVIDENCE_HANDLE")

    def test_source_chunk_evidence_reference_is_accepted(self):
        class SourceChunkPlugin(ConfigQualityPlugin):
            def inspect(self, work_items, check, context):
                packets = super().inspect(work_items, check, context)
                packet = packets[0]
                evidence = packet.evidence[0]
                payload = dict(evidence.payload)
                payload["source_chunk_id"] = "chunk:config:1"
                return [replace(
                    packet,
                    evidence=(replace(evidence, payload=payload),),
                )]

            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": result["result"],
                    "findings": result["findings"],
                    "reason": result["reason"],
                }

        contract = domain_contract(result_schema={
            "type": "object", "additionalProperties": False,
            "required": ["result", "findings", "reason", "supportedBy"],
            "properties": {
                "result": {"const": "scanned_no_issue"},
                "reason": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "status": {"const": "satisfied"},
                        "reason": {"type": "string", "minLength": 1},
                    },
                }},
                "supportedBy": {"type": "array", "items": {"type": "string"}},
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: SourceChunkPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            pending = controller.advance(controller.active_run_id)
            result = controller.advance(
                controller.active_run_id,
                domain_result={
                    "result": "scanned_no_issue",
                    "reason": "All dimensions are satisfied.",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                    "supportedBy": ["R1"],
                },
            )
            self.assertEqual(result["status"], "completed")

    def test_unknown_or_duplicate_evidence_reference_fails_before_ledger_mutation(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": result["result"],
                    "findings": result["findings"],
                    "reason": result["reason"],
                }

        contract = domain_contract(result_schema={
            "type": "object", "additionalProperties": False,
            "required": ["result", "findings", "reason", "supportedBy"],
            "properties": {
                "result": {"const": "scanned_no_issue"},
                "reason": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "status": {"const": "satisfied"},
                        "reason": {"type": "string", "minLength": 1},
                    },
                }},
                "supportedBy": {"type": "array", "items": {"type": "string"}},
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            pending = controller.advance(controller.active_run_id)
            run_id = controller.active_run_id
            ledger_path = Path(directory) / "output" / run_id / f"{run_id}.platform-ledger.json"
            before = ledger_path.read_text(encoding="utf-8")
            base = {
                "result": "scanned_no_issue",
                "reason": "All dimensions are satisfied.",
                "findings": [
                    {"dimension": name, "status": "satisfied", "reason": "ok"}
                    for name in ("parseable", "required_keys", "value_types")
                ],
            }
            for refs in (["evidence:unknown"], ["evidence:unknown", "evidence:unknown"]):
                with self.assertRaises(PlatformContractError) as rejected:
                    controller.advance(run_id, domain_result={**base, "supportedBy": refs})
                self.assertEqual(rejected.exception.code, "PLATFORM_EVIDENCE_REFERENCE_FORBIDDEN")
                self.assertEqual(ledger_path.read_text(encoding="utf-8"), before)

    def test_mapper_cross_packet_evidence_reference_is_rejected(self):
        class DomainPlugin(ConfigQualityPlugin):
            def map_domain_result(self, result, packet, check, context):
                del packet, check, context
                return {
                    "result": result["result"],
                    "findings": result["findings"],
                    "reason": result["reason"],
                    "details": {"evidenceRefs": ["evidence:other-work-item"]},
                }

        contract = domain_contract(result_schema={
            "type": "object", "additionalProperties": False,
            "required": ["result", "findings", "reason"],
            "properties": {
                "result": {"const": "scanned_no_issue"},
                "reason": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "status": {"const": "satisfied"},
                        "reason": {"type": "string", "minLength": 1},
                    },
                }},
            },
        })
        registration = replace(
            config_quality_registration(),
            plugin_factory=lambda _runtime=None: DomainPlugin(),
            execution_modes=frozenset({"interactive"}),
            domain_result_contracts=(contract,),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            controller.start(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
            )
            controller.advance(controller.active_run_id)
            with self.assertRaises(PlatformContractError) as rejected:
                controller.advance(controller.active_run_id, domain_result={
                    "result": "scanned_no_issue",
                    "reason": "All dimensions are satisfied.",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                })
            self.assertEqual(rejected.exception.code, "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
