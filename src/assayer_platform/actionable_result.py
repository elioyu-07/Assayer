"""Validation for the platform-owned actionable result envelope.

Dimension Findings remain the coverage truth.  This module validates the
optional root-cause remediation view without interpreting domain rules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .contract import DecisionProposal, InvestigationPacket, PlatformContractError
from .evidence_claim import validate_evidence_claims
from .registry import _schema_root


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError("RESULT_DELIVERY_INVALID", f"{label} must be non-empty")
    return value


def _array(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (tuple, list)):
        raise PlatformContractError("RESULT_DELIVERY_INVALID", f"{label} must be an array")
    return value


def _validator() -> Draft202012Validator:
    root = _schema_root()
    schemas: dict[str, Any] = {}
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas["actionable-result.schema.json"]
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))


def validate_result_delivery(
    delivery: Mapping[str, Any], proposal: DecisionProposal, packet: InvestigationPacket,
) -> dict[str, Any]:
    """Validate and return a JSON-safe actionable result envelope.

    The platform checks shape, identity and closure only.  It does not decide
    whether a domain recommendation is substantively correct.
    """
    value = _plain(delivery)
    error = next(_validator().iter_errors(value), None)
    if error is not None:
        location = ".".join(str(item) for item in error.absolute_path) or "root"
        raise PlatformContractError(
            "RESULT_DELIVERY_INVALID",
            f"Actionable result validation failed at {location}: {error.message}",
        )
    dimensions = {item.dimension: item.status for item in proposal.findings}
    packet_evidence = {item.evidence_id for item in packet.evidence}
    claims = validate_evidence_claims(value.get("evidenceClaims", ()), packet)
    claim_ids = {str(item["claimId"]) for item in claims}
    claims_by_id = {str(item["claimId"]): item for item in claims}
    seen: set[str] = set()
    covered: set[str] = set()
    for index, raw in enumerate(value["remediations"]):
        remediation = dict(raw)
        remediation_id = _text(remediation["remediationId"], f"remediations[{index}].remediationId")
        if remediation_id in seen:
            raise PlatformContractError("RESULT_DELIVERY_INVALID", f"Duplicate remediationId: {remediation_id}")
        seen.add(remediation_id)
        mapped = set(_text(item, f"remediations[{index}].dimensions item") for item in _array(remediation["dimensions"], f"remediations[{index}].dimensions"))
        unknown = mapped - set(dimensions)
        if unknown:
            raise PlatformContractError("RESULT_DELIVERY_INVALID", f"Remediation {remediation_id} references unknown dimensions: {sorted(unknown)}")
        actionable = {
            item for item in mapped if dimensions[item] in {"violated", "conflicted"}
        }
        if actionable != mapped:
            raise PlatformContractError(
                "RESULT_DELIVERY_INVALID",
                f"Remediation {remediation_id} must map only violated or conflicted dimensions",
            )
        covered.update(mapped)
        refs = set(_text(item, f"remediations[{index}].evidenceRefs item") for item in _array(remediation["evidenceRefs"], f"remediations[{index}].evidenceRefs"))
        if not refs.issubset(packet_evidence):
            raise PlatformContractError("RESULT_DELIVERY_INVALID", f"Remediation {remediation_id} references Evidence outside this InvestigationPacket")
        claim_refs = set(_text(item, f"remediations[{index}].claimRefs item") for item in _array(remediation.get("claimRefs", ()), f"remediations[{index}].claimRefs"))
        if not claim_refs.issubset(claim_ids):
            raise PlatformContractError("RESULT_DELIVERY_INVALID", f"Remediation {remediation_id} references an unknown Evidence Claim")
        for claim_id in claim_refs:
            claim_evidence = set(claims_by_id[claim_id]["evidenceRefs"])
            if not claim_evidence.issubset(refs):
                raise PlatformContractError(
                    "RESULT_DELIVERY_INVALID",
                    f"Remediation {remediation_id} does not carry all EvidenceRefs used by claim {claim_id}",
                )
    if value["status"] == "complete":
        actionable_dimensions = {
            dimension for dimension, status in dimensions.items()
            if status in {"violated", "conflicted"}
        }
        if covered != actionable_dimensions:
            missing = sorted(actionable_dimensions - covered)
            raise PlatformContractError(
                "RESULT_DELIVERY_INCOMPLETE",
                f"Complete actionable delivery does not cover actionable dimensions: {missing}",
            )
    if proposal.result == "issue_found" and value["status"] == "complete" and not value["remediations"]:
        raise PlatformContractError("RESULT_DELIVERY_INCOMPLETE", "An issue result requires at least one remediation")
    return value


def extract_result_delivery(
    proposal: DecisionProposal, packet: InvestigationPacket,
) -> tuple[str, list[dict[str, Any]]]:
    """Return actionability status and validated remediations from a proposal."""
    raw = proposal.details.get("result_delivery") if isinstance(proposal.details, Mapping) else None
    if raw is None:
        return "not_declared", []
    if not isinstance(raw, Mapping):
        raise PlatformContractError("RESULT_DELIVERY_INVALID", "Decision details.result_delivery must be an object")
    value = validate_result_delivery(raw, proposal, packet)
    return str(value["status"]), [dict(item) for item in value["remediations"]]


def extract_result_delivery_bundle(
    proposal: DecisionProposal, packet: InvestigationPacket,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return actionability, remediations and validated Evidence Claims."""
    raw = proposal.details.get("result_delivery") if isinstance(proposal.details, Mapping) else None
    if raw is None:
        return "not_declared", [], []
    if not isinstance(raw, Mapping):
        raise PlatformContractError("RESULT_DELIVERY_INVALID", "Decision details.result_delivery must be an object")
    value = validate_result_delivery(raw, proposal, packet)
    return (
        str(value["status"]),
        [dict(item) for item in value["remediations"]],
        [dict(item) for item in value.get("evidenceClaims", ())],
    )


