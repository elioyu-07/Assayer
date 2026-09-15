"""Tests for the SDK-only browser snapshot provider adapter."""

from __future__ import annotations

import unittest

from assayer_browser_provider import (
    BrowserSnapshotProvider,
    browser_registration,
)
from assayer_platform.provider_conformance import inspect_provider_registration
from assayer_platform import (
    BoundCapabilityProvider,
    CapabilityNegotiator,
    CapabilityProfile,
    CheckContract,
)
from assayer_plugin_sdk import BrowserSnapshot, ProviderRequest, WorkItem


def _request(*, state_digest: str = "work-item-state") -> ProviderRequest:
    return ProviderRequest(
        request_id="request-1",
        idempotency_key="request-1",
        run_id="run-1",
        work_item_id="object-1",
        check_id="FUA-10",
        check_version="1.1.0",
        provider_id="assayer.browser-snapshot",
        provider_version="0.1.0",
        capability="browser_snapshot",
        source_identity="https://example.test/orders",
        state_digest=state_digest,
        scope={},
        limits={"maxItems": 1},
    )


class _Source:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def observe_snapshot(self):
        self.calls += 1
        return self.snapshot


class BrowserProviderTest(unittest.TestCase):
    def test_registration_passes_provider_conformance(self):
        report = inspect_provider_registration(
            browser_registration(), construct_implementation=True,
        )
        self.assertTrue(report.passed, report.as_dict())

    def test_provider_serializes_host_snapshot_as_one_fact(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders",
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
            route="/orders",
            dom_digest="a" * 64,
            candidates=[{"kind": "filter_region"}],
        )
        response = BrowserSnapshotProvider(_Source(snapshot)).collect(
            _request(state_digest=snapshot.state_digest), None,
        )

        self.assertEqual(response.status, "succeeded")
        self.assertIsNone(response.failure)
        self.assertEqual(len(response.facts), 1)
        self.assertEqual(response.facts[0].kind, "browser_snapshot")
        self.assertEqual(response.facts[0].payload["stateDigest"], snapshot.state_digest)
        self.assertEqual(response.facts[0].payload["domDigest"], "a" * 64)

    def test_provider_rejects_a_stale_snapshot(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders",
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
            route="/orders",
        )
        response = BrowserSnapshotProvider(_Source(snapshot)).collect(_request(), None)

        self.assertEqual(response.status, "failed")
        self.assertIsNotNone(response.failure)
        self.assertEqual(response.failure.code, "stale_state")

    def test_provider_rejects_a_page_identity_change(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders",
            url="https://example.test/other",
            origin="https://example.test",
            title="Orders",
            route="/other",
        )
        response = BrowserSnapshotProvider(_Source(snapshot)).collect(_request(), None)

        self.assertEqual(response.status, "failed")
        self.assertIsNotNone(response.failure)
        self.assertEqual(response.failure.code, "source_changed")

    def test_host_binding_injects_snapshot_source_and_issues_bound_evidence(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders",
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
            route="/orders",
            dom_digest="a" * 64,
        )
        registration = browser_registration()
        negotiation = CapabilityNegotiator().negotiate(
            registration,
            ("browser_snapshot",),
            CapabilityProfile(frozenset({"browser_snapshot"})),
            user_profile=CapabilityProfile(frozenset({"browser_snapshot"})),
            scope={},
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiation,
            run_id="run-browser",
            scope={},
            runtime=_Source(snapshot),
        )
        check = CheckContract(
            "FUA-10", "1.1.0", ("frontend_object",), ("page",),
            ("needs_review",), ("browser_snapshot",), ("browser_snapshot",),
            "needs_review", (),
        )
        item = WorkItem(
            "object-1", "frontend_object", snapshot.url, snapshot.state_digest,
        )

        result = bound.collect(item, check, "browser_snapshot")

        self.assertIsNone(result.failure)
        self.assertEqual(len(result.evidence), 1)
        evidence = result.evidence[0]
        self.assertEqual(evidence.kind, "browser_snapshot")
        self.assertEqual(evidence.source_identity, snapshot.url)
        self.assertEqual(evidence.source_state_digest, snapshot.state_digest)
        bound.close()

    def test_host_source_discovery_freezes_browser_snapshot_for_collect(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders",
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
            route="/orders",
            dom_digest="a" * 64,
        )
        source = _Source(snapshot)
        registration = browser_registration()
        negotiation = CapabilityNegotiator().negotiate(
            registration,
            ("browser_snapshot",),
            CapabilityProfile(frozenset({"browser_snapshot"})),
            user_profile=CapabilityProfile(frozenset({"browser_snapshot"})),
            scope={},
        )
        bound = BoundCapabilityProvider(
            registration, negotiation, run_id="run-browser-freeze", scope={}, runtime=source,
        )
        check = CheckContract(
            "FUA-10", "1.1.0", ("frontend_object",), ("page",),
            ("needs_review",), ("browser_snapshot",), ("browser_snapshot",),
            "needs_review", (),
        )

        items = bound.discover_work_items(check, "browser_snapshot")
        result = bound.collect(items[0], check, "browser_snapshot")

        self.assertEqual(source.calls, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].identity, snapshot.url)
        self.assertEqual(items[0].state_digest, snapshot.state_digest)
        self.assertIsNone(result.failure)
        bound.close()

    def test_provider_fails_closed_without_host_source(self):
        response = BrowserSnapshotProvider().collect(_request(), None)

        self.assertEqual(response.status, "failed")
        self.assertIsNotNone(response.failure)
        self.assertEqual(response.failure.code, "capability_unavailable")

    def test_provider_classifies_invalid_host_snapshot(self):
        response = BrowserSnapshotProvider(_Source(object())).collect(_request(), None)

        self.assertEqual(response.status, "failed")
        self.assertIsNotNone(response.failure)
        self.assertEqual(response.failure.code, "source_error")


if __name__ == "__main__":
    unittest.main()
