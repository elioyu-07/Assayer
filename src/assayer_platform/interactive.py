"""Domain-neutral interactive plugin protocol.

This module is the transport-independent controller for Agent-driven plugin
runs.  It deliberately knows only about WorkItems, InvestigationPackets and
DecisionProposals; browser concepts remain behind the browser capability
provider. MCP/CLI transports can wrap this controller without duplicating
lifecycle or validation rules.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .agent_contract import DomainResultContract
from .capability_negotiation import CapabilityNegotiator
from .contract import (
    CapabilityProfile,
    CommitReceipt,
    DecisionProposal,
    DimensionObservation,
    Finding,
    InvestigationPacket,
    PlatformLedger,
    PlatformContext,
    PlatformContractError,
    PlatformRunResult,
    WorkFailure,
    WorkItem,
)
from .plugin_registry import PluginRegistry, PluginRegistration
from .provider_execution import BoundCapabilityProvider
from .provider_registry import ProviderRegistry
from .plugin_sdk import to_json_value, validate_entity_id
from .result_delivery import StagedResultDocument
from .audit_report import render_audit_report
from .canonical_result import validate_canonical_result
from .actionable_result import extract_result_delivery
from .evidence_graph import validate_candidate_evidence_graph_projection
from .evidence_collection import EvidenceCollectionPager
from .evidence_reference import validate_domain_evidence_references
from .error_policy import (
    DEFAULT_AGENT_CORRECTION_BUDGET,
    boundary_error_policy,
)
from .ledger import JsonPlatformLedgerStore, workflow_progress
from .incremental_review import (
    JsonCoverageLedgerStore,
    ReviewAtom,
    append_legacy_domain_result,
    ensure_legacy_domain_results,
)
from .incremental_review_runtime import IncrementalReviewCoordinator
from .common_review_decision import assemble_common_review_decisions
from .common_review import COMMON_REVIEW_CONTRACT
from .compiled_review_plan import compile_review_items
from .document_source import DocumentSnapshotStore, HostDocumentSource
from .ownership import RunOwnership
from .session import InteractivePlatformRun, InteractivePlatformSession
from .task_context import TaskContext
from .evidence_handles import EvidenceHandleRegistry
from assayer_plugin_sdk.plugin_compatibility import HOST_PROTOCOL_VERSION, negotiate_plugin_compatibility
from assayer_plugin_sdk.browser import BrowserSnapshot
from assayer_plugin_sdk.simple import Document
from assayer_plugin_sdk.simple_compiler import InvariantProgram, compile_invariants


INTERACTIVE_PROTOCOL_VERSION = ".".join(HOST_PROTOCOL_VERSION.split(".")[:2])
INTERACTIVE_OPERATIONS = (
    "start",
    "resume",
    "advance",
    "get_result",
    "discover",
    "inspect",
    "recover",
    "progress",
)

_LOG = logging.getLogger(__name__)

DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE = 20
MAX_EVIDENCE_COLLECTION_PAGE_SIZE = 100
INITIAL_SEMANTIC_EVIDENCE_PREVIEW_SIZE = 4
# Agent-facing semantic tasks are deliberately much smaller than the durable
# InvestigationPacket.  The full packet remains in the Host ledger; this
# limit protects the transport/client boundary from accidentally rendering a
# complete source corpus in one turn.
AGENT_TASK_PAYLOAD_LIMIT = 24 * 1024
AGENT_VALUE_STRING_LIMIT = 512
AGENT_VALUE_ARRAY_LIMIT = 80


def _simple_document_from_packet(
    packet: InvestigationPacket, snapshot_store: DocumentSnapshotStore,
) -> Document:
    """Create the author-facing Document only from one Host-frozen source snapshot."""
    snapshots: list[tuple[str, Mapping[str, Any]]] = []
    for evidence in packet.evidence:
        if not isinstance(evidence.payload, Mapping):
            continue
        value = evidence.payload.get("documentSnapshot")
        if value is not None:
            if not isinstance(value, Mapping):
                raise PlatformContractError(
                    "INVALID_DOCUMENT_SNAPSHOT",
                    "The frozen documentSnapshot must be an object",
                    work_item_id=packet.work_item.work_item_id,
                )
            snapshots.append((evidence.evidence_id, value))
    if not snapshots:
        raise PlatformContractError(
            "SIMPLE_DOCUMENT_SNAPSHOT_REQUIRED",
            "A Simple plugin requires one Host-owned frozen documentSnapshot",
            work_item_id=packet.work_item.work_item_id,
        )
    if len(snapshots) != 1:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT",
            "A Simple plugin WorkItem must have exactly one frozen documentSnapshot",
            work_item_id=packet.work_item.work_item_id,
        )
    evidence_id, snapshot = snapshots[0]
    if set(snapshot) != {"snapshotId", "closed", "lineCount", "chunkCount"}:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT",
            "documentSnapshot index does not match its Host contract",
            work_item_id=packet.work_item.work_item_id,
        )
    snapshot_id = snapshot["snapshotId"]
    if not isinstance(snapshot_id, str):
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT", "documentSnapshot identity is malformed",
            work_item_id=packet.work_item.work_item_id,
        )
    frozen, chunks = snapshot_store.load(snapshot_id)
    if (
        frozen.closed is not snapshot["closed"]
        or len(frozen.text.splitlines()) != snapshot["lineCount"]
        or len(chunks) != snapshot["chunkCount"]
        or hashlib.sha256(frozen.text.encode("utf-8")).hexdigest()
        != packet.work_item.state_digest
    ):
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT",
            "documentSnapshot index differs from its frozen content",
            work_item_id=packet.work_item.work_item_id,
        )
    indexed_chunks = packet.evidence[
        next(index for index, item in enumerate(packet.evidence) if item.evidence_id == evidence_id)
    ].payload.get("sourceChunks")
    expected_index = snapshot_store.packet_index(
        chunks, work_item_id=packet.work_item.work_item_id,
    )
    if _plain(indexed_chunks) != expected_index:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT",
            "documentSnapshot chunk index differs from its frozen content",
            work_item_id=packet.work_item.work_item_id,
        )
    coverage_refs = tuple(
        item.evidence_id
        for item in packet.evidence
        if isinstance(item.payload, Mapping)
        and item.payload.get("documentCoverage") == {
            "snapshotId": snapshot_id, "closed": frozen.closed,
        }
    )
    if len(coverage_refs) != 1:
        raise PlatformContractError(
            "INVALID_DOCUMENT_SNAPSHOT",
            "documentSnapshot requires one matching closed-scope coverage record",
            work_item_id=packet.work_item.work_item_id,
        )
    return Document._from_snapshot(
        frozen.text,
        evidence_refs=(evidence_id,),
        closed=frozen.closed,
        chunks=tuple({
            "anchor": indexed["source_chunk_id"],
            "startLine": item.start_line,
            "endLine": item.end_line,
            "text": item.text,
            "headingPath": item.heading_path,
        } for indexed, item in zip(expected_index, chunks)),
        coverage_refs=coverage_refs,
    )


def _simple_browser_document_from_packet(packet: InvestigationPacket) -> Document:
    """Create a Simple ``Document`` view from one Host-issued browser Evidence."""
    records = tuple(
        evidence for evidence in packet.evidence
        if evidence.kind == "browser_snapshot"
    )
    if len(records) != 1:
        raise PlatformContractError(
            "SIMPLE_BROWSER_SNAPSHOT_REQUIRED",
            "A Simple browser plugin requires exactly one Host browser snapshot",
            work_item_id=packet.work_item.work_item_id,
        )
    evidence = records[0]
    payload = evidence.payload
    if not isinstance(payload, Mapping) or payload.get("format") != "browser_snapshot":
        raise PlatformContractError(
            "INVALID_BROWSER_SNAPSHOT",
            "The browser Evidence payload does not match its published format",
            work_item_id=packet.work_item.work_item_id,
        )
    field_map = {
        "visible_text": "visibleText",
        "entrypoints": "entrypoints",
        "candidates": "candidates",
        "network_summary": "networkSummary",
        "route": "route",
        "state_kind": "stateKind",
        "structure_summary": "structureSummary",
        "active_tab": "activeTab",
        "dom_digest": "domDigest",
        "visual_digest": "visualDigest",
        "state_digest": "stateDigest",
        "url": "url",
        "origin": "origin",
        "title": "title",
    }
    try:
        snapshot = BrowserSnapshot(**{
            field: payload[key] for field, key in field_map.items()
        })
    except (KeyError, TypeError, PlatformContractError) as error:
        raise PlatformContractError(
            "INVALID_BROWSER_SNAPSHOT",
            "The browser Evidence payload cannot be reconstructed as a frozen snapshot",
            work_item_id=packet.work_item.work_item_id,
        ) from error
    if snapshot.url != packet.work_item.identity or snapshot.state_digest != packet.work_item.state_digest:
        raise PlatformContractError(
            "INVALID_BROWSER_SNAPSHOT",
            "The browser Evidence snapshot does not match its WorkItem state",
            work_item_id=packet.work_item.work_item_id,
        )
    return Document._from_browser_snapshot(
        snapshot,
        evidence_refs=(evidence.evidence_id,),
    )


def _simple_browser_packets(
    work_items: Sequence[WorkItem], check: Any, provider: BoundCapabilityProvider,
) -> tuple[InvestigationPacket, ...]:
    """Collect Host-owned browser Evidence and build generic Simple packets."""
    capabilities = tuple(check.required_capabilities)
    if len(capabilities) != 1 or capabilities[0] != "browser_snapshot":
        raise PlatformContractError(
            "SIMPLE_BROWSER_CAPABILITY_INVALID",
            "A browser Simple Check must require exactly browser_snapshot",
        )
    packets: list[InvestigationPacket] = []
    for item in work_items:
        try:
            collected = provider.collect(item, check, "browser_snapshot")
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "PROVIDER_FACTS_UNAVAILABLE",
                "The browser provider failed while collecting the frozen snapshot",
                work_item_id=item.work_item_id,
            ) from error
        if collected.failure is not None:
            raise PlatformContractError(
                f"PROVIDER_{collected.failure.code.upper()}",
                collected.failure.message,
                work_item_id=item.work_item_id,
            )
        if not collected.evidence:
            raise PlatformContractError(
                "PROVIDER_EVIDENCE_MISSING",
                "The browser provider returned no Evidence for the WorkItem",
                work_item_id=item.work_item_id,
            )
        refs = tuple(record.evidence_id for record in collected.evidence)
        dimensions = tuple(
            DimensionObservation(
                dimension,
                ("A frozen browser snapshot is available for semantic review.",),
                refs,
                "unresolved",
            )
            for dimension in check.dimensions
        )
        packets.append(InvestigationPacket(
            item,
            check.check_id,
            check.version,
            dimensions,
            tuple(collected.evidence),
            "not_required",
        ))
    return tuple(packets)


def _simple_declaration(plugin: Any) -> Mapping[str, Any] | None:
    value = getattr(plugin, "_assayer_simple_declaration", None)
    adapter = getattr(plugin, "_assayer_compile_scan", None)
    if value is None and not callable(adapter):
        return None
    if not isinstance(value, Mapping) or not callable(adapter):
        raise PlatformContractError(
            "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
            "Simple plugin declaration and generated scan bridge must be present together",
        )
    input_kind = value.get("input")
    if not isinstance(input_kind, str) or not input_kind.strip():
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN", "Simple plugin input kind is required",
        )
    return value


def _simple_invariants(plugin: Any) -> InvariantProgram:
    """Compile the plugin's bound invariant methods into one Host program."""
    methods = tuple(
        getattr(plugin, name) for name in dir(plugin)
        if callable(getattr(plugin, name, None))
        and hasattr(getattr(plugin, name), "_assayer_invariant_declaration")
    )
    return compile_invariants(methods)


def _common_review_evidence_content(
    run: InteractivePlatformRun,
    bindings: Sequence[tuple[str, str]],
    snapshot_store: DocumentSnapshotStore,
) -> dict[str, Any]:
    """Build an allowlisted expansion view only for the current batch refs."""
    requested = {internal_ref for _public_ref, internal_ref in bindings}
    content: dict[str, Any] = {}
    for packet in run.investigations.values():
        for evidence in packet.evidence:
            payload = evidence.payload if isinstance(evidence.payload, Mapping) else {}
            snapshot_index = payload.get("documentSnapshot")
            coverage = payload.get("documentCoverage")
            if evidence.evidence_id in requested:
                if isinstance(snapshot_index, Mapping):
                    content[evidence.evidence_id] = {
                        "kind": "document",
                        "closed": snapshot_index.get("closed"),
                        "lineCount": snapshot_index.get("lineCount"),
                        "chunkCount": snapshot_index.get("chunkCount"),
                    }
                elif isinstance(coverage, Mapping):
                    content[evidence.evidence_id] = {
                        "kind": "document_coverage",
                        "closed": coverage.get("closed"),
                    }
            raw_chunks = payload.get("sourceChunks", ())
            if not isinstance(raw_chunks, (tuple, list)):
                continue
            requested_chunks = {
                str(item.get("source_chunk_id"))
                for item in raw_chunks
                if isinstance(item, Mapping)
                and str(item.get("source_chunk_id") or "") in requested
            }
            if not requested_chunks:
                continue
            frozen_chunks: dict[str, Any] = {}
            if isinstance(snapshot_index, Mapping) and isinstance(
                snapshot_index.get("snapshotId"), str,
            ):
                _snapshot, stored = snapshot_store.load(snapshot_index["snapshotId"])
                frozen_chunks = {
                    indexed["source_chunk_id"]: item
                    for indexed, item in zip(
                        snapshot_store.packet_index(
                            stored, work_item_id=packet.work_item.work_item_id,
                        ),
                        stored,
                    )
                }
            for raw_chunk in raw_chunks:
                if not isinstance(raw_chunk, Mapping):
                    continue
                chunk_id = str(raw_chunk.get("source_chunk_id") or "")
                if chunk_id not in requested_chunks:
                    continue
                stored = frozen_chunks.get(chunk_id)
                if stored is not None:
                    content[chunk_id] = {
                        "text": stored.text,
                        "lineRange": {
                            "start": stored.start_line, "end": stored.end_line,
                        },
                        "headingPath": list(stored.heading_path),
                    }
                    continue
                excerpt = raw_chunk.get("excerpt") or raw_chunk.get("content")
                if isinstance(excerpt, str):
                    start = raw_chunk.get("start_line", raw_chunk.get("startLine"))
                    end = raw_chunk.get("end_line", raw_chunk.get("endLine"))
                    heading_path = raw_chunk.get(
                        "heading_path", raw_chunk.get("headingPath", ()),
                    )
                    content[chunk_id] = {
                        "text": excerpt,
                        **({
                            "lineRange": {"start": start, "end": end},
                        } if isinstance(start, int) and isinstance(end, int) else {}),
                        **({
                            "headingPath": [str(item) for item in heading_path],
                        } if isinstance(heading_path, (tuple, list)) else {}),
                    }
    return content


def _common_review_atoms(
    run: InteractivePlatformRun, plugin: Any, context: PlatformContext,
    snapshot_store: DocumentSnapshotStore,
) -> tuple[ReviewAtom, ...]:
    """Compile frozen Investigation dimensions into Host review atoms."""
    atoms: list[ReviewAtom] = []
    for work_item_id in run.work_items:
        packet = run.investigations.get(work_item_id)
        if packet is None:
            continue
        simple_adapter = getattr(plugin, "_assayer_compile_scan", None)
        legacy_adapter = getattr(plugin, "_assayer_compiled_review_items", None)
        if callable(simple_adapter) and callable(legacy_adapter):
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "A plugin cannot combine Simple scan and legacy compiled review adapters",
                work_item_id=work_item_id,
            )
        compiled_atoms: tuple[ReviewAtom, ...] = ()
        review_context: Mapping[str, Any] | None = None
        dimension_supports: Mapping[str, tuple[str, ...]] = {}
        try:
            if callable(simple_adapter):
                declaration = _simple_declaration(plugin)
                if declaration is not None and declaration.get("input") == "browser_snapshot":
                    document = _simple_browser_document_from_packet(packet)
                else:
                    document = _simple_document_from_packet(packet, snapshot_store)
                compiled_plan = compile_review_items(
                    simple_adapter(document),
                    run_id=run.context.run_id,
                    packet=packet,
                )
                compiled_atoms = compiled_plan.atoms
                review_context = compiled_plan.context
                dimension_supports = compiled_plan.dimension_supports
            elif callable(legacy_adapter):
                compiled_plan = compile_review_items(
                    legacy_adapter(packet, run.check, context),
                    run_id=run.context.run_id,
                    packet=packet,
                )
                compiled_atoms = compiled_plan.atoms
                review_context = compiled_plan.context
                dimension_supports = compiled_plan.dimension_supports
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "Generated Simple SDK review adapter failed",
                work_item_id=work_item_id,
            ) from error
        declared_dimensions = {dimension.name for dimension in packet.dimensions}
        if set(dimension_supports) - declared_dimensions:
            raise PlatformContractError(
                "INVALID_COMPILED_REVIEW_PLAN",
                "Compiled dimension support references an undeclared dimension",
                work_item_id=work_item_id,
            )
        for index, dimension in enumerate(packet.dimensions, 1):
            atoms.append(ReviewAtom.create(
                run_id=run.context.run_id,
                work_item_id=work_item_id,
                kind="dimension",
                source_anchor=f"dimension:{index}:{dimension.name}",
                rule_id=f"{run.check.check_id}:{dimension.name}",
                payload={
                    "dimension": dimension.name,
                    "instruction": (
                        "Review the frozen observations and decide whether this "
                        "declared dimension is satisfied."
                    ),
                    "subject": packet.work_item.kind,
                    "observations": list(dimension.observations),
                    "supportIds": list(
                        dimension_supports.get(dimension.name, dimension.evidence_refs)
                    ),
                    **({"context": review_context} if review_context is not None else {}),
                },
            ))
        atoms.extend(compiled_atoms)
    if not atoms:
        raise PlatformContractError(
            "COMMON_REVIEW_PLAN_EMPTY",
            "A common-review Run requires at least one typed review dimension",
        )
    return tuple(atoms)

