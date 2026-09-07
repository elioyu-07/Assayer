"""Executable Agent contract registration and conformance tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

from assayer_platform import (
    AGENT_CONTRACT_SCHEMA_DIALECT,
    AgentContractBundle,
    PlatformContractError,
    PluginRegistry,
    inspect_plugin_registration,
)
from tests.helpers import config_quality_registration


INSTRUCTIONS_SHA256 = hashlib.sha256(b"review instructions").hexdigest()


def agent_contract(**overrides) -> AgentContractBundle:
    values = {
        "contract_id": "dev.assayer.fixture.review",
        "contract_version": "1.0.0",
        "check_id": "CFG-001",
        "check_version": "1.0.0",
        "checkpoint_payload_schemas": {
            "configuration-items": {
                "$schema": AGENT_CONTRACT_SCHEMA_DIALECT,
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"enum": ["PASS", "REWORK"]}},
            },
        },
        "finalization_schema": {
            "$schema": AGENT_CONTRACT_SCHEMA_DIALECT,
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}},
        },
        "semantic_instructions_path": "semantic-review.md",
        "semantic_instructions_sha256": INSTRUCTIONS_SHA256,
    }
    values.update(overrides)
    return AgentContractBundle(**values)


def interactive_registration(*contracts, review_payload_schema=None):
    base = config_quality_registration()
    return replace(
        base,
        execution_modes=frozenset({"interactive"}),
        agent_contracts=contracts,
        review_payload_schema=review_payload_schema or {},
    )


class AgentContractBundleTests(unittest.TestCase):
    def test_digest_is_deterministic_and_input_mutation_cannot_change_bundle(self):
        source = {
            "configuration-items": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
        }
        first = agent_contract(checkpoint_payload_schemas=source)
        source["configuration-items"]["properties"]["status"]["type"] = "integer"
        second = agent_contract(checkpoint_payload_schemas={
            "configuration-items": {
                "properties": {"status": {"type": "string"}},
                "type": "object",
            },
        })

        self.assertEqual(first.contract_digest, second.contract_digest)
        self.assertEqual(
            first.checkpoint_payload_schemas["configuration-items"]
            ["properties"]["status"]["type"],
            "string",
        )
        self.assertRegex(first.contract_digest, r"^sha256:[a-f0-9]{64}$")

    def test_non_json_contract_value_fails_at_construction(self):
        with self.assertRaises(PlatformContractError) as rejected:
            agent_contract(checkpoint_payload_schemas={"configuration-items": {"const": object()}})
        self.assertEqual(rejected.exception.code, "INVALID_AGENT_CONTRACT_BUNDLE")


class AgentContractRegistrationTests(unittest.TestCase):
    def test_complete_agent_contract_passes_registration_conformance(self):
        registration = interactive_registration(agent_contract())
        report = inspect_plugin_registration(registration)

        self.assertTrue(report.passed, report.as_dict())
        self.assertEqual(
            registration.agent_contracts[0].check_ref,
            ("CFG-001", "1.0.0"),
        )
        self.assertIs(
            registration.agent_contract_for(("CFG-001", "1.0.0")),
            registration.agent_contracts[0],
        )

    def test_legacy_registration_remains_compatible_during_model_slice(self):
        registration = replace(
            config_quality_registration(),
            execution_modes=frozenset({"interactive"}),
        )
        self.assertTrue(inspect_plugin_registration(registration).passed)

    def test_unknown_check_also_reports_incomplete_check_coverage(self):
        registration = interactive_registration(agent_contract(
            check_id="UNKNOWN", contract_id="dev.assayer.fixture.unknown",
        ))
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}

        self.assertIn("PLUGIN_AGENT_CONTRACT_CHECK_UNKNOWN", codes)
        self.assertIn("PLUGIN_AGENT_CONTRACT_CHECK_COVERAGE_INCOMPLETE", codes)

    def test_duplicate_check_contracts_are_rejected(self):
        registration = interactive_registration(
            agent_contract(),
            agent_contract(contract_id="dev.assayer.fixture.review-copy"),
        )
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_DUPLICATE", codes)

    def test_legacy_review_schema_conflicts_with_executable_contract(self):
        registration = interactive_registration(
            agent_contract(), review_payload_schema={"type": "object"},
        )
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_CONFLICT", codes)

    def test_agent_contract_requires_interactive_execution_mode(self):
        base = config_quality_registration()
        registration = replace(base, agent_contracts=(agent_contract(),))
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_MODE_INVALID", codes)

    def test_invalid_boundary_schema_fails_registration(self):
        registration = interactive_registration(agent_contract(
            checkpoint_payload_schemas={
                "configuration-items": {"type": "not-a-json-schema-type"},
            },
        ))
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_SCHEMA_INVALID", codes)

        with self.assertRaises(PlatformContractError) as rejected:
            PluginRegistry((registration,))
        self.assertEqual(rejected.exception.code, "PLUGIN_AGENT_CONTRACT_SCHEMA_INVALID")

    def test_boundary_schema_must_explicitly_be_an_object(self):
        registration = interactive_registration(agent_contract(
            checkpoint_payload_schemas={
                "configuration-items": {"oneOf": [{"type": "object"}]},
            },
        ))
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_SCHEMA_INVALID", codes)

    def test_checkpoint_contract_requires_a_finalization_schema(self):
        registration = interactive_registration(agent_contract(
            finalization_schema=None,
        ))
        codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
        self.assertIn("PLUGIN_AGENT_CONTRACT_FINALIZATION_MISSING", codes)

    def test_remote_and_unresolved_schema_references_are_rejected(self):
        remote = interactive_registration(agent_contract(
            checkpoint_payload_schemas={
                "configuration-items": {
                    "type": "object",
                    "properties": {"status": {"$ref": "https://example.invalid/status.json"}},
                },
            },
        ))
        unresolved = interactive_registration(agent_contract(
            checkpoint_payload_schemas={
                "configuration-items": {
                    "type": "object",
                    "properties": {"status": {"$ref": "#/$defs/missing"}},
                },
            },
        ))

        for registration in (remote, unresolved):
            codes = {issue.code for issue in inspect_plugin_registration(registration).issues}
            self.assertIn("PLUGIN_AGENT_CONTRACT_SCHEMA_REFERENCE_INVALID", codes)

    def test_bundle_metadata_is_validated_by_the_registration_gate(self):
        registration = interactive_registration(agent_contract(
            contract_id="bad id",
            contract_version="latest",
            semantic_instructions_path="../review.md",
            semantic_instructions_sha256="not-a-digest",
            schema_dialect="https://example.invalid/schema",
        ))
        issues = inspect_plugin_registration(registration).issues

        self.assertGreaterEqual(
            sum(issue.code == "PLUGIN_AGENT_CONTRACT_INVALID" for issue in issues),
            5,
        )


if __name__ == "__main__":
    unittest.main()
