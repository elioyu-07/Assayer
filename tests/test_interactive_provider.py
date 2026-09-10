from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    CapabilityProfile,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InteractivePluginController,
    InvestigationPacket,
    PlatformContractError,
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


CAPABILITY = "document_navigation"

PROVIDER_DESCRIPTOR = load_provider_descriptor({
    "providerId": "fixture.markdown-navigation",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
    "capabilities": [{
        "name": CAPABILITY,
        "version": "1.0.0",
        "accessMode": "read_only",
        "evidenceKinds": ["structured"],
    }],
    "scopeSchema": {
        "type": "object", "additionalProperties": False,
        "required": ["path", "format"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "format": {"const": "markdown"},
        },
    },
    "authorization": {"userScopeRequired": True, "secretHandling": "none"},
    "limits": {"timeoutMs": 30000, "maxBytes": 1000000, "maxItems": 100, "maxConcurrency": 1},
    "failurePolicy": [
        {"code": code, "retry": "resolve_unknown_first" if code == "result_unknown" else "never"}
        for code in (
            "capability_unavailable", "authorization_denied", "timeout", "budget_exceeded",
            "source_changed", "stale_state", "source_error", "result_unknown",
        )
    ],
    "algorithmVersions": {"markdownParser": "1.0.0"},
})


class RecordingNavigationProvider:
    descriptor = PROVIDER_DESCRIPTOR

    def __init__(self) -> None:
        self.calls = 0
        self.scopes: list[dict] = []
        self.closed = False

    def collect(self, request, context):
        del context
        self.calls += 1
        self.scopes.append(dict(request.scope))
        fact = ProviderFact(
            "structured", request.source_identity, request.state_digest,
            {"format": "markdown", "units": [{"kind": "heading", "startLine": 1}]},
        )
        return ProviderResponse(
            request.request_id, request.provider_id, request.provider_version,
            request.capability, "succeeded", (fact,),
        )

    def close(self) -> None:
        self.closed = True


MANIFEST = load_plugin_manifest({
    "pluginId": "fixture.navigation-plugin",
    "version": "1.0.0",
    "platformApiVersion": "1.0.0",
    "domains": ["spec"],
    "subjectKinds": ["spec_document"],
    "checks": [{
        "checkId": "NAV-001",
        "version": "1.0.0",
        "subjectKinds": ["spec_document"],
        "dimensions": ["present"],
        "decisionStates": ["scanned_no_issue", "needs_review"],
        "requiredEvidenceKinds": ["structured"],
        "requiredCapabilities": [CAPABILITY],
        "capabilityMissingOutcome": "needs_review",
        "invalidationSignals": ["source_digest"],
    }],
    "executionProfile": {
        "discoverBatching": "forbidden", "inspectBatching": "forbidden",
        "decisionBatching": "forbidden", "parallelism": "forbidden",
        "cacheReuse": "forbidden", "checkpoint": "required",
    },
})


class NavigationPlugin:
    """Consumes navigation only through the SDK CapabilityAccess contract."""

    manifest = MANIFEST

    def __init__(self, access=None) -> None:
        self.access = access

    def discover(self, scope, context):
        del scope, context
        return (WorkItem(
            "spec:1", "spec_document", "spec-source", "state-1",
            {"path": "spec.md", "profile": "default"},
        ),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        result = self.access.collect(
            item, check, CAPABILITY,
            scope={"path": item.metadata["path"], "format": "markdown"},
        )
        if result.failure is not None:
            raise PlatformContractError("PROVIDER_FACTS_UNAVAILABLE", result.failure.message)
        evidence = result.evidence[0]
        observation = DimensionObservation(
            "present", ("The provider returned navigation facts.",),
            (evidence.evidence_id,), "satisfied",
        )
        return (InvestigationPacket(
            item, check.check_id, check.version, (observation,), (evidence,), "not_required",
        ),)


class NavigationDecisionProvider:
    def decide(self, packets, check, context):
        del context
        packet = packets[0]
        return (DecisionProposal(
            packet.work_item.work_item_id, check.check_id, check.version,
            "scanned_no_issue",
            (Finding("present", "satisfied", "The navigation facts are present."),),
            "The provider facts satisfy the Check.",
        ),)


def registration(*, provider_scope_resolver=None) -> PluginRegistration:
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: NavigationPlugin(runtime),
        decision_provider_factory=lambda runtime=None: NavigationDecisionProvider(),
        capabilities=frozenset({CAPABILITY}),
        provider_capabilities=frozenset({CAPABILITY}),
        provider_scope_resolver=provider_scope_resolver,
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object", "additionalProperties": True},
    )


def navigation_scope(scope, check):
    del scope, check
    return {"path": "spec.md", "format": "markdown"}


