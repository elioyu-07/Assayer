"""Contract tests for the SDK browser snapshot boundary."""

from __future__ import annotations

import unittest

from assayer_plugin_sdk import (
    BrowserSnapshot,
    BrowserSnapshotSource,
    PlatformContractError,
    ProviderSourceSnapshot,
)


class BrowserSnapshotTest(unittest.TestCase):
    def test_snapshot_freezes_records_and_derives_stable_digest(self):
        first = BrowserSnapshot(
            visible_text="Orders",
            candidates=[{"kind": "filter", "metadata": {"label": "Status"}}],
            network_summary={"pendingReadRequests": 0},
        )
        second = BrowserSnapshot(
            visible_text="Orders",
            candidates=[{"kind": "filter", "metadata": {"label": "Status"}}],
            network_summary={"pendingReadRequests": 0},
        )

        self.assertEqual(first.state_digest, second.state_digest)
        self.assertEqual(len(first.state_digest), 64)
        with self.assertRaises(TypeError):
            first.candidates[0]["kind"] = "button"
        with self.assertRaises(TypeError):
            first.candidates[0]["metadata"]["label"] = "Other"

    def test_snapshot_rejects_invalid_shape_or_digest(self):
        with self.assertRaises(PlatformContractError) as invalid_records:
            BrowserSnapshot(visible_text="", candidates=["not-an-object"])
        self.assertEqual(invalid_records.exception.code, "INVALID_BROWSER_SNAPSHOT")

        with self.assertRaises(PlatformContractError) as invalid_digest:
            BrowserSnapshot(visible_text="", state_digest="z" * 64)
        self.assertEqual(invalid_digest.exception.code, "INVALID_BROWSER_SNAPSHOT")

    def test_snapshot_source_protocol_is_structural(self):
        class Source:
            def observe_snapshot(self):
                return BrowserSnapshot(visible_text="ok")

        self.assertIsInstance(Source(), BrowserSnapshotSource)

    def test_provider_source_snapshot_is_immutable_and_typed(self):
        value = ProviderSourceSnapshot("https://example.test", "state-1", {"title": "Orders"})

        self.assertEqual(value.source_identity, "https://example.test")
        with self.assertRaises(TypeError):
            value.metadata["title"] = "Other"
        with self.assertRaises(PlatformContractError):
            ProviderSourceSnapshot("", "state-1")
        with self.assertRaises(PlatformContractError):
            ProviderSourceSnapshot("source", "state-1", metadata=[])


if __name__ == "__main__":
    unittest.main()
