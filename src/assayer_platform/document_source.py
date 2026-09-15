"""Host-owned frozen document source boundary for ordinary Simple plugins."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .contract import (
    CheckContract,
    DimensionObservation,
    EvidenceRecord,
    InvestigationPacket,
    PlatformContractError,
    WorkItem,
)


@dataclass(frozen=True)
class DocumentSnapshot:
    """One immutable, decoded source state owned by the Host."""

    text: str
    closed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.closed, bool):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT",
                "DocumentSnapshot requires decoded text and an explicit closure state",
            )

@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    start_line: int
    end_line: int
    text: str
    heading_path: tuple[str, ...] = ()

    def indexed(self) -> dict[str, Any]:
        return {
            "source_chunk_id": self.chunk_id,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "headingPath": list(self.heading_path),
        }


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SNAPSHOT_ID = re.compile(r"^document-snapshot:([0-9a-f]{64})$")
_CHUNK_LIMIT = 2400


def _chunks(text: str, source_digest: str) -> tuple[DocumentChunk, ...]:
    lines = text.splitlines()
    if not lines:
        return (DocumentChunk(
            f"document-chunk:{source_digest[:16]}:1", 1, 1, "", (),
        ),)
    result: list[DocumentChunk] = []
    heading_path: list[str] = []
    pending: list[tuple[int, str]] = []
    pending_chars = 0

    def flush() -> None:
        nonlocal pending, pending_chars
        if not pending:
            return
        result.append(DocumentChunk(
            f"document-chunk:{source_digest[:16]}:{len(result) + 1}",
            pending[0][0], pending[-1][0],
            "\n".join(item for _line, item in pending),
            tuple(heading_path),
        ))
        pending = []
        pending_chars = 0

    for line_number, line in enumerate(lines, 1):
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            heading_path = heading_path[:level - 1]
            heading_path.append(heading.group(2).strip())
        parts = [line[index:index + _CHUNK_LIMIT] for index in range(0, len(line), _CHUNK_LIMIT)] or [""]
        for part in parts:
            added = len(part) + (1 if pending else 0)
            if pending and pending_chars + added > _CHUNK_LIMIT:
                flush()
            pending.append((line_number, part))
            pending_chars += len(part) + (1 if len(pending) > 1 else 0)
            if pending_chars >= _CHUNK_LIMIT:
                flush()
    flush()
    return tuple(result)


class DocumentSnapshotStore:
    """Content-addressed side store; ledgers retain only the bounded index."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve() / "document-snapshots"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, snapshot_id: str) -> Path:
        match = _SNAPSHOT_ID.fullmatch(snapshot_id)
        if match is None:
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Document snapshot identity is malformed",
            )
        return self.root / f"{match.group(1)}.json"

    def save(self, snapshot: DocumentSnapshot, *, state_digest: str) -> tuple[str, tuple[DocumentChunk, ...]]:
        raw = snapshot.text.encode("utf-8")
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != state_digest:
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Document snapshot does not match its frozen state",
            )
        snapshot_id = f"document-snapshot:{actual_digest}"
        chunks = _chunks(snapshot.text, actual_digest)
        value = {
            "schemaVersion": "1.0.0",
            "snapshotId": snapshot_id,
            "stateDigest": actual_digest,
            "closed": snapshot.closed,
            "text": snapshot.text,
            "chunks": [item.indexed() for item in chunks],
        }
        destination = self._path(snapshot_id)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) + "\n"
        if destination.exists():
            try:
                existing = destination.read_text(encoding="utf-8")
            except OSError as error:
                raise PlatformContractError(
                    "DOCUMENT_SNAPSHOT_UNAVAILABLE",
                    "The existing frozen document snapshot could not be read",
                ) from error
            if existing != encoded:
                raise PlatformContractError(
                    "DOCUMENT_SNAPSHOT_CONFLICT",
                    "Content-addressed document snapshot storage is inconsistent",
                )
        else:
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.tmp"
            )
            try:
                temporary.write_text(encoded, encoding="utf-8")
                temporary.replace(destination)
            except OSError as error:
                temporary.unlink(missing_ok=True)
                raise PlatformContractError(
                    "DOCUMENT_SNAPSHOT_PERSIST_FAILED",
                    "The frozen document snapshot could not be persisted",
                ) from error
        return snapshot_id, chunks

    def load(self, snapshot_id: str) -> tuple[DocumentSnapshot, tuple[DocumentChunk, ...]]:
        source = self._path(snapshot_id)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PlatformContractError(
                "DOCUMENT_SNAPSHOT_UNAVAILABLE",
                "The frozen document snapshot is missing or unreadable",
            ) from error
        if not isinstance(value, Mapping) or set(value) != {
            "schemaVersion", "snapshotId", "stateDigest", "closed", "text", "chunks",
        } or value.get("schemaVersion") != "1.0.0" or value.get("snapshotId") != snapshot_id:
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Stored document snapshot shape is invalid",
            )
        snapshot = DocumentSnapshot(value["text"], value["closed"])
        digest = hashlib.sha256(snapshot.text.encode("utf-8")).hexdigest()
        if digest != value["stateDigest"] or snapshot_id != f"document-snapshot:{digest}":
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Stored document snapshot digest is invalid",
            )
        expected_chunks = _chunks(snapshot.text, digest)
        if value["chunks"] != [item.indexed() for item in expected_chunks]:
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Stored document chunk index is invalid",
            )
        return snapshot, expected_chunks

    @staticmethod
    def packet_index(
        chunks: Sequence[DocumentChunk], *, work_item_id: str,
    ) -> list[dict[str, Any]]:
        """Namespace content chunks to one WorkItem Evidence lineage."""
        token = hashlib.sha256(work_item_id.encode("utf-8")).hexdigest()[:16]
        return [{
            **item.indexed(),
            "source_chunk_id": f"document-chunk:{token}:{index}",
        } for index, item in enumerate(chunks, 1)]