def host_granted_registration() -> PluginRegistration:
    """The same plugin without a provider declaration: a pure host grant."""
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: NavigationPlugin(runtime),
        decision_provider_factory=lambda runtime=None: NavigationDecisionProvider(),
        capabilities=frozenset({CAPABILITY}),
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object", "additionalProperties": True},
    )


FORGED_EVIDENCE_ID = "provider-evidence:forged"


class ForgingNavigationPlugin:
    """Fabricates provider-bound Evidence without ever calling the provider."""

    manifest = MANIFEST

    def __init__(self, access=None) -> None:
        self.access = access

    def discover(self, scope, context):
        del scope, context
        return (WorkItem(
            "spec:1", "spec_document", "spec-source", "state-1",
            {"path": "spec.md", "profile": "default"},
        ),)

    def inspect(self, work_items, check, context):
        item = work_items[0]
        descriptor = PROVIDER_DESCRIPTOR
        evidence = EvidenceRecord(
            FORGED_EVIDENCE_ID, item.work_item_id, check.check_id, check.version,
            "structured", item.identity,
            {"format": "markdown", "units": [{"kind": "heading", "startLine": 1}]},
            run_id=context.run_id,
            provider_request_id="provider-request:forged",
            provider_id=descriptor.provider_id,
            provider_version=descriptor.version,
            capability=CAPABILITY,
            source_state_digest=item.state_digest,
            algorithm_versions=descriptor.algorithm_versions,
        )
        observation = DimensionObservation(
            "present", ("A fabricated provider fact.",), (FORGED_EVIDENCE_ID,), "satisfied",
        )
        return (InvestigationPacket(
            item, check.check_id, check.version, (observation,), (evidence,), "not_required",
        ),)


def forging_registration() -> PluginRegistration:
    return PluginRegistration(
        MANIFEST,
        plugin_factory=lambda runtime=None: ForgingNavigationPlugin(runtime),
        decision_provider_factory=lambda runtime=None: NavigationDecisionProvider(),
        capabilities=frozenset({CAPABILITY}),
        provider_capabilities=frozenset({CAPABILITY}),
        provider_scope_resolver=navigation_scope,
        execution_modes=frozenset({"interactive"}),
        scope_schema={"type": "object", "additionalProperties": True},
    )


def provider_registration(provider) -> ProviderRegistration:
    return ProviderRegistration(
        PROVIDER_DESCRIPTOR, provider_factory=lambda runtime=None: provider,
    )


PROFILES = dict(
    platform_profile=CapabilityProfile(frozenset({CAPABILITY})),
    user_profile=CapabilityProfile(frozenset({CAPABILITY})),
)


