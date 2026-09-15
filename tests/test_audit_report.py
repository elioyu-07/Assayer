"""Platform-owned formal audit report projection."""

from __future__ import annotations

import dataclasses
from pathlib import Path
import tempfile
import unittest

from assayer_platform.audit_report import render_audit_report
from assayer_platform.contract import (
    CommitReceipt,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformLedger,
    PlatformRun,
    WorkItem,
)
from assayer_platform.kernel import PlatformKernel
from assayer_platform.ledger import JsonPlatformLedgerStore
from assayer_plugin_sdk.contract import PlatformContext
from tests.helpers.config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider


class AuditReportProjectionTests(unittest.TestCase):
    @staticmethod
    def common_review_ledger(*, pending: bool = True) -> PlatformLedger:
        work_item = WorkItem(
            "document:refund", "markdown", "/private/tmp/order-refund-spec.md",
            "0" * 64,
        )
        chunk_ref = "document-chunk:refund:1"
        evidence = EvidenceRecord(
            "document-evidence:refund", work_item.work_item_id, "SPEC-001", "3.1.0",
            "document_snapshot", work_item.identity,
            {
                "sourceChunks": [{
                    "source_chunk_id": chunk_ref,
                    "startLine": 118,
                    "endLine": 132,
                    "headingPath": ["退款权限"],
                    "excerpt": "客服人员可以在订单详情页发起退款。",
                }],
            },
        )
        packet = InvestigationPacket(
            work_item, "SPEC-001", "3.1.0",
            (
                DimensionObservation("权限约束", ("拒绝路径未定义。",), (chunk_ref,), "violated"),
                DimensionObservation("财务审批", ("审批制度不可用。",), (chunk_ref,), "unresolved"),
            ),
            (evidence,), "not_required",
        )
        common_review = [
            {
                "kind": "candidate", "subject": "PERM-001",
                "value": {
                    "disposition": "confirmed", "reason": "现有证据确认该问题。",
                    "support": {"refs": [chunk_ref]},
                    "finding": {
                        "title": "退款权限缺少拒绝规则",
                        "message": "文档未规定越权、超额及状态受限情况下的处理方式。",
                        "severity": "P1",
                        "recommendation": "补充角色范围、金额上限及拒绝响应，并增加越权退款验收案例。",
                        "support": {"refs": [chunk_ref]},
                    },
                },
            },
            {
                "kind": "dimension", "subject": "权限约束",
                "value": {
                    "dimension": "权限约束", "verdict": "violated",
                    "reason": "权限约束未满足。",
                    "support": {"refs": [chunk_ref]},
                },
            },
        ]
        if pending:
            common_review.append({
                "kind": "dimension", "subject": "财务审批",
                "value": {
                    "dimension": "财务审批", "verdict": "unresolved",
                    "reason": "现有材料不足以确认审批条件。",
                    "support": {"refs": [chunk_ref]},
                    "unknown": {
                        "reason": "外部财务制度未提供。",
                        "missingInformation": ["金额阈值", "审批失败处理"],
                    },
                },
            })
        decision = DecisionProposal(
            work_item.work_item_id, "SPEC-001", "3.1.0", "issue_found",
            (
                Finding("权限约束", "violated", "权限约束未满足。"),
                *(
                    (Finding("财务审批", "unresolved", "外部财务制度未提供。"),)
                    if pending else ()
                ),
            ),
            "审计发现确认问题。", {"commonReview": common_review},
        )
        run = PlatformRun(
            "run-formal-report", "ass-spec", "3.1.0", "SPEC-001", "3.1.0",
            "0" * 64, subject_kinds=("markdown",),
        )
        receipt = CommitReceipt(
            "commit:refund", work_item.work_item_id, "SPEC-001", "3.1.0",
            "issue_found", "durable",
        )
        return PlatformLedger(
            run, "completed", receipts=(receipt,), work_items=(work_item,),
            investigations=(packet,), decisions=(decision,),
        )

    def test_report_uses_fixed_formal_tables_and_one_row_per_problem(self):
        report = render_audit_report(self.common_review_ledger()).decode("utf-8")

        self.assertIn("# 审计报告", report)
        self.assertIn("## 一、审计结论", report)
        self.assertIn("## 二、问题明细", report)
        self.assertIn("## 三、待确认事项", report)
        self.assertIn("## 四、覆盖情况", report)
        self.assertIn("## 五、审计信息", report)
        self.assertIn("| 严重程度 | 问题位置 | 问题说明 | 判定依据 | 整改要求 |", report)
        self.assertIn("order-refund-spec.md → 退款权限 → 第118–132行", report)
        self.assertIn("“客服人员可以在订单详情页发起退款。”", report)
        self.assertEqual(report.count("退款权限缺少拒绝规则"), 1)
        self.assertNotIn("检查项“权限约束”未满足要求", report)
        self.assertNotIn("/private/tmp", report)

    def test_pending_chapter_is_omitted_when_no_item_is_pending(self):
        report = render_audit_report(
            self.common_review_ledger(pending=False),
        ).decode("utf-8")

        self.assertNotIn("## 三、待确认事项", report)
        self.assertIn("| 已完成 | 不通过 | 1 | 0 | 完整 |", report)

    def test_domain_result_links_render_one_actionable_root_cause(self):
        ledger = self.common_review_ledger(pending=False)
        packet = dataclasses.replace(
            ledger.investigations[0],
            dimensions=(
                DimensionObservation(
                    "CHK-01", ("变更就绪内容不完整。",),
                    (ledger.investigations[0].evidence[0].evidence_id,), "violated",
                ),
                DimensionObservation(
                    "CHK-11", ("缺少发布回退方案。",),
                    (ledger.investigations[0].evidence[0].evidence_id,), "violated",
                ),
            ),
        )
        evidence_id = packet.evidence[0].evidence_id
        decision = dataclasses.replace(
            ledger.decisions[0],
            findings=(
                Finding("CHK-01", "violated", "变更就绪内容不完整。"),
                Finding("CHK-11", "violated", "缺少发布回退方案。"),
            ),
            details={
                "result_delivery": {
                    "schemaVersion": "1.0.0",
                    "status": "complete",
                    "evidenceClaims": [{
                        "claimId": "claim-change-readiness",
                        "kind": "direct",
                        "evidenceRefs": [evidence_id],
                        "scope": {
                            "sourceRef": evidence_id,
                            "documentPath": "[LOCAL_PATH]",
                            "startLine": 118,
                            "endLine": 132,
                        },
                        "observed": [
                            "文档描述了阶段演进，但没有发布切换、失败回退和上线验证方案。",
                        ],
                        "conclusion": "现网变更缺少可执行的发布闭环。",
                    }],
                    "remediations": [{
                        "remediationId": "finding-change-readiness",
                        "title": "现网变更缺少发布闭环",
                        "severity": "P2",
                        "dimensions": ["CHK-11", "CHK-01"],
                        "affectedElements": ["发布切换", "失败回退"],
                        "evidenceRefs": [evidence_id],
                        "claimRefs": ["claim-change-readiness"],
                        "problem": "未定义发布切换、失败回退和上线验证。",
                        "impact": "发布团队无法判定失败后的恢复路径。",
                        "recommendation": "补充发布切换和回退方案。",
                        "nextAction": "由技术负责人确认切换与回退步骤。",
                        "closureEvidence": "演练记录证明切换和回退均可执行。",
                        "owner": {"status": "assigned", "identity": "技术负责人"},
                    }],
                },
            },
        )
        report = render_audit_report(dataclasses.replace(
            ledger, investigations=(packet,), decisions=(decision,),
        )).decode("utf-8")

        self.assertIn("| 已完成 | 不通过 | 1 | 0 | 完整 |", report)
        self.assertIn("| P2 | order-refund-spec.md → 第118–132行", report)
        self.assertIn(
            "文档描述了阶段演进，但没有发布切换、失败回退和上线验证方案。",
            report,
        )
        self.assertIn("整改措施：补充发布切换和回退方案。", report)
        self.assertIn("执行要求：由技术负责人确认切换与回退步骤。", report)
        self.assertIn("完成标准：演练记录证明切换和回退均可执行。", report)
        self.assertIn("2 个检查项未通过，归并为 1 个独立问题。", report)
        self.assertNotIn("依据已冻结证据形成该判定", report)
        self.assertNotIn("依据检查要求完成整改并补充验证证据", report)
        self.assertNotIn("检查项“CHK-01”未满足要求", report)
        self.assertNotIn("检查项“CHK-11”未满足要求", report)

    def test_reviewer_origin_finding_groups_violated_dimensions(self):
        ledger = self.common_review_ledger(pending=False)
        chunk_ref = "document-chunk:refund:1"
        packet = dataclasses.replace(
            ledger.investigations[0],
            dimensions=(
                DimensionObservation(
                    "CHK-05", ("重试规则前后矛盾。",), (chunk_ref,), "violated",
                ),
                DimensionObservation(
                    "CHK-17", ("失败恢复责任不一致。",), (chunk_ref,), "violated",
                ),
            ),
        )
        support = {"refs": [chunk_ref], "reason": "两处规则无法同时成立。"}
        common_review = [
            {
                "kind": "dimension", "subject": "CHK-05",
                "value": {
                    "dimension": "CHK-05", "verdict": "violated",
                    "reason": "重试规则前后矛盾。",
                    "applicability": {"state": "applicable"},
                    "confidence": {"level": "high"}, "support": support,
                    "findings": [{
                        "title": "消息失败后的处理规则前后矛盾",
                        "message": (
                            "消息失败后的处理规则前后矛盾。"
                            "研发无法判断是否应当重试。"
                        ),
                        "severity": "P2",
                        "recommendation": "确定唯一的失败处理规则并统一相关章节。",
                        "support": support,
                        "affectedDimensions": ["CHK-05", "CHK-17"],
                    }],
                },
            },
            {
                "kind": "dimension", "subject": "CHK-17",
                "value": {
                    "dimension": "CHK-17", "verdict": "violated",
                    "reason": "失败恢复责任不一致。",
                    "applicability": {"state": "applicable"},
                    "confidence": {"level": "high"}, "support": support,
                },
            },
        ]
        decision = dataclasses.replace(
            ledger.decisions[0],
            findings=(
                Finding("CHK-05", "violated", "重试规则前后矛盾。"),
                Finding("CHK-17", "violated", "失败恢复责任不一致。"),
            ),
            details={"commonReview": common_review},
        )

        report = render_audit_report(dataclasses.replace(
            ledger, investigations=(packet,), decisions=(decision,),
        )).decode("utf-8")

        self.assertIn("| 已完成 | 不通过 | 1 | 0 | 完整 |", report)
        self.assertEqual(report.count("消息失败后的处理规则前后矛盾"), 1)
        self.assertIn("消息失败后的处理规则前后矛盾：研发无法判断是否应当重试。", report)
        self.assertIn("| P2 | order-refund-spec.md → 退款权限 → 第118–132行", report)
        self.assertIn("2 个检查项未通过，归并为 1 个独立问题。", report)
        self.assertNotIn("检查项“CHK-05”未满足要求", report)
        self.assertNotIn("检查项“CHK-17”未满足要求", report)

    def test_terminal_ledger_store_publishes_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "broken.json"
            source.write_text("not-json", encoding="utf-8")
            store = JsonPlatformLedgerStore(root / "output")
            result = PlatformKernel(store).run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-report-artifact", frozenset({"structured_read"})),
            )

            report_path = store.root / f"{result.run_id}.audit-report.md"

            self.assertTrue(report_path.is_file())
            self.assertIn("## 二、问题明细", report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
