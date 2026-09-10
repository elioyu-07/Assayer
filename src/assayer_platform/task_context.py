"""Host-owned semantic task context for the domain-result boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class TaskContext:
    """Immutable binding kept by the Host, never authored by the Agent."""

    run_id: str
    kind: str
    work_item_id: str
    collection_id: str | None
    item_ids: tuple[str, ...]
    contract_digest: str
    task_digest: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_ids", tuple(str(item) for item in self.item_ids))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @classmethod
    def from_task(
        cls, run_id: str, task: Mapping[str, Any], *,
        contract_digest: str, task_digest: str,
    ) -> "TaskContext":
        return cls(
            run_id=run_id,
            kind=str(task.get("kind") or ""),
            work_item_id=str(task.get("workItemId") or ""),
            collection_id=(
                str(task["collectionId"])
                if task.get("collectionId") is not None else None
            ),
            item_ids=tuple(task.get("itemIds", ())),
            contract_digest=contract_digest,
            task_digest=task_digest,
            payload=dict(task),
        )


__all__ = ["TaskContext"]
