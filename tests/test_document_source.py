"""Tests for the Host-owned Simple document source boundary."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assayer_platform.contract import PlatformContractError
from assayer_platform.document_source import (
    DocumentSnapshot, DocumentSnapshotStore, HostDocumentSource,
)
from tests.helpers import ConfigQualityPlugin


class HostDocumentSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "source.md"
        self.path.write_text("# Frozen\n\npermission", encoding="utf-8")
        self.check = ConfigQualityPlugin.manifest.checks[0]
        self.store = DocumentSnapshotStore(Path(self.temporary.name) / "run")
        self.source = HostDocumentSource(self.store)

    def test_host_discovers_and_freezes_the_exact_utf8_source(self):
        items = self.source.discover(
            {"files": [{"path": str(self.path)}]},
            input_kind="markdown",
            check=self.check,
        )

        self.assertIn("snapshotId", items[0].metadata)
        self.assertEqual(items[0].metadata["snapshotClosed"], True)
        self.assertEqual(items[0].metadata["snapshotLineCount"], 3)

        packets = self.source.inspect(items, self.check, run_id="run:document-source")

        self.assertEqual(len(items), 1)
        self.assertNotIn("path", items[0].metadata)
        snapshot_index = packets[0].evidence[0].payload["documentSnapshot"]
        self.assertEqual(snapshot_index["closed"], True)
        self.assertEqual(snapshot_index["lineCount"], 3)
        self.assertNotIn("# Frozen", str(packets[0].evidence[0].payload))
        snapshot, chunks = self.store.load(snapshot_index["snapshotId"])
        self.assertEqual(snapshot.text, "# Frozen\n\npermission")
        self.assertEqual(snapshot_index["chunkCount"], len(chunks))
        self.assertEqual(
            {item.name for item in packets[0].dimensions}, set(self.check.dimensions),
        )
        self.assertTrue(all(
            dimension.evidence_refs == (packets[0].evidence[0].evidence_id,)
            for dimension in packets[0].dimensions
        ))

    def test_inspection_reuses_the_discovery_snapshot(self):
        items = self.source.discover(
            {"files": [str(self.path)]}, input_kind="markdown", check=self.check,
        )
        snapshot_id = items[0].metadata["snapshotId"]
        # Replacing the source with identical bytes keeps the state digest
        # stable, while proving inspection resolves the already persisted
        # snapshot rather than depending on a second decode.
        self.path.write_bytes(self.path.read_bytes())

        packets = self.source.inspect(items, self.check, run_id="run:reuse")

        self.assertEqual(
            packets[0].evidence[0].payload["documentSnapshot"]["snapshotId"],
            snapshot_id,
        )

    def test_invalid_utf8_fails_without_exposing_decoder_details(self):
        self.path.write_bytes(b"\xff\xfe")
        items = self.source.discover(
            {"files": [str(self.path)]}, input_kind="document", check=self.check,
        )

        with self.assertRaises(PlatformContractError) as rejected:
            self.source.inspect(items, self.check, run_id="run:invalid-encoding")

        self.assertEqual(rejected.exception.code, "DOCUMENT_ENCODING_UNSUPPORTED")

    def test_identical_documents_keep_distinct_work_item_chunk_lineage(self):
        second = Path(self.temporary.name) / "second.md"
        second.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        items = self.source.discover(
            {"files": [str(self.path), str(second)]},
            input_kind="markdown",
            check=self.check,
        )

        packets = self.source.inspect(items, self.check, run_id="run:two-documents")

        first_chunks = packets[0].evidence[0].payload["sourceChunks"]
        second_chunks = packets[1].evidence[0].payload["sourceChunks"]
        self.assertNotEqual(
            first_chunks[0]["source_chunk_id"], second_chunks[0]["source_chunk_id"],
        )
        self.assertEqual(
            packets[0].evidence[0].payload["documentSnapshot"]["snapshotId"],
            packets[1].evidence[0].payload["documentSnapshot"]["snapshotId"],
        )

    def test_scope_shape_and_input_kind_are_fail_closed(self):
        invalid_calls = (
            lambda: self.source.discover({}, input_kind="markdown", check=self.check),
            lambda: self.source.discover(
                {"files": [{"path": str(self.path), "extra": True}]},
                input_kind="markdown", check=self.check,
            ),
            lambda: self.source.discover(
                {"files": [str(self.path)]}, input_kind="browser", check=self.check,
            ),
        )
        expected = (
            "INVALID_DOCUMENT_SCOPE", "INVALID_DOCUMENT_SCOPE", "SIMPLE_INPUT_UNSUPPORTED",
        )
        for call, code in zip(invalid_calls, expected):
            with self.subTest(code=code), self.assertRaises(PlatformContractError) as rejected:
                call()
            self.assertEqual(rejected.exception.code, code)

    def test_snapshot_shape_is_typed(self):
        with self.assertRaises(PlatformContractError) as rejected:
            DocumentSnapshot("text", closed="yes")  # type: ignore[arg-type]
        self.assertEqual(rejected.exception.code, "INVALID_DOCUMENT_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()
