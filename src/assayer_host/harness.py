"""Executable deterministic end-to-end harness for the Host Core.

The harness is intentionally explicit about its test adapters.  It is useful for
contract demonstrations and CI smoke tests; it does not pretend to audit a real
browser.  Production callers must inject browser-backed adapters, while the
Host's defaults remain fail-closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .action_safety import DeterministicActionAdapter
from .auth import CredentialVault, DeterministicLoginAdapter, LoginSecret
from .core import HostCore
from .evidence import DeterministicEvidenceAdapter
from .object_identity import DeterministicObjectIdentityAdapter, ObjectMatch, ObjectVerification
from .page import CandidateObservation, DeterministicPageAdapter, EntrypointObservation, PageObservation
from .recovery import DeterministicRecoveryAdapter


def _request(scan: dict | None, tool: str, key: str, revision: int, input_data: dict) -> dict:
    request = {
        "protocolVersion": "1.0",
        "requestId": f"harness-{key}",
        "agentTurnId": "harness-turn-001",
        "tool": tool,
        "idempotencyKey": key,
        "input": input_data,
    }
    if scan is not None:
        request.update({"scanId": scan["scanId"], "runId": scan["runId"], "expectedRunRevision": revision})
    return request


def _expect_ok(step: str, response: dict) -> dict:
    if response.get("status") != "ok" or not isinstance(response.get("result"), dict):
        raise RuntimeError(f"{step} failed: {json.dumps(response, ensure_ascii=False, sort_keys=True)}")
    return response["result"]


def _fixture_adapters(result: str) -> tuple[DeterministicPageAdapter, DeterministicObjectIdentityAdapter]:
    if result == "scanned_no_issue":
        return DeterministicPageAdapter(), DeterministicObjectIdentityAdapter()
    page = PageObservation(
        url="https://test.example.com/orders", origin="https://test.example.com", route="/orders",
        title="订单列表", state_kind="page",
        dom_material='<main><section role="search">订单筛选 查询</section></main>',
        identity_material="/orders|page|订单列表", visible_text="订单列表 订单筛选 查询",
        entrypoints=(EntrypointObservation("safe_action", "订单筛选", "查看筛选区"),),
        candidates=(CandidateObservation("filter_region", "订单筛选", "search", "orders-filter"),),
        network_summary={"pendingReadRequests": 0, "observedWrites": 0},
    )
    verification = ObjectVerification(
        status="matched", candidate_count=1,
        matched_dimensions=("role", "accessible_name", "business_region"),
        match=ObjectMatch(
            host_locator_id="locator-orders-filter-001", identity_material="filter_region|search|订单筛选|orders",
            role="search", accessible_name="订单筛选", visible_text="订单筛选 查询",
            x=20, y=80, width=640, height=120, viewport_width=1280, viewport_height=800,
        ),
    )
    return DeterministicPageAdapter(page), DeterministicObjectIdentityAdapter(verification)


def run_deterministic_harness(output_dir: str | Path, *, result: str = "scanned_no_issue") -> dict:
    """Run every Host lifecycle stage and return the final completion result.

    ``result`` may be ``scanned_no_issue`` or ``issue_found``.  All identifiers
    and observations come from deterministic adapters, making repeated runs
    structurally reproducible (timestamps and generated IDs are Host facts).
    """
    if result not in {"scanned_no_issue", "issue_found"}:
        raise ValueError("result must be scanned_no_issue or issue_found")
    destination = Path(output_dir).expanduser().resolve()
    page_adapter, identity_adapter = _fixture_adapters(result)
    vault = CredentialVault()
    vault.put("harness-credential", LoginSecret("harness-user", "deterministic-secret"))
    core = HostCore(
        credential_vault=vault,
        login_adapter=DeterministicLoginAdapter(),
        page_adapter=page_adapter,
        object_identity_adapter=identity_adapter,
        action_adapter=DeterministicActionAdapter(),
        recovery_adapter=DeterministicRecoveryAdapter(),
        evidence_adapter=DeterministicEvidenceAdapter(),
    )
    try:
        started_response = core.handle(_request(None, "start_audit", "bootstrap", 0, {
            "url": "https://test.example.com",
            "ruleRegistryVersion": "1.0.0",
            "outputDir": str(destination),
            "browserProfile": "deterministic",
            "credentialHandle": "harness-credential",
        }))
        scan = _expect_ok("bootstrap", started_response)
        page_id = scan["currentPageStateId"]

        inspected = _expect_ok("inspect_page", core.handle(_request(scan, "inspect_page", "inspect-page", 1, {
            "pageStateId": page_id, "include": ["route", "visibleText", "objects", "safeEntrypoints", "networkSummary"],
        })))
        if not inspected["candidateRefs"] or not inspected["entrypointRefs"]:
            raise RuntimeError("deterministic fixture did not discover its expected candidate and entrypoint")
        candidate_id = inspected["candidateRefs"][0]
        verified = _expect_ok("inspect_object", core.handle(_request(scan, "inspect_object", "inspect-object", 1, {"candidateId": candidate_id})))
        object_id = verified["objectId"]
        case_result = _expect_ok("begin_case", core.handle(_request(scan, "begin_case", "begin-case", 1, {
            "objectId": object_id,
            "rule": {"ruleId": "FUA-10", "version": "1.0.0"},
            "kind": "observation",
            "purpose": "端到端验证筛选规则覆盖",
            "plannedCoverageDimensions": ["filter_present", "query_action", "reset_action", "binding_to_list"],
        })))
        case_id = case_result["caseId"]
        action_response = core.handle(_request(scan, "perform_action", "perform-action", 2, {
            "pageStateId": page_id, "caseId": case_id, "objectId": object_id,
            "type": "focus", "intent": "观察筛选区", "parameters": {},
        }))
        _expect_ok("perform_action", action_response)
        evidence_response = core.handle(_request(scan, "capture_evidence", "capture-evidence", 3, {
            "pageStateId": page_id, "objectId": object_id, "caseId": case_id,
            "includeRawVisual": result == "issue_found",
        }))
        evidence_result = _expect_ok("capture_evidence", evidence_response)
        restore_response = core.handle(_request(scan, "restore_case", "restore-case", 4, {
            "caseId": case_id, "pageStateId": page_id, "objectId": object_id,
            "fallback": "refresh_and_replay_safe_entrypoints",
        }))
        _expect_ok("restore_case", restore_response)
        evidence_id = evidence_result["evidenceId"]
        finding_statuses = {dimension: "satisfied" for dimension in ("filter_present", "query_action", "reset_action", "binding_to_list")}
        if result == "issue_found":
            finding_statuses["reset_action"] = "violated"
        findings = _expect_ok("record_findings", core.handle(_request(scan, "record_findings", "record-findings", 5, {
            "objectId": object_id, "rule": {"ruleId": "FUA-10", "version": "1.0.0"},
            "findings": [{"dimension": dimension, "status": status,
                          "reasonText": "确定性 Evidence 支持该维度状态",
                          "evidenceRefs": [evidence_id], "caseRefs": [case_id]}
                         for dimension, status in finding_statuses.items()],
        })))
        prepare_input = {
            "objectId": object_id, "rule": {"ruleId": "FUA-10", "version": "1.0.0"},
            "result": result, "reasonText": "确定性 Harness 已完成规则覆盖和恢复屏障",
            "findingRefs": findings["findingRefs"], "evidenceRefs": [evidence_id], "caseRefs": [case_id],
        }
        if result == "issue_found":
            prepare_input.update({
                "rawVisualRef": evidence_result["screenshotRef"], "severity": "P2",
                "title": "筛选区缺少重置", "message": "筛选区未提供可验证的重置动作。",
                "impact": "用户无法一键恢复筛选条件。", "recommendation": "增加绑定同一列表的重置动作。",
            })
        prepared = _expect_ok("prepare_decision", core.handle(_request(scan, "prepare_decision", "prepare-decision", 6, prepare_input)))
        committed = core.handle(_request(scan, "commit_decision", "commit-decision", 7, {
            "pendingDecisionId": prepared["pendingDecisionId"],
        }))
        _expect_ok("commit_decision", committed)
        entrypoint_id = inspected["entrypointRefs"][0]
        completion = core.handle(_request(scan, "complete_audit", "complete-audit", 8, {
            "visitedPageStateRefs": [page_id], "processedObjectRefs": [object_id],
            "processedEntrypointRefs": [entrypoint_id], "skippedEntrypoints": [],
            "ruleSummaries": [{
                "rule": {"ruleId": "FUA-10", "version": "1.0.0"}, "assessmentCount": 1,
                "resultCounts": {result: 1}, "coverageComplete": True,
            }],
            "unprocessedEntrypointRefs": [], "completionReason": "确定性端到端 Harness 完成全部覆盖",
        }))
        completion_result = _expect_ok("complete_audit", completion)
        return {
            "mode": "deterministic",
            "scanId": scan["scanId"], "runId": scan["runId"], "result": result,
            "steps": ["bootstrap", "inspect_page", "inspect_object", "begin_case", "perform_action", "capture_evidence", "restore_case", "record_findings", "prepare_decision", "commit_decision", "complete_audit"],
            "completion": completion_result,
            "artifacts": sorted(completion_result.get("artifactPaths", [])),
            "outputDir": str(destination),
            "responses": {"action": action_response["status"], "restore": restore_response["status"], "commit": committed["status"]},
        }
    finally:
        core.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Assayer deterministic end-to-end Host harness")
    parser.add_argument("--output-dir", required=True, help="报告输出目录")
    parser.add_argument("--result", choices=("scanned_no_issue", "issue_found"), default="scanned_no_issue")
    args = parser.parse_args(argv)
    summary = run_deterministic_harness(args.output_dir, result=args.result)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
