"""Read-only compatibility bridge to the existing product MCP facade."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Protocol

from assayer_platform.contract import (
    CheckContract,
    CommitReceipt,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    WorkItem,
)


class ProductToolCaller(Protocol):
    def call_tool(self, name: str, arguments: object) -> Mapping[str, Any]: ...


def _structured(response: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    value = response.get("structuredContent")
    if not isinstance(value, Mapping):
        raise RuntimeError(f"The frontend runtime returned an invalid {operation} response")
    if value.get("status") != "ok":
        error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
        raise RuntimeError(str(error.get("message", f"The frontend {operation} operation failed")))
    result = value.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError(f"The frontend runtime omitted the {operation} result")
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ProductFrontendRuntime:
    """Translate current product tools into read-only platform investigation facts.

    The caller must own an already-started frontend audit.  Discovery and
    investigation remain delegated to the existing facade, preserving its
    browser safety, recovery, and durable ledger rules.
    """

    def __init__(self, caller: ProductToolCaller):
        self._caller = caller

    def discover_work_items(self, scope: Any, context: PlatformContext) -> Sequence[WorkItem]:
        del scope, context
        result = _structured(self._caller.call_tool("discover_scope", {}), "discovery")
        current_page = result.get("currentPageStateId")
        if not isinstance(current_page, str) or not current_page:
            raise RuntimeError("Frontend discovery did not return the current runtime state")
        items = []
        for candidate in result.get("candidates", []):
            if not isinstance(candidate, Mapping) or candidate.get("status") != "pending":
                continue
            candidate_id = candidate.get("candidateId")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise RuntimeError("Frontend discovery returned a candidate without an identity")
            potential = candidate.get("potentialRules", [])
            if not any(isinstance(rule, Mapping) and rule.get("ruleId") == "FUA-10" for rule in potential):
                continue
            state_digest = _digest({
                "pageStateId": current_page,
                "candidateId": candidate_id,
                "kind": candidate.get("kind"),
                "label": candidate.get("label"),
            })
            items.append(WorkItem(
                f"frontend:{candidate_id}", "frontend_object", candidate_id, state_digest,
                {
                    "candidateId": candidate_id,
                    "pageStateId": current_page,
                    "runtime_state_digest": state_digest,
                    "kind": candidate.get("kind", "unknown"),
                    "label": candidate.get("label", ""),
                },
            ))
        return tuple(items)

    def inspect_work_items(
        self, work_items: Sequence[WorkItem], check: CheckContract, context: PlatformContext,
    ) -> Sequence[InvestigationPacket]:
        del context
        packets = []
        for item in work_items:
            result = _structured(self._caller.call_tool("investigate_object", {
                "candidateId": item.metadata["candidateId"],
                "pageStateId": item.metadata["pageStateId"],
                "rule": {"ruleId": check.check_id, "version": check.version},
                "plannedCoverageDimensions": list(check.dimensions),
                "purpose": "Collect decision-ready evidence through the frontend compatibility plugin.",
            }), "investigation")
            evidence = (
                EvidenceRecord(
                    f"platform:{item.work_item_id}:visual", item.work_item_id,
                    check.check_id, check.version, "runtime_visual", item.identity,
                    {"observation": result.get("observation", {}), "legacyEvidenceRefs": tuple(result.get("evidenceRefs", ()))},
                ),
                EvidenceRecord(
                    f"platform:{item.work_item_id}:structured", item.work_item_id,
                    check.check_id, check.version, "runtime_dom", item.identity,
                    {"evidence": result.get("evidence", {}), "legacyEvidenceRefs": tuple(result.get("evidenceRefs", ()))},
                ),
            )
            refs = tuple(record.evidence_id for record in evidence)
            dimensions = tuple(DimensionObservation(
                dimension,
                ("Aligned frontend evidence is available for semantic review.",),
                refs,
                "unresolved",
            ) for dimension in check.dimensions)
            recovery = result.get("recovery") if isinstance(result.get("recovery"), Mapping) else {}
            recovery_status = "restored" if result.get("readyForDecision") and recovery.get("finalStatus") == "restored" else "failed"
            packets.append(InvestigationPacket(
                item, check.check_id, check.version, dimensions, evidence, recovery_status,
                result.get("caseId"),
                {"objectId": result.get("objectId"), "rawVisualRef": result.get("rawVisualRef")},
            ))
        return tuple(packets)


class FrontendDecisionCommitter:
    """Commit a validated frontend proposal through the product facade.

    The facade still performs the browser-domain atomic write for backwards
    compatibility, but the receipt returned here is a Platform receipt.  The
    Host Assessment is recorded as a projection reference in receipt metadata;
    it is deliberately not used as the Platform commit identity.
    """

    def __init__(self, caller: ProductToolCaller):
        self._caller = caller

    @staticmethod
    def _legacy_refs(packet: InvestigationPacket) -> tuple[str, ...]:
        refs = []
        for evidence in packet.evidence:
            values = evidence.payload.get("legacyEvidenceRefs", ())
            if isinstance(values, str):
                values = (values,)
            refs.extend(value for value in values if isinstance(value, str))
        return tuple(dict.fromkeys(refs))

    def _build_arguments(
        self, proposal: DecisionProposal, packet: InvestigationPacket,
        check: CheckContract,
    ) -> dict[str, Any]:
        object_id = packet.metadata.get("objectId")
        case_ref = packet.case_ref
        legacy_refs = self._legacy_refs(packet)
        if not isinstance(object_id, str) or not object_id:
            raise PlatformContractError("COMMIT_REFERENCE", "Frontend packet does not contain the verified object reference")
        if not isinstance(case_ref, str) or not case_ref:
            raise PlatformContractError("COMMIT_REFERENCE", "Frontend packet does not contain the recovered Case reference")
        if not legacy_refs:
            raise PlatformContractError("COMMIT_REFERENCE", "Frontend packet does not contain legacy Evidence references")
        findings = [{
            "dimension": finding.dimension,
            "status": finding.status,
            "reasonText": finding.reason,
            "evidenceRefs": list(legacy_refs),
            "caseRefs": [case_ref],
        } for finding in proposal.findings]
        arguments: dict[str, Any] = {
            "objectId": object_id,
            "rule": {"ruleId": check.check_id, "version": check.version},
            "result": proposal.result,
            "reasonText": proposal.reason,
            "findings": findings,
            "evidenceRefs": list(legacy_refs),
            "caseRefs": [case_ref],
        }
        if proposal.result == "needs_review":
            blocker = proposal.details.get("blocker")
            arguments["blocker"] = dict(blocker) if isinstance(blocker, Mapping) else {
                "code": "SEMANTIC_REVIEW_REQUIRED", "message": "Semantic review is required.",
            }
        if proposal.result == "issue_found":
            required = ("severity", "title", "message", "impact", "recommendation")
            missing = [key for key in required if key not in proposal.details]
            if missing:
                raise PlatformContractError("COMMIT_REFERENCE", f"Issue decision is missing required details: {', '.join(missing)}")
            raw_visual_ref = proposal.details.get("rawVisualRef")
            if not isinstance(raw_visual_ref, str) or not raw_visual_ref:
                raise PlatformContractError("COMMIT_REFERENCE", "Issue decision does not have a captured Raw Visual reference")
            arguments.update({key: proposal.details[key] for key in required})
            arguments["rawVisualRef"] = raw_visual_ref
        return arguments

    def commit(
        self, proposal: DecisionProposal, packet: InvestigationPacket,
        check: CheckContract, context: PlatformContext,
    ) -> CommitReceipt:
        arguments = self._build_arguments(proposal, packet, check)
        response = self._caller.call_tool("prepare_decision", arguments)
        structured = response.get("structuredContent") if isinstance(response, Mapping) else None
        if not isinstance(structured, Mapping):
            raise PlatformContractError("COMMIT_FAILED", "Frontend facade returned an invalid commit response")
        if structured.get("status") != "ok":
            error = structured.get("error") if isinstance(structured.get("error"), Mapping) else {}
            raise PlatformContractError(
                str(error.get("code", "COMMIT_FAILED")),
                str(error.get("message", "Frontend decision commit failed")),
            )
        result = structured.get("result") if isinstance(structured.get("result"), Mapping) else {}
        return self.receipt_for_host_result(proposal, packet, check, context, result)

    def commit_atomic(
        self, proposal: DecisionProposal, packet: InvestigationPacket,
        check: CheckContract, context: PlatformContext,
        host_commit: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> tuple[CommitReceipt | None, Mapping[str, Any]]:
        """Own the interactive decision seam while delegating Host mechanics.

        ``host_commit`` is a narrow callback for the browser-domain atomic
        sequence (record Finding -> prepare -> commit). The plugin owns the
        proposal-to-command mapping and Platform receipt creation; the Host
        owns only its existing safety and persistence mechanics.
        """
        arguments = self._build_arguments(proposal, packet, check)
        committed = host_commit(arguments)
        response = committed.get("structuredContent") if isinstance(committed, Mapping) else None
        if not isinstance(response, Mapping):
            raise PlatformContractError("COMMIT_FAILED", "Frontend facade returned an invalid commit response")
        if response.get("status") != "ok":
            return None, committed
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise PlatformContractError("COMMIT_FAILED", "Frontend facade omitted the committed Assessment result")
        receipt = self.receipt_for_host_result(proposal, packet, check, context, result)
        return receipt, committed

    @staticmethod
    def receipt_for_host_result(
        proposal: DecisionProposal,
        packet: InvestigationPacket,
        check: CheckContract,
        context: PlatformContext,
        result: Mapping[str, Any],
    ) -> CommitReceipt:
        """Translate one already-committed Host Assessment into a receipt.

        This narrow seam lets the interactive compatibility facade reuse the
        plugin-owned commit contract after it has executed the legacy atomic
        Host sequence.  The deterministic Platform ID is stable for one Run,
        WorkItem, Check, and Host result, while ``hostAssessmentId`` preserves
        the exact projection that must reconcile at publication time.
        """
        assessment_id = result.get("assessmentId")
        if not isinstance(assessment_id, str) or not assessment_id:
            raise PlatformContractError("COMMIT_FAILED", "Frontend facade did not return an Assessment reference")
        object_id = packet.metadata.get("objectId") or packet.work_item.identity
        result_digest = _digest(dict(result))
        commit_digest = _digest({
            "runId": context.run_id,
            "workItemId": proposal.work_item_id,
            "checkId": check.check_id,
            "checkVersion": check.version,
            "result": proposal.result,
            "hostAssessmentId": assessment_id,
        })[:32]
        return CommitReceipt(
            f"platform-commit:{context.run_id}:{commit_digest}",
            proposal.work_item_id, check.check_id, check.version,
            proposal.result, "durable",
            {
                "hostAssessmentId": assessment_id,
                "hostObjectId": object_id,
                "hostResultDigest": result_digest,
            },
            "platform",
        )


# Temporary source-compatibility alias for callers that imported the first
# extraction name. New registrations use the authority-accurate class name.
FrontendLedgerCommitter = FrontendDecisionCommitter
