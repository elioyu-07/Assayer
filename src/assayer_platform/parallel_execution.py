"""Fail-closed planning for domain-neutral WorkItem inspection parallelism."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator, RefResolver

from .contract import ExecutionProfile, PlatformContractError
from .registry import _schema_root


@dataclass(frozen=True)
class ParallelExecutionPlan:
    mode: str
    reason: str
    task_count: int
    worker_count: int
    ordered_merge: bool = True
    failure_isolation: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "mode": self.mode,
            "reason": self.reason,
            "taskCount": self.task_count,
            "workerCount": self.worker_count,
            "orderedMerge": self.ordered_merge,
            "failureIsolation": self.failure_isolation,
        }


class ParallelExecutionPlanner:
    """Compute the safe execution mode without widening plugin or runtime policy."""

    def __init__(self) -> None:
        root = _schema_root()
        schema = json.loads((root / "parallel-execution.schema.json").read_text(encoding="utf-8"))
        common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            schema,
            resolver=RefResolver(
                schema["$id"], schema,
                store={common["$id"]: common, "common.schema.json": common},
            ),
        )

    def plan(
        self,
        profile: ExecutionProfile,
        limits: Mapping[str, Any],
        *,
        task_count: int,
    ) -> ParallelExecutionPlan:
        if not isinstance(task_count, int) or isinstance(task_count, bool) or task_count < 0:
            raise PlatformContractError(
                "PARALLEL_TASK_COUNT_INVALID",
                "Parallel task count must be a nonnegative integer",
            )
        if profile.parallelism == "allowed" and profile.ordering != "independent":
            raise PlatformContractError(
                "PARALLEL_ORDERING_UNSAFE",
                "Parallel inspection requires independent WorkItem ordering",
            )
        if profile.parallelism != "allowed":
            result = ParallelExecutionPlan("serial", "plugin_forbidden", task_count, 1)
        elif task_count < 2:
            result = ParallelExecutionPlan("serial", "insufficient_work", task_count, 1)
        elif "maxConcurrency" not in limits:
            result = ParallelExecutionPlan("serial", "limit_missing", task_count, 1)
        else:
            limit = limits["maxConcurrency"]
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise PlatformContractError(
                    "PARALLEL_LIMIT_INVALID",
                    "Runtime maxConcurrency must be a positive integer",
                )
            if limit == 1:
                result = ParallelExecutionPlan("serial", "limit_one", task_count, 1)
            else:
                result = ParallelExecutionPlan(
                    "parallel", "enabled", task_count, min(limit, task_count),
                )
        self._validator.validate(result.as_dict())
        return result


__all__ = ["ParallelExecutionPlan", "ParallelExecutionPlanner"]
