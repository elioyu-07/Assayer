"""Deterministic compiler for Simple SDK invariant declarations.

This module deliberately emits all views from one descriptor: JSON Schema
annotations, Agent guidance, runtime failures, and generated example tests.
It does not attempt to translate arbitrary Python into JSON Schema.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import inspect
from typing import Any

from .contract import PlatformContractError


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class InvariantDescriptor:
    rule_id: str
    guidance: str
    location: str
    positive: tuple[Any, ...]
    negative: tuple[Any, ...]
    validate: Callable[[Any], Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "guidance": self.guidance,
            "location": self.location,
            "positiveCases": _plain(self.positive),
            "negativeCases": _plain(self.negative),
        }


@dataclass(frozen=True)
class InvariantProgram:
    descriptors: tuple[InvariantDescriptor, ...]

    @property
    def schema_annotation(self) -> dict[str, Any]:
        return {
            "x-assayer-invariants": [item.as_dict() for item in self.descriptors],
        }

    @property
    def agent_rules(self) -> tuple[dict[str, str], ...]:
        return tuple({
            "ruleId": item.rule_id,
            "instruction": item.guidance,
            "location": item.location,
        } for item in self.descriptors)

    @property
    def generated_cases(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "caseId": f"{item.rule_id}:{kind}:{ordinal}",
                "ruleId": item.rule_id,
                "kind": kind,
                "input": _plain(value),
                "expectedValid": expected,
            }
            for item in self.descriptors
            for kind, values, expected in (
                ("positive", item.positive, True),
                ("negative", item.negative, False),
            )
            for ordinal, value in enumerate(values, 1)
        )

    def validate(self, value: Any) -> None:
        errors: list[Mapping[str, Any]] = []
        for item in self.descriptors:
            try:
                valid = item.validate(value)
            except Exception as error:
                raise PlatformContractError(
                    "INVARIANT_EXECUTION_FAILED",
                    f"Invariant {item.rule_id} failed to execute: {error}",
                    errors=({
                        "ruleId": item.rule_id,
                        "location": item.location,
                        "pointer": item.location,
                    },),
                ) from error
            if not isinstance(valid, bool):
                raise PlatformContractError(
                    "INVALID_INVARIANT_RESULT",
                    f"Invariant {item.rule_id} must return bool",
                    errors=({
                        "ruleId": item.rule_id,
                        "location": item.location,
                        "pointer": item.location,
                    },),
                )
            if not valid:
                errors.append({
                    "ruleId": item.rule_id,
                    "location": item.location,
                    "pointer": item.location,
                    "message": item.guidance,
                })
        if errors:
            raise PlatformContractError(
                "DOMAIN_INVARIANT_VIOLATION",
                "Domain value violates declared invariants",
                errors=tuple(errors),
            )

    def run_generated_cases(self) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        by_rule = {item.rule_id: item for item in self.descriptors}
        for case in self.generated_cases:
            actual = by_rule[case["ruleId"]].validate(case["input"])
            if not isinstance(actual, bool) or actual is not case["expectedValid"]:
                raise PlatformContractError(
                    "INVARIANT_CASE_FAILED",
                    f"Generated invariant case failed: {case['caseId']}",
                    errors=({
                        "ruleId": case["ruleId"],
                        "location": by_rule[case["ruleId"]].location,
                    },),
                )
            results.append({"caseId": case["caseId"], "status": "passed"})
        return tuple(results)


def compile_invariants(owner: object | Iterable[Callable[..., Any]]) -> InvariantProgram:
    """Extract and validate invariant declarations in deterministic rule order."""
    if isinstance(owner, Iterable) and not isinstance(owner, (str, bytes, type)):
        callables = tuple(owner)
    else:
        callables = tuple(
            member for _, member in inspect.getmembers(owner, callable)
            if hasattr(member, "_assayer_invariant_declaration")
        )
    descriptors: list[InvariantDescriptor] = []
    seen: set[str] = set()
    for function in callables:
        declaration = getattr(function, "_assayer_invariant_declaration", None)
        if not isinstance(declaration, Mapping):
            continue
        rule_id = str(declaration["rule"])
        if rule_id in seen:
            raise PlatformContractError(
                "DUPLICATE_INVARIANT", f"Invariant rule is declared twice: {rule_id}",
            )
        signature = inspect.signature(function)
        try:
            signature.bind(object())
        except TypeError as error:
            raise PlatformContractError(
                "INVALID_SIMPLE_PLUGIN",
                f"Invariant {rule_id} must accept one review value",
            ) from error
        seen.add(rule_id)
        descriptors.append(InvariantDescriptor(
            rule_id,
            str(declaration["guidance"]),
            str(declaration["location"]),
            tuple(declaration["positive"]),
            tuple(declaration["negative"]),
            function,
        ))
    descriptors.sort(key=lambda item: item.rule_id)
    return InvariantProgram(tuple(descriptors))


__all__ = ["InvariantDescriptor", "InvariantProgram", "compile_invariants"]
