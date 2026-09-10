"""Host-owned Evidence reference resolution for domain results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contract import InvestigationPacket, PlatformContractError


def _pointer(path: Sequence[Any]) -> str:
    if not path:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _collect_nested_ids(value: Any, allowed: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"source_chunk_id", "sourceChunkId", "evidence_id", "evidenceId"}:
                if isinstance(item, str) and item:
                    allowed.add(item)
            _collect_nested_ids(item, allowed)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _collect_nested_ids(item, allowed)


def host_evidence_references(packet: InvestigationPacket) -> frozenset[str]:
    """Return all stable references issued by the Host for one packet."""
    allowed = {item.evidence_id for item in packet.evidence}
    for evidence in packet.evidence:
        _collect_nested_ids(evidence.payload, allowed)
    return frozenset(allowed)


def resolve_evidence_references(
    packet: InvestigationPacket, references: Sequence[str], *,
    error_code: str = "DOMAIN_EVIDENCE_REFERENCE_INVALID",
) -> list[dict[str, Any]]:
    """Resolve source-chunk references through the Host-owned packet view.

    Plugins may consume this public SDK helper, but must not implement their
    own Evidence namespace or source-chunk index.
    """
    payload = packet.evidence[0].payload if packet.evidence else {}
    chunks = payload.get("sourceChunks", ()) if isinstance(payload, Mapping) else ()
    by_id = {
        str(item.get("source_chunk_id")): dict(item)
        for item in chunks
        if isinstance(item, Mapping) and item.get("source_chunk_id")
    }
    resolved: list[dict[str, Any]] = []
    for reference in references:
        ref = str(reference)
        item = by_id.get(ref)
        if item is None:
            raise PlatformContractError(
                error_code,
                f"Unknown source Evidence reference: {ref}",
            )
        resolved.append({
            key: item[key]
            for key in ("source_chunk_id", "document_path", "source_digest", "start_line", "end_line")
            if key in item
        })
    return resolved


def _is_evidence_refs_field(key: Any) -> bool:
    return key in {
        "evidence_refs", "evidenceRefs", "classificationEvidenceRefs",
        "supportedBy", "supported_by",
    }


def validate_domain_evidence_references(
    value: Mapping[str, Any], packet: InvestigationPacket, *,
    error_code: str = "DOMAIN_EVIDENCE_REFERENCE_INVALID",
    error_message: str = "DomainResult contains an Evidence reference outside the current InvestigationPacket",
) -> tuple[str, ...]:
    """Validate and return canonical stable Evidence refs in a domain value.

    Only stable string references are accepted.  Paths, line ranges, digests,
    or free-form Evidence objects are not transport references and therefore
    cannot be smuggled into the Agent result.
    """
    allowed = host_evidence_references(packet)
    collected: list[str] = []

    def visit(current: Any, path: tuple[Any, ...]) -> None:
        if isinstance(current, Mapping):
            for key, item in current.items():
                child_path = (*path, key)
                if _is_evidence_refs_field(key):
                    if not isinstance(item, (tuple, list)):
                        raise PlatformContractError(
                            error_code,
                            f"Evidence references at {_pointer(child_path)} must be an array",
                        )
                    seen_at_field: set[str] = set()
                    for index, raw_ref in enumerate(item):
                        ref = raw_ref
                        # Plugin mappers may retain their internal canonical
                        # source-chunk object for downstream domain evaluators.
                        # The Host still validates only its stable ID and
                        # ignores copied path/line/digest metadata.
                        if (
                            error_code != "DOMAIN_EVIDENCE_REFERENCE_INVALID"
                            and isinstance(raw_ref, Mapping)
                        ):
                            ref = (
                                raw_ref.get("source_chunk_id")
                                or raw_ref.get("sourceChunkId")
                                or raw_ref.get("evidence_id")
                                or raw_ref.get("evidenceId")
                            )
                        if not isinstance(ref, str) or not ref.strip():
                            raise PlatformContractError(
                                error_code,
                                f"Evidence reference at {_pointer((*child_path, index))} must be a non-empty stable ID",
                            )
                        if ref in seen_at_field:
                            raise PlatformContractError(
                                error_code,
                                f"Duplicate Evidence reference at {_pointer((*child_path, index))}",
                            )
                        seen_at_field.add(ref)
                        if ref not in allowed:
                            raise PlatformContractError(
                                error_code,
                                error_message + f": {ref}",
                            )
                        collected.append(ref)
                visit(item, child_path)
        elif isinstance(current, (tuple, list)):
            for index, item in enumerate(current):
                visit(item, (*path, index))

    visit(value, ())
    return tuple(dict.fromkeys(collected))


__all__ = [
    "host_evidence_references",
    "resolve_evidence_references",
    "validate_domain_evidence_references",
]
