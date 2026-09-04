from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    PlatformContractError,
    ProviderRegistration,
    ProviderRegistry,
    inspect_provider_registration,
    load_provider_descriptor,
)
from assayer_platform.provider_conformance import main


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
FAILURE_POLICY = [
    {"code": "capability_unavailable", "retry": "never"},
    {"code": "authorization_denied", "retry": "never"},
    {"code": "timeout", "retry": "safe_with_same_request"},
    {"code": "budget_exceeded", "retry": "never"},
    {"code": "source_changed", "retry": "safe_with_same_request"},
    {"code": "stale_state", "retry": "never"},
    {"code": "source_error", "retry": "safe_with_same_request"},
    {"code": "result_unknown", "retry": "resolve_unknown_first"},
]


def descriptor_value(provider_id="fixture.file-provider", **overrides):
    value = {
        "providerId": provider_id,
        "version": "1.0.0",
        "platformApiVersion": "1.0.0",
        "capabilities": [{
            "name": "structured_read",
            "version": "1.0.0",
            "accessMode": "read_only",
            "evidenceKinds": ["structured"],
        }],
        "scopeSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"files": {"type": "array"}},
        },
        "authorization": {
            "userScopeRequired": True,
            "secretHandling": "none",
        },
        "limits": {
            "timeoutMs": 30000,
            "maxBytes": 1000000,
            "maxItems": 100,
            "maxConcurrency": 1,
        },
        "failurePolicy": list(FAILURE_POLICY),
        "algorithmVersions": {
            "sourceIdentity": "1.0.0",
            "stateDigest": "1.0.0",
        },
    }
    value.update(overrides)
    return value


class FixtureProvider:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def collect(self, request, context):
        return {"request": request, "runId": context.run_id}


def registration(provider_id="fixture.file-provider", **descriptor_overrides):
    descriptor = load_provider_descriptor(descriptor_value(provider_id, **descriptor_overrides))
    return ProviderRegistration(
        descriptor,
        provider_factory=lambda runtime=None: FixtureProvider(descriptor),
    )


