"""Installed DomainResult acceptance journey for the minimal plugin."""

from __future__ import annotations

import json
from pathlib import Path


def _result(response):
    return response["structuredContent"]["result"]


def run(*, registration, output_root, transport_factory):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    input_path = output_root / "input.json"
    input_path.write_text(json.dumps({"required": True}), encoding="utf-8")
    transport = transport_factory(output_root)
    try:
        started = _result(transport.call_tool("start_plugin_run", {
            "pluginId": registration.manifest.plugin_id,
            "checkId": "TST-001",
            "scope": {"files": [{"path": str(input_path), "requiredKeys": ["required"]}]},
        }))
        run_id = started["runId"]
        boundary = _result(transport.call_tool("advance_plugin_run", {}))
        if boundary["result"]["semanticTask"]["kind"] != "domain_review":
            raise RuntimeError("minimal plugin did not expose a domain-review task")
        transport.close()
        transport = transport_factory(output_root)
        resumed = _result(transport.call_tool("resume_plugin_run", {"runId": run_id}))
        task = resumed["result"]["semanticTask"]
        if "investigation" in task or "evidenceHandles" in task:
            raise RuntimeError("minimal plugin exposed a legacy Agent boundary")
        evidence = task["agentView"]["evidence"]
        evidence_refs = [evidence[0]["evidenceRef"]] if evidence else []
        terminal = _result(transport.call_tool("advance_plugin_run", {
            "domainResult": {
                "result": "scanned_no_issue",
                "findings": [{
                    "dimension": "required_keys",
                    "status": "satisfied",
                    "reason": "All required keys are present.",
                    "supportedBy": evidence_refs,
                }],
                "reason": "The deterministic fixture satisfies the required key check.",
            },
        }))
        if terminal["status"] != "completed":
            raise RuntimeError("minimal DomainResult acceptance did not complete")
        replay = _result(transport.call_tool("advance_plugin_run", {}))
        if replay.get("replayed") is not True:
            raise RuntimeError("minimal acceptance did not replay its terminal result")
        decisions = replay["result"]["decisions"]
        transport.call_tool("get_plugin_result", {"sectionId": decisions["sectionId"]})
        ledger_path = output_root / run_id / f"{run_id}.platform-ledger.json"
        return {
            "schemaVersion": "2.0.0",
            "status": "passed",
            "checks": [{
                "checkId": "TST-001",
                "checkVersion": "1.0.0",
                "completedRuns": 1,
                "domainResultSubmissions": 1,
                "agentCorrections": 0,
                "ledgerPaths": [ledger_path.relative_to(output_root).as_posix()],
                "resumeVerified": resumed.get("resumed") is True,
                "replayVerified": True,
                "terminalPublicationVerified": (output_root / run_id / "result-summary.json").is_file(),
            }],
        }
    finally:
        transport.close()
