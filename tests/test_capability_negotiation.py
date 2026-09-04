from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    CapabilityNegotiator,
    CapabilityProfile,
    PlatformContractError,
    ProviderRegistration,
    ProviderRegistry,
    load_provider_descriptor,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def provider_registration(
    provider_id="fixture.source-provider", *, capabilities=None,
    user_scope_required=True,
):
    capability_values = capabilities or [{
        "name": "structured_read", "version": "1.0.0",
        "accessMode": "read_only", "evidenceKinds": ["structured"],
    }]
    failures = [
        {"code": code, "retry": "resolve_unknown_first" if code == "result_unknown" else "never"}
        for code in (
            "capability_unavailable", "authorization_denied", "timeout",
            "budget_exceeded", "source_changed", "stale_state",
            "source_error", "result_unknown",
        )
    ]
    descriptor = load_provider_descriptor({
        "providerId": provider_id,
        "version": "1.2.0",
        "platformApiVersion": "1.0.0",
        "capabilities": capability_values,
        "scopeSchema": {
            "type": "object", "additionalProperties": False,
            "required": ["sources"],
            "properties": {"sources": {"type": "array"}},
        },
        "authorization": {
            "userScopeRequired": user_scope_required,
            "secretHandling": "none",
        },
        "limits": {
            "timeoutMs": 30000, "maxBytes": 1000000,
            "maxItems": 100, "maxConcurrency": 4,
        },
        "failurePolicy": failures,
        "algorithmVersions": {"sourceIdentity": "1.0.0"},
    })

    class Provider:
        def __init__(self):
            self.descriptor = descriptor

        def collect(self, request, context):
            return request, context

    return ProviderRegistration(descriptor, provider_factory=Provider)


class CapabilityNegotiationTests(unittest.TestCase):
    def test_ready_profile_is_the_four_way_intersection_with_minimum_limits(self):
        result = CapabilityNegotiator().negotiate(
            provider_registration(),
            ("structured_read",),
            CapabilityProfile(
                frozenset({"structured_read", "platform_extra"}),
                {"timeoutMs": 20000, "maxConcurrency": 2},
            ),
            user_profile=CapabilityProfile(
                frozenset({"structured_read", "user_extra"}),
                {"maxItems": 25, "maxBytes": 500000},
            ),
            scope={"sources": ["spec.md"]},
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.granted, ("structured_read",))
        self.assertEqual(dict(result.limits), {
            "timeoutMs": 20000,
            "maxBytes": 500000,
            "maxItems": 25,
            "maxConcurrency": 2,
        })
        context = result.context("run-provider")
        self.assertEqual(context.capabilities, frozenset({"structured_read"}))
        self.assertEqual(context.capability_profile.limits["maxItems"], 25)

    def test_no_party_can_widen_the_requested_profile(self):
        result = CapabilityNegotiator().negotiate(
            provider_registration(),
            ("structured_read",),
            CapabilityProfile(frozenset({"structured_read", "platform_extra"})),
            user_profile=CapabilityProfile(frozenset({"structured_read", "user_extra"})),
            scope={"sources": []},
        )
        self.assertNotIn("platform_extra", result.granted)
        self.assertNotIn("user_extra", result.granted)

    def test_provider_platform_and_user_denials_are_distinguished(self):
        negotiator = CapabilityNegotiator()
        provider_missing = negotiator.negotiate(
            provider_registration(),
            ("repository_read",),
            CapabilityProfile(frozenset({"repository_read"})),
            user_profile=CapabilityProfile(frozenset({"repository_read"})),
            scope={"sources": []},
        )
        platform_denied = negotiator.negotiate(
            provider_registration(),
            ("structured_read",),
            CapabilityProfile(frozenset()),
            user_profile=CapabilityProfile(frozenset({"structured_read"})),
            scope={"sources": []},
        )
        user_denied = negotiator.negotiate(
            provider_registration(),
            ("structured_read",),
            CapabilityProfile(frozenset({"structured_read"})),
            user_profile=None,
            scope={"sources": []},
        )
        self.assertEqual(provider_missing.denied[0]["code"], "provider_absent")
        self.assertEqual(platform_denied.denied[0]["code"], "platform_denied")
        self.assertEqual(user_denied.denied[0]["code"], "user_scope_missing")
        with self.assertRaises(PlatformContractError) as blocked:
            user_denied.context("run-blocked")
        self.assertEqual(blocked.exception.code, "CAPABILITY_NEGOTIATION_BLOCKED")

    def test_optional_user_scope_defaults_only_to_requested_capabilities(self):
        result = CapabilityNegotiator().negotiate(
            provider_registration(user_scope_required=False),
            ("structured_read",),
            CapabilityProfile(frozenset({"structured_read"})),
            user_profile=None,
            scope={"sources": []},
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.granted, ("structured_read",))

    def test_invalid_provider_scope_fails_before_runtime_construction(self):
        registration = provider_registration()
        with self.assertRaises(PlatformContractError) as error:
            CapabilityNegotiator().negotiate(
                registration,
                ("structured_read",),
                CapabilityProfile(frozenset({"structured_read"})),
                user_profile=CapabilityProfile(frozenset({"structured_read"})),
                scope={"path": "outside-schema"},
            )
        self.assertEqual(error.exception.code, "PROVIDER_SCOPE_INVALID")

    def test_invalid_budget_or_capability_request_fails_closed(self):
        with self.assertRaises(PlatformContractError) as budget:
            CapabilityNegotiator().negotiate(
                provider_registration(),
                ("structured_read",),
                CapabilityProfile(frozenset({"structured_read"}), {"timeoutMs": 0}),
                user_profile=CapabilityProfile(frozenset({"structured_read"})),
                scope={"sources": []},
            )
        self.assertEqual(budget.exception.code, "CAPABILITY_LIMIT_INVALID")
        with self.assertRaises(PlatformContractError) as request:
            CapabilityNegotiator().negotiate(
                provider_registration(),
                ("structured_read", 3),
                CapabilityProfile(frozenset({"structured_read"})),
                user_profile=CapabilityProfile(frozenset({"structured_read"})),
                scope={"sources": []},
            )
        self.assertEqual(request.exception.code, "CAPABILITY_REQUEST_INVALID")

    def test_registry_selects_one_provider_covering_the_complete_requirement(self):
        structured = provider_registration("fixture.structured-provider")
        combined = provider_registration(
            "fixture.combined-provider",
            capabilities=[
                {
                    "name": "structured_read", "version": "1.0.0",
                    "accessMode": "read_only", "evidenceKinds": ["structured"],
                },
                {
                    "name": "visual_read", "version": "1.0.0",
                    "accessMode": "read_only", "evidenceKinds": ["visual"],
                },
            ],
        )
        registry = ProviderRegistry((structured, combined))
        self.assertIs(
            registry.select_for_capabilities(("structured_read", "visual_read")),
            combined,
        )
        with self.assertRaises(PlatformContractError) as ambiguous:
            registry.select_for_capabilities(("structured_read",))
        self.assertEqual(ambiguous.exception.code, "PROVIDER_AMBIGUOUS")

    def test_negotiation_result_satisfies_public_schema(self):
        result = CapabilityNegotiator().negotiate(
            provider_registration(),
            ("structured_read",),
            CapabilityProfile(frozenset({"structured_read"})),
            user_profile=CapabilityProfile(frozenset({"structured_read"})),
            scope={"sources": []},
        )
        schemas = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
        schema = schemas["capability-negotiation.schema.json"]
        Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        ).validate(result.as_dict())


if __name__ == "__main__":
    unittest.main()
