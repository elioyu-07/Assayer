"""A deterministic, read-only JSON configuration quality plugin."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from ...contract import (
    CheckContract,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PluginManifest,
    WorkItem,
)
from ...registry import load_plugin_manifest


_MANIFEST = Path(__file__).with_name("manifest.json")


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


class ConfigQualityPlugin:
    """Discover and inspect JSON files without exposing configuration values."""

    manifest: PluginManifest = load_plugin_manifest(_MANIFEST)

    def discover(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]:
        del context
        if isinstance(scope, (str, Path)):
            entries = [{"path": str(scope)}]
        elif isinstance(scope, dict):
            entries = scope.get("files", [])
            if isinstance(entries, (str, Path)):
                entries = [{"path": str(entries)}]
        else:
            entries = scope or []
        items: list[WorkItem] = []
        for entry in entries:
            config = {"path": entry} if isinstance(entry, (str, Path)) else dict(entry)
            path = Path(config["path"]).expanduser().resolve()
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            required_keys = tuple(sorted(set(config.get("requiredKeys", ()))))
            expected_types = dict(sorted(config.get("expectedTypes", {}).items()))
            requirements_digest = hashlib.sha256(json.dumps(
                {"requiredKeys": required_keys, "expectedTypes": expected_types},
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            metadata = {
                "path": str(path),
                "requiredKeys": required_keys,
                "expectedTypes": expected_types,
                "requirements_digest": requirements_digest,
            }
            identity_digest = hashlib.sha256(str(path).encode()).hexdigest()
            items.append(WorkItem(f"configuration:{identity_digest[:16]}", "configuration_file", str(path), digest, metadata))
        return items

    def inspect(self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext) -> Sequence[InvestigationPacket]:
        del context
        packets: list[InvestigationPacket] = []
        for item in work_items:
            path = Path(item.metadata["path"])
            raw = path.read_bytes()
            current_digest = hashlib.sha256(raw).hexdigest()
            if current_digest != item.state_digest:
                raise ValueError("Configuration changed after discovery")
            evidence_id = f"evidence:{item.work_item_id}:{current_digest[:16]}"
            try:
                document = json.loads(raw.decode("utf-8"))
                parse_status = "satisfied"
                parse_observation = "The file is valid JSON."
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                document = None
                parse_status = "violated"
                parse_observation = f"The file is not valid JSON ({type(exc).__name__})."
            evidence = EvidenceRecord(
                evidence_id, item.work_item_id, check.check_id, check.version, "structured",
                item.identity,
                {"sourceDigest": current_digest, "parseable": document is not None,
                 "topLevelKeys": sorted(document) if isinstance(document, dict) else [],
                 "valueTypes": {key: _type_name(value) for key, value in document.items()} if isinstance(document, dict) else {}},
            )
            required = tuple(item.metadata.get("requiredKeys", ()))
            expected = dict(item.metadata.get("expectedTypes", {}))
            keys_ok = document is not None and (not required or isinstance(document, dict) and all(key in document for key in required))
            types_ok = document is not None and (not expected or isinstance(document, dict) and all(key in document and _type_name(document[key]) == expected_type for key, expected_type in expected.items()))
            dimensions = (
                DimensionObservation("parseable", (parse_observation,), (evidence_id,), parse_status),
                DimensionObservation("required_keys", ("All required keys are present." if keys_ok else "One or more required keys are missing.",), (evidence_id,), "satisfied" if keys_ok else ("unresolved" if document is None else "violated")),
                DimensionObservation("value_types", ("All configured value types match." if types_ok else "One or more configured value types do not match.",), (evidence_id,), "satisfied" if types_ok else ("unresolved" if document is None else "violated")),
            )
            packets.append(InvestigationPacket(item, check.check_id, check.version, dimensions, (evidence,), "not_required"))
        return packets


class ConfigurationDecisionProvider:
    """Reference semantic provider for the deterministic configuration check."""

    def decide(self, packets: Sequence[InvestigationPacket], check: CheckContract, context: PlatformContext) -> Sequence[DecisionProposal]:
        del context
        result: list[DecisionProposal] = []
        for packet in packets:
            findings = tuple(Finding(d.name, d.candidate_status, d.observations[0]) for d in packet.dimensions)
            statuses = {finding.status for finding in findings}
            decision = "issue_found" if "violated" in statuses else "needs_review" if statuses.intersection({"unresolved", "blocked", "conflicted"}) else "scanned_no_issue"
            result.append(DecisionProposal(packet.work_item.work_item_id, check.check_id, check.version, decision, findings, "Configuration quality dimensions evaluated."))
        return result
