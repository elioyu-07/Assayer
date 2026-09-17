"""Contract-native interactive session bootstrap.

This is the first runtime path that accepts a ``CompiledPluginContract``
directly.  It creates only platform session state; no plugin module,
registration, factory, or domain-result contract is constructed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .compiled_plugin_contract import CompiledPluginContract
from .contract import (
    CheckContract,
    ExecutionProfile,
    PlatformContext,
    PlatformContractError,
    PluginManifest,
)
from .ledger import JsonPlatformLedgerStore, _plain
from .capability_negotiation import CapabilityNegotiator
from .contract import CapabilityProfile, ProviderCollectionResult, WorkItem
from .provider_execution import BoundCapabilityProvider
from .provider_registry import ProviderRegistry
from .incremental_review import BatchVerdict, CoverageLedger, JsonCoverageLedgerStore, ReviewAtom
from .session import InteractivePlatformSession


TERMINAL_DECISION_STATES = (
    "satisfied", "violated", "not_applicable", "unknown", "blocked",
)


@dataclass(frozen=True)
class CompiledRun:
    run_id: str
    contract: CompiledPluginContract
    check: CheckContract
    scope: Any
    context: PlatformContext
    session: InteractivePlatformSession
    run: Any
    provider: BoundCapabilityProvider | None = None
    capability: str | None = None
    work_items: tuple[WorkItem, ...] = ()
    collections: tuple[ProviderCollectionResult, ...] = ()
    coverage: CoverageLedger | None = None


def _manifest(contract: CompiledPluginContract) -> PluginManifest:
    payload = contract.payload
    input_value = payload["input"]
    subject_kind = str(input_value["subjectKind"])
    checks: list[CheckContract] = []
    required_capability = (
        ("browser_snapshot",) if input_value["kind"] == "browser_snapshot"
        else ("document_navigation",)
    )
    evidence_kind = (
        ("browser_snapshot",) if input_value["kind"] == "browser_snapshot"
        else ("structured",)
    )
    for item in payload["checks"]:
        checks.append(CheckContract(
            check_id=str(item["id"]),
            version=contract.version,
            subject_kinds=(subject_kind,),
            dimensions=tuple(str(value) for value in item["dimensions"]),
            decision_states=(
                *TERMINAL_DECISION_STATES,
            ),
            required_evidence_kinds=evidence_kind,
            required_capabilities=required_capability,
            capability_missing_outcome="needs_review",
            invalidation_signals=(
                "browser_state_digest" if input_value["kind"] == "browser_snapshot"
                else "source_digest",
            ),
        ))
    return PluginManifest(
        plugin_id=contract.plugin_id,
        version=contract.version,
        platform_api_version="1.0.0",
        domains=(contract.plugin_id.rsplit(".", 1)[-1],),
        subject_kinds=(subject_kind,),
        checks=tuple(checks),
        execution_profile=ExecutionProfile(
            discover_batching="allowed",
            inspect_batching="allowed",
            decision_batching="allowed",
            parallelism="forbidden",
            cache_reuse="allowed",
            max_batch_size=32,
            ordering="independent",
            failure_splitting="allowed",
        ),
    )


class CompiledInteractiveController:
    """Start contract-native Runs without a plugin registry or plugin code."""

    def __init__(self, contract: CompiledPluginContract, output_root: str | Path) -> None:
        self.contract = contract
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.manifest = _manifest(contract)
        self.session = InteractivePlatformSession(self.manifest)
        self._runs: dict[str, CompiledRun] = {}
        self._coverage_store = JsonCoverageLedgerStore(self.output_root / "coverage")

    @staticmethod
    def _capability(contract: CompiledPluginContract) -> str:
        # Capability names are requested by the compiled contract and resolved
        # only through an installed Provider. The platform does not implement
        # a document or browser capability itself.
        return "browser_snapshot" if contract.input_kind == "browser_snapshot" else "document_navigation"

    def _run(self, run_id: str) -> CompiledRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise PlatformContractError("UNKNOWN_RUN", f"Compiled Run does not exist: {run_id}") from error

    @property
    def active_run_id(self) -> str | None:
        return next(iter(self._runs), None)

    def start(
        self,
        *,
        plugin_id: str,
        check_id: str,
        scope: Any,
        run_id: str | None = None,
        capabilities: Sequence[str] = (),
    ) -> dict[str, Any]:
        if plugin_id != self.contract.plugin_id:
            raise PlatformContractError("PLUGIN_NOT_FOUND", f"Compiled plugin is not {plugin_id}")
        scope_schema = self.contract.payload["input"]["scopeSchema"]
        scope_error = next(
            Draft202012Validator(scope_schema, format_checker=FormatChecker()).iter_errors(scope),
            None,
        )
        if scope_error is not None:
            raise PlatformContractError("INVALID_SCOPE", "Plugin business scope does not satisfy its compiled contract")
        matches = tuple(item for item in self.manifest.checks if item.check_id == check_id)
        if len(matches) != 1:
            raise PlatformContractError("UNKNOWN_CHECK", f"Compiled plugin does not declare Check {check_id}")
        check = matches[0]
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        if (
            run_id in self._runs
            or self._coverage_store.load(run_id) is not None
            or (self.output_root / run_id).exists()
        ):
            raise PlatformContractError("RUN_CONFLICT", f"Run already exists: {run_id}")
        context = PlatformContext(run_id, frozenset(capabilities))
        run_root = self.output_root / run_id
        run = self.session.begin(
            context,
            scope,
            check.check_id,
            check.version,
            JsonPlatformLedgerStore(run_root),
        )
        self._runs[run_id] = CompiledRun(
            run_id, self.contract, check, scope, context, self.session, run,
        )
        return {
            "runId": run_id,
            "status": "started",
            "plugin": {
                "pluginId": self.contract.plugin_id,
                "version": self.contract.version,
                "contractDigest": self.contract.digest,
            },
            "check": {"checkId": check.check_id, "version": check.version},
            "runtime": "compiled_plugin_contract",
            "decisionStates": list(TERMINAL_DECISION_STATES),
        }

    def bind_provider(
        self,
        run_id: str,
        *,
        provider_registry: ProviderRegistry,
        platform_profile: CapabilityProfile | None = None,
        user_profile: CapabilityProfile | None = None,
        provider_id: str | None = None,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Bind the platform provider required by the compiled input contract."""
        state = self._run(run_id)
        if state.provider is not None:
            return {"runId": run_id, "status": "bound", "provider": state.provider.registration.descriptor.provider_id}
        capability = self._capability(self.contract)
        registration = provider_registry.select_for_capabilities((capability,), provider_id=provider_id)
        platform = platform_profile or CapabilityProfile(frozenset({capability}))
        user = user_profile or CapabilityProfile(frozenset({capability}))
        provider_scope = self._provider_scope(state.scope)
        negotiation = CapabilityNegotiator().negotiate(
            registration, (capability,), platform, user_profile=user, scope=provider_scope,
        )
        provider = BoundCapabilityProvider(
            registration, negotiation, run_id=run_id, scope=provider_scope, runtime=runtime,
        )
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, provider.context,
            state.session, state.run, provider, capability, state.work_items,
            state.collections, state.coverage,
        )
        return {
            "runId": run_id,
            "status": "bound",
            "provider": {
                "providerId": registration.descriptor.provider_id,
                "version": registration.descriptor.version,
                "capability": capability,
                "negotiation": negotiation.as_dict(),
            },
        }

    def _provider_scope(self, scope: Any) -> Mapping[str, Any]:
        if self.contract.input_kind == "browser_snapshot":
            return dict(scope)
        files = scope.get("files") if isinstance(scope, Mapping) else None
        if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)) or not files:
            raise PlatformContractError("PROVIDER_SCOPE_INVALID", "Document input requires at least one file")
        first = files[0]
        path = first.get("path") if isinstance(first, Mapping) else first
        if not isinstance(path, str) or not path:
            raise PlatformContractError("PROVIDER_SCOPE_INVALID", "Document file path is invalid")
        return {"path": path, "format": "markdown" if self.contract.input_kind == "markdown" else "document"}

    def discover(self, run_id: str) -> dict[str, Any]:
        """Discover and freeze all provider sources for a Run."""
        state = self._run(run_id)
        if state.provider is None or state.capability is None:
            raise PlatformContractError("PROVIDER_NOT_BOUND", "Bind a provider before discovery")
        # Discovery is Provider-owned. There is deliberately no platform
        # fallback that reads Markdown/files or opens a browser when a
        # Provider declines discovery.
        if state.coverage is not None:
            raise PlatformContractError("REVIEW_PLAN_FROZEN", "A planned review cannot replace its sources or coverage")
        items = state.provider.discover_work_items(state.check, state.capability)
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, state.context,
            state.session, state.run, state.provider, state.capability, items,
            state.collections, state.coverage,
        )
        return {"runId": run_id, "status": "discovered", "workItems": [self._work_item_dict(item) for item in items]}

    def collect(self, run_id: str) -> dict[str, Any]:
        """Collect every discovered source; provider failures remain traceable."""
        state = self._run(run_id)
        if state.provider is None or state.capability is None:
            raise PlatformContractError("PROVIDER_NOT_BOUND", "Bind a provider before collection")
        if state.coverage is not None:
            raise PlatformContractError("REVIEW_PLAN_FROZEN", "A planned review cannot replace its sources or coverage")
        items = state.work_items or state.provider.discover_work_items(state.check, state.capability)
        collected = []
        for item in items:
            cursor: str | None = None
            seen_cursors: set[str | None] = set()
            exhausted = False
            while not exhausted:
                page_scope = self._provider_scope(state.scope)
                if cursor is not None and state.contract.input_kind in {"markdown", "document"}:
                    page_scope["page"] = {"cursor": cursor}
                result = state.provider.collect(item, state.check, state.capability, scope=page_scope)
                collected.append(result)
                if result.failure is not None or state.contract.input_kind not in {"markdown", "document"}:
                    exhausted = True
                else:
                    next_cursor = None
                    for evidence in result.evidence:
                        candidate = evidence.payload.get("nextCursor")
                        if candidate is not None:
                            next_cursor = str(candidate)
                            break
                    if next_cursor is None or next_cursor in seen_cursors:
                        exhausted = True
                    else:
                        seen_cursors.add(cursor)
                        cursor = next_cursor
        results = tuple(collected)
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, state.context,
            state.session, state.run, state.provider, state.capability, tuple(items),
            results, state.coverage,
        )
        return {
            "runId": run_id,
            "status": "collected",
            "collections": [self._collection_dict(result) for result in results],
        }

    def plan_review_batches(
        self, run_id: str, *, max_batch_items: int = 32, max_batch_bytes: int = 24 * 1024,
    ) -> dict[str, Any]:
        """Create exhaustive Host-owned coverage: element × Check × Dimension."""
        state = self._run(run_id)
        if state.provider is None:
            raise PlatformContractError("PROVIDER_NOT_BOUND", "Bind a provider before review planning")
        if state.coverage is not None:
            raise PlatformContractError("REVIEW_PLAN_FROZEN", "A planned review cannot replace its sources or coverage")
        if not state.collections:
            self.collect(run_id)
            state = self._run(run_id)
        atoms: list[ReviewAtom] = []
        for result in state.collections:
            if result.failure is not None:
                # A provider failure is itself a traceable blocked element; it
                # cannot silently remove the source from coverage.
                elements = (("$provider_failure", {"code": result.failure.code, "message": result.failure.message}),)
                evidence_refs: tuple[str, ...] = ()
            else:
                if state.contract.input_kind == "markdown":
                    elements = tuple(
                        (unit["unitId"], unit)
                        for evidence in result.evidence
                        for unit in evidence.payload.get("units", ())
                    ) or (("$empty", {}),)
                else:
                    elements = tuple(
                        (f"{evidence.evidence_id}:{path}", value)
                        for evidence in result.evidence
                        for path, value in self._enumerate_elements(evidence.payload)
                    ) or (("$empty", {}),)
                evidence_refs = tuple(item.evidence_id for item in result.evidence)
            check_decl = next(
                item for item in state.contract.payload["checks"]
                if item["id"] == state.check.check_id
            )
            for path, value in elements:
                check = check_decl
                for dimension in check["dimensions"]:
                        element_id = self._element_id(result.request, path)
                        atom = ReviewAtom.create(
                            run_id=run_id,
                            work_item_id=result.request.work_item_id,
                            kind="review_element",
                            source_anchor=path,
                            rule_id=f"{check['id']}:{dimension}",
                            payload={
                                "context": {
                                    "plugin": {"pluginId": state.contract.plugin_id, "version": state.contract.version, "contractDigest": state.contract.digest},
                                    "evidenceBindings": [{
                                        "evidenceId": evidence.evidence_id,
                                        "runId": evidence.run_id,
                                        "workItemId": evidence.work_item_id,
                                        "checkId": evidence.check_id,
                                        "checkVersion": evidence.check_version,
                                        "sourceIdentity": evidence.source_identity,
                                        "sourceStateDigest": evidence.source_state_digest,
                                        "providerId": evidence.provider_id,
                                        "providerVersion": evidence.provider_version,
                                        "providerRequestId": evidence.provider_request_id,
                                        "capability": evidence.capability,
                                    } for evidence in result.evidence],
                                },
                                "elementId": element_id,
                                "sourcePath": path,
                                "element": value,
                                "checkId": check["id"],
                                "checkVersion": state.contract.version,
                                "dimension": dimension,
                                "evidenceRefs": list(evidence_refs),
                                "semanticInstruction": state.contract.payload["semanticReview"]["text"],
                                "providerFailure": result.failure is not None,
                                "requiredInitialState": "blocked" if result.failure is not None else "unknown",
                            },
                        )
                        atoms.append(atom)
        coverage = CoverageLedger.plan(
            run_id, atoms, max_batch_items=max_batch_items, max_batch_bytes=max_batch_bytes,
        )
        self._coverage_store.save(coverage)
        state = self._run(run_id)
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, state.context,
            state.session, state.run, state.provider, state.capability, state.work_items,
            state.collections, coverage,
        )
        return {
            "runId": run_id,
            "status": "planned",
            "coverage": coverage.as_dict(),
            "decisionStates": list(TERMINAL_DECISION_STATES),
        }

    def submit_review_batch(
        self, run_id: str, batch_id: str, decisions: Sequence[Mapping[str, Any]], *,
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        """Accept one Agent verdict after enforcing the platform five-state shape."""
        state = self._run(run_id)
        ledger = state.coverage
        if ledger is None:
            raise PlatformContractError("REVIEW_PLAN_REQUIRED", "Plan ReviewBatches before submitting a verdict")
        batch = next((item for item in ledger.batches if item.batch_id == batch_id), None)
        if batch is None:
            raise PlatformContractError("UNKNOWN_REVIEW_BATCH", "ReviewBatch is not in this Run")
        expected = set(batch.atom_ids)
        if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes, bytearray)):
            raise PlatformContractError("INVALID_REVIEW_DECISION", "Batch decisions must be an array")
        normalized = [dict(item) for item in decisions if isinstance(item, Mapping)]
        if len(normalized) != len(decisions) or {item.get("atomId") for item in normalized} != expected:
            raise PlatformContractError("INVALID_REVIEW_DECISION", "A verdict must cover the batch exactly once")
        atoms_by_id = {atom.atom_id: atom for atom in ledger.atoms}
        issued_evidence = state.provider.issued_evidence() if state.provider is not None else {}
        for decision in normalized:
            atom_id = decision.get("atomId")
            status = decision.get("state")
            if status not in TERMINAL_DECISION_STATES:
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Decision state is outside the five-state vocabulary")
            evidence_refs = decision.get("evidenceRefs", ())
            if not isinstance(evidence_refs, Sequence) or isinstance(evidence_refs, (str, bytes, bytearray)):
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Evidence refs must be an array")
            atom = atoms_by_id.get(str(atom_id))
            declared_refs = set(atom.payload.get("evidenceRefs", ())) if atom is not None else set()
            for evidence_ref in evidence_refs:
                evidence = issued_evidence.get(str(evidence_ref))
                if evidence is None or str(evidence_ref) not in declared_refs:
                    raise PlatformContractError("INVALID_REVIEW_DECISION", "Decision references Evidence not issued for this atom")
                if (
                    evidence.run_id != run_id
                    or evidence.work_item_id != atom.work_item_id
                    or evidence.check_id != state.check.check_id
                    or evidence.check_version != state.contract.version
                    or evidence.source_state_digest != next(
                        item.state_digest for item in state.work_items if item.work_item_id == atom.work_item_id
                    )
                ):
                    raise PlatformContractError("INVALID_REVIEW_DECISION", "Decision Evidence is outside the current Run source binding")
            if status in {"satisfied", "violated"} and not evidence_refs:
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Satisfied or violated decisions require Evidence refs")
            if status == "unknown" and not decision.get("missingInformation"):
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Unknown decisions require missing information")
            if status == "blocked" and not decision.get("blockedReason"):
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Blocked decisions require a blocking reason")
            if status == "not_applicable" and not decision.get("applicabilityBasis"):
                raise PlatformContractError("INVALID_REVIEW_DECISION", "Not-applicable decisions require an applicability basis")
        if batch.status == "planned":
            ledger = ledger.offer(batch_id)
        verdict = BatchVerdict(
            submission_id or f"submission-{uuid.uuid4().hex}", batch_id, tuple(normalized),
        )
        ledger = ledger.accept(verdict)
        self._coverage_store.save(ledger)
        state = self._run(run_id)
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, state.context,
            state.session, state.run, state.provider, state.capability, state.work_items,
            state.collections, ledger,
        )
        return {"runId": run_id, "status": "accepted", "batchId": batch_id, "submissionId": verdict.submission_id}

    def finalize(self, run_id: str) -> dict[str, Any]:
        """Close a Run only after every planned atom has a terminal five-state verdict."""
        state = self._run(run_id)
        ledger = state.coverage
        if ledger is None:
            raise PlatformContractError("REVIEW_PLAN_REQUIRED", "Plan ReviewBatches before finalization")
        if any(entry.status != "accepted" for entry in ledger.entries):
            raise PlatformContractError("REVIEW_NOT_CLOSED", "Every ReviewAtom must have a terminal verdict before finalization")
        terminal = ledger.finalize("completed")
        self._coverage_store.save(terminal)
        self._runs[run_id] = CompiledRun(
            state.run_id, state.contract, state.check, state.scope, state.context,
            state.session, state.run, state.provider, state.capability, state.work_items,
            state.collections, terminal,
        )
        return self.get_result(run_id)

    def get_result(self, run_id: str) -> dict[str, Any]:
        return self.read_result(self.output_root, run_id)

    @staticmethod
    def read_result(output_root: str | Path, run_id: str) -> dict[str, Any]:
        """Derive the terminal view from validated durable coverage, even after restart."""
        ledger = JsonCoverageLedgerStore(Path(output_root) / "coverage").load(run_id)
        if ledger is None or ledger.terminal_status is None:
            raise PlatformContractError("RESULT_NOT_AVAILABLE", "The compiled Run is not terminal")
        if ledger.run_id != run_id:
            raise PlatformContractError("INVALID_COVERAGE_LEDGER", "Coverage belongs to another Run")
        return {
            "runId": run_id, "status": ledger.terminal_status,
            "coverage": ledger.as_dict(),
            "decisionStates": list(TERMINAL_DECISION_STATES),
            "trace": {
                "coverage": f"coverage/{run_id}.review-coverage.json",
                "coverageDigest": "sha256:" + hashlib.sha256(ledger.canonical_bytes()).hexdigest(),
            },
        }

    @staticmethod
    def _work_item_dict(item: WorkItem) -> dict[str, Any]:
        return {
            "workItemId": item.work_item_id, "kind": item.kind,
            "identity": item.identity, "stateDigest": item.state_digest,
            "metadata": _plain(item.metadata),
        }

    @staticmethod
    def _collection_dict(result: ProviderCollectionResult) -> dict[str, Any]:
        return {
            "workItemId": result.request.work_item_id,
            "status": "failed" if result.failure else "succeeded",
            "evidence": [item.evidence_id for item in result.evidence],
            **({"failure": {"code": result.failure.code, "message": result.failure.message}} if result.failure else {}),
        }

    @staticmethod
    def _element_id(request: Any, path: str) -> str:
        material = f"{request.source_identity}\x1f{request.state_digest}\x1f{path}"
        return "element:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _enumerate_elements(cls, value: Any, path: str = "$") -> tuple[tuple[str, Any], ...]:
        """Enumerate every JSON node, retaining containers and leaves."""
        found: list[tuple[str, Any]] = [(path, value)]
        if isinstance(value, Mapping):
            for key in sorted(value):
                found.extend(cls._enumerate_elements(value[key], f"{path}.{key}"))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found.extend(cls._enumerate_elements(item, f"{path}[{index}]"))
        return tuple(found)

    def close(self, run_id: str) -> None:
        state = self._run(run_id)
        if state.provider is not None:
            state.provider.close()


__all__ = ["CompiledInteractiveController", "CompiledRun"]
