"""Source template for compiler-generated business-case acceptance."""

from __future__ import annotations


def generated_acceptance_source() -> str:
    return '''"""Generated from Policy Pack business cases; do not edit."""
import json
from pathlib import Path

from assayer_plugin_sdk import BrowserSnapshot

from .registration import _ROOT

CASES = json.loads((_ROOT / "business-cases.json").read_text(encoding="utf-8"))["cases"]


class _FixtureBrowserSnapshotSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def observe_snapshot(self):
        return self.snapshot


def _result(response):
    return response["structuredContent"]["result"]


def _submission(task, final):
    decisions = []
    for item in task["items"]:
        if item["kind"] == "dimension":
            verdict = {
                "ready": "satisfied", "rework": "violated",
                "needs_review": "unresolved", "not_applicable": "not_applicable",
            }[final]
            value = {
                "dimension": item["dimension"], "verdict": verdict,
                "reason": "The business case declares this dimension outcome.",
                "applicability": (
                    {"state": "not_applicable", "reason": "The case is out of scope."}
                    if final == "not_applicable" else {"state": "applicable"}
                ),
                "confidence": {"level": "high" if final != "needs_review" else "low"},
                "support": {"refs": item["support"]},
            }
            if final == "needs_review":
                value["unknown"] = {
                    "reason": "The case requires additional information.",
                    "missingInformation": ["domain clarification"],
                }
        elif item["kind"] == "candidate":
            if final == "rework":
                value = {
                    "disposition": "confirmed",
                    "reason": "The business case confirms this candidate.",
                    "support": {"refs": item["support"]},
                    "finding": {
                        "title": item["subject"], "message": item["message"],
                        "severity": item["severity"],
                        "recommendation": item["recommendation"],
                        "support": {"refs": item["support"]},
                    },
                }
            elif final == "needs_review":
                value = {
                    "disposition": "needs_review",
                    "reason": "The business case remains unresolved.",
                    "support": {"refs": item["support"]},
                    "unknown": {
                        "reason": "The case requires additional information.",
                        "missingInformation": ["domain clarification"],
                    },
                }
            else:
                value = {
                    "disposition": "suppressed",
                    "reason": "The business case does not confirm this candidate.",
                    "support": {"refs": item["support"]},
                }
        else:
            verdict = {
                "ready": "confirmed", "rework": "rejected",
                "needs_review": "unknown", "not_applicable": "not_applicable",
            }[final]
            value = {
                "relationship": item["relationship"], "verdict": verdict,
                "reason": "The business case declares this relationship outcome.",
                "applicability": (
                    {"state": "not_applicable", "reason": "The case is out of scope."}
                    if final == "not_applicable" else {"state": "applicable"}
                ),
                "confidence": {"level": "high" if final != "needs_review" else "low"},
                "support": {"refs": item["support"]},
            }
            if final == "needs_review":
                value["unknown"] = {
                    "reason": "The relationship requires additional information.",
                    "missingInformation": ["domain clarification"],
                }
        decisions.append({
            "itemRef": item["itemRef"], "kind": item["kind"], "value": value,
        })
    return {"decisions": decisions}


def run(*, registration, output_root, transport_factory):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    metrics = {
        check.check_id: {
            "checkId": check.check_id, "checkVersion": check.version,
            "completedRuns": 0, "reviewBatchSubmissions": 0,
            "agentCorrections": 0, "ledgerPaths": [],
            "resumeVerified": True, "replayVerified": True,
            "terminalPublicationVerified": True,
        }
        for check in registration.manifest.checks
    }
    for case in CASES:
        check = next(
            item for item in registration.manifest.checks
            if item.check_id == case["checkId"]
        )
        provider_runtime = None
        if case.get("inputKind") == "browser_snapshot":
            snapshot = BrowserSnapshot(**case["inputData"])
            scope = {"url": snapshot.url}
            provider_runtime = _FixtureBrowserSnapshotSource(snapshot)
        else:
            source = output_root / (
                "input-" + case["caseId"] + "." + case["inputSuffix"]
            )
            source.write_text(case["inputText"], encoding="utf-8")
            scope = {"files": [{"path": str(source)}]}
        transport = transport_factory(output_root, provider_runtime=provider_runtime)
        try:
            started = _result(transport.call_tool("start_plugin_run", {
                "pluginId": registration.manifest.plugin_id,
                "checkId": check.check_id,
                "checkVersion": check.version,
                "scope": scope,
            }))
            run_id = started["runId"]
            boundary = _result(transport.call_tool("advance_plugin_run", {}))
            first_task = boundary["result"]["semanticTask"]
            if first_task["kind"] != "common_review":
                raise RuntimeError("generated plugin did not expose common review")
            transport.close()
            transport = transport_factory(
                output_root, provider_runtime=provider_runtime,
            )
            current = _result(transport.call_tool("resume_plugin_run", {"runId": run_id}))
            submissions = 0
            observed_rules = []
            while current["status"] not in {"completed", "partial", "failed"}:
                task = current.get("result", {}).get("semanticTask")
                if not task:
                    current = _result(transport.call_tool("advance_plugin_run", {}))
                    continue
                observed_rules.extend(
                    item["rule"] for item in task["items"]
                    if item["kind"] == "candidate"
                )
                submissions += 1
                current = _result(transport.call_tool("advance_plugin_run", {
                    "reviewSubmission": _submission(task, case["expectedFinal"]),
                }))
            if current["status"] != "completed":
                raise RuntimeError("business-case lifecycle did not complete")
            replay = _result(transport.call_tool("advance_plugin_run", {}))
            if replay.get("replayed") is not True:
                raise RuntimeError("terminal replay was not observed")
            section = replay["result"]["decisions"]
            transport.call_tool("get_plugin_result", {
                "sectionId": section["sectionId"], "pageSize": 1,
            })
            ledger = output_root / run_id / (run_id + ".platform-ledger.json")
            durable = json.loads(ledger.read_text(encoding="utf-8"))
            actual = [item["result"] for item in durable["decisions"]]
            if actual != [case["expectedDecision"]]:
                raise RuntimeError("business case final mismatch: " + case["caseId"])
            if sorted(set(observed_rules)) != sorted(case["expectedCandidateRules"]):
                raise RuntimeError("business case candidate mismatch: " + case["caseId"])
            record = metrics[check.check_id]
            record["completedRuns"] += 1
            record["reviewBatchSubmissions"] += submissions
            record["ledgerPaths"].append(ledger.relative_to(output_root).as_posix())
            record["terminalPublicationVerified"] = (
                record["terminalPublicationVerified"]
                and (output_root / run_id / "result-summary.json").is_file()
            )
        finally:
            transport.close()
    return {
        "schemaVersion": "2.0.0", "status": "passed",
        "checks": list(metrics.values()),
    }
'''


__all__ = ["generated_acceptance_source"]
