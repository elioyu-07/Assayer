from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any


class HostError(Exception):
    """Structured protocol error exposed by Host Core."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False,
        next_step: str = "stop", owner: str | None = None,
        retry_disposition: str | None = None, request_id: str | None = None,
        contract_digest: str | None = None,
        errors: Sequence[Mapping[str, Any]] = (),
        correction_budget: Mapping[str, Any] | None = None,
        terminal_status: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.next_step = next_step
        self.owner = owner
        self.retry_disposition = retry_disposition
        self.request_id = request_id
        self.contract_digest = contract_digest
        self.errors = tuple(dict(item) for item in errors)
        self.correction_budget = (
            dict(correction_budget) if correction_budget is not None else None
        )
        self.terminal_status = terminal_status

    def __str__(self) -> str:
        if self.owner is None:
            return self.message
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)

    def as_dict(self) -> dict:
        result = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "requiredNextStep": self.next_step,
        }
        if self.owner is not None:
            result["owner"] = self.owner
        if self.retry_disposition is not None:
            result["retryDisposition"] = self.retry_disposition
        if self.request_id is not None:
            result["requestId"] = self.request_id
        if self.contract_digest is not None:
            result["contractDigest"] = self.contract_digest
        if self.errors:
            result["errors"] = [dict(item) for item in self.errors]
        if self.correction_budget is not None:
            result["correctionBudget"] = dict(self.correction_budget)
        if self.terminal_status is not None:
            result["terminalStatus"] = self.terminal_status
        return result