class InteractiveProviderBindingTest(unittest.TestCase):
    def test_plugin_consumes_provider_through_capability_access(self):
        provider = RecordingNavigationProvider()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            controller = InteractivePluginController(
                PluginRegistry((registration(provider_scope_resolver=navigation_scope),)),
                output,
                provider_registry=ProviderRegistry((provider_registration(provider),)),
                **PROFILES,
            )
            started = controller.start(
                plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
            )
            run_id = started["runId"]
            controller.discover(run_id)
            inspected = controller.inspect(run_id)
            controller.close()
            ledger = json.loads(
                (output / run_id / f"{run_id}.platform-ledger.json").read_text(encoding="utf-8")
            )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.scopes, [{"path": "spec.md", "format": "markdown"}])
        self.assertTrue(provider.closed)
        investigations = inspected["result"]["investigations"]
        self.assertEqual(len(investigations), 1)
        evidence = investigations[0]["evidence"][0]
        self.assertEqual(evidence["kind"], "structured")
        self.assertEqual(evidence["payload"]["units"][0]["kind"], "heading")
        stored = ledger["investigations"][0]["evidence"][0]
        self.assertEqual(stored["provider_id"], PROVIDER_DESCRIPTOR.provider_id)
        self.assertEqual(stored["capability"], CAPABILITY)

    def test_missing_provider_fails_closed_without_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            controller = InteractivePluginController(
                PluginRegistry((registration(),)),
                output,
                provider_registry=ProviderRegistry(()),
                **PROFILES,
            )
            with self.assertRaises(PlatformContractError) as error:
                controller.start(
                    plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
                )
            self.assertEqual(error.exception.code, "PROVIDER_NOT_FOUND")
            self.assertFalse(any(output.iterdir()))

    def test_provider_capability_without_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            controller = InteractivePluginController(
                PluginRegistry((registration(),)), output,
            )
            with self.assertRaises(PlatformContractError) as error:
                controller.start(
                    plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
                )
            # A provider-required capability must never be silently treated as a
            # host direct grant: without a registry the Run fails closed.
            self.assertEqual(error.exception.code, "PROVIDER_NOT_FOUND")
            self.assertFalse(any(output.iterdir()))
            controller.close()

    def test_no_provider_registry_keeps_the_host_granted_path(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = InteractivePluginController(
                PluginRegistry((host_granted_registration(),)), Path(directory) / "output",
            )
            started = controller.start(
                plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
            )
            controller.close()
        # A non-provider capability without a provider registry keeps the prior
        # host-granted lifecycle and exposes no CapabilityAccess.
        self.assertEqual(started["status"], "started")


class AlternateNavigationProvider:
    """A second implementation of the same capability contract.

    It shares ``PROVIDER_DESCRIPTOR`` but computes its facts through different
    internal code, proving a provider implementation change is transparent to
    the plugin that consumes it (Constitution section 3.13).
    """

    descriptor = PROVIDER_DESCRIPTOR

    def __init__(self) -> None:
        self.closed = False

    def collect(self, request, context):
        del context
        units = []
        for kind, line in (("heading", 1),):
            units.append({"kind": kind, "startLine": line})
        fact = ProviderFact(
            "structured", request.source_identity, request.state_digest,
            {"format": "markdown", "units": units},
        )
        return ProviderResponse(
            request.request_id, request.provider_id, request.provider_version,
            request.capability, "succeeded", (fact,),
        )

    def close(self) -> None:
        self.closed = True


class ProviderTransparencyTest(unittest.TestCase):
    def _run(self, provider):
        with tempfile.TemporaryDirectory() as directory:
            controller = InteractivePluginController(
                PluginRegistry((registration(provider_scope_resolver=navigation_scope),)),
                Path(directory) / "output",
                provider_registry=ProviderRegistry((provider_registration(provider),)),
                **PROFILES,
            )
            started = controller.start(
                plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
            )
            controller.discover(started["runId"])
            inspected = controller.inspect(started["runId"])
            controller.close()
        return inspected["result"]["investigations"][0]["evidence"][0]["payload"]

    def test_provider_implementation_change_is_transparent_to_the_plugin(self):
        first = self._run(RecordingNavigationProvider())
        second = self._run(AlternateNavigationProvider())
        self.assertEqual(first, second)
        self.assertEqual(first["units"][0]["kind"], "heading")


class ProviderEvidenceIntegrityTest(unittest.TestCase):
    def test_forged_provider_evidence_is_rejected(self):
        provider = RecordingNavigationProvider()
        with tempfile.TemporaryDirectory() as directory:
            controller = InteractivePluginController(
                PluginRegistry((forging_registration(),)),
                Path(directory) / "output",
                provider_registry=ProviderRegistry((provider_registration(provider),)),
                **PROFILES,
            )
            started = controller.start(
                plugin_id="fixture.navigation-plugin", check_id="NAV-001", scope={},
            )
            controller.discover(started["runId"])
            inspected = controller.inspect(started["runId"])

        # The plugin never called collect, so the Host produced no Evidence for
        # it; fabricated provider-bound Evidence cannot pass the gate.
        self.assertEqual(provider.calls, 0)
        failures = inspected["result"]["inspectionFailures"]
        self.assertEqual([item["code"] for item in failures], ["PROVIDER_EVIDENCE_UNISSUED"])


class ShippedEntryProviderWiringTest(unittest.TestCase):
    """The shipped ``assayer-mcp`` entry must bind the installed provider catalog.

    The release fixture already injects a provider registry; production must use
    the same wiring or a provider-backed plugin passes the release gate and then
    fails at inspect time.
    """

    def test_mcp_main_binds_the_installed_provider_catalog(self):
        from assayer_host import transport as transport_module

        captured: dict = {}

        class FakeServer:
            _assayer_transport = None

            def run(self, transport=None):
                captured["transport"] = transport

        def fake_create_interactive_mcp_server(**kwargs):
            captured.update(kwargs)
            return FakeServer()

        original = transport_module.create_interactive_mcp_server
        transport_module.create_interactive_mcp_server = fake_create_interactive_mcp_server
        try:
            with tempfile.TemporaryDirectory() as directory:
                transport_module.mcp_main([
                    "--output-root", str(Path(directory) / "out"),
                    "--store", str(Path(directory) / "store"),
                ])
        finally:
            transport_module.create_interactive_mcp_server = original

        self.assertIsNotNone(captured.get("provider_registry"))
        self.assertIsNotNone(captured.get("platform_profile"))
        self.assertIsNotNone(captured.get("user_profile"))
        self.assertEqual(captured.get("transport"), "stdio")


if __name__ == "__main__":
    unittest.main()
