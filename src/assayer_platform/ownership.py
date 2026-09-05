"""Portable ownership and fencing records for one mutable platform Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contract import PlatformContractError


@dataclass(frozen=True)
class RunOwnership:
    """The non-secret identity of the Host currently allowed to mutate a Run.

    ``owner_epoch`` is a fencing token.  A replacement Host gets a different
    token, so a late request from the previous owner cannot be accepted even if
    its process is still alive.
    """

    run_id: str
    owner_epoch: str
    process_id: int | None = None
    released: bool = False
    release_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not self.owner_epoch:
            raise PlatformContractError("INVALID_RUN_OWNERSHIP", "Run ownership identity is incomplete")
        if self.process_id is not None and (isinstance(self.process_id, bool) or self.process_id < 0):
            raise PlatformContractError("INVALID_RUN_OWNERSHIP", "Run ownership process ID is invalid")
        if self.released and not self.release_reason:
            raise PlatformContractError("INVALID_RUN_OWNERSHIP", "Released ownership must include a reason")

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schemaVersion": "1.0.0",
            "runId": self.run_id,
            "ownerEpoch": self.owner_epoch,
        }
        if self.process_id is not None:
            value["processId"] = self.process_id
        if self.released:
            value["released"] = True
            value["releaseReason"] = self.release_reason
        return value

    def released_record(self, reason: str) -> "RunOwnership":
        if not reason:
            raise PlatformContractError("INVALID_RUN_OWNERSHIP", "Ownership release reason is required")
        return RunOwnership(self.run_id, self.owner_epoch, self.process_id, True, reason)

    def assert_active(self, run_id: str, owner_epoch: str) -> None:
        if self.released or self.run_id != run_id or self.owner_epoch != owner_epoch:
            raise PlatformContractError(
                "STALE_RUN_OWNER",
                "This Host no longer owns the Run; resume it only after the current owner releases it",
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunOwnership":
        if not isinstance(value, Mapping):
            raise PlatformContractError("RUN_OWNERSHIP_FAILED", "Run ownership record is not an object")
        try:
            return cls(
                str(value["runId"]), str(value["ownerEpoch"]),
                int(value["processId"]) if value.get("processId") is not None else None,
                bool(value.get("released", False)),
                str(value["releaseReason"]) if value.get("releaseReason") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PlatformContractError(
                "RUN_OWNERSHIP_FAILED", "Run ownership record is malformed",
            ) from error


__all__ = ["RunOwnership"]
