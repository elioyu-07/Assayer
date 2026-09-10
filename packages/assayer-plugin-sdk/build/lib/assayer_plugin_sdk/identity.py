"""Stable source identity and state digests for platform artifacts.

These helpers produce deterministic, byte-level digests for source documents
and multi-document scopes.  They carry no domain meaning and are reused by any
plugin that must persist or compare source state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def digest_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of a byte sequence."""
    return hashlib.sha256(data).hexdigest()


def document_state_digest(documents: Sequence[Mapping[str, Any]]) -> str:
    """Return a stable digest for an ordered set of source documents.

    The projection hashes only stable identity fields (document id, role,
    resolved path, and per-document source digest), sorted by document id, so
    two equivalent scopes yield the same digest regardless of input order.
    """
    projection = sorted((
        {
            "document_id": str(document["document_id"]),
            "role": str(document["role"]),
            "path": str(document["path"]),
            "source_digest": str(document["source_digest"]),
        }
        for document in documents
    ), key=lambda document: document["document_id"])
    return digest_bytes(json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


__all__ = ["digest_bytes", "document_state_digest"]
