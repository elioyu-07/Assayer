"""Rule-specific evaluation behind a site-agnostic runtime boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RuleEvaluation:
    result: str
    reason: str
    blocker: dict | None = None


class RuntimeRuleEvaluator(Protocol):
    def supports(self, rule: dict) -> bool: ...
    def evaluate(self, rule: dict, evidence: dict) -> RuleEvaluation: ...


class Fua10RuntimeEvaluator:
    """Evaluate the versioned FUA-10 evidence contract, never a site."""

    def supports(self, rule: dict) -> bool:
        return rule.get("ruleId") == "FUA-10" and rule.get("version") == "1.0.0"

    def evaluate(self, rule: dict, evidence: dict) -> RuleEvaluation:
        content = ((evidence.get("payload") or {}).get("content")
                   if isinstance(evidence, dict) else None)
        if not isinstance(content, dict):
            return RuleEvaluation("needs_review", "Evidence 不是可解释的结构化运行态事实", {
                "code": "EVIDENCE_SHAPE_UNAVAILABLE", "message": "缺少结构化控件和列表绑定事实",
            })
        controls = content.get("controls")
        signals = content.get("bindingSignals")
        if not isinstance(controls, list) or not isinstance(signals, dict):
            return RuleEvaluation("needs_review", "Evidence 缺少 FUA-10 所需的控件或绑定事实", {
                "code": "COVERAGE_FACT_UNAVAILABLE", "message": "无法从运行态证据确认筛选区和列表的关系",
            })
        has_filter = any(isinstance(item, dict) and item.get("tag") in {"input", "select", "textarea"}
                         for item in controls)
        has_query = any(isinstance(item, dict) and item.get("semanticAction") == "query" for item in controls)
        has_reset = any(isinstance(item, dict) and item.get("semanticAction") == "reset" for item in controls)
        binding = signals.get("sameContainerList") is True and signals.get("pageListCount") == 1
        if all((has_filter, has_query, has_reset, binding)):
            return RuleEvaluation(
                "scanned_no_issue",
                "运行态证据完成 FUA-10 四个最低覆盖维度，未发现相反证据",
            )
        missing = [name for name, value in (
            ("filter_present", has_filter), ("query_action", has_query),
            ("reset_action", has_reset), ("binding_to_list", binding),
        ) if not value]
        detail = ", ".join(missing)
        return RuleEvaluation(
            "needs_review", f"运行态证据尚未充分证明 FUA-10：缺少 {detail}",
            {"code": "FUA10_COVERAGE_INCOMPLETE", "message": f"未能确认维度：{detail}"},
        )


class RuleEvaluationEngine:
    """Dispatch by a frozen rule reference; unsupported rules fail closed."""

    def __init__(self, evaluators: tuple[RuntimeRuleEvaluator, ...] | None = None):
        self._evaluators = evaluators or (Fua10RuntimeEvaluator(),)

    def evaluate(self, rule: dict, evidence: dict) -> RuleEvaluation:
        evaluator = next((item for item in self._evaluators if item.supports(rule)), None)
        if evaluator is None:
            return RuleEvaluation("needs_review", "当前运行器没有该冻结规则的语义评估器", {
                "code": "RULE_EVALUATOR_UNAVAILABLE", "message": "规则只能保守进入人工复核",
            })
        return evaluator.evaluate(rule, evidence)
