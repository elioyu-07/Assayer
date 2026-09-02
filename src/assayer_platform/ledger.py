"""Durable JSON storage for the generic platform ledger."""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .contract import PlatformContractError, PlatformLedger


class PlatformLedgerStore(Protocol):
    def save(self, ledger: PlatformLedger) -> Path | None: ...

    def load(self, run_id: str) -> dict[str, Any] | None: ...


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def _journal_line(event: Any) -> str:
    subject = f" work_item={event.work_item_id}" if event.work_item_id else ""
    operation = f" operation={event.operation_id}" if event.operation_id else ""
    details = " ".join(f"{key}={value}" for key, value in event.details.items() if value is not None)
    suffix = f" {details}" if details else ""
    return f"{event.sequence:04d} {event.occurred_at} {event.name} outcome={event.outcome}{operation}{subject}{suffix}\n"


def render_platform_artifacts(ledger: PlatformLedger) -> dict[str, bytes]:
    """Render the canonical platform ledger and readable event timelines."""
    return {
        "platform-ledger.json": (json.dumps(_plain(ledger), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        "platform-events.jsonl": "".join(
            json.dumps(_plain(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in ledger.events
        ).encode("utf-8"),
        "platform-run.log": "".join(_journal_line(event) for event in ledger.events).encode("utf-8"),
    }


def write_platform_artifacts(ledger: PlatformLedger, root: str | Path) -> tuple[Path, ...]:
    """Atomically write platform trace files into a scan output directory."""
    destination_root = Path(root).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in render_platform_artifacts(ledger).items():
        destination = destination_root / name
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        written.append(destination)
    return tuple(written)


class JsonPlatformLedgerStore:
    """Persist the canonical ledger plus machine and human event timelines."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{2,127}", run_id):
            raise PlatformContractError("INVALID_RUN_ID", "Run ID is not safe for ledger persistence")
        return self.root / f"{run_id}.platform-ledger.json"

    def save(self, ledger: PlatformLedger) -> Path:
        destination = self._path(ledger.run.run_id)
        rendered = render_platform_artifacts(ledger)
        self._atomic_write(destination, rendered["platform-ledger.json"].decode("utf-8"))
        events_path = self.root / f"{ledger.run.run_id}.platform-events.jsonl"
        self._atomic_write(events_path, rendered["platform-events.jsonl"].decode("utf-8"))
        journal_path = self.root / f"{ledger.run.run_id}.platform-run.log"
        self._atomic_write(journal_path, rendered["platform-run.log"].decode("utf-8"))
        return destination

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(destination: Path, text: str) -> None:
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(destination)
