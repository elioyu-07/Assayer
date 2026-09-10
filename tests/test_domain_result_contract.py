"""Tests for the platform-envelope-free SDK v2 domain contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform import (
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
            transport.call_tool("start_plugin_run", {
                "pluginId": "test.config-quality", "checkId": "CFG-001",
                "scope": {"files": [{"path": str(source)}]},
            })
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
        registered = replace(
            config_quality_registration(),
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

    def test_registration_gate_rejects_mixed_legacy_and_domain_contracts(self):
        from dataclasses import replace
        from assayer_platform.agent_contract import AgentContractBundle

        legacy = AgentContractBundle(
            contract_id="dev.assayer.fixture.legacy",
            contract_version="1.0.0",
            check_id="CFG-001",
            check_version="1.0.0",
            checkpoint_payload_schemas={},
            finalization_schema=None,
            semantic_instructions_path="legacy.md",
            semantic_instructions_sha256=INSTRUCTIONS_SHA256,
        )
        with self.assertRaises(PlatformContractError) as rejected:
            replace(
                config_quality_registration(),
                execution_modes=frozenset({"interactive"}),
                agent_contracts=(legacy,),
                domain_result_contracts=(domain_contract(),),
            )
        self.assertEqual(rejected.exception.code, "PLUGIN_LEGACY_CONTRACT_UNSUPPORTED")

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
            protocol_min_version="1.2.0",
            protocol_max_version="1.2.0",
            protocol_capabilities=frozenset({
                "task_local_evidence_handles", "domain_result",
            }),
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
            self.assertEqual(task["kind"], "domain_review")
            self.assertNotIn("workItemId", task)
            self.assertNotIn("taskDigest", task)
            self.assertIn("domainContract", task)
            self.assertIn("semanticInstructions", task["domainContract"])
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
                "workItemId", "taskDigest", "contractDigest", "checkpointId",
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
            "required": ["result", "findings", "reason", "evidenceRefs"],
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
                "evidenceRefs": {"type": "array", "items": {"type": "string"}},
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
            evidence_id = pending["result"]["semanticTask"]["investigation"]["evidence"][0]["evidenceId"]
            handles = pending["result"]["semanticTask"]["evidenceHandles"]
            self.assertEqual(handles[0]["evidenceRef"], "R1")
            result = controller.advance(
                controller.active_run_id,
                domain_result={
                    "result": "scanned_no_issue",
                    "reason": "All dimensions are satisfied.",
                    "findings": [
                        {"dimension": name, "status": "satisfied", "reason": "ok"}
                        for name in ("parseable", "required_keys", "value_types")
                    ],
                    "evidenceRefs": ["R1"],
                },
            )
            self.assertEqual(result["status"], "completed")
            ledger_path = Path(directory) / "output" / controller.terminal_run_id / f"{controller.terminal_run_id}.platform-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["decisions"][0]["details"]["evidenceRefs"], [evidence_id])

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
            self.assertEqual(first["result"]["semanticTask"]["evidenceHandles"][0]["evidenceRef"], "R1")
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
            self.assertEqual(second["result"]["semanticTask"]["evidenceHandles"][0]["evidenceRef"], "R2")
            run_id = controller.active_run_id
            controller.close()
            controller = InteractivePluginController(
                PluginRegistry((registration,)), Path(directory) / "output",
            )
            resumed = controller.resume(run_id)
            self.assertEqual(
                resumed["result"]["semanticTask"]["evidenceHandles"][0]["evidenceRef"],
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
            "required": ["result", "findings", "reason", "evidenceRefs"],
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
                "evidenceRefs": {"type": "array", "items": {"type": "string"}},
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
                    "evidenceRefs": ["chunk:config:1"],
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
            "required": ["result", "findings", "reason", "evidenceRefs"],
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
                "evidenceRefs": {"type": "array", "items": {"type": "string"}},
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
                    controller.advance(run_id, domain_result={**base, "evidenceRefs": refs})
                self.assertEqual(rejected.exception.code, "DOMAIN_EVIDENCE_REFERENCE_INVALID")
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