def _finding(value: Mapping[str, Any]) -> Finding:
    return Finding(
        value.get("dimension", ""),
        value.get("status", ""),
        value.get("reason") or value.get("reasonText") or "",
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def _plugin_identity(registration: PluginRegistration) -> dict[str, Any]:
    """Publish the resolved plugin identity so the Agent can immediately confirm
    which plugin version it is driving without guessing from opaque tool names."""
    manifest = registration.manifest
    return {
        "pluginId": manifest.plugin_id,
        "version": manifest.version,
        "platformApiVersion": manifest.platform_api_version,
    }


def _semantic_instructions_uri(
    plugin_id: str, check_id: str, check_version: str,
) -> str:
    """Return the Host-owned MCP resource URI for one frozen domain contract."""
    return (
        f"assayer://plugins/{plugin_id}/checks/{check_id}/"
        f"{check_version}/semantic-instructions"
    )


def _domain_result_contract_task(
    contract: DomainResultContract, *, plugin_id: str,
) -> dict[str, Any]:
    """Publish only the domain contract; Host identity stays server-side."""
    value = contract.as_dict()
    return {
        "contractId": value["contractId"],
        "contractVersion": value["contractVersion"],
        "checkId": value["checkId"],
        "checkVersion": value["checkVersion"],
        "inputKind": "domainResult",
        "resultSchema": _plain(value["resultSchema"]),
        # Keep the contract's resource identity visible so a client can
        # correlate the semantic guidance with the frozen executable
        # contract.  The resource contents are intentionally not loaded into
        # the task; semanticRules and resultShape below are derived from the
        # same immutable contract and are the actionable Agent guidance.
        "semanticInstructions": {
            **_plain(value["semanticInstructions"]),
            "uri": _semantic_instructions_uri(
                plugin_id, contract.check_id, contract.check_version,
            ),
        },
        "resultShape": _plain(contract.result_shape),
        **({"semanticRules": _plain(value["semanticRules"])} if "semanticRules" in value else {}),
    }


def _json_pointer(path: Sequence[Any]) -> str:
    if not path:
        return ""
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _validate_domain_result_schema(
    schema: Mapping[str, Any], value: Any, *, work_item_id: str | None = None,
    error_code: str = "DOMAIN_RESULT_INVALID",
    error_message: str = "DomainResult does not satisfy the Run-frozen domain contract; correct the reported fields",
) -> None:
    errors = sorted(
        Draft202012Validator(dict(schema)).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            tuple(str(part) for part in error.absolute_schema_path),
            error.message,
        ),
    )
    if not errors:
        return
    projected: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(path: Sequence[Any], keyword: str, message: str) -> None:
        pointer = _json_pointer(tuple(path)) or "/"
        identity = (pointer, keyword, message)
        if identity not in seen and len(projected) < 8:
            seen.add(identity)
            projected.append({
                "pointer": pointer,
                "keyword": keyword,
                "message": message[:512],
            })

    for error in errors:
        if len(projected) >= 8:
            break
        path = tuple(error.absolute_path)
        keyword = str(error.validator or "schema")
        if keyword == "required" and isinstance(error.instance, Mapping):
            required = error.validator_value
            missing = (
                [field for field in required if field not in error.instance]
                if isinstance(required, (tuple, list)) else []
            )
            for field in missing:
                add((*path, field), "required", "Required field is missing")
            if missing:
                continue
        if (
            keyword == "additionalProperties"
            and error.validator_value is False
            and isinstance(error.instance, Mapping)
            and isinstance(error.schema, Mapping)
        ):
            properties = error.schema.get("properties", {})
            allowed = set(properties) if isinstance(properties, Mapping) else set()
            unknown = sorted(
                key for key in error.instance
                if isinstance(key, str) and key not in allowed
            )
            for field in unknown:
                add(
                    (*path, field), "additionalProperties",
                    "Field is not declared by this contract",
                )
            if unknown:
                continue
        if keyword == "enum" and isinstance(error.validator_value, (tuple, list)):
            allowed_values = ", ".join(
                item if isinstance(item, str) else json.dumps(
                    item, ensure_ascii=False, sort_keys=True,
                )
                for item in error.validator_value
            )
            add(path, "enum", f"Allowed values: {allowed_values}")
            continue
        add(
            path,
            keyword,
            f"Value does not satisfy the declared {keyword} constraint",
        )
    structured_errors = tuple(projected)
    raise PlatformContractError(
        error_code,
        error_message,
        errors=structured_errors,
        work_item_id=work_item_id,
    )


def _result_evidence_graphs(investigations: Sequence[InvestigationPacket]) -> list[dict[str, Any]]:
    """Build a bounded summary of optional plugin evidence graphs."""
    result: list[dict[str, Any]] = []
    for packet in investigations:
        payload = packet.evidence[0].payload if packet.evidence else None
        graph = payload.get("candidateGraph") if isinstance(payload, Mapping) else None
        if not isinstance(graph, Mapping):
            continue
        validate_candidate_evidence_graph_projection(graph)
        result.append({"workItemId": packet.work_item.work_item_id, **_plain(graph)})
    return result


def _evidence_graph_progress(run: InteractivePlatformRun) -> dict[str, Any]:
    """Summarize candidate coverage from immutable packets and DomainResults."""
    total = covered = 0
    pending_ids: list[str] = []
    by_item: list[dict[str, Any]] = []
    decided_work_items = set(run.decisions)
    for packet in run.investigations.values():
        payload = packet.evidence[0].payload if packet.evidence else None
        graph = payload.get("candidateGraph") if isinstance(payload, Mapping) else None
        candidates = payload.get("candidateFindings", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(graph, Mapping) or not isinstance(candidates, (tuple, list)):
            continue
        validate_candidate_evidence_graph_projection(graph)
        ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, Mapping) and item.get("candidate_id")]
        # A deterministic plugin may already emit a fully disposed graph. For
        # an interactive Run, however, the committed DomainResult is the
        # durable coverage boundary.  Once the WorkItem has a Decision, all
        # scanner candidates have passed through the semantic review boundary;
        # retaining the immutable scanner graph's pending flag here produced
        # a contradictory terminal report (confirmed/suppressed candidates
        # alongside a graph claiming that every candidate was still pending).
        work_item_id = packet.work_item.work_item_id
        pending = [] if graph.get("coverageComplete") is True or work_item_id in decided_work_items else list(ids)
        item_covered = len(ids) - len(pending)
        total += len(ids)
        covered += item_covered
        pending_ids.extend(pending)
        by_item.append({
            "workItemId": packet.work_item.work_item_id,
            "candidateCount": len(ids),
            "coveredCandidateCount": item_covered,
            "pendingCandidateIds": pending,
            "coverageComplete": not pending,
        })
    return {
        "candidateCount": total,
        "coveredCandidateCount": covered,
        "pendingCandidateIds": pending_ids,
        "coverageComplete": not pending_ids,
        "workItems": by_item,
    }


def _proposal(value: Mapping[str, Any], check_id: str, check_version: str) -> DecisionProposal:
    return DecisionProposal(
        value.get("workItemId") or value.get("work_item_id") or "",
        value.get("checkId", check_id),
        value.get("checkVersion", check_version),
        value.get("result", ""),
        tuple(_finding(item) for item in value.get("findings", ())),
        value.get("reason") or value.get("decisionReason") or "",
        value.get("details", {}),
    )


def _decision_view(item: DecisionProposal) -> dict[str, Any]:
    return {
        "workItemId": item.work_item_id,
        "checkId": item.check_id,
        "checkVersion": item.check_version,
        "result": item.result,
        "reason": item.reason,
        "findings": {
            finding.dimension: {"status": finding.status, "reason": finding.reason}
            for finding in item.findings
        },
    }


