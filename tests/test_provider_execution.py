from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    BoundCapabilityProvider,
    CapabilityNegotiator,
    CapabilityProfile,
    PlatformContractError,
    ProviderFact,
    ProviderFailure,
    ProviderRegistration,
    ProviderRuntimeLease,
    ProviderResponse,
    WorkItem,
    load_plugin_manifest,
    load_provider_descriptor,
)
from assayer_platform.registry import schema_store


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def provider_descriptor(*, max_bytes=10000, max_items=10, **overrides):
    value = {
        "providerId": "fixture.source-provider",
        "version": "1.2.0",
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
            "required": ["source"],
            "properties": {"source": {"type": "string"}},
        },
        "authorization": {
            "userScopeRequired": True,
            "secretHandling": "none",
        },
        "limits": {
            "timeoutMs": 30000,
            "maxBytes": max_bytes,
            "maxItems": max_items,
            "maxConcurrency": 1,
        },
        "failurePolicy": [
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
        ],
        "algorithmVersions": {
            "sourceIdentity": "1.0.0",
            "stateDigest": "1.0.0",
            "sanitization": "1.0.0",
        },
    }
    value.update(overrides)
    return load_provider_descriptor(value)


class RecordingProvider:
    def __init__(self, descriptor, mode="success"):
        self.descriptor = descriptor
        self.mode = mode
        self.calls = 0
        self.requests = []
        self.closed = False
        self.close_calls = 0

    def collect(self, request, context):
        self.calls += 1
        self.requests.append((request, context))
        if self.mode == "result_unknown":
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "result_unknown",
                    "Internal detail at /private/target with token=secret",
                ),
            )
        if self.mode == "unclassified":
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure("unexpected_failure", "private detail"),
            )
        if self.mode == "missing_failure":
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
            )
        fact = ProviderFact(
            "undeclared" if self.mode == "wrong_kind" else "structured",
            "another-source" if self.mode == "wrong_source" else request.source_identity,
            "another-state" if self.mode == "wrong_state" else request.state_digest,
            {"observed": True, "content": "x" * (2000 if self.mode == "large" else 1)},
        )
        facts = (fact, fact) if self.mode == "many" else (fact,)
        return ProviderResponse(
            request.request_id + ("-wrong" if self.mode == "wrong_request" else ""),
            "fixture.substitute" if self.mode == "wrong_provider" else request.provider_id,
            request.provider_version,
            request.capability,
            "succeeded",
            facts,
        )

    def close(self):
        self.closed = True
        self.close_calls += 1


def provider_registration(provider, descriptor=None):
    descriptor = descriptor or provider.descriptor
    return ProviderRegistration(descriptor, provider_factory=lambda runtime=None: provider)


CHECK_MANIFEST = load_plugin_manifest({
    "pluginId": "fixture.provider-plugin",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
    "compatibility": {
        "protocolMinVersion": "1.2.0", "protocolMaxVersion": "1.2.0",
        "sdkMinVersion": "0.1.2", "sdkMaxVersion": "0.1.2",
    },
    "domains": ["fixture"],
    "subjectKinds": ["fixture_item"],
    "checks": [{
        "checkId": "FIX-101",
        "version": "1.0.0",
        "subjectKinds": ["fixture_item"],
        "dimensions": ["present"],
        "decisionStates": ["scanned_no_issue", "needs_review"],
        "requiredEvidenceKinds": ["structured"],
        "requiredCapabilities": ["structured_read"],
        "capabilityMissingOutcome": "needs_review",
        "invalidationSignals": ["source_digest"],
    }],
    "executionProfile": {
        "discoverBatching": "forbidden",
        "inspectBatching": "forbidden",
        "decisionBatching": "forbidden",
        "parallelism": "forbidden",
        "cacheReuse": "allowed",
    },
})


def negotiate(registration, *, max_bytes=None):
    limits = {} if max_bytes is None else {"maxBytes": max_bytes}
    return CapabilityNegotiator().negotiate(
        registration,
        ("structured_read",),
        CapabilityProfile(frozenset({"structured_read"}), limits),
        user_profile=CapabilityProfile(frozenset({"structured_read"})),
        scope={"source": "fixture"},
    )