class ProviderConformanceTests(unittest.TestCase):
    def test_valid_provider_passes_static_and_constructed_conformance(self):
        report = inspect_provider_registration(
            registration(), construct_implementation=True,
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.provider_id, "fixture.file-provider")

    def test_descriptor_can_be_loaded_from_an_independent_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.json"
            path.write_text(json.dumps(descriptor_value()), encoding="utf-8")
            descriptor = load_provider_descriptor(path)
        self.assertEqual(descriptor.provider_id, "fixture.file-provider")
        self.assertEqual(descriptor.capabilities[0].name, "structured_read")

    def test_platform_api_mismatch_fails_before_registration(self):
        with self.assertRaises(PlatformContractError) as error:
            load_provider_descriptor(descriptor_value(platformApiVersion="2.0.0"))
        self.assertEqual(error.exception.code, "PROVIDER_API_INCOMPATIBLE")

    def test_scope_schema_must_be_valid_and_object_shaped(self):
        with self.assertRaises(PlatformContractError) as malformed:
            load_provider_descriptor(descriptor_value(
                scopeSchema={"type": "not-a-type"},
            ))
        self.assertEqual(malformed.exception.code, "PROVIDER_SCOPE_SCHEMA_INVALID")
        with self.assertRaises(PlatformContractError) as array:
            load_provider_descriptor(descriptor_value(
                scopeSchema={"type": "array"},
            ))
        self.assertEqual(array.exception.code, "PROVIDER_SCOPE_SCHEMA_INVALID")

    def test_duplicate_capabilities_and_failures_are_actionable(self):
        value = descriptor_value()
        value["capabilities"].append(dict(value["capabilities"][0]))
        value["failurePolicy"].append(dict(value["failurePolicy"][0]))
        descriptor = load_provider_descriptor(value)
        report = inspect_provider_registration(ProviderRegistration(
            descriptor, provider_factory=lambda: FixtureProvider(descriptor),
        ))
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PROVIDER_CAPABILITY_DUPLICATE", "PROVIDER_FAILURE_DUPLICATE"},
        )
        self.assertTrue(all(issue.next_action for issue in report.issues))

    def test_complete_failure_taxonomy_and_unknown_reconciliation_are_required(self):
        incomplete = registration(failurePolicy=FAILURE_POLICY[:-1])
        incomplete_report = inspect_provider_registration(incomplete)
        self.assertIn(
            "PROVIDER_FAILURE_POLICY_INCOMPLETE",
            {issue.code for issue in incomplete_report.issues},
        )
        unsafe_policy = [
            {**item, "retry": "safe_with_same_request"}
            if item["code"] == "result_unknown" else item
            for item in FAILURE_POLICY
        ]
        unsafe = inspect_provider_registration(registration(failurePolicy=unsafe_policy))
        self.assertIn(
            "PROVIDER_UNKNOWN_RETRY_UNSAFE",
            {issue.code for issue in unsafe.issues},
        )

    def test_controlled_action_requires_user_scope(self):
        capabilities = [{
            "name": "safe_interaction",
            "version": "1.0.0",
            "accessMode": "controlled_action",
            "evidenceKinds": ["interaction_receipt"],
        }]
        report = inspect_provider_registration(registration(
            capabilities=capabilities,
            authorization={"userScopeRequired": False, "secretHandling": "none"},
        ))
        self.assertIn(
            "PROVIDER_AUTHORIZATION_INCOMPLETE",
            {issue.code for issue in report.issues},
        )

    def test_algorithm_identity_is_required_for_evidence(self):
        report = inspect_provider_registration(registration(algorithmVersions={}))
        self.assertEqual(report.issues[0].code, "PROVIDER_ALGORITHM_IDENTITY_MISSING")

    def test_runtime_factory_and_implementation_are_checked_without_live_collection(self):
        descriptor = registration().descriptor
        missing = inspect_provider_registration(ProviderRegistration(descriptor))
        broken = inspect_provider_registration(
            ProviderRegistration(descriptor, provider_factory=lambda: object()),
            construct_implementation=True,
        )
        failed = inspect_provider_registration(
            ProviderRegistration(
                descriptor,
                provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("secret /private")),
            ),
            construct_implementation=True,
        )
        self.assertEqual(missing.issues[0].code, "PROVIDER_RUNTIME_UNAVAILABLE")
        self.assertEqual(
            {issue.code for issue in broken.issues},
            {"PROVIDER_IDENTITY_MISMATCH", "PROVIDER_RUNTIME_INCOMPLETE"},
        )
        self.assertEqual(failed.issues[0].code, "PROVIDER_INITIALIZATION_FAILED")
        self.assertNotIn("private", json.dumps(failed.as_dict()))

    def test_registry_rejects_duplicate_identity_and_ambiguous_capability(self):
        first = registration("fixture.first-provider")
        duplicate = registration("fixture.first-provider")
        registry = ProviderRegistry((first,))
        with self.assertRaises(PlatformContractError) as conflict:
            registry.register(duplicate)
        self.assertEqual(conflict.exception.code, "PROVIDER_CONFLICT")
        registry.register(registration("fixture.second-provider"))
        with self.assertRaises(PlatformContractError) as ambiguous:
            registry.select(capability="structured_read")
        self.assertEqual(ambiguous.exception.code, "PROVIDER_AMBIGUOUS")
        self.assertIs(registry.select(provider_id="fixture.first-provider"), first)

    def test_registry_rejects_nonconforming_registration_before_use(self):
        descriptor = registration().descriptor
        with self.assertRaises(PlatformContractError) as error:
            ProviderRegistry((ProviderRegistration(descriptor),))
        self.assertEqual(error.exception.code, "PROVIDER_RUNTIME_UNAVAILABLE")
        self.assertIn("CPV1-RUNTIME-FACTORY", error.exception.message)

    def test_cli_failure_is_schema_valid_and_suppresses_exception_details(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["missing.secret.module:registration"])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertNotIn("secret.module", payload["providers"][0]["issues"][0]["message"])
        schemas = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
        schema = schemas["provider-conformance.schema.json"]
        Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        ).validate(payload)


if __name__ == "__main__":
    unittest.main()
