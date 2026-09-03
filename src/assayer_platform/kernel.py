"""Small, domain-neutral execution kernel for plugin runs.

The kernel deliberately knows nothing about browsers or business rules.  It
coordinates plugin facts, validates the evidence/decision boundary, and keeps
the execution optimisations honest.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .contract import (
    CheckContract,
    Artifact,
    CommitReceipt,
    DecisionProposal,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PlatformEvent,
    PlatformLedger,
    Operation,
    PlatformRun,
    PlatformRunResult,
    PluginManifest,
    WorkFailure,
    WorkItem,
)
from .ledger import PlatformLedgerStore
from .plugin_registry import PluginRegistry


@runtime_checkable
class DomainPlugin(Protocol):
    manifest: PluginManifest

    def discover(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]: ...

    def inspect(
        self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext,
    ) -> Sequence[InvestigationPacket]: ...


@runtime_checkable
class SemanticDecisionProvider(Protocol):
    def decide(
        self, packets: Sequence[InvestigationPacket], check: CheckContract, context: PlatformContext,
    ) -> Sequence[DecisionProposal]: ...


@runtime_checkable
class DecisionCommitter(Protocol):
    def commit(
        self, proposal: DecisionProposal, packet: InvestigationPacket,
        check: CheckContract, context: PlatformContext,
    ) -> CommitReceipt: ...


@runtime_checkable
class ArtifactPublisher(Protocol):
    def publish(self, result: PlatformRunResult) -> Artifact: ...


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PlatformKernel:
    """Execute one plugin/check while enforcing platform invariants."""

    def __init__(self, ledger_store: PlatformLedgerStore | None = None) -> None:
        self._cache: dict[tuple[str, str, str, str, str], InvestigationPacket] = {}
        self._memory_commits: dict[tuple[str, str, str, str], CommitReceipt] = {}
        # Receipts are cached independently of the in-memory fallback so an
        # external durable committer is not called twice for the same
        # WorkItem/Check in one kernel lifetime.  A proposal digest makes a
        # replay with different semantic content fail closed.
        self._committed_receipts: dict[tuple[str, str, str, str], tuple[str, CommitReceipt]] = {}
        self._ledger_store = ledger_store

    def run_registered(
        self,
        registry: PluginRegistry,
        scope: Any,
        check_id: str,
        context: PlatformContext,
        *,
        plugin_id: str | None = None,
        check_version: str | None = None,
        runtime: Any = None,
        decision_provider: SemanticDecisionProvider | None = None,
        committer: DecisionCommitter | None = None,
    ) -> PlatformRunResult:
        """Run a plugin selected from the registry.

        Selection and construction happen before any WorkItem is discovered.
        A missing runtime/provider therefore fails closed without creating a
        misleading partially populated domain ledger.
        """
        try:
            registration = registry.select(
                plugin_id=plugin_id,
                check_id=check_id,
                check_ref=(check_id, check_version) if check_version is not None else None,
            )
            if check_version is not None:
                check = registry.check(registration, (check_id, check_version))
            else:
                matches = tuple(item for item in registration.manifest.checks if item.check_id == check_id)
                if len(matches) > 1:
                    raise PlatformContractError(
                        "AMBIGUOUS_CHECK", f"Check version is required for: {check_id}",
                    )
                check = matches[0] if matches else None
            if check is None:
                raise PlatformContractError("UNKNOWN_CHECK", f"Plugin does not declare Check {check_id}")
            plugin = registration.create_plugin(runtime)
            if getattr(plugin, "manifest", None) != registration.manifest:
                raise PlatformContractError(
                    "PLUGIN_IDENTITY_MISMATCH",
                    "Plugin factory returned an implementation with different registered metadata",
                )
            provider = decision_provider or registration.create_decision_provider(runtime)
            selected_committer = committer if committer is not None else registration.create_committer(runtime)
        except PlatformContractError as error:
            return PlatformRunResult(
                context.run_id, "failed", (),
                (WorkFailure("run", check_id, error.code, error.message),),
                {"discovered": 0, "inspected": 0, "cacheHits": 0, "operations": 0},
            )
        except Exception as error:
            return PlatformRunResult(
                context.run_id, "failed", (),
                (WorkFailure("run", check_id, "PLUGIN_INITIALIZATION_FAILED", str(error)),),
                {"discovered": 0, "inspected": 0, "cacheHits": 0, "operations": 0},
            )
        return self.run(
            plugin, scope, check.check_id, provider, context,
            check_version=check.version, committer=selected_committer,
        )

    def run(
        self,
        plugin: DomainPlugin,
        scope: Any,
        check_id: str,
        decision_provider: SemanticDecisionProvider,
        context: PlatformContext,
        *,
        check_version: str | None = None,
        committer: DecisionCommitter | None = None,
    ) -> PlatformRunResult:
        manifest = plugin.manifest
        matches = tuple(item for item in manifest.checks
                        if item.check_id == check_id and (check_version is None or item.version == check_version))
        check = matches[0] if len(matches) == 1 else None
        if check is None:
            code = "AMBIGUOUS_CHECK" if len(matches) > 1 else "UNKNOWN_CHECK"
            message = f"Check version is required for: {check_id}" if len(matches) > 1 else f"Unknown check: {check_id}"
            return PlatformRunResult(
                context.run_id, "failed", (),
                (WorkFailure("run", check_id, code, message),),
                {"discovered": 0, "inspected": 0, "cacheHits": 0, "operations": 0},
            )
        platform_run = PlatformRun(
            context.run_id, manifest.plugin_id, manifest.version,
            check.check_id, check.version, _digest(scope), _now(),
        )
        events: list[PlatformEvent] = []
        operation_records: list[Operation] = []
        operation_starts: dict[str, tuple[str, float]] = {}
        all_work_items: list[WorkItem] = []
        packets: list[InvestigationPacket] = []

        # Hydrate receipts written by an earlier process when the caller
        # intentionally resumes the same run ID.  Unknown/legacy receipt
        # metadata remains usable, but cannot prove proposal identity and is
        # therefore only replayed when the result itself is unchanged.
        if self._ledger_store is not None:
            stored = self._ledger_store.load(context.run_id)
            if isinstance(stored, Mapping):
                for raw in stored.get("receipts", ()):
                    if not isinstance(raw, Mapping):
                        continue
                    try:
                        receipt = CommitReceipt(
                            str(raw["commit_id"]), str(raw["work_item_id"]),
                            str(raw["check_id"]), str(raw["check_version"]),
                            str(raw["result"]), str(raw["durability"]),
                            raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {},
                            str(raw.get("authority", "platform")),
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    key = (context.run_id, receipt.work_item_id, receipt.check_id, receipt.check_version)
                    proposal_digest = str(receipt.metadata.get("proposalDigest", ""))
                    self._committed_receipts[key] = (proposal_digest, receipt)

        def emit(name: str, phase: str, outcome: str, *, operation_id: str | None = None,
                 work_item_id: str | None = None, details: Mapping[str, Any] | None = None) -> None:
            sequence = len(events) + 1
            events.append(PlatformEvent(
                f"event:{context.run_id}:{sequence}", sequence, context.run_id,
                name, phase, outcome, _now(), operation_id, work_item_id, check.check_id, details or {},
            ))

        def begin_operation(kind: str, work_item_id: str | None = None) -> str:
            operation_id = f"operation:{context.run_id}:{len(operation_records) + 1}"
            operation_starts[operation_id] = (_now(), time.monotonic())
            emit("operation.started", "start", "started", operation_id=operation_id,
                 work_item_id=work_item_id, details={"kind": kind})
            return operation_id

        def finish_operation(operation_id: str, kind: str, status: str,
                             work_item_id: str | None = None, error_code: str | None = None,
                             receipt_id: str | None = None) -> None:
            started_at, started_tick = operation_starts.pop(operation_id)
            ended_at = _now()
            operation_records.append(Operation(
                operation_id, kind, work_item_id or "run", check.check_id, status,
                error_code=error_code, receipt_id=receipt_id, started_at=started_at,
                ended_at=ended_at, duration_ms=max(0, int((time.monotonic() - started_tick) * 1000)),
            ))
            emit("operation.finished", "finish", "succeeded" if status == "succeeded" else "failed",
                 operation_id=operation_id, work_item_id=work_item_id,
                 details={"kind": kind, "errorCode": error_code} if error_code else {"kind": kind})

        def finalize(status: str, decisions: Sequence[DecisionProposal], failures: Sequence[WorkFailure],
                     metrics: Mapping[str, int], receipts: Sequence[CommitReceipt] = ()) -> PlatformRunResult:
            emit("platform.run.terminal", "finish", status)
            authority = receipts[0].authority if receipts else "platform"
            ledger = PlatformLedger(
                platform_run, status, tuple(operation_records), tuple(events), tuple(receipts), (),
                tuple(all_work_items), tuple(packets), tuple(decisions), tuple(failures), authority,
            )
            result = PlatformRunResult(
                context.run_id, status, tuple(decisions), tuple(failures), metrics,
                tuple(receipts), ledger,
            )
            if self._ledger_store is not None:
                self._ledger_store.save(ledger)
            return result

        emit("platform.run.started", "start", "started")
        metrics = {
            "discovered": 0, "inspected": 0, "cacheHits": 0, "operations": 0,
            "inspectionBatches": 0, "decisionBatches": 0, "batchSplits": 0,
            "adaptiveBatchReductions": 0,
            "commitAttempts": 0, "decisionsCommitted": 0, "durableCommits": 0,
            "commitReplays": 0,
        }
        failures: list[WorkFailure] = []
        decisions: list[DecisionProposal] = []
        receipts: list[CommitReceipt] = []
        discovery_operation = begin_operation("discover")
        try:
            items = tuple(plugin.discover(scope, context))
            metrics["operations"] += 1
            self._validate_work_items(items, manifest)
            all_work_items.extend(items)
            metrics["discovered"] = len(items)
            finish_operation(discovery_operation, "discover", "succeeded")
        except PlatformContractError as exc:
            finish_operation(discovery_operation, "discover", "failed", error_code=exc.code)
            failures.append(WorkFailure("run", check_id, exc.code, exc.message))
            return finalize("failed", (), failures, metrics)
        except Exception as exc:  # plugins are untrusted boundaries
            finish_operation(discovery_operation, "discover", "failed", error_code="DISCOVERY_FAILED")
            failures.append(WorkFailure("run", check_id, "DISCOVERY_FAILED", str(exc)))
            return finalize("failed", (), failures, metrics)

        applicable_items = tuple(item for item in items if item.kind in check.subject_kinds)

        missing = sorted(set(check.required_capabilities) - set(context.capabilities))
        if missing:
            for item in applicable_items:
                message = f"The runtime lacks required capability: {', '.join(missing)}"
                if check.capability_missing_outcome == "blocked":
                    failures.append(WorkFailure(item.work_item_id, check.check_id, "CAPABILITY_MISSING", message))
                else:
                    findings = tuple(Finding(dimension, "blocked", f"Required capability missing: {', '.join(missing)}")
                                     for dimension in check.dimensions)
                    decisions.append(DecisionProposal(
                        item.work_item_id, check.check_id, check.version, "needs_review", findings, message,
                    ))
            metrics["decisionsCommitted"] = 0
            status = "partial" if decisions else "failed" if failures else "completed"
            return finalize(status, decisions, failures, metrics)

        profile = manifest.execution_profile
        chunk_size = profile.inspect_batch_size
        capability_digest = _digest(sorted(context.capabilities))
        def inspect_chunk(chunk: Sequence[WorkItem]) -> int:
            pending: list[WorkItem] = []
            for item in chunk:
                key = self._cache_key(manifest, check, item, capability_digest)
                if profile.cache_reuse == "allowed" and key is not None and key in self._cache:
                    packets.append(self._cache[key])
                    metrics["cacheHits"] += 1
                else:
                    pending.append(item)
            if not pending:
                return 0
            operation_id = begin_operation("inspect", pending[0].work_item_id if len(pending) == 1 else None)
            try:
                metrics["operations"] += 1
                metrics["inspectionBatches"] += 1
                result = tuple(plugin.inspect(pending, check, context))
                metrics["inspected"] += len(result)
                self._validate_packets(result, pending, check)
                finish_operation(operation_id, "inspect", "succeeded", pending[0].work_item_id if len(pending) == 1 else None)
                for packet in result:
                    key = self._cache_key(manifest, check, packet.work_item, capability_digest)
                    if profile.cache_reuse == "allowed" and key is not None:
                        self._cache[key] = packet
                    packets.append(packet)
                return len(pending)
            except PlatformContractError as exc:
                finish_operation(operation_id, "inspect", "failed", pending[0].work_item_id if len(pending) == 1 else None, exc.code)
                if len(pending) > 1 and profile.can_split_failed_inspection:
                    metrics["batchSplits"] += 1
                    midpoint = len(pending) // 2
                    return max(
                        inspect_chunk(pending[:midpoint]),
                        inspect_chunk(pending[midpoint:]),
                    )
                else:
                    message = exc.message if len(pending) == 1 else (
                        "The inspection batch failed and the plugin does not permit safe failure splitting. "
                        f"{exc.message}"
                    )
                    failures.extend(WorkFailure(item.work_item_id, check_id, exc.code, message) for item in pending)
                    return 0
            except Exception as exc:
                finish_operation(operation_id, "inspect", "failed", pending[0].work_item_id if len(pending) == 1 else None, "INSPECTION_FAILED")
                if len(pending) > 1 and profile.can_split_failed_inspection:
                    metrics["batchSplits"] += 1
                    midpoint = len(pending) // 2
                    return max(
                        inspect_chunk(pending[:midpoint]),
                        inspect_chunk(pending[midpoint:]),
                    )
                else:
                    message = str(exc) if len(pending) == 1 else (
                        "The inspection batch failed and the plugin does not permit safe failure splitting. "
                        f"{exc}"
                    )
                    failures.extend(
                        WorkFailure(item.work_item_id, check_id, "INSPECTION_FAILED", message)
                        for item in pending
                    )
                    return 0

        adaptive_chunk_size = chunk_size
        offset = 0
        while offset < len(applicable_items):
            chunk = applicable_items[offset:offset + adaptive_chunk_size]
            splits_before = metrics["batchSplits"]
            learned_size = inspect_chunk(chunk)
            if metrics["batchSplits"] > splits_before:
                next_size = min(adaptive_chunk_size, max(learned_size, 1))
                if next_size < adaptive_chunk_size:
                    metrics["adaptiveBatchReductions"] += 1
                    adaptive_chunk_size = next_size
            offset += len(chunk)
        metrics["inspectBatchSize"] = adaptive_chunk_size

        packet_by_id = {packet.work_item.work_item_id: packet for packet in packets}
        decision_size = profile.max_batch_size if profile.decision_batching == "allowed" else 1
        for offset in range(0, len(packets), decision_size):
            decision_chunk = packets[offset:offset + decision_size]
            operation_id = begin_operation("decide")
            try:
                metrics["operations"] += 1
                metrics["decisionBatches"] += 1
                proposals = tuple(decision_provider.decide(tuple(decision_chunk), check, context))
                decision_packet_ids = {packet.work_item.work_item_id for packet in decision_chunk}
                self._validate_proposals(
                    proposals, {item_id: packet_by_id[item_id] for item_id in decision_packet_ids}, check,
                )
                finish_operation(operation_id, "decide", "succeeded")
                for proposal in proposals:
                    packet = packet_by_id[proposal.work_item_id]
                    commit_operation = begin_operation("commit", proposal.work_item_id)
                    try:
                        metrics["commitAttempts"] += 1
                        commit_key = (context.run_id, proposal.work_item_id, check.check_id, check.version)
                        proposal_digest = _digest({
                            "workItemId": proposal.work_item_id,
                            "workItemIdentity": packet.work_item.identity,
                            "workItemStateDigest": packet.work_item.state_digest,
                            "checkId": proposal.check_id,
                            "checkVersion": proposal.check_version,
                            "result": proposal.result,
                            "findings": [
                                {"dimension": item.dimension, "status": item.status, "reason": item.reason}
                                for item in proposal.findings
                            ],
                            "reason": proposal.reason,
                            "details": proposal.details,
                        })
                        replay = self._committed_receipts.get(commit_key)
                        if replay is not None:
                            previous_digest, receipt = replay
                            if previous_digest and previous_digest != proposal_digest:
                                raise PlatformContractError(
                                    "COMMIT_CONFLICT", "A different decision is already committed for this WorkItem",
                                )
                            metrics["commitReplays"] += 1
                        else:
                            receipt = (
                                committer.commit(proposal, packet, check, context)
                                if committer is not None
                                else self._commit_in_memory(proposal, packet, check, context)
                            )
                            self._validate_receipt(receipt, proposal)
                            receipt = replace(
                                receipt,
                                metadata={**dict(receipt.metadata), "proposalDigest": proposal_digest},
                            )
                            self._committed_receipts[commit_key] = (proposal_digest, receipt)
                        self._validate_receipt(receipt, proposal)
                        decisions.append(proposal)
                        receipts.append(receipt)
                        finish_operation(commit_operation, "commit", "succeeded", proposal.work_item_id, receipt_id=receipt.commit_id)
                    except PlatformContractError as exc:
                        finish_operation(commit_operation, "commit", "failed", proposal.work_item_id, exc.code)
                        failures.append(WorkFailure(proposal.work_item_id, check_id, exc.code, exc.message))
                    except Exception as exc:
                        finish_operation(commit_operation, "commit", "failed", proposal.work_item_id, "COMMIT_FAILED")
                        failures.append(WorkFailure(proposal.work_item_id, check_id, "COMMIT_FAILED", str(exc)))
            except PlatformContractError as exc:
                finish_operation(operation_id, "decide", "failed", error_code=exc.code)
                failures.extend(WorkFailure(packet.work_item.work_item_id, check_id, exc.code, exc.message)
                                for packet in decision_chunk)
            except Exception as exc:
                finish_operation(operation_id, "decide", "failed", error_code="DECISION_FAILED")
                failures.extend(WorkFailure(packet.work_item.work_item_id, check_id, "DECISION_FAILED", str(exc))
                                for packet in decision_chunk)

        decided = {proposal.work_item_id for proposal in decisions}
        for item in applicable_items:
            if item.work_item_id not in decided and not any(f.work_item_id == item.work_item_id for f in failures):
                failures.append(WorkFailure(item.work_item_id, check_id, "DECISION_MISSING", "No decision was committed"))
        metrics["decisionsCommitted"] = len(decisions)
        metrics["durableCommits"] = sum(receipt.durability == "durable" for receipt in receipts)
        if not decisions and failures:
            status = "failed"
        elif failures:
            status = "partial"
        else:
            status = "completed"
        return finalize(status, decisions, failures, metrics, receipts)

    def _commit_in_memory(
        self, proposal: DecisionProposal, packet: InvestigationPacket,
        check: CheckContract, context: PlatformContext,
    ) -> CommitReceipt:
        del packet
        key = (context.run_id, proposal.work_item_id, check.check_id, check.version)
        existing = self._memory_commits.get(key)
        if existing is not None:
            if existing.result != proposal.result:
                raise PlatformContractError("COMMIT_CONFLICT", "A different decision is already committed for this WorkItem")
            return existing
        receipt = CommitReceipt(
            f"commit:{_digest(key)[:24]}", proposal.work_item_id, check.check_id,
            check.version, proposal.result, "memory",
        )
        self._memory_commits[key] = receipt
        return receipt

    def publish_artifact(self, result: PlatformRunResult, artifact: Artifact) -> PlatformRunResult:
        """Publish a derived artifact only from a non-failed, committed run."""
        ledger = result.ledger
        if ledger is None:
            raise PlatformContractError("PUBLICATION_GATE", "A platform ledger is required before publication")
        if result.status == "failed" or ledger.status == "failed":
            raise PlatformContractError("PUBLICATION_GATE", "Failed runs cannot publish artifacts")
        receipt_ids = {receipt.commit_id for receipt in result.receipts}
        if not artifact.source_receipt_ids or not set(artifact.source_receipt_ids).issubset(receipt_ids):
            raise PlatformContractError(
                "PUBLICATION_GATE", "Artifact must reference receipts from committed decisions in this run",
            )
        prior_events = ledger.events
        terminal = prior_events[-1] if prior_events and prior_events[-1].name == "platform.run.terminal" else None
        if terminal is None:
            raise PlatformContractError("PUBLICATION_GATE", "Platform ledger has no terminal event")
        sequence = terminal.sequence
        event = PlatformEvent(
            f"event:{ledger.run.run_id}:{sequence}", sequence, ledger.run.run_id,
            "artifact.published", "finish", "succeeded", _now(), details={
                "artifactId": artifact.artifact_id, "kind": artifact.kind,
            },
        )
        terminal = replace(
            terminal, event_id=f"event:{ledger.run.run_id}:{sequence + 1}", sequence=sequence + 1,
        )
        updated_ledger = replace(
            ledger, events=prior_events[:-1] + (event, terminal),
            artifacts=ledger.artifacts + (artifact,),
        )
        if self._ledger_store is not None:
            self._ledger_store.save(updated_ledger)
        return replace(result, ledger=updated_ledger)

    def publish(self, result: PlatformRunResult, publisher: ArtifactPublisher) -> PlatformRunResult:
        """Run an external publisher only after the commit closure is valid."""
        if result.status == "failed" or result.ledger is None:
            raise PlatformContractError("PUBLICATION_GATE", "Failed or untracked runs cannot publish reports")
        if len(result.decisions) != len(result.receipts) or not result.receipts:
            raise PlatformContractError("PUBLICATION_GATE", "Every published decision must have a commit receipt")
        artifact = publisher.publish(result)
        return self.publish_artifact(result, artifact)

    @staticmethod
    def _validate_receipt(receipt: CommitReceipt, proposal: DecisionProposal) -> None:
        if not isinstance(receipt, CommitReceipt):
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Committer did not return a platform receipt")
        if (
            receipt.work_item_id != proposal.work_item_id
            or receipt.check_id != proposal.check_id
            or receipt.check_version != proposal.check_version
            or receipt.result != proposal.result
        ):
            raise PlatformContractError("INVALID_COMMIT_RECEIPT", "Commit receipt does not match the validated decision")
        if receipt.authority != "platform":
            raise PlatformContractError(
                "INVALID_COMMIT_RECEIPT", "Only the Platform may authoritatively commit a decision",
            )

    @staticmethod
    def _cache_key(
        manifest: PluginManifest, check: CheckContract, item: WorkItem, capability_digest: str,
    ) -> tuple[str, str, str, str, str] | None:
        invalidators: dict[str, Any] = {}
        for signal in check.invalidation_signals:
            if signal == "source_digest":
                invalidators[signal] = item.state_digest
            elif signal in item.metadata:
                invalidators[signal] = item.metadata[signal]
            else:
                return None
        identity = _digest((
            item.work_item_id, item.identity, item.state_digest, invalidators, capability_digest,
        ))
        return manifest.plugin_id, manifest.version, check.check_id, check.version, identity

    @staticmethod
    def _validate_work_items(items: Sequence[WorkItem], manifest: PluginManifest) -> None:
        ids: set[str] = set()
        for item in items:
            if item.work_item_id in ids:
                raise PlatformContractError("DUPLICATE_WORK_ITEM", "WorkItem IDs must be unique")
            if item.kind not in manifest.subject_kinds:
                raise PlatformContractError("UNKNOWN_SUBJECT_KIND", f"Undeclared subject kind: {item.kind}")
            ids.add(item.work_item_id)

    @staticmethod
    def _validate_packets(packets: Sequence[InvestigationPacket], expected: Sequence[WorkItem], check: CheckContract) -> None:
        expected_by_id = {item.work_item_id: item for item in expected}
        seen: set[str] = set()
        for packet in packets:
            item = expected_by_id.get(packet.work_item.work_item_id)
            if item is None or item != packet.work_item:
                raise PlatformContractError("PACKET_WORK_ITEM_MISMATCH", "Investigation packet is not bound to the current WorkItem")
            if packet.check_ref != check.ref:
                raise PlatformContractError("PACKET_CHECK_MISMATCH", "Investigation packet references another check")
            if packet.work_item.work_item_id in seen:
                raise PlatformContractError("DUPLICATE_PACKET", "Investigation packets must be unique")
            seen.add(packet.work_item.work_item_id)
            names = [dimension.name for dimension in packet.dimensions]
            if set(names) != set(check.dimensions) or len(names) != len(set(names)):
                raise PlatformContractError("DIMENSION_CLOSURE", "Investigation packet dimensions are incomplete or duplicated")
            evidence = {item.evidence_id: item for item in packet.evidence}
            if len(evidence) != len(packet.evidence):
                raise PlatformContractError("DUPLICATE_EVIDENCE", "Evidence IDs must be unique within a packet")
            if not set(check.required_evidence_kinds).issubset({item.kind for item in packet.evidence}):
                raise PlatformContractError("EVIDENCE_INCOMPLETE", "Required evidence kind is missing")
            for item in packet.evidence:
                if item.work_item_id != packet.work_item.work_item_id or item.check_id != check.check_id or item.check_version != check.version:
                    raise PlatformContractError("EVIDENCE_CLOSURE", "Evidence is bound to a different WorkItem or Check")
                if item.source_identity != packet.work_item.identity:
                    raise PlatformContractError("EVIDENCE_SOURCE", "Evidence source identity does not match the WorkItem")
            for dimension in packet.dimensions:
                if not all(reference in evidence for reference in dimension.evidence_refs):
                    raise PlatformContractError("EVIDENCE_REFERENCE", "Dimension references unknown evidence")
            referenced = {reference for dimension in packet.dimensions for reference in dimension.evidence_refs}
            referenced_kinds = {evidence[reference].kind for reference in referenced}
            if not set(check.required_evidence_kinds).issubset(referenced_kinds):
                raise PlatformContractError("EVIDENCE_INCOMPLETE", "Required evidence is not referenced by a dimension")
            if packet.recovery_status not in {"restored", "not_required"}:
                raise PlatformContractError("RECOVERY_INVALID", "Decision evidence was not safely recovered")
        if seen != set(expected_by_id):
            raise PlatformContractError("PACKET_MISSING", "Inspection must return exactly one packet per WorkItem")

    @staticmethod
    def _validate_proposals(proposals: Sequence[DecisionProposal], packets: Mapping[str, InvestigationPacket], check: CheckContract) -> None:
        seen: set[str] = set()
        for proposal in proposals:
            packet = packets.get(proposal.work_item_id)
            if packet is None or proposal.check_id != check.check_id or proposal.check_version != check.version:
                raise PlatformContractError("DECISION_REFERENCE", "Decision references an unknown WorkItem or Check")
            if proposal.work_item_id in seen:
                raise PlatformContractError("DUPLICATE_DECISION", "Only one decision may be committed per WorkItem")
            if proposal.result not in check.decision_states:
                raise PlatformContractError("DECISION_STATE", f"Decision state is not allowed by {check.check_id}")
            if {finding.dimension for finding in proposal.findings} != set(check.dimensions) or len(proposal.findings) != len(set(f.dimension for f in proposal.findings)):
                raise PlatformContractError("FINDING_CLOSURE", "Decision findings must cover every check dimension exactly once")
            statuses = {finding.status for finding in proposal.findings}
            if proposal.result == "scanned_no_issue" and statuses != {"satisfied"}:
                raise PlatformContractError("DECISION_GATE", "scanned_no_issue requires every dimension to be satisfied")
            if proposal.result == "issue_found" and "violated" not in statuses:
                raise PlatformContractError("DECISION_GATE", "issue_found requires a violated dimension")
            if proposal.result == "needs_review" and not statuses.intersection({"unresolved", "blocked", "conflicted"}):
                raise PlatformContractError("DECISION_GATE", "needs_review requires an unresolved, blocked, or conflicted dimension")
            seen.add(proposal.work_item_id)
        if seen != set(packets):
            raise PlatformContractError("DECISION_MISSING", "Decision provider must return exactly one proposal per packet")