class ProviderExecutionTests(unittest.TestCase):
    def bind(self, provider, *, max_bytes=None):
        registration = provider_registration(provider)
        return BoundCapabilityProvider(
            registration,
            negotiate(registration, max_bytes=max_bytes),
            run_id="run-provider",
            scope={"source": "fixture"},
        )

    def test_bound_provider_close_is_idempotent(self):
        provider = RecordingProvider(provider_descriptor())
        bound = self.bind(provider)
        bound.close()
        bound.close()
        self.assertEqual(provider.close_calls, 1)

    def test_bound_provider_releases_an_explicit_runtime_lease_once(self):
        provider = RecordingProvider(provider_descriptor())
        releases = []
        runtime = object()
        lease = ProviderRuntimeLease(runtime, lambda: releases.append(runtime))
        registration = ProviderRegistration(
            provider.descriptor,
            provider_factory=lambda injected: provider,
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiate(registration),
            run_id="run-provider",
            scope={"source": "fixture"},
            runtime=lease,
        )

        bound.close()
        bound.close()
        lease.close()

        self.assertEqual(releases, [runtime])
        self.assertTrue(lease.closed)

    def test_bound_provider_does_not_close_a_bare_runtime(self):
        provider = RecordingProvider(provider_descriptor())
        runtime = SimpleNamespace(close_calls=0)
        runtime.close = lambda: setattr(runtime, "close_calls", runtime.close_calls + 1)
        registration = ProviderRegistration(
            provider.descriptor,
            provider_factory=lambda injected: provider,
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiate(registration),
            run_id="run-provider",
            scope={"source": "fixture"},
            runtime=runtime,
        )

        bound.close()

        self.assertEqual(runtime.close_calls, 0)

    def test_host_creates_bounded_idempotent_request_and_evidence(self):
        provider = RecordingProvider(provider_descriptor())
        bound = self.bind(provider)
        check = CHECK_MANIFEST.checks[0]
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        first = bound.collect(item, check, "structured_read")
        second = bound.collect(item, check, "structured_read")

        self.assertIs(first, second)
        self.assertEqual(provider.calls, 1)
        request, context = provider.requests[0]
        self.assertEqual(request.run_id, "run-provider")
        self.assertEqual(request.work_item_id, item.work_item_id)
        self.assertEqual(request.source_identity, item.identity)
        self.assertEqual(request.state_digest, item.state_digest)
        self.assertEqual(context.capabilities, frozenset({"structured_read"}))
        evidence = first.evidence[0]
        self.assertEqual(evidence.provider_id, provider.descriptor.provider_id)
        self.assertEqual(evidence.provider_version, provider.descriptor.version)
        self.assertEqual(evidence.capability, "structured_read")
        self.assertEqual(evidence.source_state_digest, item.state_digest)
        self.assertEqual(
            dict(evidence.algorithm_versions),
            dict(provider.descriptor.algorithm_versions),
        )

    def test_per_request_scope_overrides_the_run_scope(self):
        provider = RecordingProvider(provider_descriptor())
        bound = self.bind(provider)
        check = CHECK_MANIFEST.checks[0]
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        first = bound.collect(item, check, "structured_read", scope={"source": "other"})
        second = bound.collect(item, check, "structured_read", scope={"source": "other"})
        third = bound.collect(item, check, "structured_read")

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(dict(provider.requests[0][0].scope), {"source": "other"})
        self.assertEqual(dict(provider.requests[1][0].scope), {"source": "fixture"})
        self.assertNotEqual(
            provider.requests[0][0].idempotency_key,
            provider.requests[1][0].idempotency_key,
        )

    def test_per_request_scope_must_satisfy_the_provider_scope_schema(self):
        bound = self.bind(RecordingProvider(provider_descriptor()))
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        with self.assertRaises(PlatformContractError) as error:
            bound.collect(
                item, CHECK_MANIFEST.checks[0], "structured_read",
                scope={"unexpected": True},
            )
        self.assertEqual(error.exception.code, "PROVIDER_SCOPE_INVALID")

    def test_response_identity_source_state_and_kind_must_match(self):
        expected = {
            "wrong_request": "PROVIDER_RESPONSE_IDENTITY_MISMATCH",
            "wrong_provider": "PROVIDER_RESPONSE_IDENTITY_MISMATCH",
            "wrong_source": "PROVIDER_SOURCE_MISMATCH",
            "wrong_state": "PROVIDER_STATE_MISMATCH",
            "wrong_kind": "PROVIDER_EVIDENCE_KIND_UNDECLARED",
        }
        check = CHECK_MANIFEST.checks[0]
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        for mode, code in expected.items():
            with self.subTest(mode=mode):
                bound = self.bind(RecordingProvider(provider_descriptor(), mode))
                with self.assertRaises(PlatformContractError) as error:
                    bound.collect(item, check, "structured_read")
                self.assertEqual(error.exception.code, code)

    def test_published_provider_result_schema_is_enforced_before_evidence(self):
        provider = RecordingProvider(provider_descriptor(
            resultSchema={
                "type": "object",
                "required": ["content"],
                "properties": {"content": {"const": "published"}},
            },
        ))
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        with self.assertRaises(PlatformContractError) as error:
            bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        self.assertEqual(error.exception.code, "PROVIDER_RESULT_INVALID")

    def test_byte_budget_returns_classified_failure_without_evidence(self):
        provider = RecordingProvider(provider_descriptor(max_bytes=10000), "large")
        bound = self.bind(provider, max_bytes=500)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        result = bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")

        self.assertEqual(result.evidence, ())
        self.assertEqual(result.failure.code, "budget_exceeded")
        self.assertEqual(result.retry, "never")

    def test_item_budget_returns_classified_failure_without_evidence(self):
        descriptor = provider_descriptor(max_items=10)
        provider = RecordingProvider(descriptor, "many")
        registration = provider_registration(provider)
        negotiation = CapabilityNegotiator().negotiate(
            registration,
            ("structured_read",),
            CapabilityProfile(
                frozenset({"structured_read"}), {"maxItems": 1},
            ),
            user_profile=CapabilityProfile(frozenset({"structured_read"})),
            scope={"source": "fixture"},
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiation,
            run_id="run-provider",
            scope={"source": "fixture"},
        )
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        result = bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")

        self.assertEqual(result.evidence, ())
        self.assertEqual(result.failure.code, "budget_exceeded")

    def test_late_provider_result_is_discarded_as_timeout(self):
        provider = RecordingProvider(provider_descriptor())
        registration = provider_registration(provider)
        negotiation = CapabilityNegotiator().negotiate(
            registration,
            ("structured_read",),
            CapabilityProfile(
                frozenset({"structured_read"}), {"timeoutMs": 1},
            ),
            user_profile=CapabilityProfile(frozenset({"structured_read"})),
            scope={"source": "fixture"},
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiation,
            run_id="run-provider",
            scope={"source": "fixture"},
        )
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        with patch(
            "assayer_platform.provider_execution.time.monotonic",
            side_effect=(1.0, 1.1),
        ):
            result = bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")

        self.assertEqual(result.evidence, ())
        self.assertEqual(result.failure.code, "timeout")

    def test_result_unknown_is_safe_and_never_blindly_replayed(self):
        provider = RecordingProvider(provider_descriptor(), "result_unknown")
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")

        first = bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        second = bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")

        self.assertIs(first, second)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(first.failure.code, "result_unknown")
        self.assertEqual(first.retry, "resolve_unknown_first")
        self.assertNotIn("private", first.failure.message.lower())
        self.assertNotIn("token", first.failure.message.lower())

    def test_unclassified_failure_is_rejected(self):
        provider = RecordingProvider(provider_descriptor(), "unclassified")
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        with self.assertRaises(PlatformContractError) as error:
            bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        self.assertEqual(error.exception.code, "PROVIDER_FAILURE_UNCLASSIFIED")

    def test_failed_response_without_failure_details_is_rejected(self):
        provider = RecordingProvider(provider_descriptor(), "missing_failure")
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        bound._validator = SimpleNamespace(validate=lambda value: None)
        with self.assertRaises(PlatformContractError) as error:
            bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        self.assertEqual(error.exception.code, "PROVIDER_RESPONSE_INVALID")

    def test_execution_envelopes_satisfy_public_schema(self):
        provider = RecordingProvider(provider_descriptor())
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        request = provider.requests[0][0]
        schemas = schema_store(SCHEMAS)
        schema = schemas["provider-execution.schema.json"]
        validator = Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        )
        validator.validate({
            "request": {
                "schemaVersion": "1.0.0",
                "requestId": request.request_id,
                "idempotencyKey": request.idempotency_key,
                "runId": request.run_id,
                "workItemId": request.work_item_id,
                "checkId": request.check_id,
                "checkVersion": request.check_version,
                "providerId": request.provider_id,
                "providerVersion": request.provider_version,
                "capability": request.capability,
                "sourceIdentity": request.source_identity,
                "stateDigest": request.state_digest,
                "scope": dict(request.scope),
                "limits": dict(request.limits),
            },
        })


if __name__ == "__main__":
    unittest.main()
