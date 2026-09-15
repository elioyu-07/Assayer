#!/usr/bin/env python3
"""Record a bounded operator-acceptance outcome without inventing evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_blocked(
    evidence_root: str | Path,
    *,
    code: str,
    summary: str,
    thread_id: str,
    evidence_file: str | Path,
    codex_version: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    root = Path(evidence_root).expanduser().resolve()
    scenario_path = root / "scenario.json"
    environment_path = root / "environment.json"
    index_path = root / "artifact-index.json"
    result_path = root / "gate-results.json"
    if result_path.exists():
        raise FileExistsError("operator gate result is immutable once recorded")
    if not all(path.is_file() for path in (scenario_path, environment_path, index_path)):
        raise ValueError("operator evidence root is not a prepared execution")
    evidence = Path(evidence_file).expanduser().resolve()
    artifacts_root = (root / "artifacts").resolve()
    try:
        relative_evidence = evidence.relative_to(artifacts_root)
    except ValueError as error:
        raise ValueError("blocking evidence must be retained under artifacts") from error
    if not evidence.is_file():
        raise ValueError("blocking evidence file does not exist")
    if not code or not summary or not thread_id or not codex_version:
        raise ValueError("blocked result identity fields cannot be empty")

    scenario = _load(scenario_path)
    if scenario.get("phase") != "prepared" or scenario.get("gateId") != "OPR-J04-B01":
        raise ValueError("only a prepared OPR-J04-B01 execution can be recorded here")
    environment = _load(environment_path)
    artifact_index = _load(index_path)
    instant = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    recorded_at = instant.isoformat().replace("+00:00", "Z")
    artifact_ref = (Path("artifacts") / relative_evidence).as_posix()

    environment["executionIdentity"] = {
        "codexVersion": codex_version,
        "assayerVersion": environment["releaseCandidate"]["pluginVersion"],
        "domainPluginVersion": None,
        "recorded": True,
    }
    scenario.update({
        "phase": "terminal",
        "status": "blocked",
        "threadId": thread_id,
        "observedAt": recorded_at,
        "runId": None,
    })
    observations = []
    for observation in scenario["requiredObservations"]:
        observations.append({
            "id": observation,
            "status": "passed" if observation == "no_internal_protocol_input" else "blocked",
            "evidence": [artifact_ref],
        })
    gate_result = {
        "schemaVersion": "1.0.0",
        "executionId": scenario["executionId"],
        "gateId": scenario["gateId"],
        "status": "blocked",
        "recordedAt": recorded_at,
        "threadId": thread_id,
        "runId": None,
        "blocker": {
            "owner": "external_model_transport",
            "code": code,
            "summary": summary,
            "productJourneyStarted": False,
            "domainPluginMutationPerformed": False,
        },
        "preconditions": {
            "cleanMarketplaceDiscovery": "passed",
            "exactAssayerInstallation": "passed",
            "naturalLanguageModelTurn": "blocked",
        },
        "observations": observations,
        "releaseAdmission": False,
    }
    entries = artifact_index.setdefault("artifacts", [])
    if not any(item.get("path") == artifact_ref for item in entries):
        entries.append({
            "path": artifact_ref,
            "mediaType": "application/x-ndjson",
            "sha256": _sha256(evidence),
            "privacy": "sanitized_diagnostic",
            "runId": None,
        })
    _write(environment_path, environment)
    _write(scenario_path, scenario)
    _write(index_path, artifact_index)
    _write(result_path, gate_result)
    return gate_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--codex-version", required=True)
    args = parser.parse_args(argv)
    try:
        result = record_blocked(
            args.evidence_root,
            code=args.code,
            summary=args.summary,
            thread_id=args.thread_id,
            evidence_file=args.evidence_file,
            codex_version=args.codex_version,
        )
    except (FileExistsError, OSError, ValueError) as error:
        print(json.dumps({
            "schemaVersion": "1.0.0",
            "status": "failed",
            "error": {
                "code": "OPERATOR_ACCEPTANCE_RECORD_FAILED",
                "message": str(error),
            },
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
