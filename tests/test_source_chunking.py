"""Contract tests for platform source chunking and identity digests."""

from __future__ import annotations

import unittest

from assayer_platform import (
    build_source_chunks,
    digest_bytes,
    document_state_digest,
    source_ref_for_line,
)


TEXT = """# Product Spec

## Module Definition
The module provides an example capability.

## State Model
The example has draft and active states.
"""


class SourceChunkingTest(unittest.TestCase):
    def test_chunking_is_deterministic_and_stable(self):
        chunks = build_source_chunks(TEXT, path="spec.md", source_digest="a" * 64)
        again = build_source_chunks(TEXT, path="spec.md", source_digest="a" * 64)
        self.assertEqual(
            [item["source_chunk_id"] for item in chunks],
            [item["source_chunk_id"] for item in again],
        )
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["heading_path"], ["Product Spec"])
        self.assertTrue(all(item["source_digest"] == "a" * 64 for item in chunks))
        self.assertTrue(all(item["start_line"] <= item["end_line"] for item in chunks))

    def test_source_ref_for_line_returns_narrowest_chunk(self):
        chunks = build_source_chunks(TEXT, path="spec.md", source_digest="b" * 64)
        ref = source_ref_for_line(chunks, 1)
        self.assertIsNotNone(ref)
        self.assertEqual(ref["start_line"], 1)
        self.assertEqual(ref["end_line"], 1)
        self.assertEqual(ref["source_digest"], "b" * 64)
        self.assertIsNone(source_ref_for_line(chunks, 10_000))


class IdentityTest(unittest.TestCase):
    def test_digest_bytes_is_stable_sha256(self):
        self.assertEqual(digest_bytes(b"abc"), digest_bytes(b"abc"))
        self.assertEqual(len(digest_bytes(b"abc")), 64)

    def test_document_state_digest_is_order_independent(self):
        documents = [
            {"document_id": "a", "role": "anchor", "path": "/a", "source_digest": "1" * 64},
            {"document_id": "b", "role": "related", "path": "/b", "source_digest": "2" * 64},
        ]
        self.assertEqual(
            document_state_digest(documents),
            document_state_digest(list(reversed(documents))),
        )


if __name__ == "__main__":
    unittest.main()
