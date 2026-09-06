"""Minimal deterministic Assayer plugin used as a platform test fixture.

This package is intentionally tiny: it exercises the external-plugin path
(static package validation, isolated install, deterministic fixture run)
without depending on the reference ``ass-spec`` distribution. It reads JSON
files and reports a deterministic ``issue_found`` when a required key is
missing, mirroring the platform's ``config_quality`` test scaffolding but as
an independently installable package.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from assayer_platform import PlatformContext, PluginRegistration
from assayer_platform.contract import (
    CheckContract,
    DimensionObservation,
    EvidenceRecord,
    InvestigationPacket,
    WorkItem,
)
from assayer_platform.evidence_graph import (
    build_candidate_evidence_graph,
    render_candidate_evidence_graph,
    validate_candidate_evidence_graph_projection,
)
from assayer_platform.registry import load_plugin_manifest


_MANIFEST = Path(__file__).with_name("manifest.json")
_SCOPE = Path(__file__).with_name("scope.schema.json")


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


class MinimalPlugin:
    """Discover and inspect JSON files without exposing configuration values."""

    manifest = load_plugin_manifest(_MANIFEST)
    evidence_graph_enabled = True

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
            metadata = {
                "path": str(path),
                "requiredKeys": required_keys,
                "expectedTypes": expected_types,
            }
            items.append(WorkItem(
                f"minimal:{digest[:16]}", "configuration_file", str(path), digest, metadata,
            ))
        return items

    def inspect(
        self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext,
    ) -> Sequence[InvestigationPacket]:
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
                parse_ok = True
            except (UnicodeDecodeError, json.JSONDecodeError):
                document = None
                parse_ok = False
            required = tuple(item.metadata.get("requiredKeys", ()))
            keys_ok = document is not None and (not required or isinstance(document, dict) and all(key in document for key in required))
            dimension = DimensionObservation(
                "required_keys",
                ("All required keys are present." if keys_ok else "One or more required keys are missing.",),
                (evidence_id,),
                "satisfied" if keys_ok else ("unresolved" if document is None else "violated"),
            )
            evidence_payload = {
                "sourceDigest": current_digest,
                "parseable": parse_ok,
                "topLevelKeys": sorted(document) if isinstance(document, dict) else [],
            }
            graph = build_candidate_evidence_graph(
                [{
                    "candidate_id": f"{item.work_item_id}:{dimension.name}",
                    "rule_id": check.check_id,
                    "object_id": dimension.name,
                    "message": dimension.observations[0],
                    "evidence": evidence_id,
                    "disposition": "needs_review" if dimension.candidate_status == "unresolved" else "confirmed",
                }],
                work_item_id=item.work_item_id,
                check_id=check.check_id, check_version=check.version,
            )
            evidence_payload["candidateGraph"] = render_candidate_evidence_graph(graph)
            validate_candidate_evidence_graph_projection(evidence_payload["candidateGraph"])
            evidence = EvidenceRecord(
                evidence_id, item.work_item_id, check.check_id, check.version, "structured",
                item.identity, evidence_payload,
            )
            packets.append(InvestigationPacket(
                item, check.check_id, check.version, (dimension,), (evidence,), "not_required",
            ))
        return packets


registration = PluginRegistration(
    MinimalPlugin.manifest,
    plugin_factory=lambda _runtime=None: MinimalPlugin(),
    capabilities=frozenset({"structured_read"}),
    result_features=frozenset({"evidence_graph"}),
    execution_modes=frozenset({"interactive"}),
    scope_schema=json.loads(_SCOPE.read_text(encoding="utf-8")),
)


__all__ = ["MinimalPlugin", "registration"]
