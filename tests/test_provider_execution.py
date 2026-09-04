from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    BoundCapabilityProvider,
    CapabilityNegotiator,
    CapabilityProfile,
    DecisionProposal,
    DimensionObservation,
    Finding,
    InvestigationPacket,
    PlatformContractError,
    PlatformRunner,
    PluginRegistration,
    PluginRegistry,
    ProviderFact,
    ProviderFailure,
    ProviderRegistration,
    ProviderRegistry,
    ProviderResponse,
    WorkItem,
    load_plugin_manifest,
    load_provider_descriptor,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def provider_descriptor(*, max_bytes=10000, max_items=10):
    return load_provider_descriptor({
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
    })


class RecordingProvider:
    def __init__(self, descriptor, mode="success"):
        self.descriptor = descriptor
        self.mode = mode
        self.calls = 0
        self.requests = []
        self.closed = False

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


def provider_registration(provider, descriptor=None):
    descriptor = descriptor or provider.descriptor
    return ProviderRegistration(descriptor, provider_factory=lambda runtime=None: provider)


CHECK_MANIFEST = load_plugin_manifest({
    "pluginId": "fixture.provider-plugin",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
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
        "checkpoint": "required",
    },
})


class ProviderPlugin:
    manifest = CHECK_MANIFEST

    def __init__(self, provider, tamper=None):
        self.provider = provider
        self.tamper = tamper

    def discover(self, scope, context):
        del scope, context
        return (WorkItem(
            "fixture-item",
            "fixture_item",
            "fixture-source",
            "fixture-state",
        ),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        result = self.provider.collect(item, check, "structured_read")
        if result.failure is not None:
            raise PlatformContractError(
                "PROVIDER_FACTS_UNAVAILABLE",
                result.failure.message,
            )
        evidence = result.evidence[0]
        if self.tamper == "version":
            evidence = replace(evidence, provider_version="9.0.0")
        elif self.tamper == "algorithm":
            evidence = replace(evidence, algorithm_versions={"sourceIdentity": "9.0.0"})
        elif self.tamper == "state":
            evidence = replace(evidence, source_state_digest="stale-state")
        observation = DimensionObservation(
            "present",
            ("The provider returned the required structured fact.",),
            (evidence.evidence_id,),
            "satisfied",
        )
        return (InvestigationPacket(
            item,
            check.check_id,
            check.version,
            (observation,),
            (evidence,),
            "not_required",
        ),)


class FixtureDecisionProvider:
    def __init__(self, runtime=None):
        del runtime

    def decide(self, packets, check, context):
        del context
        packet = packets[0]
        return (DecisionProposal(
            packet.work_item.work_item_id,
            check.check_id,
            check.version,
            "scanned_no_issue",
            (Finding("present", "satisfied", "The required fact is present."),),
            "The provider Evidence satisfies the Check.",
        ),)


def plugin_registration(*, tamper=None, factory_counter=None):
    def create(runtime=None):
        if factory_counter is not None:
            factory_counter["calls"] += 1
        return ProviderPlugin(runtime, tamper)

    return PluginRegistration(
        CHECK_MANIFEST,
        plugin_factory=create,
        decision_provider_factory=lambda runtime=None: FixtureDecisionProvider(runtime),
        capabilities=frozenset({"structured_read"}),
        scope_schema={"type": "object", "additionalProperties": False},
    )


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

    def test_blocked_negotiation_constructs_neither_provider_nor_plugin(self):
        provider_calls = {"calls": 0}
        plugin_calls = {"calls": 0}
        descriptor = provider_descriptor()

        def create_provider(runtime=None):
            del runtime
            provider_calls["calls"] += 1
            return RecordingProvider(descriptor)

        with tempfile.TemporaryDirectory() as directory:
            runner = PlatformRunner(
                PluginRegistry((plugin_registration(factory_counter=plugin_calls),)),
                directory,
            )
            with self.assertRaises(PlatformContractError) as error:
                runner.run_with_provider(
                    plugin_id="fixture.provider-plugin",
                    check_id="FIX-101",
                    scope={},
                    provider_registry=ProviderRegistry((
                        ProviderRegistration(descriptor, provider_factory=create_provider),
                    )),
                    provider_scope={"source": "fixture"},
                    platform_profile=CapabilityProfile(frozenset()),
                    user_profile=CapabilityProfile(frozenset({"structured_read"})),
                )
        self.assertEqual(error.exception.code, "CAPABILITY_NEGOTIATION_BLOCKED")
        self.assertEqual(provider_calls["calls"], 0)
        self.assertEqual(plugin_calls["calls"], 0)

    def test_provider_bound_runner_persists_complete_evidence_identity(self):
        provider = RecordingProvider(provider_descriptor())
        with tempfile.TemporaryDirectory() as directory:
            runner = PlatformRunner(
                PluginRegistry((plugin_registration(),)),
                directory,
            )
            result = runner.run_with_provider(
                plugin_id="fixture.provider-plugin",
                check_id="FIX-101",
                scope={},
                provider_registry=ProviderRegistry((provider_registration(provider),)),
                provider_scope={"source": "fixture"},
                platform_profile=CapabilityProfile(frozenset({"structured_read"})),
                user_profile=CapabilityProfile(frozenset({"structured_read"})),
                run_id="run-provider-ledger",
            )

            self.assertEqual(result.status, "completed")
            self.assertTrue(provider.closed)
            evidence = result.ledger.investigations[0].evidence[0]
            self.assertEqual(evidence.run_id, result.run_id)
            self.assertEqual(evidence.provider_request_id, provider.requests[0][0].request_id)
            ledger_path = Path(directory) / result.run_id / f"{result.run_id}.platform-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            stored = ledger["investigations"][0]["evidence"][0]
            self.assertEqual(stored["provider_id"], provider.descriptor.provider_id)
            self.assertEqual(stored["source_state_digest"], "fixture-state")

    def test_kernel_rejects_tampered_provider_evidence_before_decision(self):
        for tamper, code in {
            "version": "PROVIDER_EVIDENCE_IDENTITY_MISMATCH",
            "algorithm": "PROVIDER_EVIDENCE_ALGORITHM_MISMATCH",
            "state": "PROVIDER_EVIDENCE_STATE_MISMATCH",
        }.items():
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                provider = RecordingProvider(provider_descriptor())
                runner = PlatformRunner(
                    PluginRegistry((plugin_registration(tamper=tamper),)),
                    directory,
                )
                result = runner.run_with_provider(
                    plugin_id="fixture.provider-plugin",
                    check_id="FIX-101",
                    scope={},
                    provider_registry=ProviderRegistry((provider_registration(provider),)),
                    provider_scope={"source": "fixture"},
                    platform_profile=CapabilityProfile(frozenset({"structured_read"})),
                    user_profile=CapabilityProfile(frozenset({"structured_read"})),
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.decisions, ())
                self.assertEqual(result.failures[0].code, code)

    def test_execution_envelopes_satisfy_public_schema(self):
        provider = RecordingProvider(provider_descriptor())
        bound = self.bind(provider)
        item = WorkItem("fixture-item", "fixture_item", "fixture-source", "fixture-state")
        bound.collect(item, CHECK_MANIFEST.checks[0], "structured_read")
        request = provider.requests[0][0]
        schemas = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
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