def _terminal_result_views(
    status: str, work_items: Sequence[WorkItem], investigations: Sequence[InvestigationPacket],
    decisions: Sequence[DecisionProposal], failures: Sequence[WorkFailure], *,
    discovery_complete: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive a concise, domain-neutral terminal presentation from durable facts."""
    work_item_ids = {item.work_item_id for item in work_items}
    decided_ids = {item.work_item_id for item in decisions}
    failed_ids = {item.work_item_id for item in failures if item.work_item_id in work_item_ids}
    unprocessed = work_item_ids - decided_ids - failed_ids
    outcome_counts = {
        result: sum(item.result == result for item in decisions)
        for result in (
            "issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise",
        )
    }
    review_items = []
    for decision in decisions:
        if decision.result != "needs_review":
            continue
        gaps = {
            finding.dimension: {"status": finding.status, "reason": finding.reason}
            for finding in decision.findings
            if finding.status in {"unresolved", "blocked", "conflicted"}
        }
        dimensions = ", ".join(sorted(gaps)) or "the unresolved audit dimensions"
        review_items.append({
            "workItemId": decision.work_item_id,
            "checkId": decision.check_id,
            "reason": decision.reason,
            "gaps": gaps,
            "nextAction": (
                f"Resolve the missing or conflicting facts documented for {dimensions}, "
                "then start a new Run to obtain an evidence-backed decision."
            ),
        })
    for failure in failures:
        if failure.work_item_id not in work_item_ids or failure.work_item_id in decided_ids:
            continue
        review_items.append({
            "workItemId": failure.work_item_id,
            "checkId": failure.check_id,
            "reason": failure.message,
            "gaps": {
                "contractBoundary": {
                    "status": "blocked",
                    "reason": failure.message,
                    "code": failure.code,
                },
            },
            "nextAction": (
                "Resolve the recorded contract or input blocker, then start a new "
                "Run for this WorkItem; this Run contains no fabricated Decision."
            ),
        })

    discovered = len(work_item_ids)
    inspected = len({item.work_item.work_item_id for item in investigations})
    decided = len(decided_ids)
    failure_count = len(failures)
    needs_review_count = len(review_items)
    packet_by_id = {
        item.work_item.work_item_id: item for item in investigations
    }
    actionability_statuses: list[str] = []
    remediation_count = 0
    if status != "failed":
        for decision in decisions:
            packet = packet_by_id.get(decision.work_item_id)
            if packet is None:
                continue
            delivery_status, remediations = extract_result_delivery(decision, packet)
            actionability_statuses.append(delivery_status)
            remediation_count += len(remediations)
    actionability = (
        "not_declared" if not actionability_statuses or "not_declared" in actionability_statuses
        else "partial" if "partial" in actionability_statuses
        else "complete"
    )
    if status == "failed":
        message = (
            f"The Run failed with {failure_count} recorded failure(s). "
            "No formal conclusion from this Run is valid."
        )
        next_action = (
            "Resolve the recorded failures, then start a new Run; "
            "do not use this Run as a formal conclusion."
        )
    elif status == "partial":
        incomplete_scope = 0 if discovery_complete else 1
        message = (
            f"The Run closed with {decided} valid recorded decision(s), "
            f"{len(unprocessed)} unfinished WorkItem(s), and "
            f"{failure_count} recorded failure(s)."
        )
        if incomplete_scope:
            message += " Scope discovery did not finish, so additional WorkItems may exist."
        next_action = (
            "Review unfinished WorkItems and failures, resolve the stated blockers, "
            "then start a new Run for the unfinished scope."
        )
    elif needs_review_count:
        message = (
            f"The Run completed its declared scope; {needs_review_count} decision(s) "
            "still require review before they can be treated as resolved."
        )
        if remediation_count:
            message += f" {remediation_count} confirmed remediation item(s) also require changes."
        next_action = (
            f"Address the {remediation_count} confirmed remediation item(s), then review "
            f"the {needs_review_count} unresolved decision(s) and their missing facts."
            if remediation_count else
            f"Review the {needs_review_count} needs-review decision(s) and resolve "
            "the concrete gaps listed for each WorkItem."
        )
    elif outcome_counts["issue_found"]:
        message = (
            f"The Run completed its declared scope and found "
            f"{outcome_counts['issue_found']} issue decision(s)."
        )
        next_action = "Review the issue decisions and their evidence-backed reasons."
    else:
        message = f"The Run completed its declared scope with {decided} recorded decision(s)."
        next_action = "No further audit action is required."

    overview = {
        "schemaVersion": "1.0.0",
        "status": status,
        "conclusionValidity": "invalidated" if status == "failed" else "valid",
        "message": message,
        "coverage": {
            "discoveryComplete": discovery_complete,
            "discovered": discovered,
            "inspected": inspected,
            "decided": decided,
            "failed": len(failed_ids),
            "unprocessed": len(unprocessed),
            "complete": status == "completed" and not unprocessed and not failures,
        },
        "outcomes": outcome_counts if status != "failed" else {
            result: 0 for result in outcome_counts
        },
        "needsReviewCount": needs_review_count if status != "failed" else 0,
        "remediationCount": remediation_count if status != "failed" else 0,
        "actionability": actionability if status != "failed" else "not_declared",
        "failureCount": failure_count,
        "invalidatedDecisionCount": decided if status == "failed" else 0,
        "nextAction": next_action,
    }
    visible_decisions = [] if status == "failed" else [_decision_view(item) for item in decisions]
    return overview, visible_decisions, [] if status == "failed" else review_items


def _work_item(item: WorkItem) -> dict[str, Any]:
    return {
        "workItemId": item.work_item_id,
        "kind": item.kind,
        "identity": item.identity,
        "stateDigest": item.state_digest,
        "metadata": _plain(item.metadata),
    }


def _packet(packet: InvestigationPacket, *, include_evidence: bool = True,
            evidence_ids: set[str] | None = None) -> dict[str, Any]:
    evidence = packet.evidence if include_evidence else ()
    if evidence_ids is not None:
        evidence = tuple(item for item in packet.evidence if item.evidence_id in evidence_ids)
    return {
        "workItem": _work_item(packet.work_item),
        "checkId": packet.check_id,
        "checkVersion": packet.check_version,
        "dimensions": [{
            "name": item.name,
            "observations": list(item.observations),
            "evidenceRefs": list(item.evidence_refs),
            "candidateStatus": item.candidate_status,
        } for item in packet.dimensions],
        "evidenceIndex": [{
            "evidenceId": item.evidence_id,
            "kind": item.kind,
            "sourceIdentity": item.source_identity,
        } for item in packet.evidence],
        "evidence": [{
            "evidenceId": item.evidence_id,
            "workItemId": item.work_item_id,
            "checkId": item.check_id,
            "checkVersion": item.check_version,
            "kind": item.kind,
            "sourceIdentity": item.source_identity,
            "payload": _plain(item.payload),
        } for item in evidence],
        "recoveryStatus": packet.recovery_status,
        "caseRef": packet.case_ref,
        "metadata": _plain(packet.metadata),
    }


def _domain_packet(packet: InvestigationPacket) -> dict[str, Any]:
    """Project an InvestigationPacket without Host identity fields."""
    value = _packet(packet, include_evidence=True)
    value.pop("workItem", None)
    value.pop("checkId", None)
    value.pop("checkVersion", None)
    value.pop("recoveryStatus", None)
    value.pop("caseRef", None)
    value["subject"] = {
        "kind": packet.work_item.kind,
        "identity": packet.work_item.identity,
        "metadata": _plain(packet.work_item.metadata),
    }
    value["evidenceIndex"] = [{
        "evidenceId": item.evidence_id,
        "kind": item.kind,
        "sourceIdentity": item.source_identity,
    } for item in packet.evidence]
    value["evidence"] = [{
        "evidenceId": item.evidence_id,
        "kind": item.kind,
        "sourceIdentity": item.source_identity,
        "payload": _plain(item.payload),
    } for item in packet.evidence]
    return value


def _domain_agent_view(
    packet: InvestigationPacket, registry: EvidenceHandleRegistry,
    *, domain_data: Mapping[str, Any] | Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Build the minimal Agent view without platform Evidence identities.

    Domain plugins may expose a compact, domain-owned projection through
    ``build_domain_agent_data``.  The immutable InvestigationPacket remains
    the source of truth; this projection only controls what crosses the
    Agent-facing transport boundary.  The fallback remains domain-owned data
    and never exposes the historical platform investigation projection.
    """
    dimensions = []
    for item in packet.dimensions:
        refs = [
            handle for reference in item.evidence_refs
            if (handle := registry.handle_for_reference(reference)) is not None
        ]
        dimensions.append({
            "name": item.name,
            "observations": list(item.observations),
            "evidenceRefs": refs,
            "candidateStatus": item.candidate_status,
        })
    if domain_data is None:
        evidence_payloads = [
            _agent_safe_value(item.payload, registry)
            for item in packet.evidence
        ]
    else:
        evidence_payloads = _agent_safe_value(domain_data, registry)
    evidence_page = registry.page(page_size=INITIAL_SEMANTIC_EVIDENCE_PREVIEW_SIZE)
    view = {
        "subject": {"kind": packet.work_item.kind},
        "dimensions": dimensions,
        "evidence": evidence_page["items"],
        "evidencePaging": {
            "pageSize": INITIAL_SEMANTIC_EVIDENCE_PREVIEW_SIZE,
            "total": evidence_page["page"]["total"],
            "nextCursor": evidence_page["nextCursor"],
            "expandTool": "expand_semantic_evidence",
        },
        "domainData": evidence_payloads,
    }
    return _bound_agent_view(view)


_AGENT_PRIVATE_KEYS = frozenset({
    "runid", "workitemid", "taskdigest", "contractdigest",
    "evidenceid", "sourcechunkid", "sourcedigest", "documentpath", "path",
    "startline", "endline", "line", "sourceidentity", "revision",
    "runrevision", "checkid", "checkversion",
})


def _agent_safe_value(value: Any, registry: EvidenceHandleRegistry) -> Any:
    """Project domain material while removing Host identity and location data."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        internal_ref = (
            value.get("source_chunk_id") or value.get("sourceChunkId")
            or value.get("evidence_id") or value.get("evidenceId")
        )
        if isinstance(internal_ref, str):
            handle = registry.handle_for_reference(internal_ref)
            if handle is not None:
                result["evidenceRef"] = handle
        for key, item in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in _AGENT_PRIVATE_KEYS:
                continue
            result[str(key)] = _agent_safe_value(item, registry)
        return result
    if isinstance(value, (tuple, list)):
        return [_agent_safe_value(item, registry) for item in value]
    if isinstance(value, str):
        handle = registry.handle_for_reference(value)
        return handle if handle is not None else value
    return value


def _compact_agent_value(
    value: Any, *, string_limit: int = AGENT_VALUE_STRING_LIMIT,
    array_limit: int = AGENT_VALUE_ARRAY_LIMIT, depth: int = 0,
) -> Any:
    """Bound generic domain material without domain-specific plugin code.

    The Host owns this projection.  It keeps the durable packet untouched and
    makes truncation explicit so a plugin never has to implement its own
    source/document pagination merely to stay inside an Agent context.
    """
    if depth > 12:
        return {"truncated": True, "reason": "maximum projection depth"}
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        return value[:string_limit] + "… [truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[str(key)] = _compact_agent_value(
                item, string_limit=string_limit, array_limit=array_limit,
                depth=depth + 1,
            )
        return result
    if isinstance(value, (tuple, list)):
        values = [
            _compact_agent_value(
                item, string_limit=string_limit, array_limit=array_limit,
                depth=depth + 1,
            )
            for item in value[:array_limit]
        ]
        if len(value) > array_limit:
            values.append({
                "truncated": True,
                "omittedCount": len(value) - array_limit,
            })
        return values
    return value


def _bound_agent_view(
    view: Mapping[str, Any], *, payload_limit: int = AGENT_TASK_PAYLOAD_LIMIT,
) -> dict[str, Any]:
    """Return a deterministic, size-bounded Agent view.

    We progressively tighten only the presentation projection.  If a plugin
    supplies unusually large domain data, the Host still returns a valid task
    rather than failing the Run or requiring plugin-owned pagination.
    """
    string_limit = AGENT_VALUE_STRING_LIMIT
    array_limit = AGENT_VALUE_ARRAY_LIMIT
    # Four tightening passes are enough to move from the normal projection to
    # the safety fallback; keep this explicitly bounded for resilience scans
    # and to avoid a malformed value creating an accidental infinite loop.
    for _ in range(8):
        candidate = _compact_agent_value(
            view, string_limit=string_limit, array_limit=array_limit,
        )
        encoded = json.dumps(
            candidate, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= payload_limit:
            return candidate
        if string_limit > 128 or array_limit > 20:
            string_limit = max(128, string_limit // 2)
            array_limit = max(20, array_limit // 2)
            continue
        # Preserve the semantic dimensions and opaque evidence handles even
        # when a hostile/accidental domain payload remains oversized.
        return {
            "subject": candidate.get("subject", {}),
            "dimensions": candidate.get("dimensions", []),
            "evidence": candidate.get("evidence", []),
            "evidencePaging": candidate.get("evidencePaging", {
                "total": 0,
                "nextCursor": None,
                "expandTool": "expand_semantic_evidence",
            }),
            "domainData": {
                "truncated": True,
                "reason": "Agent task payload limit exceeded; expand immutable evidence on demand",
            },
        }
    # The loop above always returns, but keep a deterministic defensive
    # fallback if its bounds are changed in a future revision.
    return {
        "subject": {"kind": "unknown"},
        "dimensions": [],
        "evidence": [],
        "evidencePaging": {
            "total": 0,
            "nextCursor": None,
            "expandTool": "expand_semantic_evidence",
        },
        "domainData": {
            "truncated": True,
            "reason": "Agent task payload limit exceeded",
        },
    }


def _bound_semantic_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce the budget on the complete semantic task, not just evidence."""
    candidate = _plain(task)
    encoded = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= AGENT_TASK_PAYLOAD_LIMIT:
        return candidate
    # Keep the executable contract intact and spend the remaining budget on
    # the Agent view.  Contract metadata is normally small; if a plugin ships
    # an unusually large contract, the final fallback remains explicit rather
    # than silently emitting an unbounded task.
    without_view = dict(candidate)
    view = without_view.pop("agentView", {})
    base_size = len(json.dumps(
        without_view, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    remaining = max(1024, AGENT_TASK_PAYLOAD_LIMIT - base_size - 128)
    if isinstance(view, Mapping):
        without_view["agentView"] = _bound_agent_view(view, payload_limit=remaining)
    else:
        without_view["agentView"] = {
            "subject": {"kind": "unknown"},
            "dimensions": [], "evidence": [],
            "domainData": {"truncated": True, "reason": "Agent task payload limit exceeded"},
        }
    candidate = without_view
    encoded = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= AGENT_TASK_PAYLOAD_LIMIT:
        return candidate
    # The domain contract is executable and cannot be truncated.  Preserve it
    # and expose a compact, explicit Agent view; future contract releases must
    # keep their own schema/instructions bounded by the same platform budget.
    candidate["agentView"] = {
        "subject": {"kind": "unknown"},
        "dimensions": [], "evidence": [],
        "evidencePaging": {"total": 0, "nextCursor": None, "expandTool": "expand_semantic_evidence"},
        "domainData": {"truncated": True, "reason": "Agent task payload limit exceeded"},
    }
    final_size = len(json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    if final_size > AGENT_TASK_PAYLOAD_LIMIT:
        raise PlatformContractError(
            "PLUGIN_SEMANTIC_TASK_TOO_LARGE",
            "The plugin domain contract exceeds the Host semantic task payload limit",
        )
    return candidate


def _collection_group_key(collection_id: str, values: Mapping[str, Any]) -> str:
    canonical = json.dumps(_plain(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{collection_id}\x1f{canonical}".encode("utf-8")).hexdigest()[:16]
    return f"group:{digest}"


def _resolve_json_pointer(payload: Any, pointer: str) -> Any:
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise PlatformContractError(
            "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer must be empty or start with '/'",
        )
    current = payload
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer does not resolve",
                )
            current = current[token]
        elif isinstance(current, (tuple, list)):
            try:
                index = int(token)
            except ValueError:
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer contains a nonnumeric array index",
                ) from None
            if index < 0 or index >= len(current):
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer is outside an array",
                )
            current = current[index]
        else:
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer traverses a scalar value",
            )
    return current


def _evidence_collections(packet: InvestigationPacket) -> dict[str, dict[str, Any]]:
    """Validate and resolve declarative, domain-neutral Evidence collections."""
    raw_descriptors = packet.metadata.get("evidenceCollections", ())
    if not isinstance(raw_descriptors, (tuple, list)):
        raise PlatformContractError(
            "INVALID_EVIDENCE_COLLECTION", "Investigation evidenceCollections must be an array",
        )
    evidence_by_id = {item.evidence_id: item for item in packet.evidence}
    collections: dict[str, dict[str, Any]] = {}
    for descriptor in raw_descriptors:
        if not isinstance(descriptor, Mapping):
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Each Evidence collection descriptor must be an object",
            )
        collection_id = descriptor.get("collectionId")
        evidence_id = descriptor.get("evidenceId")
        pointer = descriptor.get("jsonPointer")
        item_id_field = descriptor.get("itemIdField")
        group_by = descriptor.get("groupBy", ())
        review_required = descriptor.get("reviewRequired", True)
        if not all(isinstance(value, str) and value for value in (
            collection_id, evidence_id, item_id_field,
        )) or not isinstance(pointer, str):
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection identity, Evidence, pointer, and item ID field are required",
            )
        if collection_id in collections:
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection IDs must be unique within an InvestigationPacket",
            )
        if not isinstance(review_required, bool):
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection reviewRequired must be boolean",
            )
        if evidence_id not in evidence_by_id:
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection references unknown Evidence",
            )
        if not isinstance(group_by, (tuple, list)) or any(
            not isinstance(field, str) or not field for field in group_by
        ) or len(set(group_by)) != len(group_by):
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection groupBy must contain unique nonempty field names",
            )
        items = _resolve_json_pointer(evidence_by_id[evidence_id].payload, pointer)
        if not isinstance(items, (tuple, list)):
            raise PlatformContractError(
                "INVALID_EVIDENCE_COLLECTION", "Evidence collection jsonPointer must resolve to an array",
            )
        item_ids: list[str] = []
        seen_item_ids: set[str] = set()
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, Mapping):
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection items must be objects",
                )
            item_id = item.get(item_id_field)
            if not isinstance(item_id, str) or not item_id:
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection items require stable nonempty string IDs",
                )
            if item_id in seen_item_ids:
                raise PlatformContractError(
                    "INVALID_EVIDENCE_COLLECTION", "Evidence collection item IDs must be unique",
                )
            item_ids.append(item_id)
            seen_item_ids.add(item_id)
            if group_by:
                if any(field not in item for field in group_by):
                    raise PlatformContractError(
                        "INVALID_EVIDENCE_COLLECTION", "Evidence collection grouping field is missing from an item",
                    )
                values = {field: _plain(item[field]) for field in group_by}
                try:
                    json.dumps(values, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    raise PlatformContractError(
                        "INVALID_EVIDENCE_COLLECTION", "Evidence collection grouping values must be JSON-compatible",
                    ) from None
                group_key = _collection_group_key(collection_id, values)
                group = groups.setdefault(group_key, {"groupKey": group_key, "values": values, "itemIds": []})
                group["itemIds"].append(item_id)
        collections[collection_id] = {
            "collectionId": collection_id,
            "evidenceId": evidence_id,
            "jsonPointer": pointer,
            "itemIdField": item_id_field,
            "groupBy": tuple(group_by),
            "reviewRequired": review_required,
            "items": tuple(items),
            "itemIds": tuple(item_ids),
            "groups": groups,
        }
    return collections


def _collection_index(collections: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "collectionId": collection["collectionId"],
        "evidenceId": collection["evidenceId"],
        "itemCount": len(collection["itemIds"]),
        "itemIdField": collection["itemIdField"],
        "groupBy": list(collection["groupBy"]),
        "reviewRequired": collection["reviewRequired"],
        "groupCount": len(collection["groups"]),
        "groups": [{
            "groupKey": group["groupKey"],
            "values": _plain(group["values"]),
            "count": len(group["itemIds"]),
        } for group in collection["groups"].values()],
    } for collection in collections.values()]