def _input_entries(scope: object) -> tuple[object, ...]:
    if not isinstance(scope, Mapping):
        raise PlatformContractError(
            "INVALID_DOCUMENT_SCOPE",
            "Simple document scope must be an object containing a files array",
        )
    entries = scope.get("files")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or not entries:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SCOPE",
            "Simple document scope requires a nonempty files array",
        )
    return tuple(entries)


def _entry_path(value: object) -> Path:
    if isinstance(value, (str, Path)):
        raw_path: object = value
    elif isinstance(value, Mapping) and set(value) == {"path"}:
        raw_path = value["path"]
    else:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SCOPE",
            "Every Simple document input must be a path or an object containing only path",
        )
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise PlatformContractError(
            "INVALID_DOCUMENT_SCOPE", "Simple document path must be nonempty",
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise PlatformContractError(
            "DOCUMENT_SOURCE_UNAVAILABLE", "The requested document source is not a file",
        )
    return path


def _read(path: Path) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PlatformContractError(
            "DOCUMENT_SOURCE_UNAVAILABLE", "The document source could not be read",
        ) from error
    return raw, hashlib.sha256(raw).hexdigest()


class HostDocumentSource:
    """Discover and freeze local documents without plugin filesystem access."""

    def __init__(self, snapshot_store: DocumentSnapshotStore):
        if not isinstance(snapshot_store, DocumentSnapshotStore):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SNAPSHOT", "Host document source requires a snapshot store",
            )
        self.snapshot_store = snapshot_store

    def discover(
        self, scope: object, *, input_kind: str, check: CheckContract,
    ) -> tuple[WorkItem, ...]:
        if not isinstance(input_kind, str) or not input_kind.strip():
            raise PlatformContractError(
                "INVALID_SIMPLE_PLUGIN", "Simple plugin input kind is required",
            )
        if input_kind.strip() not in {"document", "markdown"}:
            raise PlatformContractError(
                "SIMPLE_INPUT_UNSUPPORTED",
                "The built-in document source supports document and markdown inputs",
            )
        if len(check.subject_kinds) != 1:
            raise PlatformContractError(
                "INVALID_SIMPLE_PLUGIN",
                "A Simple document Check must declare exactly one subject kind",
            )
        paths = tuple(_entry_path(item) for item in _input_entries(scope))
        identities = tuple(str(path) for path in paths)
        if len(identities) != len(set(identities)):
            raise PlatformContractError(
                "INVALID_DOCUMENT_SCOPE", "Simple document inputs must be unique",
            )
        work_items: list[WorkItem] = []
        for path in paths:
            raw, state_digest = _read(path)
            identity = str(path)
            identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            # Freeze a valid source as soon as the Host discovers it.  The
            # WorkItem keeps only a bounded index; the decoded content lives
            # in the content-addressed snapshot store.  Invalid UTF-8 is
            # intentionally deferred to inspection so older callers retain
            # the stable DOCUMENT_ENCODING_UNSUPPORTED boundary error.
            metadata: dict[str, Any] = {"inputKind": input_kind.strip()}
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                metadata["sourceEncoding"] = "invalid_utf8"
            else:
                snapshot_id, chunks = self.snapshot_store.save(
                    DocumentSnapshot(text), state_digest=state_digest,
                )
                metadata.update({
                    "snapshotId": snapshot_id,
                    "snapshotClosed": True,
                    "snapshotLineCount": len(text.splitlines()),
                    "snapshotChunkCount": len(chunks),
                })
            work_items.append(WorkItem(
                f"document:{identity_digest[:24]}",
                check.subject_kinds[0],
                identity,
                state_digest,
                metadata,
            ))
        return tuple(work_items)

    def inspect(
        self, work_items: Sequence[WorkItem], check: CheckContract, *, run_id: str,
    ) -> tuple[InvestigationPacket, ...]:
        packets: list[InvestigationPacket] = []
        for work_item in work_items:
            path = Path(work_item.identity)
            # Reading the source here is only an invalidation check.  The
            # actual bytes used by the plugin come from the snapshot frozen at
            # discovery, never from a reopened path.
            _raw, state_digest = _read(path)
            if state_digest != work_item.state_digest:
                raise PlatformContractError(
                    "DOCUMENT_SOURCE_CHANGED",
                    "The document changed after discovery and cannot be reviewed in this Run",
                    work_item_id=work_item.work_item_id,
                )
            metadata = work_item.metadata
            snapshot_id = metadata.get("snapshotId")
            if not isinstance(snapshot_id, str):
                # Preserve the old error boundary for a source that was not
                # decodable during discovery.  Legacy WorkItems without a
                # snapshot index are also supported for one compatibility
                # window and are frozen here before packet construction.
                try:
                    text = _raw.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise PlatformContractError(
                        "DOCUMENT_ENCODING_UNSUPPORTED",
                        "Simple document input must be valid UTF-8",
                        work_item_id=work_item.work_item_id,
                    ) from error
                snapshot = DocumentSnapshot(text)
                snapshot_id, chunks = self.snapshot_store.save(
                    snapshot, state_digest=state_digest,
                )
            else:
                snapshot, chunks = self.snapshot_store.load(snapshot_id)
                text = snapshot.text
                expected_metadata = {
                    "snapshotClosed": snapshot.closed,
                    "snapshotLineCount": len(text.splitlines()),
                    "snapshotChunkCount": len(chunks),
                }
                if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                    raise PlatformContractError(
                        "INVALID_DOCUMENT_SNAPSHOT",
                        "WorkItem snapshot metadata differs from its frozen content",
                        work_item_id=work_item.work_item_id,
                    )
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != state_digest:
                    raise PlatformContractError(
                        "INVALID_DOCUMENT_SNAPSHOT",
                        "Frozen document snapshot does not match the WorkItem state",
                        work_item_id=work_item.work_item_id,
                    )
            snapshot_payload = {
                "snapshotId": snapshot_id,
                "closed": snapshot.closed,
                "lineCount": len(text.splitlines()),
                "chunkCount": len(chunks),
            }
            evidence_kinds = check.required_evidence_kinds or ("document_snapshot",)
            evidence_records: list[EvidenceRecord] = []
            for index, evidence_kind in enumerate(evidence_kinds):
                evidence_id = (
                    "document-evidence:"
                    + hashlib.sha256(
                        f"{run_id}\x1f{work_item.work_item_id}\x1f{state_digest}\x1f{evidence_kind}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:32]
                )
                evidence_records.append(EvidenceRecord(
                    evidence_id,
                    work_item.work_item_id,
                    check.check_id,
                    check.version,
                    evidence_kind,
                    work_item.identity,
                    (
                        {
                            "documentSnapshot": snapshot_payload,
                            "sourceChunks": self.snapshot_store.packet_index(
                                chunks, work_item_id=work_item.work_item_id,
                            ),
                        }
                        if index == 0 else {"sourceState": "frozen"}
                    ),
                ))
            coverage_evidence = EvidenceRecord(
                "document-coverage-evidence:" + hashlib.sha256(
                    f"{run_id}\x1f{work_item.work_item_id}\x1f{state_digest}\x1fcoverage".encode(
                        "utf-8"
                    )
                ).hexdigest()[:32],
                work_item.work_item_id,
                check.check_id,
                check.version,
                "document_coverage",
                work_item.identity,
                {"documentCoverage": {"snapshotId": snapshot_id, "closed": snapshot.closed}},
            )
            evidence_refs = tuple(item.evidence_id for item in evidence_records)
            dimensions = tuple(
                DimensionObservation(
                    name,
                    ("Review this declared dimension against the frozen document.",),
                    evidence_refs,
                    "unresolved",
                )
                for name in check.dimensions
            )
            packets.append(InvestigationPacket(
                work_item,
                check.check_id,
                check.version,
                dimensions,
                (*evidence_records, coverage_evidence),
                "not_required",
            ))
        return tuple(packets)


__all__ = ["DocumentSnapshot", "DocumentSnapshotStore", "HostDocumentSource"]