def build_actionable_result(
    decisions: Sequence[Mapping[str, Any]],
    checklist_status: Mapping[str, str],
    evidence_by_dimension: Mapping[str, Sequence[str]],
    *,
    evidence_id: str | None,
    source_chunks: Sequence[Mapping[str, Any]],
    work_item_identity: str,
    document_path: str,
    confirmed_status: str,
    actionable_statuses: frozenset[str] | set[str],
    absence_pattern: Any = None,
) -> dict[str, Any]:
    """Project confirmed decisions into the remediation + claim envelope.

    The platform assembles the shape from generic decision fields.  The plugin
    supplies its domain vocabulary (confirmed status, actionable statuses, and
    the absence-detection pattern); the platform never interprets those tokens.
    """
    remediations: list[dict[str, Any]] = []
    evidence_claims: list[dict[str, Any]] = []
    end_line = max(
        [int(item.get("end_line", 1)) for item in source_chunks if isinstance(item, Mapping)] or [1],
    )
    covered: set[str] = set()
    for finding in decisions:
        if finding.get("status") != confirmed_status:
            continue
        dimension = str(finding.get("dimension") or "")
        if checklist_status.get(dimension) not in actionable_statuses:
            continue
        affected = finding.get("affected_elements")
        if not isinstance(affected, (tuple, list)) or not affected:
            affected = [finding.get("object_id")]
        affected = [str(item).strip() for item in affected if str(item or "").strip()]
        owner = finding.get("resolution_owner")
        owner_value = (
            {"status": "assigned", "identity": str(owner).strip()}
            if isinstance(owner, str) and owner.strip()
            else {
                "status": "unassigned",
                "reason": "The reviewed evidence does not identify an accountable resolution owner.",
            }
        )
        recommendation = str(finding.get("recommendation") or "").strip()
        next_action = str(finding.get("next_action") or "").strip()
        if not next_action:
            next_action = f"Assign an accountable owner and implement this recommendation: {recommendation}"
        gap = str(finding.get("gap") or "").strip()
        finding_id = str(finding.get("finding_id") or "")
        absence = bool(
            absence_pattern.search(gap) if absence_pattern is not None else False
        )
        claim_id = f"claim:{finding_id}"
        claim: dict[str, Any] = {
            "claimId": claim_id,
            "kind": "absence" if absence else "direct",
            "evidenceRefs": [evidence_id] if evidence_id else [],
            "scope": {
                "sourceRef": evidence_id or work_item_identity,
                "documentPath": document_path,
                "startLine": max(1, int(finding.get("line") or 1)),
                "endLine": end_line,
            },
            "observed": [str(finding.get("evidence") or gap)],
            "conclusion": gap,
        }
        if absence:
            claim["searchedFor"] = [
                "The requirement or record described by this finding",
                "A positive, directly observable specification statement in the declared scope",
            ]
        evidence_claims.append(claim)
        remediations.append({
            "remediationId": finding_id,
            "title": gap,
            "severity": str(finding.get("severity") or ""),
            "dimensions": [dimension],
            "affectedElements": affected,
            "evidenceRefs": list(evidence_by_dimension.get(dimension, [])),
            "problem": gap,
            "impact": str(finding.get("impact") or "").strip(),
            "recommendation": recommendation,
            "nextAction": next_action,
            "closureEvidence": str(finding.get("closure_evidence") or "").strip(),
            "owner": owner_value,
            "claimRefs": [claim_id],
        })
        covered.add(dimension)
    actionable_dimensions = {
        check_id for check_id, status in checklist_status.items()
        if status in actionable_statuses
    }
    return {
        "schemaVersion": "1.0.0",
        "status": "complete" if covered == actionable_dimensions else "partial",
        "remediations": remediations,
        "evidenceClaims": evidence_claims,
    }


__all__ = [
    "build_actionable_result",
    "extract_result_delivery",
    "extract_result_delivery_bundle",
    "validate_result_delivery",
]