class InteractivePluginController:
    """Run the generic interactive lifecycle for one registered plugin.

    The controller is intentionally small: it owns selection, scope
    validation, lifecycle routing and response shaping, while
    :class:`InteractivePlatformRun` remains the source of truth for gates and
    durable decisions. A runtime resolver is injected by a product adapter
    (for example, a browser or API runtime); no runtime is assumed here.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        output_root: str | Path = "./assayer-output",
        *,
        runtime_resolver: Callable[[PluginRegistration, Any], Any] | None = None,
        provider_runtime: Any = None,
        provider_runtime_resolver: Callable[[PluginRegistration, Any, Any], Any] | None = None,
        capabilities_resolver: Callable[[PluginRegistration, Any], Sequence[str]] | None = None,
        provider_registry: ProviderRegistry | None = None,
        platform_profile: CapabilityProfile | Callable[[PluginRegistration, Any], CapabilityProfile] | None = None,
        user_profile: CapabilityProfile | Callable[[PluginRegistration, Any], CapabilityProfile] | None = None,
    ) -> None:
        self.registry = registry
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.runtime_resolver = runtime_resolver
        # Provider runtimes are Host-owned adapters (for example a
        # BrowserSnapshotSource).  They are injected as opaque objects; the
        # controller never starts a browser or reaches into the runtime.
        self.provider_runtime = provider_runtime
        # Product adapters may need one provider source per Run (for example a
        # browser session bound to the requested URL).  Resolve this lazily
        # after the Host has selected the Check and validated the business
        # scope; the returned object remains opaque to the controller.
        self.provider_runtime_resolver = provider_runtime_resolver
        self.capabilities_resolver = capabilities_resolver
        self.provider_registry = provider_registry
        self.platform_profile = platform_profile
        self.user_profile = user_profile
        self._controller_epoch = uuid.uuid4().hex
        self._runs: dict[str, dict[str, Any]] = {}
        self._ownership_locks: dict[str, Any] = {}
        self._terminal_results: dict[str, dict[str, Any]] = {}
        self._resume_error: PlatformContractError | None = None
        self._terminal_error: PlatformContractError | None = None
        self._restore_latest_terminal_result()

    def _bind_capability_provider(
        self,
        run_id: str,
        registration: PluginRegistration,
        check: Any,
        scope: Any,
        host_capabilities: Sequence[str],
    ) -> tuple[BoundCapabilityProvider | None, frozenset[str]]:
        """Bind a negotiated provider when the Check requires provider capabilities.

        Returns the bound provider (or ``None`` when no provider registry is
        configured) and the effective capability set for the Run context.  A
        required capability with no supplying provider fails closed with
        ``PROVIDER_NOT_FOUND`` instead of degrading to ``needs_review``.
        """
        required = frozenset(check.required_capabilities)
        host_caps = frozenset(host_capabilities)
        provider_required = required & frozenset(getattr(registration, "provider_capabilities", ()))
        if not provider_required:
            return None, required | host_caps
        if self.provider_registry is None:
            # A declared provider capability must never degrade to a Host direct
            # grant: without a provider registry the Host cannot bind the
            # provider, so fail closed before any Run or ledger is created.
            raise PlatformContractError(
                "PROVIDER_NOT_FOUND",
                "The Check requires provider capabilities but the Host has no provider registry configured",
            )
        platform_profile = self._resolve_profile(self.platform_profile, registration, check)
        if platform_profile is None:
            raise PlatformContractError(
                "PROVIDER_NOT_FOUND",
                "The Check requires provider capabilities but no platform capability profile is configured",
            )
        provider_registration = self.provider_registry.select_for_capabilities(
            sorted(provider_required),
        )
        provider_scope: Mapping[str, Any] = {}
        registration_resolver = getattr(registration, "provider_scope_resolver", None)
        resolved = registration_resolver(scope, check) if registration_resolver is not None else None
        if resolved is not None:
            if not isinstance(resolved, Mapping):
                raise PlatformContractError(
                    "PROVIDER_SCOPE_INVALID",
                    "Provider scope resolver must return a mapping or None",
                )
            provider_scope = resolved
        negotiation = CapabilityNegotiator().negotiate(
            provider_registration,
            sorted(provider_required),
            platform_profile,
            user_profile=self._resolve_profile(self.user_profile, registration, check),
            scope=provider_scope,
        )
        resolved_provider_runtime = self.provider_runtime
        if self.provider_runtime_resolver is not None:
            try:
                resolved_provider_runtime = self.provider_runtime_resolver(
                    registration, check, scope,
                )
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PROVIDER_RUNTIME_UNAVAILABLE",
                    "The Host could not create the provider runtime for this Run",
                ) from error
        bound = BoundCapabilityProvider(
            provider_registration, negotiation, run_id=run_id, scope=provider_scope,
            runtime=resolved_provider_runtime,
        )
        return bound, host_caps | frozenset(negotiation.granted)

    @staticmethod
    def _resolve_profile(
        value: CapabilityProfile | Callable[[PluginRegistration, Any], CapabilityProfile] | None,
        registration: PluginRegistration,
        check: Any,
    ) -> CapabilityProfile | None:
        if value is None:
            return None
        if callable(value):
            return value(registration, check)
        return value

    @staticmethod
    def _close_bound_provider(state: Mapping[str, Any] | None) -> None:
        if not isinstance(state, Mapping):
            return
        provider = state.get("capability_provider")
        if provider is not None:
            provider.close()

    @property
    def operations(self) -> tuple[str, ...]:
        return INTERACTIVE_OPERATIONS

    @property
    def active_run_id(self) -> str | None:
        return next(iter(self._runs), None)

    @property
    def terminal_run_id(self) -> str | None:
        return next(iter(self._terminal_results), None)

    @property
    def resume_error(self) -> PlatformContractError | None:
        return self._resume_error

    @property
    def terminal_error(self) -> PlatformContractError | None:
        return self._terminal_error

    def start(
        self,
        *,
        plugin_id: str,
        check_id: str,
        scope: Any,
        check_version: str | None = None,
        run_id: str | None = None,
        runtime: Any = None,
        capabilities: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        registration = self.registry.select(
            plugin_id=plugin_id, check_id=check_id,
            check_ref=(check_id, check_version) if check_version else None,
        )
        if "interactive" not in registration.execution_modes:
            raise PlatformContractError(
                "PLUGIN_EXECUTION_MODE_UNSUPPORTED",
                "The selected plugin does not declare interactive execution",
            )
        scope_error = next(Draft202012Validator(registration.scope_schema).iter_errors(scope), None)
        if scope_error is not None:
            raise PlatformContractError(
                "INVALID_SCOPE", "Plugin business scope does not satisfy its registered schema",
            )
        matches = tuple(item for item in registration.manifest.checks if item.check_id == check_id)
        if check_version is None:
            if len(matches) != 1:
                raise PlatformContractError("AMBIGUOUS_CHECK", f"Check version is required for: {check_id}")
            check = matches[0]
        else:
            check = self.registry.check(registration, (check_id, check_version))
        domain_result_contract = registration.domain_result_contract_for(check.ref)
        common_review_mode = "common_review" in registration.result_features
        if common_review_mode and domain_result_contract is not None:
            raise PlatformContractError(
                "PLUGIN_SEMANTIC_CONTRACT_CONFLICT",
                "A common-review Check cannot also publish a complete DomainResultContract",
            )
        if domain_result_contract is None and not common_review_mode:
            raise PlatformContractError(
                "PLUGIN_DOMAIN_RESULT_CONTRACT_REQUIRED",
                "Interactive Checks must use common review or publish a DomainResultContract",
            )
        # Negotiate before creating a plugin, claiming ownership, or writing
        # a ledger.  Incompatible combinations therefore leave no partial Run.
        compatibility = negotiate_plugin_compatibility(registration.compatibility)
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        if run_id in self._runs:
            raise PlatformContractError("RUN_CONFLICT", "An interactive Run with this ID already exists")
        existing_root = self.output_root / run_id
        if existing_root.exists() and any(existing_root.iterdir()):
            raise PlatformContractError(
                "RUN_CONFLICT", "An interactive Run with this ID already has durable state; use explicit resume",
            )
        requested_caps = tuple(capabilities) if capabilities is not None else tuple(
            self.capabilities_resolver(registration, scope)
            if self.capabilities_resolver is not None else registration.capabilities
        )
        bound_provider, effective_caps = self._bind_capability_provider(
            run_id, registration, check, scope, requested_caps,
        )
        if bound_provider is not None:
            resolved_runtime = bound_provider
            caps = tuple(sorted(effective_caps))
            context = PlatformContext(
                run_id, effective_caps, dict(bound_provider.context.limits),
            )
        else:
            resolved_runtime = runtime
            if self.runtime_resolver is not None:
                resolved_runtime = self.runtime_resolver(registration, scope)
            caps = requested_caps
            context = PlatformContext(run_id, frozenset(caps))
        try:
            plugin = registration.create_plugin(resolved_runtime)
        except PlatformContractError:
            self._close_bound_provider({"capability_provider": bound_provider})
            raise
        except Exception as error:
            self._close_bound_provider({"capability_provider": bound_provider})
            raise PlatformContractError("PLUGIN_INITIALIZATION_FAILED", "Interactive plugin could not be initialized") from error
        if getattr(plugin, "manifest", None) != registration.manifest:
            self._close_bound_provider({"capability_provider": bound_provider})
            raise PlatformContractError("PLUGIN_IDENTITY_MISMATCH", "Plugin factory returned different registered metadata")
        if domain_result_contract is not None and not common_review_mode:
            missing = [
                name for name in ("map_domain_result",)
                if not callable(getattr(plugin, name, None))
            ]
            if missing:
                # This is a plugin implementation defect, not an Agent input
                # problem.  Detect it before ownership/ledger creation so a
                # malformed release cannot reach a semantic boundary and
                # consume the Agent correction budget.
                self._close_bound_provider({"capability_provider": bound_provider})
                raise PlatformContractError(
                    "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                    "The domain-result plugin is missing required operation(s): "
                    + ", ".join(missing),
                )
        run_root = self.output_root / run_id
        store = JsonPlatformLedgerStore(run_root)
        coverage_store = JsonCoverageLedgerStore(run_root)
        self._claim_ownership(run_id)
        try:
            run = InteractivePlatformSession(registration.manifest).begin(
                context, scope, check.check_id, check.version, store,
            )
            self._runs[run_id] = {
                "registration": registration,
                "domain_result_contract": domain_result_contract,
                "review_mode": "common_review" if common_review_mode else "domain_result",
                "review_coordinator": None,
                "scope": scope,
                "context": context,
                "plugin": plugin,
                "run": run,
                "committer": registration.create_committer(resolved_runtime),
                "evidence_collections": {},
                "inspect_batch_size": registration.manifest.execution_profile.inspect_batch_size,
                "discovery_complete": False,
                "active_semantic_task": None,
                "active_semantic_task_payload": None,
                "task_context": None,
                "evidence_handle_registry": None,
                "compatibility": compatibility,
                "capability_provider": bound_provider,
                "coverage_store": coverage_store,
                "coverage_ledger": None,
            }
            self._write_resume_descriptor(run_id, registration, scope, caps)
        except Exception:
            self._close_bound_provider({"capability_provider": bound_provider})
            self._runs.pop(run_id, None)
            self._release_ownership(run_id, reason="start_failed")
            raise
        result = {
            "plugin": _plugin_identity(registration),
            "check": {"checkId": check.check_id, "version": check.version},
        }
        result["compatibility"] = compatibility.as_dict()
        return self._response(run_id, "started", result)

    def _terminal_pointer_path(self) -> Path:
        return self.output_root / ".latest-plugin-run.json"

    def _write_terminal_pointer(self, run_id: str) -> None:
        destination = self._terminal_pointer_path()
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            temporary.write_text(json.dumps({
                "schemaVersion": "1.0.0", "runId": run_id,
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        except OSError as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run acknowledgement could not be durably published",
            ) from error

    def _hydrate_terminal_result(
        self, run_id: str, ledger: Mapping[str, Any],
    ) -> None:
        status = ledger.get("status")
        if status not in {"completed", "partial", "failed"}:
            raise PlatformContractError("RESULT_NOT_AVAILABLE", "Run ledger is not terminal")
        result_path = self.output_root / run_id / "result-summary.json"
        pending_result_path = self.output_root / run_id / "result-summary.pending.json"
        source_path = result_path if result_path.is_file() else pending_result_path
        try:
            value = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run result artifact is unavailable or invalid",
            ) from error
        full_result = value.get("result")
        if (
            value.get("runId") != run_id or value.get("status") != status
            or not isinstance(full_result, Mapping)
        ):
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run result identity does not match its ledger",
            )
        audit_report = full_result.get("auditReport")
        audit_report_path = self.output_root / run_id / f"{run_id}.audit-report.md"
        if not isinstance(audit_report, str):
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run formal audit report is unavailable",
            )
        try:
            published_report = audit_report_path.read_text(encoding="utf-8")
        except OSError as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run formal audit report is unavailable",
            ) from error
        if published_report != audit_report:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED",
                "Terminal Run formal audit report differs from its result payload",
            )
        canonical_path = self.output_root / run_id / f"{run_id}.canonical-result.json"
        ledger_path = self.output_root / run_id / f"{run_id}.platform-ledger.json"
        try:
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            validate_canonical_result(
                canonical,
                ledger_bytes=ledger_path.read_bytes(),
                expected_run_id=run_id,
                expected_status=str(status),
            )
        except (OSError, ValueError, PlatformContractError) as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED",
                "Canonical terminal result is unavailable, invalid, or detached from its ledger",
            ) from error
        expected_digest = hashlib.sha256(json.dumps(
            full_result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if value.get("sourceDigest") != expected_digest:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run result digest does not match its payload",
            )
        if source_path == pending_result_path:
            try:
                pending_result_path.replace(result_path)
            except OSError as error:
                raise PlatformContractError(
                    "RESULT_PUBLICATION_FAILED", "Terminal Run result could not be promoted",
                ) from error
        document = StagedResultDocument({
            key: item for key, item in full_result.items()
            if key in {
                "resultOverview", "summary", "decisions", "reviewItems",
                "evidenceGraph", "failures", "auditReport",
            }
        }, source_digest=expected_digest)
        workflow = ledger.get("workflow")
        if not isinstance(workflow, Mapping) or workflow.get("state") != status:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Terminal Run workflow does not match its result",
            )
        run_revision = len(tuple(ledger.get("operations", ())))
        result_payload = dict(document.overview)
        metrics = full_result.get("metrics", {})
        result_payload["metrics"] = dict(metrics) if isinstance(metrics, Mapping) else {}
        artifacts = full_result.get("artifacts", ())
        result_payload["artifacts"] = list(dict.fromkeys([
            *(artifacts if isinstance(artifacts, (tuple, list)) else ()), result_path.name,
        ]))
        canonical_name = f"{run_id}.canonical-result.json"
        result_payload["canonicalResult"] = canonical_name
        result_payload["resultDelivery"] = document.descriptor()
        events = ledger.get("events", ())
        boundary_event = next((
            event for event in reversed(events)
            if isinstance(event, Mapping)
            and event.get("name") in {"platform.workflow.transition", "platform.run.terminal"}
        ), None)
        result_payload["progress"] = workflow_progress(
            workflow,
            discovered=int(result_payload["metrics"].get("discovered", 0) or 0),
            inspected=int(result_payload["metrics"].get("inspected", 0) or 0),
            decisions=int(result_payload["metrics"].get("decisionsCommitted", 0) or 0),
            entered_at=(
                str(boundary_event.get("occurred_at"))
                if boundary_event is not None and boundary_event.get("occurred_at") is not None
                else None
            ),
        )
        response = self._response(
            run_id, str(status), result_payload, workflow=dict(workflow),
            run_revision=run_revision,
        )
        operations = tuple(ledger.get("operations", ()))
        if operations and isinstance(operations[-1], Mapping):
            operation_id = operations[-1].get("operation_id")
            if isinstance(operation_id, str) and operation_id:
                response["operationId"] = operation_id
                response["replayed"] = False
                response["result"]["durableBoundary"]["lastOperationId"] = operation_id
        self._terminal_results = {run_id: {
            "document": document, "workflow": dict(workflow),
            "runRevision": run_revision, "response": response,
        }}

    def _restore_latest_terminal_result(self) -> None:
        pointer = self._terminal_pointer_path()
        if not pointer.is_file():
            return
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            run_id = str(value["runId"])
            ledger = JsonPlatformLedgerStore(self.output_root / run_id).load(run_id)
            if ledger is None:
                raise PlatformContractError("RESULT_NOT_AVAILABLE", "Latest terminal Run ledger is missing")
            self._hydrate_terminal_result(run_id, ledger)
        except PlatformContractError as error:
            self._terminal_error = error
        except Exception:
            self._terminal_error = PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Latest terminal Run could not be reconstructed",
            )

    def _write_resume_descriptor(
        self, run_id: str, registration: PluginRegistration, scope: Any,
        capabilities: Sequence[str],
    ) -> None:
        descriptor = {
            "schemaVersion": "1.0.0", "runId": run_id,
            "ownerEpoch": self._controller_epoch,
            "pluginId": registration.manifest.plugin_id,
            "pluginVersion": registration.manifest.version,
            "checkId": self._runs[run_id]["run"].check.check_id,
            "checkVersion": self._runs[run_id]["run"].check.version,
            "scope": _plain(scope), "capabilities": sorted(set(capabilities)),
            "semanticMode": self._runs[run_id].get("review_mode", "domain_result"),
        }
        domain_result_contract = self._runs[run_id].get("domain_result_contract")
        if domain_result_contract is not None:
            descriptor["domainResultContract"] = domain_result_contract.as_dict(include_digest=True)
        compatibility = self._runs[run_id].get("compatibility")
        if compatibility is not None:
            descriptor["compatibility"] = compatibility.as_dict()
        run_path = self.output_root / run_id / "platform-resume.json"
        try:
            temporary = run_path.with_name(run_path.name + ".tmp")
            temporary.write_text(
                json.dumps(descriptor, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(run_path)
        except OSError as error:
            raise PlatformContractError(
                "RUN_RESUME_PERSIST_FAILED", "Interactive Run resume metadata could not be persisted",
            ) from error

    def _owner_path(self, run_id: str) -> Path:
        return self.output_root / run_id / "platform-owner.json"

    def _lock_path(self, run_id: str) -> Path:
        return self.output_root / run_id / "platform-owner.lock"

    def _claim_ownership(self, run_id: str) -> None:
        validate_entity_id(run_id, label="Run ID", code="INVALID_RUN_ID")
        if run_id in self._ownership_locks:
            return
        run_root = self.output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        try:
            handle = self._lock_path(run_id).open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise PlatformContractError(
                "RUN_ALREADY_ACTIVE",
                f"Run {run_id} is owned by another live Host; continue there or close it before resuming",
            ) from error
        except OSError as error:
            if "handle" in locals():
                handle.close()
            raise PlatformContractError(
                "RUN_OWNERSHIP_FAILED", "Run ownership could not be acquired",
            ) from error
        owner = RunOwnership(run_id, self._controller_epoch, os.getpid())
        destination = self._owner_path(run_id)
        try:
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_text(
                json.dumps(owner.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as error:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise PlatformContractError(
                "RUN_OWNERSHIP_FAILED", "Run ownership metadata could not be persisted",
            ) from error
        self._ownership_locks[run_id] = handle

    def _release_ownership(self, run_id: str, *, reason: str) -> None:
        handle = self._ownership_locks.pop(run_id, None)
        if handle is None:
            return
        destination = self._owner_path(run_id)
        if destination.is_file():
            try:
                value = json.loads(destination.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                value = {}
            try:
                owner = RunOwnership.from_mapping(value)
            except PlatformContractError:
                owner = None
            if owner is not None and owner.owner_epoch == self._controller_epoch:
                try:
                    temporary = destination.with_name(destination.name + ".tmp")
                    temporary.write_text(
                        json.dumps(owner.released_record(reason).as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    temporary.replace(destination)
                except OSError as error:
                    _LOG.debug("Run ownership release metadata could not be persisted", exc_info=error)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def resume(self, run_id: str) -> dict[str, Any]:
        """Explicitly claim and restore one durable interactive Run."""
        if self._runs:
            raise PlatformContractError("RUN_CONFLICT", "This Host already owns an interactive plugin Run")
        validate_entity_id(run_id, label="Run ID", code="INVALID_RUN_ID")
        descriptor_path = self.output_root / run_id / "platform-resume.json"
        if not descriptor_path.is_file():
            raise PlatformContractError(
                "RUN_RESUME_FAILED", "No recoverable Run exists for the requested Run ID",
            )
        self._claim_ownership(run_id)
        bound_provider: BoundCapabilityProvider | None = None
        try:
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            if descriptor.get("runId") != run_id:
                raise PlatformContractError("RUN_MISMATCH", "Run path and resume descriptor differ")
            run_root = self.output_root / run_id
            store = JsonPlatformLedgerStore(run_root)
            coverage_store = JsonCoverageLedgerStore(run_root)
            coverage_ledger = coverage_store.load(run_id)
            ledger = store.load(run_id)
            if ledger is None:
                raise PlatformContractError("RUN_RESUME_FAILED", "The requested Run ledger is missing")
            if ledger.get("status") != "running":
                self._hydrate_terminal_result(run_id, ledger)
                self._write_terminal_pointer(run_id)
                descriptor_path.unlink(missing_ok=True)
                self._release_ownership(run_id, reason="terminal_replay")
                return self.terminal_status(run_id)
            registration = self.registry.select(
                plugin_id=str(descriptor["pluginId"]),
                check_ref=(str(descriptor["checkId"]), str(descriptor["checkVersion"])),
            )
            if registration.manifest.version != descriptor.get("pluginVersion"):
                raise PlatformContractError(
                    "PLUGIN_IDENTITY_MISMATCH", "The active Run requires a different installed plugin version",
                )
            compatibility = negotiate_plugin_compatibility(registration.compatibility)
            frozen_compatibility = descriptor.get("compatibility")
            if frozen_compatibility != compatibility.as_dict():
                raise PlatformContractError(
                    "RUN_RESTART_REQUIRED",
                    "The active Run was created under a different or unrecorded interaction protocol; start a new Run",
                )
            domain_result_contract = registration.domain_result_contract_for(
                (str(descriptor["checkId"]), str(descriptor["checkVersion"])),
            )
            common_review_mode = "common_review" in registration.result_features
            expected_semantic_mode = "common_review" if common_review_mode else "domain_result"
            if descriptor.get("semanticMode", "domain_result") != expected_semantic_mode:
                raise PlatformContractError(
                    "RUN_RESTART_REQUIRED",
                    "The active Run was created under a different semantic review mode",
                )
            if common_review_mode and domain_result_contract is not None:
                raise PlatformContractError(
                    "PLUGIN_SEMANTIC_CONTRACT_CONFLICT",
                    "A common-review Check cannot also publish a complete DomainResultContract",
                )
            frozen_domain_result_contract = descriptor.get("domainResultContract")
            current_domain_result_contract = (
                domain_result_contract.as_dict(include_digest=True)
                if domain_result_contract is not None else None
            )
            if frozen_domain_result_contract != current_domain_result_contract:
                raise PlatformContractError(
                    "DOMAIN_RESULT_CONTRACT_STALE",
                    "The active Run requires a different executable domain-result contract",
                )
            scope = descriptor["scope"]
            check = self.registry.check(
                registration,
                (str(descriptor["checkId"]), str(descriptor["checkVersion"])),
            )
            requested_caps = tuple(str(item) for item in descriptor.get("capabilities", ()))
            bound_provider, effective_caps = self._bind_capability_provider(
                run_id, registration, check, scope, requested_caps,
            )
            if bound_provider is not None:
                capabilities = tuple(sorted(effective_caps))
                context = PlatformContext(
                    run_id, effective_caps, dict(bound_provider.context.limits),
                )
                resolved_runtime = bound_provider
            else:
                capabilities = requested_caps
                context = PlatformContext(run_id, frozenset(capabilities))
                resolved_runtime = None
                if self.runtime_resolver is not None:
                    resolved_runtime = self.runtime_resolver(registration, scope)
            plugin = registration.create_plugin(resolved_runtime)
            if getattr(plugin, "manifest", None) != registration.manifest:
                raise PlatformContractError(
                    "PLUGIN_IDENTITY_MISMATCH", "Plugin factory returned different registered metadata",
                )
            expected_scope_digest = hashlib.sha256(json.dumps(
                scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ).encode()).hexdigest()
            raw_run = ledger.get("run", {})
            if not isinstance(raw_run, Mapping) or raw_run.get("scope_digest") != expected_scope_digest:
                raise PlatformContractError(
                    "RUN_SCOPE_MISMATCH", "Resume scope does not match the active Run ledger",
                )
            run = InteractivePlatformRun.restore(
                registration.manifest, check, context, store, ledger,
            )
            collections = {
                work_item_id: _evidence_collections(packet)
                for work_item_id, packet in run.investigations.items()
            }
            self._runs[run_id] = {
                "registration": registration, "scope": scope, "context": context,
                "plugin": plugin, "run": run,
                "domain_result_contract": domain_result_contract,
                "review_mode": expected_semantic_mode,
                "review_coordinator": (
                    IncrementalReviewCoordinator(coverage_ledger, coverage_store)
                    if common_review_mode and coverage_ledger is not None else None
                ),
                "committer": registration.create_committer(resolved_runtime),
                "evidence_collections": collections,
                "inspect_batch_size": registration.manifest.execution_profile.inspect_batch_size,
                "discovery_complete": run.discovery_complete,
                "active_semantic_task": None,
                "active_semantic_task_payload": None,
                "task_context": None,
                "evidence_handle_registry": None,
                "compatibility": compatibility,
                "capability_provider": bound_provider,
                "coverage_store": coverage_store,
                "coverage_ledger": coverage_ledger,
            }
            self._write_resume_descriptor(run_id, registration, scope, capabilities)
            response = self.advance(run_id)
            response["resumed"] = True
            return response
        except PlatformContractError as error:
            self._close_bound_provider({"capability_provider": bound_provider})
            self._runs.pop(run_id, None)
            self._release_ownership(run_id, reason="resume_failed")
            raise
        except Exception as error:
            self._close_bound_provider({"capability_provider": bound_provider})
            self._runs.pop(run_id, None)
            self._release_ownership(run_id, reason="resume_failed")
            raise PlatformContractError(
                "RUN_RESUME_FAILED", "The active interactive Run could not be reconstructed",
            ) from error

    def _assert_active_owner(self, run_id: str) -> None:
        handle = self._ownership_locks.get(run_id)
        if handle is None or handle.closed:
            raise PlatformContractError(
                "STALE_RUN_OWNER", "This Host does not hold the writer lock for the requested Run",
            )
        try:
            value = json.loads(self._owner_path(run_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PlatformContractError(
                "RUN_OWNERSHIP_FAILED", "The Run ownership record is unavailable",
            ) from error
        try:
            RunOwnership.from_mapping(value).assert_active(run_id, self._controller_epoch)
        except PlatformContractError:
            raise

    def close(self) -> None:
        """Release live writer locks without deleting recoverable Run state."""
        for state in tuple(self._runs.values()):
            self._close_bound_provider(state)
        for run_id in tuple(self._ownership_locks):
            self._release_ownership(run_id, reason="shutdown")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as error:
            _LOG.debug("Run ownership could not be released during finalization", exc_info=error)

    def discover(self, run_id: str) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        try:
            declaration = _simple_declaration(state["plugin"])
            source_capabilities = frozenset(
                getattr(state["registration"], "provider_source_capabilities", ())
            )
            bound_provider = state.get("capability_provider")
            if source_capabilities:
                if not isinstance(bound_provider, BoundCapabilityProvider):
                    raise PlatformContractError(
                        "PROVIDER_SOURCE_DISCOVERY_UNAVAILABLE",
                        "The Check declares provider-owned source discovery but "
                        "no provider is bound",
                    )
                if len(source_capabilities) != 1:
                    raise PlatformContractError(
                        "PROVIDER_SOURCE_DISCOVERY_INVALID",
                        "A Check must declare exactly one provider source capability",
                    )
                items = bound_provider.discover_work_items(
                    state["run"].check, next(iter(source_capabilities)),
                )
            elif declaration is not None:
                items = HostDocumentSource(DocumentSnapshotStore(
                    state["coverage_store"].root,
                )).discover(
                    state["scope"], input_kind=declaration["input"], check=state["run"].check,
                )
            else:
                items = tuple(state["plugin"].discover(state["scope"], state["context"]))
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError("DISCOVERY_FAILED", "Interactive plugin discovery failed") from error
        state["run"].record_discovery(items)
        state["discovery_complete"] = True
        return self._response(run_id, "discovered", {"workItems": [_work_item(item) for item in items]})

    def inspect(
        self, run_id: str, work_item_ids: Sequence[str] | None = None,
        *, cursor: str | None = None, page_size: int | None = None,
        include_evidence: bool = True,
    ) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        requested_ids = tuple(work_item_ids or tuple(run.work_items))
        missing = [item_id for item_id in requested_ids if item_id not in run.work_items]
        if missing:
            raise PlatformContractError("UNKNOWN_WORK_ITEM", "Inspection references an undiscovered WorkItem")
        if page_size is not None and (not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1):
            raise PlatformContractError("INVALID_PAGE_SIZE", "Inspection page size must be a positive integer")
        profile = run.manifest.execution_profile
        maximum = profile.inspect_batch_size
        adaptive_size = min(state["inspect_batch_size"], maximum)
        size = min(page_size or adaptive_size, adaptive_size)
        fingerprint = hashlib.sha256("\x1f".join(requested_ids).encode("utf-8")).hexdigest()[:16]
        start = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor.startswith(f"{fingerprint}:"):
                raise PlatformContractError("INVALID_CURSOR", "Inspection cursor does not match the requested WorkItems")
            try:
                start = int(cursor.split(":", 1)[1])
            except (TypeError, ValueError):
                raise PlatformContractError("INVALID_CURSOR", "Inspection cursor is malformed") from None
            if start < 0 or start > len(requested_ids):
                raise PlatformContractError("INVALID_CURSOR", "Inspection cursor is outside the requested WorkItems")
        selected_ids = requested_ids[start:start + size]
        selected = tuple(run.work_items[item_id] for item_id in selected_ids)
        packets: list[InvestigationPacket] = []
        inspection_failures: list[WorkFailure] = []
        attempts = 0
        splits = 0
        successful_batch_sizes: list[int] = []
        bound_provider = state.get("capability_provider")
        provider_evidence_expectation = (
            bound_provider.evidence_expectation if bound_provider is not None else None
        )

        def inspect_batch(batch: Sequence[WorkItem]) -> None:
            nonlocal attempts, splits
            if not batch:
                return
            attempts += 1
            try:
                declaration = _simple_declaration(state["plugin"])
                if declaration is not None:
                    if declaration.get("input") == "browser_snapshot":
                        if not isinstance(bound_provider, BoundCapabilityProvider):
                            raise PlatformContractError(
                                "PROVIDER_SOURCE_DISCOVERY_UNAVAILABLE",
                                "A browser Simple plugin requires a bound browser provider",
                            )
                        batch_packets = _simple_browser_packets(
                            batch, run.check, bound_provider,
                        )
                    else:
                        batch_packets = HostDocumentSource(DocumentSnapshotStore(
                            state["coverage_store"].root,
                        )).inspect(
                            batch, run.check, run_id=run.context.run_id,
                        )
                else:
                    batch_packets = tuple(
                        state["plugin"].inspect(batch, run.check, state["context"])
                    )
                from .kernel import PlatformKernel
                issued_provider_evidence = (
                    bound_provider.issued_evidence() if bound_provider is not None else None
                )
                PlatformKernel._validate_packets(
                    batch_packets, batch, run.check,
                    provider_evidence_expectation=provider_evidence_expectation,
                    issued_provider_evidence=issued_provider_evidence,
                )
                batch_collections = {
                    packet.work_item.work_item_id: _evidence_collections(packet)
                    for packet in batch_packets
                }
            except PlatformContractError as error:
                handle_batch_failure(batch, error.code, error.message)
                return
            except Exception as error:
                handle_batch_failure(batch, "INSPECTION_FAILED", str(error) or "Interactive plugin inspection failed")
                return
            successful_batch_sizes.append(len(batch))
            for packet in batch_packets:
                run.record_investigation(
                    packet,
                    provider_evidence_expectation=provider_evidence_expectation,
                    issued_provider_evidence=issued_provider_evidence,
                )
                state["evidence_collections"][packet.work_item.work_item_id] = batch_collections[
                    packet.work_item.work_item_id
                ]
                packets.append(packet)

        def handle_batch_failure(batch: Sequence[WorkItem], code: str, message: str) -> None:
            nonlocal splits
            if len(batch) > 1 and profile.can_split_failed_inspection:
                splits += 1
                midpoint = len(batch) // 2
                next_size = max(len(batch[:midpoint]), len(batch[midpoint:]))
                run.record_inspection_split(
                    tuple(item.work_item_id for item in batch), code, next_size,
                )
                inspect_batch(batch[:midpoint])
                inspect_batch(batch[midpoint:])
                return
            failure_message = message
            if len(batch) > 1:
                failure_message = (
                    "The inspection batch failed and the plugin does not permit safe failure splitting. "
                    f"{message}"
                )
            for item in batch:
                failure = WorkFailure(item.work_item_id, run.check.check_id, code, failure_message)
                run.record_inspection_failure(item.work_item_id, code, failure_message)
                inspection_failures.append(failure)

        inspect_batch(selected)
        if splits:
            learned_size = max(successful_batch_sizes, default=1)
            state["inspect_batch_size"] = min(adaptive_size, learned_size)
        run.record_inspection_batch_metrics(
            attempts=attempts, splits=splits, failures=len(inspection_failures),
            effective_batch_size=state["inspect_batch_size"],
        )
        next_cursor = None
        next_index = start + len(selected_ids)
        if next_index < len(requested_ids):
            next_cursor = f"{fingerprint}:{next_index}"
        return self._response(run_id, "inspected", {
            "investigations": [_packet(packet, include_evidence=include_evidence) for packet in packets],
            "inspectionFailures": [{
                "workItemId": failure.work_item_id,
                "checkId": failure.check_id,
                "code": failure.code,
                "message": failure.message,
            } for failure in inspection_failures],
            "nextCursor": next_cursor,
            "inspectedRange": {"start": start, "count": len(selected_ids), "total": len(requested_ids)},
            "evidenceIncluded": include_evidence,
            "evidenceCollectionIndex": {
                packet.work_item.work_item_id: _collection_index(
                    state["evidence_collections"][packet.work_item.work_item_id],
                ) for packet in packets
            },
            "batching": {
                "selected": len(selected),
                "attempts": attempts,
                "splits": splits,
                "effectiveBatchSize": state["inspect_batch_size"],
                "adapted": state["inspect_batch_size"] < maximum,
            },
        })

    def expand_investigation(
        self, run_id: str, work_item_id: str, evidence_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Return selected immutable Evidence for a previously inspected item."""
        state = self._state(run_id)
        packet = state["run"].investigations.get(work_item_id)
        if packet is None:
            raise PlatformContractError("UNKNOWN_INVESTIGATION", "Evidence expansion references an uninspected WorkItem")
        requested = set(evidence_ids or (item.evidence_id for item in packet.evidence))
        known = {item.evidence_id for item in packet.evidence}
        unknown = requested - known
        if unknown:
            raise PlatformContractError("UNKNOWN_EVIDENCE", "Evidence expansion references an unknown Evidence ID")
        return self._response(run_id, "evidence_expanded", {
            "workItemId": work_item_id,
            "investigation": _packet(packet, evidence_ids=requested),
            "evidenceIncluded": True,
        })

    def expand_evidence_collection(
        self, run_id: str, work_item_id: str, collection_id: str,
        *, cursor: str | None = None, page_size: int | None = None,
        group_key: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded page from a plugin-declared Evidence collection."""
        state = self._state(run_id)
        if work_item_id not in state["run"].investigations:
            raise PlatformContractError(
                "UNKNOWN_INVESTIGATION", "Evidence collection expansion references an uninspected WorkItem",
            )
        collection = state["evidence_collections"].get(work_item_id, {}).get(collection_id)
        if collection is None:
            raise PlatformContractError("UNKNOWN_EVIDENCE_COLLECTION", "Evidence collection is not declared for this WorkItem")
        page = EvidenceCollectionPager(
            collection, work_item_id=work_item_id, collection_id=collection_id,
        ).page(cursor=cursor, page_size=page_size, group_key=group_key)
        return self._response(run_id, "evidence_collection_expanded", {
            "workItemId": work_item_id,
            "collection": {
                "collectionId": collection_id,
                "evidenceId": collection["evidenceId"],
                "itemIdField": collection["itemIdField"],
                "groupBy": list(collection["groupBy"]),
                "reviewRequired": collection["reviewRequired"],
                "totalItems": len(collection["itemIds"]),
                "selectedGroupKey": group_key,
                "selectedItems": page["selectedItems"],
            },
            "groupSummaries": _plain(page["groupSummaries"]),
            "items": [_plain(item) for item in page["items"]],
            "itemIds": page["itemIds"],
            "page": page["page"],
            "nextCursor": page["nextCursor"],
        })

    def expand_semantic_evidence(
        self, run_id: str, *, cursor: str | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Expand the active semantic task's opaque evidence handles.

        This Host-owned path gives the Agent on-demand evidence without
        exposing WorkItem/Evidence identities or requiring a plugin to declare
        a bespoke collection and pagination implementation.
        """
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        active = state.get("active_semantic_task")
        registry = state.get("evidence_handle_registry")
        if not isinstance(active, Mapping) or not isinstance(registry, EvidenceHandleRegistry):
            raise PlatformContractError(
                "SEMANTIC_TASK_UNAVAILABLE",
                "There is no active semantic task with expandable evidence",
            )
        size = page_size or DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE
        common_review = active.get("kind") == "common_review"
        if common_review:
            # Source chunks are at most 2.4 KiB. Eight items keep the complete
            # expansion envelope under the same 24 KiB Agent-task budget.
            size = min(size, 8)
        page = registry.page(cursor=cursor, page_size=size)
        bounded_items = [
            _compact_agent_value(
                _agent_safe_value(item, registry),
                string_limit=2400 if common_review else AGENT_VALUE_STRING_LIMIT,
            )
            for item in page["items"]
        ]
        return self._response(run_id, "semantic_evidence_expanded", {
            "evidence": bounded_items,
            "evidenceRefs": page["itemIds"],
            "page": page["page"],
            "nextCursor": page["nextCursor"],
        })

    @staticmethod
    def _record_transport_timing(
        state: Mapping[str, Any], started_ns: int | None, *, persist: bool = True,
    ) -> None:
        """Persist successful direct-transport timing at a safe boundary."""
        if not isinstance(started_ns, int):
            return
        run = state.get("run")
        if isinstance(run, InteractivePlatformRun) and run.status == "running":
            run.record_transport_timing(
                duration_ms=max(0, int((time.monotonic_ns() - started_ns) / 1_000_000)),
                persist=persist,
            )

    @staticmethod
    def _require_active_semantic_task(
        state: Mapping[str, Any], *, task_digest: Any, kind: str,
        work_item_id: str, collection_id: str | None = None,
        item_ids: Sequence[str] | None = None,
    ) -> None:
        active = state.get("active_semantic_task")
        matches = (
            isinstance(active, Mapping)
            and task_digest == active.get("taskDigest")
            and kind == active.get("kind")
            and work_item_id == active.get("workItemId")
        )
        if collection_id is not None:
            matches = matches and collection_id == active.get("collectionId")
        if item_ids is not None:
            matches = matches and list(item_ids) == active.get("itemIds")
        if not matches:
            raise PlatformContractError(
                "AGENT_SEMANTIC_TASK_STALE",
                "Agent input does not match the exact current semantic task; refresh the boundary and do not switch WorkItem, collection, or item IDs",
                errors=({
                    "pointer": "/taskDigest",
                    "keyword": "currentSemanticTask",
                    "message": "taskDigest and task identity must match the current semanticTask",
                },),
                work_item_id=work_item_id or None,
            )




    @staticmethod
    def _evidence_handle_offset(
        run: InteractivePlatformRun, work_item_id: str,
    ) -> int:
        """Return a deterministic Run-local ordinal for one semantic task.

        Handles stay short while never being reused by a later WorkItem in the
        same Run.  The value is derived from frozen investigations, so resume
        reconstructs the same handle set without persisting Agent-visible IDs.
        """
        offset = 0
        for candidate_id in run.work_items:
            if candidate_id == work_item_id:
                break
            packet = run.investigations.get(candidate_id)
            if packet is not None:
                offset += len(EvidenceHandleRegistry.from_packet(candidate_id, packet).handles)
        return offset

    def _publish_semantic_task(
        self, state: Mapping[str, Any], task: dict[str, Any],
    ) -> dict[str, Any]:
        started_ns = time.monotonic_ns()
        domain_contract = state.get("domain_result_contract")
        if domain_contract is None:
            state["active_semantic_task"] = None
            state["task_context"] = None
            return task
        run: InteractivePlatformRun = state["run"]
        work_item_id = str(task.get("workItemId") or "")
        binding = {
            "runId": run.context.run_id,
            "workItemId": work_item_id,
            "kind": task.get("kind"),
            "collectionId": task.get("collectionId"),
            "itemIds": list(task.get("itemIds", ())),
            "contractDigest": domain_contract.contract_digest,
        }
        task_digest = "sha256:" + hashlib.sha256(json.dumps(
            binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        state["active_semantic_task"] = {
            "taskDigest": task_digest,
            "kind": task.get("kind"),
            "workItemId": work_item_id,
            "collectionId": task.get("collectionId"),
            "itemIds": list(task.get("itemIds", ())),
        }
        public_task = dict(task)
        packet = run.investigations.get(work_item_id)
        registry = None
        if packet is not None:
            registry = EvidenceHandleRegistry.from_packet(
                work_item_id, packet,
                start_ordinal=InteractivePluginController._evidence_handle_offset(
                    run, work_item_id,
                ),
            )
            state["evidence_handle_registry"] = registry
            public_task["agentView"] = _domain_agent_view(
                packet,
                registry,
                domain_data=InteractivePluginController._domain_agent_data(state, packet),
            )
        public_task.pop("investigation", None)
        public_task.pop("evidenceHandles", None)
        if "reviewContext" in public_task and registry is not None:
            # Document context is already part of the compact domain
            # projection.  Do not repeat the large source-fact index
            # on every semantic boundary.
            raw_review_context = public_task["reviewContext"]
            if isinstance(raw_review_context, Mapping):
                context_only = {
                    "documentContext": raw_review_context.get("documentContext", {}),
                }
                public_task["reviewContext"] = _agent_safe_value(
                    context_only, registry,
                )
            else:
                public_task.pop("reviewContext", None)
        for field_name in (
            "workItemId", "collectionId", "itemIds",
            "coverage",
        ):
            public_task.pop(field_name, None)
        public_task["domainContract"] = _domain_result_contract_task(
            domain_contract,
            plugin_id=state["registration"].manifest.plugin_id,
        )
        try:
            public_task = _bound_semantic_task(public_task)
        except PlatformContractError:
            # Do not leave a half-published semantic boundary when a plugin
            # contract itself is larger than the platform's hard budget.
            state["active_semantic_task"] = None
            state["task_context"] = None
            state["active_semantic_task_payload"] = None
            state["evidence_handle_registry"] = None
            raise
        state["task_context"] = TaskContext.from_task(
            run.context.run_id, task,
            contract_digest=domain_contract.contract_digest,
            task_digest=task_digest,
        )
        state["active_semantic_task_payload"] = _plain(public_task)
        payload_bytes = len(json.dumps(
            public_task, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
        run.record_semantic_task_metrics(
            duration_ms=max(0, int((time.monotonic_ns() - started_ns) / 1_000_000)),
            payload_bytes=payload_bytes,
        )
        state["semantic_task_published_at_ns"] = time.monotonic_ns()
        return public_task

    def _publish_common_review_task(
        self, state: Mapping[str, Any], task: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish an already bounded common-review task without plugin Schema."""
        started_ns = time.monotonic_ns()
        public_task = _plain(task)
        invariant_program = _simple_invariants(state["plugin"])
        if invariant_program.agent_rules:
            public_task["invariantRules"] = list(invariant_program.agent_rules)
        registration: PluginRegistration = state["registration"]
        semantic_digest = registration.semantic_instructions_sha256
        if semantic_digest is not None:
            check = state["run"].check
            public_task["semanticInstructions"] = {
                "sha256": semantic_digest,
                "uri": _semantic_instructions_uri(
                    registration.manifest.plugin_id,
                    check.check_id,
                    check.version,
                ),
            }
        coordinator = state.get("review_coordinator")
        if not isinstance(coordinator, IncrementalReviewCoordinator):
            raise PlatformContractError(
                "COMMON_REVIEW_UNAVAILABLE", "The common-review coordinator is unavailable",
            )
        evidence_bindings = coordinator.active_evidence_bindings
        public_task["evidencePaging"] = {
            "total": len(evidence_bindings),
            "pageSize": min(DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE, 8),
            "nextCursor": None,
            "expandTool": "expand_semantic_evidence",
        }
        encoded = json.dumps(
            public_task, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > AGENT_TASK_PAYLOAD_LIMIT:
            raise PlatformContractError(
                "REVIEW_BATCH_LIMIT_EXCEEDED",
                "Projected common-review task exceeds the Host Agent payload limit",
            )
        run: InteractivePlatformRun = state["run"]
        task_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        state["evidence_handle_registry"] = EvidenceHandleRegistry.from_bindings(
            task_digest,
            evidence_bindings,
            tuple(run.investigations.values()),
            content_by_reference=_common_review_evidence_content(
                run,
                evidence_bindings,
                DocumentSnapshotStore(state["coverage_store"].root),
            ),
        )
        state["active_semantic_task"] = {
            "taskDigest": task_digest,
            "kind": "common_review",
            "workItemId": "",
            "collectionId": None,
            "itemIds": [item["itemRef"] for item in public_task["items"]],
        }
        state["task_context"] = TaskContext.from_task(
            run.context.run_id,
            {
                "kind": "common_review",
                "itemIds": state["active_semantic_task"]["itemIds"],
            },
            contract_digest=COMMON_REVIEW_CONTRACT,
            task_digest=task_digest,
        )
        state["active_semantic_task_payload"] = public_task
        run.record_semantic_task_metrics(
            duration_ms=max(0, int((time.monotonic_ns() - started_ns) / 1_000_000)),
            payload_bytes=len(encoded),
        )
        state["semantic_task_published_at_ns"] = time.monotonic_ns()
        return public_task

    def _semantic_task(self, state: Mapping[str, Any], page_size: int) -> dict[str, Any] | None:
        """Return the next bounded semantic input without changing Evidence or decisions."""
        active_payload = state.get("active_semantic_task_payload")
        if isinstance(active_payload, Mapping):
            return _plain(active_payload)
        run: InteractivePlatformRun = state["run"]
        if state.get("review_mode") == "common_review":
            coordinator = state.get("review_coordinator")
            if coordinator is None:
                limit = state["context"].limits.get("maxReviewBatchItems", 64)
                if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                    limit = 64
                coordinator = IncrementalReviewCoordinator.start(
                    state["coverage_store"],
                    run_id=run.context.run_id,
                    atoms=_common_review_atoms(
                        run,
                        state["plugin"],
                        state["context"],
                        DocumentSnapshotStore(state["coverage_store"].root),
                    ),
                    max_batch_items=limit,
                    max_batch_bytes=AGENT_TASK_PAYLOAD_LIMIT,
                )
                state["review_coordinator"] = coordinator
            task = coordinator.next_task()
            state["coverage_ledger"] = coordinator.ledger
            if task is None:
                return None
            return self._publish_common_review_task(state, task)
        for work_item_id in run.work_items:
            packet = run.investigations.get(work_item_id)
            if packet is None or work_item_id in run.decisions:
                continue
            collections = state["evidence_collections"].get(work_item_id, {})
            evidence_payload = packet.evidence[0].payload if packet.evidence else {}
            review_context = None
            if isinstance(evidence_payload, Mapping):
                raw_context = evidence_payload.get("documentContext")
                raw_facts = evidence_payload.get("sourceFacts")
                if isinstance(raw_context, Mapping):
                    # Keep the semantic context visible at every bounded
                    # Agent turn without repeating the full source document.
                    review_context = {"documentContext": _plain(raw_context)}
                    if isinstance(raw_facts, Mapping):
                        identifiers = raw_facts.get("identifiers", {})
                        compact_identifiers = {}
                        if isinstance(identifiers, Mapping):
                            for kind, values in identifiers.items():
                                if isinstance(values, (tuple, list)):
                                    compact_identifiers[str(kind)] = [
                                        _plain(item) for item in values[:200]
                                    ]
                        review_context["sourceFacts"] = {
                            "schemaVersion": raw_facts.get("schemaVersion"),
                            "identifierCounts": {
                                str(kind): len(values) if isinstance(values, (tuple, list)) else 0
                                for kind, values in identifiers.items()
                            } if isinstance(identifiers, Mapping) else {},
                            "identifiers": compact_identifiers,
                            "explicitStatements": [
                                _plain(item) for item in raw_facts.get("explicitStatements", ())[:80]
                            ] if isinstance(raw_facts.get("explicitStatements"), (tuple, list)) else [],
                        }
            reference_collection_index = _collection_index({
                collection_id: collection
                for collection_id, collection in collections.items()
                if not collection["reviewRequired"]
            })
            domain_contract = state.get("domain_result_contract")
            if domain_contract is not None:
                coverage_ledger = ensure_legacy_domain_results(
                    state.get("coverage_ledger"),
                    run_id=run.context.run_id,
                    work_item_ids=tuple(run.investigations),
                    check_id=run.check.check_id,
                )
                atom = next(
                    item for item in coverage_ledger.atoms
                    if item.work_item_id == work_item_id
                )
                batch = next(
                    item for item in coverage_ledger.batches
                    if atom.atom_id in item.atom_ids
                )
                if batch.status == "planned":
                    coverage_ledger = coverage_ledger.offer(batch.batch_id)
                elif batch.status not in {"offered", "accepted"}:
                    raise PlatformContractError(
                        "INVALID_REVIEW_TRANSITION",
                        "Current legacy WorkItem is not available for semantic review",
                        work_item_id=work_item_id,
                    )
                self._persist_coverage(state, coverage_ledger, work_item_id=work_item_id)
                # DomainResult is currently a complete WorkItem submission.
                # Paging and Evidence expansion remain Host-owned and never
                # become Agent-authored platform fields.
                task = {
                    "kind": "domain_review",
                    "workItemId": work_item_id,
                    "investigation": _domain_packet(packet),
                }
                if review_context is not None:
                    task["reviewContext"] = review_context
                return self._publish_semantic_task(state, task)
            raise PlatformContractError(
                "DOMAIN_RESULT_REQUIRED",
                "Interactive Runs require a DomainResultContract",
            )
        state["active_semantic_task"] = None
        state["task_context"] = None
        state["active_semantic_task_payload"] = None
        return None

    @staticmethod
    def _persist_coverage(
        state: Mapping[str, Any], coverage_ledger: Any, *,
        work_item_id: str | None = None,
    ) -> None:
        coverage_store = state.get("coverage_store")
        if not isinstance(coverage_store, JsonCoverageLedgerStore):
            raise PlatformContractError(
                "COVERAGE_PERSISTENCE_FAILED",
                "Interactive Run has no Host-owned coverage store",
                work_item_id=work_item_id,
            )
        try:
            coverage_store.save(coverage_ledger)
        except PlatformContractError:
            raise
        except OSError as error:
            raise PlatformContractError(
                "COVERAGE_PERSISTENCE_FAILED",
                "Review coverage could not be durably persisted",
                work_item_id=work_item_id,
            ) from error
        state["coverage_ledger"] = coverage_ledger

    @staticmethod
    def _domain_agent_data(
        state: Mapping[str, Any], packet: InvestigationPacket,
    ) -> Mapping[str, Any] | Sequence[Any] | None:
        """Ask a domain plugin for a bounded Agent projection when available.

        The Host never derives domain meaning from the projection.  It only
        validates that the plugin returned JSON-safe data before applying the
        generic Evidence-handle sanitization.  Plugins without the optional
        hook keep the historical full-packet behavior during migration.
        """
        builder = getattr(state.get("plugin"), "build_domain_agent_data", None)
        if not callable(builder):
            return None
        try:
            value = builder(packet, state["run"].check, state["context"])
            return to_json_value(
                value,
                label="Domain Agent task data",
                work_item_id=packet.work_item.work_item_id,
            )
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "The domain plugin failed while building its Agent task projection",
                work_item_id=packet.work_item.work_item_id,
            ) from error

    def submit_domain_result(
        self, run_id: str, result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Accept one plugin-defined DomainResult bound to the current task.

        The Agent supplies only ``result``.  The Host resolves the active
        TaskContext, validates the frozen domain contract, invokes optional
        plugin semantics, and lets the existing platform Decision/ledger path
        perform the durable commit.
        """
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        contract = state.get("domain_result_contract")
        if contract is None:
            raise PlatformContractError(
                "DOMAIN_RESULT_CONTRACT_UNAVAILABLE",
                "The active Run does not expose the domain-result submission boundary",
            )
        task_context = state.get("task_context")
        if not isinstance(task_context, TaskContext) or task_context.kind != "domain_review":
            raise PlatformContractError(
                "AGENT_SEMANTIC_TASK_STALE",
                "There is no active domain-result task; refresh the current semantic boundary",
            )
        if not isinstance(result, Mapping):
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT",
                "DomainResult must be an object",
                work_item_id=task_context.work_item_id,
            )
        registry = state.get("evidence_handle_registry")
        bound_result = to_json_value(
            self._resolve_task_handles(result, registry, task_context.work_item_id),
            label="DomainResult",
            work_item_id=task_context.work_item_id,
        )
        # ``to_json_value`` returns a detached JSON object.  Keep the public
        # contract strict: a schema-valid value is the only value that may
        # reach plugin semantic hooks, and no plugin can observe a mutable
        # caller-owned mapping.
        if not isinstance(bound_result, dict):
            raise PlatformContractError(
                "INVALID_DOMAIN_RESULT",
                "DomainResult must be an object",
                work_item_id=task_context.work_item_id,
            )
        _validate_domain_result_schema(
            contract.result_schema, bound_result, work_item_id=task_context.work_item_id,
            error_code="DOMAIN_RESULT_INVALID",
            error_message="DomainResult does not satisfy the Run-frozen domain contract; correct the reported fields",
        )
        run: InteractivePlatformRun = state["run"]
        packet = run.investigations.get(task_context.work_item_id)
        if packet is None:
            raise PlatformContractError(
                "UNKNOWN_INVESTIGATION",
                "The active domain-result task has no InvestigationPacket",
                work_item_id=task_context.work_item_id,
            )
        agent_evidence_refs = validate_domain_evidence_references(
            bound_result,
            packet,
        )
        validator = getattr(state["plugin"], "validate_domain_result", None)
        if callable(validator):
            try:
                validator(bound_result, packet, run.check, state["context"])
            except PlatformContractError as error:
                if error.code != "PLUGIN_SEMANTIC_INPUT_INVALID":
                    raise PlatformContractError(
                        "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                        "The plugin rejected schema-valid DomainResult outside its declared semantic error contract",
                        work_item_id=task_context.work_item_id,
                    ) from error
                raise
            except (AssertionError, KeyError, TypeError) as error:
                raise PlatformContractError(
                    "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                    "The plugin failed while validating schema-valid DomainResult",
                    work_item_id=task_context.work_item_id,
                ) from error
            except Exception as error:
                raise PlatformContractError(
                    "PLUGIN_RUNTIME_FAILURE",
                    "The plugin failed unexpectedly while validating DomainResult",
                    work_item_id=task_context.work_item_id,
                ) from error
        mapper = getattr(state["plugin"], "map_domain_result", None)
        if not callable(mapper):
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "The domain-result plugin does not implement map_domain_result",
                work_item_id=task_context.work_item_id,
            )
        try:
            projected = mapper(bound_result, packet, run.check, state["context"])
        except PlatformContractError as error:
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "The plugin failed to map schema-valid DomainResult",
                work_item_id=task_context.work_item_id,
            ) from error
        except (AssertionError, KeyError, TypeError) as error:
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "The plugin failed while mapping schema-valid DomainResult",
                work_item_id=task_context.work_item_id,
            ) from error
        except Exception as error:
            raise PlatformContractError(
                "PLUGIN_RUNTIME_FAILURE",
                "The plugin failed unexpectedly while mapping DomainResult",
                work_item_id=task_context.work_item_id,
            ) from error
        if not isinstance(projected, Mapping):
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "map_domain_result must return a Decision projection object",
                work_item_id=task_context.work_item_id,
            )
        platform_fields = {
            "runId", "run_id", "workItemId", "work_item_id", "taskDigest",
            "task_digest", "contractDigest", "contract_digest",
            "finalization", "revision", "runRevision", "run_revision",
            "checkId", "checkVersion",
        }
        leaked = sorted(platform_fields & set(projected))
        if leaked:
            raise PlatformContractError(
                "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                "map_domain_result returned Host-owned platform fields: " + ", ".join(leaked),
                work_item_id=task_context.work_item_id,
            )
        projected_evidence_refs = validate_domain_evidence_references(
            projected,
            packet,
            error_code="PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
            error_message=(
                "map_domain_result returned an Evidence reference "
                "outside the current InvestigationPacket"
            ),
        )
        decision = dict(projected)
        canonical_evidence_refs = tuple(dict.fromkeys(
            (*agent_evidence_refs, *projected_evidence_refs),
        ))
        if canonical_evidence_refs:
            details = decision.get("details", {})
            if not isinstance(details, Mapping):
                raise PlatformContractError(
                    "PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH",
                    "map_domain_result returned non-object Decision details",
                    work_item_id=task_context.work_item_id,
                )
            details = dict(details)
            details["evidenceRefs"] = list(canonical_evidence_refs)
            decision["details"] = details
        decision["workItemId"] = task_context.work_item_id
        decision["checkId"] = run.check.check_id
        decision["checkVersion"] = run.check.version
        coverage_ledger = append_legacy_domain_result(
            state.get("coverage_ledger"),
            run_id=run_id,
            work_item_id=task_context.work_item_id,
            check_id=run.check.check_id,
            domain_result=bound_result,
        )
        self._persist_coverage(
            state, coverage_ledger, work_item_id=task_context.work_item_id,
        )
        response = self._commit_decision_proposals(run_id, (decision,))
        state["task_context"] = None
        return response

    def _commit_common_review_if_complete(
        self, run_id: str,
    ) -> dict[str, Any] | None:
        state = self._state(run_id)
        if state.get("review_mode") != "common_review":
            return None
        coordinator = state.get("review_coordinator")
        if not isinstance(coordinator, IncrementalReviewCoordinator):
            return None
        if any(batch.status != "accepted" for batch in coordinator.ledger.batches):
            return None
        run: InteractivePlatformRun = state["run"]
        undecided = set(run.investigations) - set(run.decisions)
        if not undecided:
            return None
        decisions = assemble_common_review_decisions(
            coordinator.ledger,
            check_id=run.check.check_id,
            check_version=run.check.version,
        )
        return self._commit_decision_proposals(run_id, decisions)

    def submit_common_review(
        self, run_id: str, submission: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Accept one common batch through generated invariant and Host validation."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        if state.get("review_mode") != "common_review":
            raise PlatformContractError(
                "COMMON_REVIEW_UNAVAILABLE",
                "The active Run does not use the Host common-review boundary",
            )
        task_context = state.get("task_context")
        if not isinstance(task_context, TaskContext) or task_context.kind != "common_review":
            raise PlatformContractError(
                "AGENT_SEMANTIC_TASK_STALE",
                "There is no active common-review task; refresh the semantic boundary",
            )
        coordinator = state.get("review_coordinator")
        if not isinstance(coordinator, IncrementalReviewCoordinator):
            raise PlatformContractError(
                "COMMON_REVIEW_UNAVAILABLE", "The common-review coordinator is unavailable",
            )
        _simple_invariants(state["plugin"]).validate(submission)
        coordinator.submit(submission)
        state["coverage_ledger"] = coordinator.ledger
        state["active_semantic_task"] = None
        state["active_semantic_task_payload"] = None
        state["task_context"] = None
        state["evidence_handle_registry"] = None
        committed = self._commit_common_review_if_complete(run_id)
        if committed is not None:
            return committed
        return self._response(run_id, "review_batch_accepted", {
            "accepted": True,
            "remainingBatches": sum(
                batch.status == "planned" for batch in coordinator.ledger.batches
            ),
        })

    @staticmethod
    def _resolve_task_handles(
        value: Mapping[str, Any], registry: Any, task_key: str,
    ) -> dict[str, Any]:
        """Resolve task-local Agent handles before plugin semantic validation.

        Only Host-issued task-local handles are translated through the active
        registry. Platform Evidence IDs are never accepted as a compatibility
        fallback.
        """
        if registry is None:
            return dict(value)

        def visit(current: Any) -> Any:
            if isinstance(current, Mapping):
                result: dict[str, Any] = {}
                for key, item in current.items():
                    if key in {"evidenceRefs", "evidence_refs", "classificationEvidenceRefs"}:
                        raise PlatformContractError(
                            "PLATFORM_EVIDENCE_REFERENCE_FORBIDDEN",
                            "DomainResult must use supportedBy task-local handles instead of platform Evidence fields",
                        )
                    if key in {"supportedBy", "supported_by"} and isinstance(item, (tuple, list)):
                        resolved: list[Any] = []
                        for ref in item:
                            if isinstance(ref, str) and ref.startswith("R"):
                                try:
                                    bound = registry.resolve(ref, task_key=task_key)
                                except PlatformContractError as error:
                                    if error.code in {
                                        "STALE_EVIDENCE_HANDLE",
                                        "CROSS_TASK_EVIDENCE_HANDLE",
                                    }:
                                        raise
                                    # Preserve the value so the normal Host
                                    # evidence validator emits the canonical
                                    # unknown-reference error.
                                    resolved.append(ref)
                                else:
                                    resolved.append(bound.source_chunk_id or bound.evidence_id)
                            else:
                                raise PlatformContractError(
                                    "PLATFORM_EVIDENCE_REFERENCE_FORBIDDEN",
                                    "DomainResult Evidence must use Host-issued task-local handles",
                                )
                        result[str(key)] = resolved
                    else:
                        result[str(key)] = visit(item)
                return result
            if isinstance(current, (tuple, list)):
                return [visit(item) for item in current]
            return current

        return visit(value)

    def advance(
        self, run_id: str, *, domain_result: Mapping[str, Any] | None = None,
        review_submission: Mapping[str, Any] | None = None,
        closeout: Mapping[str, Any] | None = None,
        page_size: int | None = None,
        transport_started_ns: int | None = None,
    ) -> dict[str, Any]:
        """Drive deterministic work until the next semantic boundary or terminal result."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        if state["run"].status in {"completed", "partial", "failed"}:
            ledger = state["run"].store.load(run_id)
            if ledger is None:
                raise PlatformContractError("RUN_RESUME_FAILED", "Terminal Run ledger is missing")
            self._hydrate_terminal_result(run_id, ledger)
            self._write_terminal_pointer(run_id)
            self._runs.pop(run_id, None)
            (self.output_root / run_id / "platform-resume.json").unlink(missing_ok=True)
            self._release_ownership(run_id, reason="terminal_replay")
            return self.terminal_status(run_id)
        accepted_operation_id: str | None = None
        operation_replayed = False
        if closeout is not None:
            if domain_result is not None or review_submission is not None:
                raise PlatformContractError(
                    "WORKFLOW_INPUT_CONFLICT",
                    "Run closeout cannot be combined with semantic input",
                )
            return self.finish(
                run_id, str(closeout["status"]), closeout.get("failures", ()),
                transport_started_ns=transport_started_ns,
            )
        size = min(page_size or DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE, MAX_EVIDENCE_COLLECTION_PAGE_SIZE)
        if domain_result is not None and review_submission is not None:
            raise PlatformContractError(
                "WORKFLOW_INPUT_CONFLICT",
                "Submit either a DomainResult or a common review batch, not both",
            )
        if domain_result is not None:
            published_at_ns = state.get("semantic_task_published_at_ns")
            domain_response = self.submit_domain_result(run_id, domain_result)
            # Do not persist wait telemetry until the DomainResult itself has
            # passed validation; rejected Agent input must leave the ledger
            # byte-for-byte unchanged.
            state.pop("semantic_task_published_at_ns", None)
            if isinstance(published_at_ns, int):
                state["run"].record_agent_wait(
                    duration_ms=max(0, int((time.monotonic_ns() - published_at_ns) / 1_000_000)),
                )
            accepted_operation_id = domain_response.get("operationId")
            operation_replayed = bool(domain_response.get("replayed"))
        elif review_submission is not None:
            published_at_ns = state.get("semantic_task_published_at_ns")
            review_response = self.submit_common_review(run_id, review_submission)
            state.pop("semantic_task_published_at_ns", None)
            if isinstance(published_at_ns, int):
                state["run"].record_agent_wait(
                    duration_ms=max(0, int((time.monotonic_ns() - published_at_ns) / 1_000_000)),
                )
            accepted_operation_id = review_response.get("operationId")
            operation_replayed = bool(review_response.get("replayed"))
        else:
            recovered_commit = self._commit_common_review_if_complete(run_id)
            if recovered_commit is not None:
                accepted_operation_id = recovered_commit.get("operationId")
                operation_replayed = bool(recovered_commit.get("replayed"))
        # One call can cross at most discovery, one bounded inspection batch,
        # and the resulting semantic or terminal boundary.
        for _ in range(3):
            workflow = self._workflow_for_state(state)
            if workflow["state"] == "running" and workflow["requiredNextStep"] == "discover_work_items":
                self.discover(run_id)
                continue
            if workflow["state"] == "running" and workflow["requiredNextStep"] == "inspect_work_items":
                run: InteractivePlatformRun = state["run"]
                failed_ids = {failure.work_item_id for failure in run.failures}
                pending_ids = tuple(
                    item_id for item_id in run.work_items
                    if item_id not in run.investigations and item_id not in failed_ids
                )
                self.inspect(run_id, pending_ids, page_size=min(size, len(pending_ids)))
                continue
            if workflow["state"] == "awaiting_agent_decision":
                workflow = {
                    **workflow,
                    "requiredNextStep": (
                        "submit_common_review"
                        if state.get("review_mode") == "common_review"
                        else "submit_domain_result"
                    ),
                }
                state["run"].record_workflow(workflow)
                self._record_transport_timing(state, transport_started_ns)
                return self._response(
                    run_id, "awaiting_agent_decision",
                    {"semanticTask": self._semantic_task(state, size)},
                    workflow=workflow, operation_id=accepted_operation_id,
                    replayed=operation_replayed,
                )
            if workflow["state"] == "ready_to_finish":
                terminal = self.finish(
                    run_id, "completed", transport_started_ns=transport_started_ns,
                )
                if accepted_operation_id is not None:
                    terminal["operationId"] = accepted_operation_id
                    terminal["replayed"] = operation_replayed
                    self._terminal_results[run_id]["response"] = _plain(terminal)
                return terminal
            workflow = {
                **workflow,
                "requiredNextStep": "advance_plugin_run_or_recover_work_item",
            }
            state["run"].record_workflow(workflow)
            self._record_transport_timing(state, transport_started_ns)
            return self._response(
                run_id, "blocked", {"semanticTask": None}, workflow=workflow,
                operation_id=accepted_operation_id, replayed=operation_replayed,
            )
        raise PlatformContractError(
            "WORKFLOW_ADVANCE_INVALID", "Host workflow did not reach a semantic or terminal boundary",
        )


    def _commit_decision_proposals(
        self, run_id: str, decisions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        for item in decisions:
            if not isinstance(item, Mapping):
                raise PlatformContractError(
                    "INVALID_DECISION", "Decision input must be an object",
                )
        assembled = tuple(dict(item) for item in decisions)
        proposals = tuple(_proposal(item, run.check.check_id, run.check.version) for item in assembled)
        # Validate the platform delivery projection before a Decision becomes
        # durable. A plugin contract/runtime mismatch must not surface for the
        # first time during terminal publication, when rollback is no longer
        # possible.
        for proposal in proposals:
            packet = run.investigations.get(proposal.work_item_id)
            if packet is None:
                raise PlatformContractError(
                    "UNKNOWN_INVESTIGATION",
                    "Decision references an uninvestigated WorkItem",
                )
            try:
                extract_result_delivery(proposal, packet)
            except PlatformContractError:
                raise
        receipts: list[CommitReceipt] = []
        operation_ids: list[str] = []
        replayed: list[bool] = []
        for proposal in proposals:
            packet = run.investigations.get(proposal.work_item_id)
            if packet is None:
                raise PlatformContractError("UNKNOWN_INVESTIGATION", "Decision references an uninvestigated WorkItem")
            canonical = json.dumps({
                "runId": run_id,
                "workItemId": proposal.work_item_id,
                "checkId": proposal.check_id,
                "checkVersion": proposal.check_version,
                "result": proposal.result,
                "findings": [{
                    "dimension": item.dimension, "status": item.status, "reason": item.reason,
                } for item in proposal.findings],
                "reason": proposal.reason,
                "details": _plain(proposal.details),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            operation_id = f"decision:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"
            existing = run.decisions.get(proposal.work_item_id)
            if existing is not None:
                if existing != proposal:
                    raise PlatformContractError(
                        "COMMIT_CONFLICT", "A different Decision is already committed for this WorkItem",
                    )
                receipt = run.receipts[proposal.work_item_id]
                replay = True
            else:
                committer = state["committer"]
                if committer is None:
                    receipt = CommitReceipt(
                        f"commit:{run_id}:{proposal.work_item_id}", proposal.work_item_id,
                        proposal.check_id, proposal.check_version, proposal.result, "memory",
                    )
                else:
                    try:
                        receipt = committer.commit(proposal, packet, run.check, state["context"])
                    except PlatformContractError:
                        raise
                    except Exception as error:
                        raise PlatformContractError("COMMIT_FAILED", "Interactive plugin commit failed") from error
                run.record_commit(proposal, receipt, operation_id=operation_id)
                replay = False
            receipts.append(receipt)
            operation_ids.append(operation_id)
            replayed.append(replay)
        state["active_semantic_task"] = None
        state["task_context"] = None
        state["active_semantic_task_payload"] = None
        return self._response(run_id, "decisions_committed", {
            "decisions": [{"workItemId": item.work_item_id, "result": item.result} for item in proposals],
            "receipts": [item.commit_id for item in receipts],
            "operationIds": operation_ids,
            "replayed": replayed,
        }, operation_id=operation_ids[0] if len(operation_ids) == 1 else None,
           replayed=bool(replayed) and all(replayed))

    def recover(self, run_id: str, work_item_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        restore = getattr(state["plugin"], "restore", None)
        if restore is None:
            status = "not_required"
            details = {"message": "The selected plugin does not require an explicit recovery operation."}
        else:
            try:
                result = restore(work_item_id, payload or {}, state["context"])
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError("RECOVERY_FAILED", "Interactive plugin recovery failed") from error
            if isinstance(result, Mapping):
                status = result.get("status", "uncertain")
                details = dict(result)
            else:
                status = str(result)
                details = {}
        if status not in {"restored", "not_required", "uncertain", "failed"}:
            raise PlatformContractError("INVALID_RECOVERY", "Plugin recovery returned an unsupported status")
        state["run"].record_recovery(work_item_id, status, details)
        return self._response(run_id, "recovered", {"workItemId": work_item_id, "status": status, **details})

    def progress(self, run_id: str) -> dict[str, Any]:
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        return self._response(run_id, "running", {
            "plugin": _plugin_identity(state["registration"]),
            "discovered": len(run.work_items),
            "inspected": len(run.investigations),
            "decisionsCommitted": len(run.decisions),
            "operations": len(run.operations),
            "evidenceGraph": _evidence_graph_progress(run),
            "inspectionBatching": {
                "attempts": run.inspection_batches,
                "splits": run.batch_splits,
                "failures": run.inspection_failures,
                "effectiveBatchSize": run.adaptive_inspect_batch_size,
        },
        })

    def record_rejection(
        self, run_id: str, error_code: str, *, work_item_id: str | None = None,
        operation_id: str | None = None, message: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Persist a Host-side rejection for the active Run when possible."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        state["run"].record_host_rejection(
            error_code, work_item_id=work_item_id,
            operation_id=operation_id, message=message, details=details,
        )

    @staticmethod
    def _submitted_work_item_id(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
        value: Any = arguments
        if tool_name == "advance_plugin_run":
            # DomainResult deliberately contains no platform identity. The
            # active task context is the sole source of WorkItem binding.
            value = None
        if isinstance(value, Mapping):
            work_item_id = value.get("workItemId")
            if isinstance(work_item_id, str) and work_item_id:
                return work_item_id
        return None

    @staticmethod
    def _boundary_descriptor(
        state: Mapping[str, Any], work_item_id: str | None,
        tool_name: str, arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        domain_contract = state.get("domain_result_contract")
        descriptor: dict[str, Any] = {
            "contractDigest": (
                domain_contract.contract_digest if domain_contract is not None else None
            ),
            "workItemId": work_item_id or "run",
        }
        if state.get("review_mode") == "common_review":
            descriptor["contractDigest"] = COMMON_REVIEW_CONTRACT
            descriptor["inputKind"] = "commonReviewSubmission"
            return descriptor
        if domain_contract is not None:
            descriptor["inputKind"] = "domainResult"
            return descriptor
        raise PlatformContractError(
            "DOMAIN_RESULT_REQUIRED",
            "Interactive Runs require a DomainResultContract",
        )

    def handle_boundary_error(
        self, run_id: str, error: PlatformContractError, *,
        tool_name: str, arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Classify one rejection, enforce its budget, and terminalize when required."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        domain_contract = state.get("domain_result_contract")
        policy = boundary_error_policy(error.code)
        if run.status != "running":
            return {
                "code": error.code,
                "message": error.message,
                "owner": policy.owner,
                "retryDisposition": policy.retry_disposition,
                "requiredNextStep": policy.required_next_step,
                "requestId": f"request:{uuid.uuid4().hex}",
                "contractDigest": None,
                "errors": [dict(item) for item in error.errors],
                "correctionBudget": None,
                "terminalStatus": None,
            }
        submitted_work_item_id = error.work_item_id or self._submitted_work_item_id(
            tool_name, arguments,
        )
        work_item_id = (
            submitted_work_item_id
            if submitted_work_item_id in run.work_items else None
        )
        if work_item_id is None:
            work_item_id = next((
                item_id for item_id in run.work_items
                if item_id in run.investigations and item_id not in run.decisions
            ), None)
        descriptor = self._boundary_descriptor(
            state, work_item_id, tool_name, arguments,
        )
        boundary_key = "boundary:" + hashlib.sha256(json.dumps(
            descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        request_id = f"request:{uuid.uuid4().hex}"
        request_digest = hashlib.sha256(json.dumps(
            _plain(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        prior_rejections = sum(
            event.name == "host.request.rejected"
            and event.details.get("boundaryKey") == boundary_key
            and event.details.get("retryDisposition") == "agent_correction"
            for event in run.events
        )
        strict_boundary = (
            domain_contract is not None
            or state.get("review_mode") == "common_review"
        )
        exhausted = (
            strict_boundary
            and policy.retry_disposition == "agent_correction"
            and prior_rejections >= DEFAULT_AGENT_CORRECTION_BUDGET
        )
        correction_budget = None
        if strict_boundary and policy.retry_disposition == "agent_correction":
            corrections_used = min(
                prior_rejections, DEFAULT_AGENT_CORRECTION_BUDGET,
            )
            correction_budget = {
                "maximumCorrections": DEFAULT_AGENT_CORRECTION_BUDGET,
                "correctionsUsed": corrections_used,
                "correctionsRemaining": max(
                    DEFAULT_AGENT_CORRECTION_BUDGET - corrections_used, 0,
                ),
                "exhausted": exhausted,
            }
        elif strict_boundary and policy.terminal_on_rejection:
            correction_budget = {
                "maximumCorrections": 0,
                "correctionsUsed": 0,
                "correctionsRemaining": 0,
                "exhausted": True,
            }

        self.record_rejection(
            run_id, error.code, work_item_id=work_item_id, message=error.message,
            details={
                "owner": policy.owner,
                "retryDisposition": policy.retry_disposition,
                "requestId": request_id,
                "requestDigest": request_digest,
                "boundaryKey": boundary_key,
                **({"correctionBudget": correction_budget} if correction_budget else {}),
            },
        )

        terminal_status = None
        outward_code = error.code
        outward_message = error.message
        required_next_step = policy.required_next_step
        retry_disposition = policy.retry_disposition
        should_terminalize = strict_boundary and (
            exhausted or policy.terminal_on_rejection
        )
        if should_terminalize:
            if exhausted:
                outward_code = "AGENT_CORRECTION_BUDGET_EXHAUSTED"
                outward_message = (
                    "Agent correction budget was exhausted; the WorkItem was left "
                    "without a Decision and the Run was closed as partial"
                )
                if error.errors:
                    details = "; ".join(
                        f"{item.get('pointer', '/')}"
                        f": {item.get('message', 'validation failed')}"
                        for item in error.errors
                    )
                    outward_message += f". Original validation errors: {details}"
                else:
                    outward_message += f". Original validation error: {error.message}"
            terminal_policy = boundary_error_policy(outward_code)
            required_next_step = terminal_policy.required_next_step
            retry_disposition = terminal_policy.retry_disposition
            failure = {
                "workItemId": work_item_id or "run",
                "checkId": run.check.check_id,
                "code": outward_code,
                "message": outward_message,
            }
            self.finish(
                run_id, "partial", (failure,), invoke_plugin_hooks=False,
            )
            terminal_status = "partial"

        return {
            "code": outward_code,
            "message": outward_message,
            "owner": policy.owner,
            "retryDisposition": retry_disposition,
            "requiredNextStep": required_next_step,
            "requestId": request_id,
            "contractDigest": None,
            "errors": [dict(item) for item in error.errors],
            "correctionBudget": correction_budget,
            "terminalStatus": terminal_status,
        }

    def get_result(
        self, run_id: str, section_id: str, *, cursor: str | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Return only the next requested delta from a terminal result."""
        terminal = self._terminal_results.get(run_id)
        if terminal is None:
            raise PlatformContractError(
                "RESULT_NOT_AVAILABLE", "No staged terminal result is available for this Run",
            )
        document: StagedResultDocument = terminal["document"]
        page = document.page(section_id, cursor=cursor, page_size=page_size)
        return self._response(
            run_id, "result_page",
            {
                **page,
                "deltaOnly": True,
                "sourceDigest": document.digest,
                "delivery": document.descriptor(),
            },
            workflow=terminal["workflow"],
            run_revision=terminal["runRevision"],
        )

    def terminal_status(self, run_id: str) -> dict[str, Any]:
        """Replay the latest terminal acknowledgement after a lost response."""
        terminal = self._terminal_results.get(run_id)
        if terminal is None:
            raise PlatformContractError(
                "RESULT_NOT_AVAILABLE", "No terminal plugin result is available for this Run",
            )
        response = _plain(terminal["response"])
        response["replayed"] = True
        return response

    @staticmethod
    def _publish_result_artifact(
        destination: Path, run_id: str, status: str,
        source_digest: str, result: Mapping[str, Any],
    ) -> None:
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            temporary.write_text(json.dumps({
                "schemaVersion": "1.0.0", "runId": run_id,
                "status": status, "sourceDigest": source_digest,
                "result": result,
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        except OSError as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Complete terminal result could not be durably published",
            ) from error

    def _terminalize_coverage(self, state: Mapping[str, Any], status: str) -> None:
        """Freeze the Host-owned review ledger before the platform terminal commit."""
        run: InteractivePlatformRun = state["run"]
        if state.get("review_mode") == "common_review":
            coordinator = state.get("review_coordinator")
            if coordinator is None:
                return
            if not isinstance(coordinator, IncrementalReviewCoordinator):
                raise PlatformContractError(
                    "COMMON_REVIEW_UNAVAILABLE", "The common-review coordinator is unavailable",
                )
            if status in {"partial", "failed"}:
                reason = f"Run closed as {status} before this review batch was accepted."
                for batch in coordinator.ledger.batches:
                    if batch.status in {"planned", "offered"}:
                        coordinator.block(batch.batch_id, reason)
            coordinator.finish(status)
            state["coverage_ledger"] = coordinator.ledger
            return
        coverage_ledger = state.get("coverage_ledger")
        if coverage_ledger is not None and coverage_ledger.terminal_status is not None:
            terminal = coverage_ledger.finalize(status)
            self._persist_coverage(state, terminal)
            return
        coverage_ledger = ensure_legacy_domain_results(
            coverage_ledger,
            run_id=run.context.run_id,
            work_item_ids=tuple(run.work_items),
            check_id=run.check.check_id,
        )
        # Active Runs created before coverage side-ledgers existed may already
        # contain durable Decisions. Recover only the missing migration record;
        # current Runs retain the original validated DomainResult instead.
        for work_item_id, decision in run.decisions.items():
            atom = next(
                item for item in coverage_ledger.atoms
                if item.work_item_id == work_item_id
            )
            batch = next(
                item for item in coverage_ledger.batches if atom.atom_id in item.atom_ids
            )
            if batch.status == "accepted":
                continue
            recovered = {
                "migrationRecoveredDecision": {
                    "result": decision.result,
                    "reason": decision.reason,
                    "findings": [
                        {
                            "dimension": finding.dimension,
                            "status": finding.status,
                            "reason": finding.reason,
                        }
                        for finding in decision.findings
                    ],
                    "details": _plain(decision.details),
                },
            }
            coverage_ledger = append_legacy_domain_result(
                coverage_ledger,
                run_id=run.context.run_id,
                work_item_id=work_item_id,
                check_id=run.check.check_id,
                domain_result=recovered,
            )
        if status in {"partial", "failed"}:
            reason = f"Run closed as {status} before this review batch was accepted."
            for batch in coverage_ledger.batches:
                if batch.status in {"planned", "offered"}:
                    coverage_ledger = coverage_ledger.block(batch.batch_id, reason)
        terminal = coverage_ledger.finalize(status)
        self._persist_coverage(state, terminal)

    def finish(
        self, run_id: str, status: str,
        failures: Sequence[Mapping[str, Any]] = (), *,
        invoke_plugin_hooks: bool = True,
        transport_started_ns: int | None = None,
    ) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        typed_failures = tuple(WorkFailure(
            item.get("workItemId", "run"), item.get("checkId", state["run"].check.check_id),
            item.get("code", "PLUGIN_FAILURE"), item.get("message", "Plugin failure"),
        ) for item in failures)
        state["run"].validate_finish(status, typed_failures)
        terminal_workflow = {
            "state": status,
            "phase": "finished",
            "canFinish": True,
            "requiredNextStep": None,
            "remaining": {
                "workItemsToInspect": 0,
                "workItemsToDecide": 0,
                "reviewItems": 0,
                "failures": len(state["run"].failures) + len(tuple(failures)),
            },
        }
        published_artifacts: list[str] = []
        host_owned_common_review = state.get("review_mode") == "common_review"
        finalize = getattr(state["plugin"], "finalize", None)
        if invoke_plugin_hooks and not host_owned_common_review and callable(finalize):
            try:
                paths = finalize(
                    tuple(state["run"].work_items.values()),
                    tuple(state["run"].investigations.values()),
                    tuple(state["run"].decisions.values()),
                    state["run"].store.root,
                    status,
                )
                published_artifacts = [str(path) for path in (paths or ())]
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PLUGIN_REPORT_FAILED", "Plugin report generation failed",
                ) from error
        summary: Mapping[str, Any] = {}
        summarize = getattr(state["plugin"], "summarize", None)
        if invoke_plugin_hooks and not host_owned_common_review and callable(summarize):
            try:
                value = summarize(
                    tuple(state["run"].work_items.values()),
                    tuple(state["run"].investigations.values()),
                    tuple(state["run"].decisions.values()),
                    status,
                )
                if value is not None:
                    if not isinstance(value, Mapping):
                        raise TypeError("plugin summary must be an object")
                    summary = to_json_value(
                        value,
                        label="Plugin result summary",
                        code="PLUGIN_SUMMARY_FAILED",
                    )
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PLUGIN_SUMMARY_FAILED", "Plugin result summary generation failed",
                ) from error
        prospective_failures = list(state["run"].failures)
        for failure in typed_failures:
            if failure not in prospective_failures:
                prospective_failures.append(failure)
        prospective_metrics = state["run"].metrics()
        canonical_result_name = f"{run_id}.canonical-result.json"
        audit_report_name = f"{run_id}.audit-report.md"
        prospective_ledger = PlatformLedger(
            state["run"].run,
            status,
            receipts=tuple(state["run"].receipts.values()),
            work_items=tuple(state["run"].work_items.values()),
            investigations=tuple(state["run"].investigations.values()),
            decisions=tuple(state["run"].decisions.values()),
            failures=tuple(prospective_failures),
            decision_authority=(
                next(iter(state["run"].receipts.values())).authority
                if state["run"].receipts else "platform"
            ),
        )
        audit_report = render_audit_report(
            prospective_ledger, snapshot_root=state["run"].store.root,
        ).decode("utf-8")
        result_overview, decision_views, review_items = _terminal_result_views(
            status,
            tuple(state["run"].work_items.values()),
            tuple(state["run"].investigations.values()),
            tuple(state["run"].decisions.values()),
            tuple(prospective_failures),
            discovery_complete=state["run"].discovery_complete,
        )
        evidence_graphs = _result_evidence_graphs(tuple(state["run"].investigations.values()))
        full_result_payload = {
            "resultOverview": result_overview,
            "decisions": decision_views,
            "reviewItems": review_items,
            "evidenceGraph": evidence_graphs,
            "failures": [
                {"workItemId": item.work_item_id, "code": item.code, "message": item.message}
                for item in prospective_failures
            ],
            "auditReport": audit_report,
            "metrics": prospective_metrics,
            "artifacts": [*published_artifacts, audit_report_name, canonical_result_name],
            "canonicalResult": canonical_result_name,
        }
        if summary and status != "failed":
            full_result_payload["summary"] = summary
        # Keep the publication boundary JSON-safe if a future result view
        # contains another recursively frozen contract value.
        full_result_payload = _plain(full_result_payload)
        full_result_digest = hashlib.sha256(json.dumps(
            full_result_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        result_document = StagedResultDocument({
            key: value for key, value in full_result_payload.items()
            if key in {
                "resultOverview", "summary", "decisions", "reviewItems",
                "evidenceGraph", "failures", "auditReport",
            }
        }, source_digest=full_result_digest)
        result_artifact = state["run"].store.root / "result-summary.json"
        pending_result_artifact = state["run"].store.root / "result-summary.pending.json"
        self._publish_result_artifact(
            pending_result_artifact, run_id, status, result_document.digest,
            full_result_payload,
        )
        # Publish the complete result before committing the terminal ledger.
        # If publication fails, the active ledger remains running and a new
        # Host can safely retry closeout from the last committed Decision.
        timing_snapshot = None
        if isinstance(transport_started_ns, int):
            timing_snapshot = (
                state["run"].transport_ms,
                state["run"].transport_samples,
                len(state["run"].events),
            )
        try:
            # Delay terminal transport telemetry until all report-generation
            # work has succeeded.  The timing event is kept in memory and is
            # committed atomically with the terminal ledger below.
            if isinstance(transport_started_ns, int):
                self._record_transport_timing(
                    state, transport_started_ns, persist=False,
                )
            self._terminalize_coverage(state, status)
            result: PlatformRunResult = state["run"].finish(status, typed_failures)
        except PlatformContractError:
            if timing_snapshot is not None:
                state["run"].transport_ms = timing_snapshot[0]
                state["run"].transport_samples = timing_snapshot[1]
                del state["run"].events[timing_snapshot[2]:]
            raise
        except Exception as error:
            if timing_snapshot is not None:
                state["run"].transport_ms = timing_snapshot[0]
                state["run"].transport_samples = timing_snapshot[1]
                del state["run"].events[timing_snapshot[2]:]
            raise PlatformContractError(
                "PLATFORM_LEDGER_PERSIST_FAILED", "Terminal platform ledger could not be durably persisted",
            ) from error
        canonical_path = state["run"].store.root / canonical_result_name
        if not canonical_path.is_file():
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Canonical result was not durably published",
            )
        audit_report_path = state["run"].store.root / audit_report_name
        if not audit_report_path.is_file():
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Formal audit report was not durably published",
            )
        try:
            published_report = audit_report_path.read_text(encoding="utf-8")
        except OSError as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Formal audit report could not be read",
            ) from error
        if published_report != audit_report:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED",
                "Formal audit report differs from the terminal result projection",
            )
        try:
            pending_result_artifact.replace(result_artifact)
        except OSError as error:
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Complete terminal result could not be promoted",
            ) from error
        result_payload = dict(result_document.overview)
        result_payload["metrics"] = dict(result.metrics)
        result_payload["artifacts"] = list(dict.fromkeys(
            [*full_result_payload["artifacts"], result_artifact.name]
        ))
        result_payload["canonicalResult"] = canonical_path.name
        result_payload["resultDelivery"] = result_document.descriptor()
        terminal_response = self._response(
            run_id, status, result_payload, workflow=terminal_workflow,
            run_revision=result.metrics["operations"],
        )
        self._terminal_results = {run_id: {
            "document": result_document,
            "workflow": terminal_workflow,
            "runRevision": result.metrics["operations"],
            "response": terminal_response,
        }}
        # A failed latest-result pointer is reported instead of returning a
        # false terminal acknowledgement. The active pointer remains the
        # recovery route for this already terminal ledger.
        self._write_terminal_pointer(run_id)
        # The durable ledger and complete result artifact are the history
        # boundary. Remove live plugin objects while retaining only the latest
        # compact paging document for this MCP session.
        self._close_bound_provider(self._runs.get(run_id))
        self._runs.pop(run_id, None)
        (self.output_root / run_id / "platform-resume.json").unlink(missing_ok=True)
        self._release_ownership(run_id, reason="terminal")
        return terminal_response

    def _state(self, run_id: str) -> dict[str, Any]:
        state = self._runs.get(run_id)
        if state is None:
            raise PlatformContractError("UNKNOWN_RUN", "Interactive platform Run does not exist")
        return state

    @staticmethod
    def _workflow_for_state(state: Mapping[str, Any]) -> dict[str, Any]:
        """Derive the next safe lifecycle boundary without making semantic decisions."""
        run: InteractivePlatformRun = state["run"]
        work_item_ids = set(run.work_items)
        inspected_ids = set(run.investigations)
        failed_ids = {failure.work_item_id for failure in run.failures}
        uninspected = work_item_ids - inspected_ids - failed_ids
        undecided = inspected_ids - set(run.decisions)
        if not state.get("discovery_complete"):
            phase, lifecycle_state, next_step = "discovery", "running", "discover_work_items"
        elif uninspected:
            phase, lifecycle_state, next_step = "inspection", "running", "inspect_work_items"
        elif undecided:
            phase, lifecycle_state = "semantic_review", "awaiting_agent_decision"
            next_step = (
                "submit_common_review"
                if state.get("review_mode") == "common_review"
                else "submit_domain_result"
            )
        elif failed_ids:
            phase, lifecycle_state, next_step = "recovery", "blocked", "recover_work_item"
        else:
            phase, lifecycle_state = "closeout", "ready_to_finish"
            next_step = "advance_plugin_run" if state.get("domain_result_contract") is not None else "finish_plugin_run"
        return {
            "state": lifecycle_state,
            "phase": phase,
            "canFinish": lifecycle_state == "ready_to_finish",
            "requiredNextStep": next_step,
            "remaining": {
                "workItemsToInspect": len(uninspected),
                "workItemsToDecide": len(undecided),
                "reviewItems": (
                    sum(
                        entry.status in {"planned", "offered"}
                        for entry in state["review_coordinator"].ledger.entries
                    )
                    if isinstance(
                        state.get("review_coordinator"), IncrementalReviewCoordinator,
                    ) else 0
                ),
                "failures": len(run.failures),
            },
        }

    def _response(
        self, run_id: str, status: str, result: Mapping[str, Any], *,
        workflow: Mapping[str, Any] | None = None,
        operation_id: str | None = None,
        replayed: bool = False,
        run_revision: int | None = None,
    ) -> dict[str, Any]:
        state = self._runs.get(run_id)
        if workflow is None:
            if state is not None:
                workflow = self._workflow_for_state(state)
                state["run"].record_workflow(workflow)
        if run_revision is None and state is not None:
            run_revision = len(state["run"].operations)
        result_payload = dict(result)
        if workflow is not None:
            result_payload.setdefault("workflow", dict(workflow))
        last_operation_id = operation_id
        if last_operation_id is None and state is not None and state["run"].operations:
            last_operation_id = state["run"].operations[-1].operation_id
        result_payload.setdefault("durableBoundary", {
            "runRevision": run_revision if run_revision is not None else 0,
            "lastOperationId": last_operation_id,
            "requiredNextStep": workflow.get("requiredNextStep") if workflow is not None else None,
        })
        if workflow is not None:
            run = state["run"] if state is not None else None
            metrics = result_payload.get("metrics") if isinstance(result_payload.get("metrics"), Mapping) else {}
            boundary_event = next((
                event for event in reversed(run.events)
                if event.name in {"platform.workflow.transition", "platform.run.terminal"}
            ), None) if run is not None else None
            result_payload.setdefault("progress", workflow_progress(
                workflow,
                discovered=len(run.work_items) if run is not None else int(metrics.get("discovered", 0) or 0),
                inspected=len(run.investigations) if run is not None else int(metrics.get("inspected", 0) or 0),
                decisions=len(run.decisions) if run is not None else int(metrics.get("decisionsCommitted", 0) or 0),
                entered_at=boundary_event.occurred_at if boundary_event is not None else (
                    run.run.started_at if run is not None else None
                ),
            ))
        response = {
            "protocolVersion": INTERACTIVE_PROTOCOL_VERSION,
            "runId": run_id,
            "runRevision": run_revision if run_revision is not None else 0,
            "status": status,
            "result": result_payload,
        }
        if operation_id is not None:
            response["operationId"] = operation_id
            response["replayed"] = replayed
        return response


__all__ = ["INTERACTIVE_OPERATIONS", "INTERACTIVE_PROTOCOL_VERSION", "InteractivePluginController"]
