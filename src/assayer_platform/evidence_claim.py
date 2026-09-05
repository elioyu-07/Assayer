"""Platform validation for portable, source-bound Evidence Claims.

Claims explain what a result asserts about its evidence.  The platform owns
identity, scope and reference integrity; plugins own the semantic meaning of
the assertion and whether the evidence is sufficient for their rule.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .contract import InvestigationPacket, PlatformContractError
from .registry import _schema_root


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError("EVIDENCE_CLAIM_INVALID", f"{label} must be non-empty")
    return value


def _validator() -> Draft202012Validator:
    root = _schema_root()
    schemas: dict[str, Any] = {}
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas["evidence-claim.schema.json"]
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))


def validate_evidence_claims(
    claims: Sequence[Any] | None, packet: InvestigationPacket,
) -> list[dict[str, Any]]:
    """Validate claim shape and binding to one InvestigationPacket.

    A claim may point at opaque source coordinates, but every EvidenceRef must
    be an EvidenceRecord from the current packet.  The platform deliberately
    does not decide whether a search was semantically complete.
    """
    raw_claims = [] if claims is None else claims
    if not isinstance(raw_claims, (tuple, list)):
        raise PlatformContractError("EVIDENCE_CLAIM_INVALID", "evidenceClaims must be an array")
    packet_evidence = {item.evidence_id for item in packet.evidence}
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_claims):
        value = _plain(raw)
        error = next(_validator().iter_errors(value), None)
        if error is not None:
            location = ".".join(str(item) for item in error.absolute_path) or "root"
            raise PlatformContractError(
                "EVIDENCE_CLAIM_INVALID",
                f"Evidence claim validation failed at evidenceClaims[{index}].{location}: {error.message}",
            )
        claim_id = _text(value["claimId"], f"evidenceClaims[{index}].claimId")
        if claim_id in seen:
            raise PlatformContractError("EVIDENCE_CLAIM_INVALID", f"Duplicate claimId: {claim_id}")
        seen.add(claim_id)
        refs = {_text(item, f"evidenceClaims[{index}].evidenceRefs item") for item in value["evidenceRefs"]}
        if not refs.issubset(packet_evidence):
            raise PlatformContractError(
                "EVIDENCE_CLAIM_INVALID",
                f"Evidence claim {claim_id} references Evidence outside this InvestigationPacket",
            )
        scope = value.get("scope")
        if isinstance(scope, Mapping):
            start, end = scope["startLine"], scope["endLine"]
            if isinstance(start, bool) or isinstance(end, bool) or end < start:
                raise PlatformContractError(
                    "EVIDENCE_CLAIM_INVALID",
                    f"Evidence claim {claim_id} has an unbounded or reversed line scope",
                )
            source_ref = str(scope["sourceRef"])
            if source_ref.startswith("evidence:") and source_ref not in packet_evidence:
                raise PlatformContractError(
                    "EVIDENCE_CLAIM_INVALID",
                    f"Evidence claim {claim_id} scope sourceRef is not in this InvestigationPacket",
                )
        validated.append(value)
    return validated


__all__ = ["validate_evidence_claims"]
