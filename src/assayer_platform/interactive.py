"""Domain-neutral interactive plugin protocol.

This module is the transport-independent controller for Agent-driven plugin
runs.  It deliberately knows only about WorkItems, InvestigationPackets and
DecisionProposals; browser concepts remain in the frontend compatibility
adapter.  MCP/CLI transports can wrap this controller without duplicating
lifecycle or validation rules.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .contract import (
    CommitReceipt,
    DecisionProposal,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PlatformRunResult,
    ReviewCheckpoint,
    WorkFailure,
    WorkItem,
)
from .plugin_registry import PluginRegistry, PluginRegistration
from .result_delivery import StagedResultDocument
from .canonical_result import validate_canonical_result
from .actionable_result import extract_result_delivery
from .evidence_graph import validate_candidate_evidence_graph_projection
from .evidence_collection import EvidenceCollectionPager
from .ledger import JsonPlatformLedgerStore, workflow_progress
from .ownership import RunOwnership
from .session import InteractivePlatformRun, InteractivePlatformSession


INTERACTIVE_PROTOCOL_VERSION = "1.0"
INTERACTIVE_OPERATIONS = (
    "start",
    "resume",
    "advance",
    "get_result",
    "discover",
    "inspect",
    "submit_decisions",
    "recover",
    "progress",
    "finish",
)

_RUN_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_LOG = logging.getLogger(__name__)

DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE = 20
MAX_EVIDENCE_COLLECTION_PAGE_SIZE = 100


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
    """Summarize candidate coverage from immutable packets and checkpoints."""
    total = covered = 0
    pending_ids: list[str] = []
    by_item: list[dict[str, Any]] = []
    checkpointed = {
        item_id
        for checkpoint in run.effective_review_checkpoints
        for item_id in checkpoint.item_ids
    }
    for packet in run.investigations.values():
        payload = packet.evidence[0].payload if packet.evidence else None
        graph = payload.get("candidateGraph") if isinstance(payload, Mapping) else None
        candidates = payload.get("candidateFindings", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(graph, Mapping) or not isinstance(candidates, (tuple, list)):
            continue
        validate_candidate_evidence_graph_projection(graph)
        ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, Mapping) and item.get("candidate_id")]
        # A deterministic plugin may already emit a fully disposed graph. For
        # Agent-review graphs, checkpoint membership is the durable coverage
        # boundary; never treat the scanner's candidate existence as review.
        pending = [] if graph.get("coverageComplete") is True else [
            item_id for item_id in ids if item_id not in checkpointed
        ]
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

    discovered = len(work_item_ids)
    inspected = len({item.work_item.work_item_id for item in investigations})
    decided = len(decided_ids)
    failure_count = len(failures)
    needs_review_count = outcome_counts["needs_review"]
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
    durable checkpoints.  A runtime resolver is injected by a product adapter
    (for example, a browser or API runtime); no runtime is assumed here.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        output_root: str | Path = "./assayer-output",
        *,
        runtime_resolver: Callable[[PluginRegistration, Any], Any] | None = None,
        capabilities_resolver: Callable[[PluginRegistration, Any], Sequence[str]] | None = None,
    ) -> None:
        self.registry = registry
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.runtime_resolver = runtime_resolver
        self.capabilities_resolver = capabilities_resolver
        self._controller_epoch = uuid.uuid4().hex
        self._runs: dict[str, dict[str, Any]] = {}
        self._ownership_locks: dict[str, Any] = {}
        self._terminal_results: dict[str, dict[str, Any]] = {}
        self._resume_error: PlatformContractError | None = None
        self._terminal_error: PlatformContractError | None = None
        self._restore_latest_terminal_result()

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
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        if run_id in self._runs:
            raise PlatformContractError("RUN_CONFLICT", "An interactive Run with this ID already exists")
        existing_root = self.output_root / run_id
        if existing_root.exists() and any(existing_root.iterdir()):
            raise PlatformContractError(
                "RUN_CONFLICT", "An interactive Run with this ID already has durable state; use explicit resume",
            )
        resolved_runtime = runtime
        if self.runtime_resolver is not None:
            resolved_runtime = self.runtime_resolver(registration, scope)
        caps = tuple(capabilities) if capabilities is not None else tuple(
            self.capabilities_resolver(registration, scope)
            if self.capabilities_resolver is not None else registration.capabilities
        )
        context = PlatformContext(run_id, frozenset(caps))
        try:
            plugin = registration.create_plugin(resolved_runtime)
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError("PLUGIN_INITIALIZATION_FAILED", "Interactive plugin could not be initialized") from error
        if getattr(plugin, "manifest", None) != registration.manifest:
            raise PlatformContractError("PLUGIN_IDENTITY_MISMATCH", "Plugin factory returned different registered metadata")
        store = JsonPlatformLedgerStore(self.output_root / run_id)
        self._claim_ownership(run_id)
        try:
            run = InteractivePlatformSession(registration.manifest).begin(
                context, scope, check.check_id, check.version, store,
            )
            self._runs[run_id] = {
                "registration": registration,
                "scope": scope,
                "context": context,
                "plugin": plugin,
                "run": run,
                "committer": registration.create_committer(resolved_runtime),
                "evidence_collections": {},
                "inspect_batch_size": registration.manifest.execution_profile.inspect_batch_size,
                "discovery_complete": False,
            }
            self._write_resume_descriptor(run_id, registration, scope, caps)
        except Exception:
            self._runs.pop(run_id, None)
            self._release_ownership(run_id, reason="start_failed")
            raise
        return self._response(run_id, "started", {
            "plugin": _plugin_identity(registration),
            "check": {"checkId": check.check_id, "version": check.version},
        })

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
            if key in {"resultOverview", "summary", "decisions", "reviewItems", "evidenceGraph", "failures"}
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
            checkpoints=int(result_payload["metrics"].get("reviewCheckpoints", 0) or 0),
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
        }
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
        if not _RUN_ID.fullmatch(run_id):
            raise PlatformContractError("INVALID_RUN_ID", "Run ID is not safe for ownership persistence")
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
        if not _RUN_ID.fullmatch(run_id):
            raise PlatformContractError("INVALID_RUN_ID", "Run ID is not safe for ownership persistence")
        descriptor_path = self.output_root / run_id / "platform-resume.json"
        if not descriptor_path.is_file():
            raise PlatformContractError(
                "RUN_RESUME_FAILED", "No recoverable Run exists for the requested Run ID",
            )
        self._claim_ownership(run_id)
        try:
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            if descriptor.get("runId") != run_id:
                raise PlatformContractError("RUN_MISMATCH", "Run path and resume descriptor differ")
            store = JsonPlatformLedgerStore(self.output_root / run_id)
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
            scope = descriptor["scope"]
            resolved_runtime = None
            if self.runtime_resolver is not None:
                resolved_runtime = self.runtime_resolver(registration, scope)
            capabilities = tuple(str(item) for item in descriptor.get("capabilities", ()))
            context = PlatformContext(run_id, frozenset(capabilities))
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
                registration.manifest,
                self.registry.check(registration, (str(descriptor["checkId"]), str(descriptor["checkVersion"]))),
                context, store, ledger,
            )
            collections = {
                work_item_id: _evidence_collections(packet)
                for work_item_id, packet in run.investigations.items()
            }
            self._runs[run_id] = {
                "registration": registration, "scope": scope, "context": context,
                "plugin": plugin, "run": run,
                "committer": registration.create_committer(resolved_runtime),
                "evidence_collections": collections,
                "inspect_batch_size": registration.manifest.execution_profile.inspect_batch_size,
                "discovery_complete": run.discovery_complete,
            }
            self._write_resume_descriptor(run_id, registration, scope, capabilities)
            response = self.advance(run_id)
            response["resumed"] = True
            return response
        except PlatformContractError as error:
            self._runs.pop(run_id, None)
            self._release_ownership(run_id, reason="resume_failed")
            raise
        except Exception as error:
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

        def inspect_batch(batch: Sequence[WorkItem]) -> None:
            nonlocal attempts, splits
            if not batch:
                return
            attempts += 1
            try:
                batch_packets = tuple(state["plugin"].inspect(batch, run.check, state["context"]))
                from .kernel import PlatformKernel
                PlatformKernel._validate_packets(batch_packets, batch, run.check)
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
                run.record_investigation(packet)
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

    def checkpoint_review(
        self, run_id: str, work_item_id: str, collection_id: str,
        item_ids: Sequence[str], payload: Mapping[str, Any],
        supersedes_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """Durably checkpoint opaque semantic review for declared collection items."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        collection = state["evidence_collections"].get(work_item_id, {}).get(collection_id)
        if collection is None:
            raise PlatformContractError(
                "UNKNOWN_EVIDENCE_COLLECTION", "Review checkpoint references an undeclared Evidence collection",
            )
        if not collection["reviewRequired"]:
            raise PlatformContractError(
                "EVIDENCE_COLLECTION_NOT_REVIEWABLE",
                "Reference Evidence collections can be expanded but cannot receive review checkpoints",
            )
        requested = tuple(sorted(item_ids))
        if len(requested) > MAX_EVIDENCE_COLLECTION_PAGE_SIZE:
            raise PlatformContractError(
                "INVALID_REVIEW_CHECKPOINT", "Review checkpoint exceeds the maximum Evidence collection page size",
            )
        unknown = set(requested) - set(collection["itemIds"])
        if unknown:
            raise PlatformContractError(
                "UNKNOWN_EVIDENCE_COLLECTION_ITEM", "Review checkpoint references an unknown collection item",
            )
        identity = {
            "runId": run_id,
            "workItemId": work_item_id,
            "collectionId": collection_id,
            "itemIds": sorted(requested),
            "payload": _plain(payload),
        }
        if supersedes_checkpoint_id is not None:
            identity["supersedesCheckpointId"] = supersedes_checkpoint_id
        canonical = json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        checkpoint_id = f"review:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"
        checkpoint = ReviewCheckpoint(
            checkpoint_id, work_item_id, run.check.check_id, run.check.version,
            collection_id, requested, payload, supersedes_checkpoint_id,
        )
        run.validate_review_checkpoint_record(checkpoint)
        validator = getattr(state["plugin"], "validate_review_checkpoint", None)
        if not callable(validator):
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_VALIDATION_UNSUPPORTED",
                "A plugin that persists semantic review checkpoints must validate them before persistence",
            )
        requested_set = set(requested)
        selected = tuple(
            item for item_id, item in zip(collection["itemIds"], collection["items"])
            if item_id in requested_set
        )
        prior = tuple(
            item for item in run.effective_review_checkpoints
            if item.work_item_id == work_item_id and item.collection_id == collection_id
            and item.checkpoint_id not in {checkpoint_id, supersedes_checkpoint_id}
        )
        try:
            validator(
                checkpoint, selected, prior, run.investigations[work_item_id],
                run.check, state["context"],
            )
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_VALIDATION_FAILED",
                "Plugin checkpoint validation failed before persistence",
            ) from error
        replayed = checkpoint_id in run.review_checkpoints
        run.record_review_checkpoints((checkpoint,))
        relevant = tuple(
            item for item in run.effective_review_checkpoints
            if item.work_item_id == work_item_id and item.collection_id == collection_id
        )
        covered = {item_id for item in relevant for item_id in item.item_ids}
        total = len(collection["itemIds"])
        return self._response(run_id, "review_checkpointed", {
            "workItemId": work_item_id,
            "collectionId": collection_id,
            "checkpointId": checkpoint_id,
            "supersedesCheckpointId": supersedes_checkpoint_id,
            "replayed": replayed,
            "acceptedItemIds": list(requested),
            "coverage": {
                "reviewedItems": len(covered),
                "remainingItems": total - len(covered),
                "totalItems": total,
                "complete": len(covered) == total,
            },
        }, operation_id=checkpoint_id, replayed=replayed)

    def _semantic_task(self, state: Mapping[str, Any], page_size: int) -> dict[str, Any] | None:
        """Return the next bounded semantic input without changing Evidence or decisions."""
        run: InteractivePlatformRun = state["run"]
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
            for collection in collections.values():
                if not collection["reviewRequired"]:
                    continue
                checkpoints = tuple(
                    item for item in run.effective_review_checkpoints
                    if item.work_item_id == work_item_id
                    and item.collection_id == collection["collectionId"]
                )
                reviewed = {item_id for checkpoint in checkpoints for item_id in checkpoint.item_ids}
                all_remaining_pairs = tuple(
                    (item_id, item) for item_id, item in zip(collection["itemIds"], collection["items"])
                    if item_id not in reviewed
                )
                selected_group = None
                remaining_pairs = all_remaining_pairs
                for group in collection["groups"].values():
                    group_ids = set(group["itemIds"])
                    group_remaining = tuple(
                        pair for pair in all_remaining_pairs if pair[0] in group_ids
                    )
                    if group_remaining:
                        selected_group = group
                        remaining_pairs = group_remaining
                        break
                if remaining_pairs:
                    selected = remaining_pairs[:page_size]
                    task = {
                        "kind": "review_evidence_items",
                        "workItemId": work_item_id,
                        "collectionId": collection["collectionId"],
                        "itemIds": [item_id for item_id, _ in selected],
                        "items": [_plain(item) for _, item in selected],
                        "group": ({
                            "groupKey": selected_group["groupKey"],
                            "values": _plain(selected_group["values"]),
                            "remainingItems": len(remaining_pairs),
                        } if selected_group is not None else None),
                        "referenceCollectionIndex": reference_collection_index,
                        "coverage": {
                            "reviewedItems": len(reviewed),
                            "remainingItems": len(all_remaining_pairs),
                            "totalItems": len(collection["itemIds"]),
                        },
                    }
                    if review_context is not None:
                        task["reviewContext"] = review_context
                    return task
            review_collections = tuple(
                collection for collection in collections.values()
                if collection["reviewRequired"]
            )
            if review_collections:
                checkpoints = tuple(
                    item for item in run.effective_review_checkpoints
                    if item.work_item_id == work_item_id
                )
                task = {
                    "kind": "finalize_decision",
                    "workItemId": work_item_id,
                    "reviewCheckpointIds": [item.checkpoint_id for item in checkpoints],
                    "referenceCollectionIndex": reference_collection_index,
                    "investigation": _packet(packet, include_evidence=False),
                }
                if review_context is not None:
                    task["reviewContext"] = review_context
                return task
            return {
                "kind": "decide_work_item",
                "workItemId": work_item_id,
                "referenceCollectionIndex": reference_collection_index,
                # A normal product client does not expose the diagnostic
                # expansion operations.  When no declared collection gives
                # the Host a bounded paging contract, return the complete
                # immutable packet at the semantic boundary so the Agent can
                # make an evidence-backed decision without falling back to a
                # low-level tool.
                "investigation": _packet(packet, include_evidence=not bool(collections)),
            }
        return None

    def advance(
        self, run_id: str, *, review_checkpoint: Mapping[str, Any] | None = None,
        decision: Mapping[str, Any] | None = None,
        closeout: Mapping[str, Any] | None = None,
        page_size: int | None = None,
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
            if review_checkpoint is not None or decision is not None:
                raise PlatformContractError(
                    "WORKFLOW_INPUT_CONFLICT",
                    "Run closeout cannot be combined with a review checkpoint or decision",
                )
            return self.finish(
                run_id, str(closeout["status"]), closeout.get("failures", ()),
            )
        size = min(page_size or DEFAULT_EVIDENCE_COLLECTION_PAGE_SIZE, MAX_EVIDENCE_COLLECTION_PAGE_SIZE)
        if review_checkpoint is not None:
            checkpoint_response = self.checkpoint_review(
                run_id,
                str(review_checkpoint["workItemId"]),
                str(review_checkpoint["collectionId"]),
                tuple(review_checkpoint["itemIds"]),
                review_checkpoint["payload"],
                review_checkpoint.get("supersedesCheckpointId"),
            )
            accepted_operation_id = checkpoint_response.get("operationId")
            operation_replayed = bool(checkpoint_response.get("replayed"))
        if decision is not None:
            submitted = dict(decision)
            work_item_id = str(submitted.get("workItemId", ""))
            if "finalization" in submitted and "reviewCheckpointIds" not in submitted:
                submitted["reviewCheckpointIds"] = [
                    item.checkpoint_id for item in state["run"].effective_review_checkpoints
                    if item.work_item_id == work_item_id
                ]
            decision_response = self.submit_decisions(run_id, (submitted,))
            accepted_operation_id = decision_response.get("operationId")
            operation_replayed = bool(decision_response.get("replayed"))

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
                workflow = {**workflow, "requiredNextStep": "advance_plugin_run"}
                state["run"].record_workflow(workflow)
                return self._response(
                    run_id, "awaiting_agent_decision",
                    {"semanticTask": self._semantic_task(state, size)},
                    workflow=workflow, operation_id=accepted_operation_id,
                    replayed=operation_replayed,
                )
            if workflow["state"] == "ready_to_finish":
                terminal = self.finish(run_id, "completed")
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
            return self._response(
                run_id, "blocked", {"semanticTask": None}, workflow=workflow,
                operation_id=accepted_operation_id, replayed=operation_replayed,
            )
        raise PlatformContractError(
            "WORKFLOW_ADVANCE_INVALID", "Host workflow did not reach a semantic or terminal boundary",
        )

    def _assemble_checkpointed_decision(
        self, state: Mapping[str, Any], value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        checkpoint_ids = value.get("reviewCheckpointIds")
        finalization = value.get("finalization")
        if checkpoint_ids is None and finalization is None:
            return value
        if not isinstance(checkpoint_ids, (tuple, list)) or not checkpoint_ids or not isinstance(finalization, Mapping):
            raise PlatformContractError(
                "INVALID_REVIEW_CHECKPOINT", "Checkpointed decision requires checkpoint IDs and finalization",
            )
        if "details" in value:
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_CONFLICT", "Checkpointed decision cannot also provide preassembled details",
            )
        run: InteractivePlatformRun = state["run"]
        work_item_id = value.get("workItemId")
        effective_ids = {item.checkpoint_id for item in run.effective_review_checkpoints}
        checkpoints: list[ReviewCheckpoint] = []
        for checkpoint_id in checkpoint_ids:
            checkpoint = run.review_checkpoints.get(checkpoint_id)
            if (
                checkpoint is None or checkpoint.work_item_id != work_item_id
                or checkpoint_id not in effective_ids
            ):
                raise PlatformContractError(
                    "UNKNOWN_REVIEW_CHECKPOINT",
                    "Decision references an unknown or superseded checkpoint for this WorkItem",
                )
            checkpoints.append(checkpoint)
        collection_ids = {item.collection_id for item in checkpoints}
        collections = state["evidence_collections"].get(work_item_id, {})
        if not collection_ids or not collection_ids.issubset(collections):
            raise PlatformContractError(
                "UNKNOWN_EVIDENCE_COLLECTION", "Decision references an unknown Evidence collection",
            )
        required_collection_ids = {
            collection_id for collection_id, collection in collections.items()
            if collection["reviewRequired"] and collection["itemIds"]
        }
        if collection_ids != required_collection_ids:
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_INCOMPLETE",
                "Decision checkpoints must cover every required Evidence collection",
            )
        for collection_id in collection_ids:
            expected = set(collections[collection_id]["itemIds"])
            covered = [
                item_id for checkpoint in checkpoints
                if checkpoint.collection_id == collection_id
                for item_id in checkpoint.item_ids
            ]
            if len(covered) != len(set(covered)):
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_CONFLICT", "Decision checkpoints cover an Evidence item more than once",
                )
            if set(covered) != expected:
                raise PlatformContractError(
                    "REVIEW_CHECKPOINT_INCOMPLETE", "Decision checkpoints must cover every Evidence collection item exactly once",
                )
        assembler = getattr(state["plugin"], "assemble_review_checkpoints", None)
        if not callable(assembler):
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_UNSUPPORTED", "Plugin does not provide checkpointed decision assembly",
            )
        try:
            details = assembler(
                tuple(checkpoints), dict(finalization), run.investigations[work_item_id],
                run.check, state["context"],
            )
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_ASSEMBLY_FAILED", "Plugin could not assemble checkpointed review details",
            ) from error
        if not isinstance(details, Mapping):
            raise PlatformContractError(
                "REVIEW_CHECKPOINT_ASSEMBLY_FAILED", "Plugin checkpoint assembler must return decision details",
            )
        assembled = dict(value)
        assembled.pop("reviewCheckpointIds", None)
        assembled.pop("finalization", None)
        assembled["details"] = dict(details)
        return assembled

    def submit_decisions(self, run_id: str, decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        run: InteractivePlatformRun = state["run"]
        assembled = tuple(self._assemble_checkpointed_decision(state, item) for item in decisions)
        proposals = tuple(_proposal(item, run.check.check_id, run.check.version) for item in assembled)
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
            "reviewCheckpoints": len(run.effective_review_checkpoints),
            "reviewCheckpointRecords": len(run.review_checkpoints),
            "reviewItemsCheckpointed": sum(len(item.item_ids) for item in run.effective_review_checkpoints),
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
    ) -> None:
        """Persist a Host-side rejection for the active Run when possible."""
        self._assert_active_owner(run_id)
        state = self._state(run_id)
        state["run"].record_host_rejection(
            error_code, work_item_id=work_item_id,
            operation_id=operation_id, message=message,
        )

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

    def finish(self, run_id: str, status: str, failures: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
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
        finalize = getattr(state["plugin"], "finalize", None)
        if callable(finalize):
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
        if callable(summarize):
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
                    summary = dict(value)
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
            "metrics": prospective_metrics,
            "artifacts": [*published_artifacts, canonical_result_name],
            "canonicalResult": canonical_result_name,
        }
        if summary and status != "failed":
            full_result_payload["summary"] = summary
        full_result_digest = hashlib.sha256(json.dumps(
            full_result_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        result_document = StagedResultDocument({
            key: value for key, value in full_result_payload.items()
            if key in {"resultOverview", "summary", "decisions", "reviewItems", "evidenceGraph", "failures"}
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
        try:
            result: PlatformRunResult = state["run"].finish(status, typed_failures)
        except PlatformContractError:
            raise
        except Exception as error:
            raise PlatformContractError(
                "PLATFORM_LEDGER_PERSIST_FAILED", "Terminal platform ledger could not be durably persisted",
            ) from error
        canonical_path = state["run"].store.root / canonical_result_name
        if not canonical_path.is_file():
            raise PlatformContractError(
                "RESULT_PUBLICATION_FAILED", "Canonical result was not durably published",
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
        review_remaining = 0
        for work_item_id in undecided:
            for collection in state.get("evidence_collections", {}).get(work_item_id, {}).values():
                if not collection["reviewRequired"]:
                    continue
                item_ids = set(collection["itemIds"])
                reviewed = {
                    item_id
                    for checkpoint in run.effective_review_checkpoints
                    if checkpoint.work_item_id == work_item_id
                    and checkpoint.collection_id == collection["collectionId"]
                    for item_id in checkpoint.item_ids
                }
                review_remaining += len(item_ids - reviewed)
        if not state.get("discovery_complete"):
            phase, lifecycle_state, next_step = "discovery", "running", "discover_work_items"
        elif uninspected:
            phase, lifecycle_state, next_step = "inspection", "running", "inspect_work_items"
        elif undecided and review_remaining:
            phase, lifecycle_state, next_step = "semantic_review", "awaiting_agent_decision", "checkpoint_review"
        elif undecided:
            phase, lifecycle_state, next_step = "semantic_review", "awaiting_agent_decision", "submit_decisions"
        elif failed_ids:
            phase, lifecycle_state, next_step = "recovery", "blocked", "recover_work_item"
        else:
            phase, lifecycle_state, next_step = "closeout", "ready_to_finish", "finish_plugin_run"
        return {
            "state": lifecycle_state,
            "phase": phase,
            "canFinish": lifecycle_state == "ready_to_finish",
            "requiredNextStep": next_step,
            "remaining": {
                "workItemsToInspect": len(uninspected),
                "workItemsToDecide": len(undecided),
                "reviewItems": review_remaining,
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
                checkpoints=(
                    len(run.effective_review_checkpoints)
                    if run is not None else int(metrics.get("reviewCheckpoints", 0) or 0)
                ),
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
