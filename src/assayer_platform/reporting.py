"""Domain-neutral report publication from committed platform decisions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .contract import Artifact, PlatformContractError, PlatformRunResult


class JsonSummaryPublisher:
    """Write a concise report that contains only successfully committed decisions."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def publish(self, result: PlatformRunResult) -> Artifact:
        if result.ledger is None or result.status == "failed":
            raise PlatformContractError("PUBLICATION_GATE", "A non-failed platform ledger is required")
        if len(result.decisions) != len(result.receipts) or not result.receipts:
            raise PlatformContractError("PUBLICATION_GATE", "Every published decision must have a commit receipt")
        run_id = result.run_id
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{2,127}", run_id):
            raise PlatformContractError("INVALID_RUN_ID", "Run ID is not safe for report publication")
        payload = {
            "runId": run_id,
            "pluginId": result.ledger.run.plugin_id,
            "pluginVersion": result.ledger.run.plugin_version,
            "check": {
                "checkId": result.ledger.run.check_id,
                "version": result.ledger.run.check_version,
            },
            "status": result.status,
            "decisions": [{
                "workItemId": decision.work_item_id,
                "result": decision.result,
                "reason": decision.reason,
                "findings": [{
                    "dimension": finding.dimension,
                    "status": finding.status,
                    "reason": finding.reason,
                } for finding in decision.findings],
                "commitId": receipt.commit_id,
                "durability": receipt.durability,
            } for decision, receipt in zip(result.decisions, result.receipts)],
            "failures": [{
                "workItemId": failure.work_item_id,
                "checkId": failure.check_id,
                "code": failure.code,
                "message": failure.message,
            } for failure in result.failures],
            "metrics": dict(result.metrics),
        }
        content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        digest = hashlib.sha256(content).hexdigest()
        destination = self.root / f"{run_id}.platform-summary.json"
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return Artifact(
            f"artifact:{digest[:24]}", "platform_summary", str(destination), digest,
            tuple(receipt.commit_id for receipt in result.receipts),
        )
