#!/usr/bin/env python3
"""Prove the M4 exit gate for the external ass-spec plugin end to end.

Journey against the real ``plugins/ass-spec`` distribution, using a durable
``PluginInstallationStore`` and the domain-neutral interactive MCP transport:

    install -> discover -> run (Agent semantic review, Spec business input)
    -> valid platform result -> upgrade -> rollback -> uninstall

The platform never imports ``ass_spec``; it only loads the package
through the lifecycle loader after static validation.  No Assayer platform
source change is required to install or update the plugin.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assayer_host import InteractivePlatformMcpToolTransport  # noqa: E402
from assayer_platform.plugin_installation import PluginInstallationStore  # noqa: E402
from assayer_platform.plugin_lifecycle import (  # noqa: E402
    PluginLifecycleManager,
    discover_plugin_registry,
)

PACKAGE = ROOT / "plugins" / "ass-spec"
PLUGIN_ID = "ass-spec"

SPEC_BUSINESS_INPUT = """# Product Spec: External Journey

## 1. Module Definition
The journey service creates and manages an isolated demonstration account.

## 2. State Model
An account is draft until verification, then becomes active.

## 3. Functional Requirements
FR-001: The service must create an account for a unique identifier.
AC-FR001-01: Given a valid identifier, when the account is created, then a
verification message is sent and the account enters the draft state.
CASE-01: Given a duplicate identifier, when registration runs, then the request
is rejected with a duplicate error.

## 4. Key Entities
The Account entity owns the identifier, verification status, and state.

## 5. Data Fields
The identifier is a required string with a maximum length of 128 characters.

## 6. Non-functional Requirements
NFR-GEN-001: Every create operation must complete within 2000 milliseconds.

## 7. Success Criteria
SC-01: Every accepted identifier produces exactly one account.

## 8. References and Compliance
The platform governance document is the selected authority.

## 9. Key Decisions
D-01: The identifier is the unique account identity.

## 10. Dependencies and Assumptions
The delivery provider must be available; otherwise the operation fails
explicitly.

## 11. Stage Differences
The first stage includes FR-001.

## 12. Revision History
Version 1.0 was created for the initial review.
"""


def _bumped_copy(package: Path, destination: Path, version: str) -> Path:
    shutil.copytree(package, destination)
    descriptor = json.loads((destination / "assayer-plugin-release.json").read_text(encoding="utf-8"))
    descriptor["pluginVersion"] = version
    (destination / "assayer-plugin-release.json").write_text(json.dumps(descriptor), encoding="utf-8")
    manifest_path = destination / "src" / "ass_spec" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata = destination / "pyproject.toml"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace(
        'version = "1.0.0"', f'version = "{version}"',
    ))
    return destination


def _run_spec(registry, output_root: Path, spec_path: Path) -> dict:
    """Drive one interactive Spec run with Agent semantic review and return
    the terminal result plus the run identity."""
    transport = InteractivePlatformMcpToolTransport(output_root, plugin_registry=registry)
    try:
        started = transport.call_tool("start_plugin_run", {
            "pluginId": PLUGIN_ID, "checkId": "SPEC-001",
            "scope": {"files": [{"path": str(spec_path)}]},
        })["structuredContent"]["result"]
        run_id = started["runId"]
        transport.call_tool("discover_work_items", {})
        inspected = transport.call_tool("inspect_work_items", {"includeEvidence": True})
        packet = inspected["structuredContent"]["result"]["result"]["investigations"][0]
        decisions = [{
            "workItemId": packet["workItem"]["workItemId"],
            "result": "scanned_no_issue",
            "reason": "The reviewed Spec satisfies every required dimension.",
            "findings": [{
                "dimension": dimension["name"], "status": "satisfied",
                "reason": dimension["observations"][0],
            } for dimension in packet["dimensions"]],
        }]
        transport.call_tool("submit_decisions", {"decisions": decisions})
        finished = transport.call_tool("finish_plugin_run", {"status": "completed"})
        result = finished["structuredContent"]["result"]
        return {"runId": run_id, "result": result}
    finally:
        transport.close()


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="assayer-spec-external-journey-") as directory:
        root = Path(directory)
        store_root = root / "installed"
        output_root = root / "output"
        spec_path = root / "spec.md"
        spec_path.write_text(SPEC_BUSINESS_INPUT, encoding="utf-8")

        store = PluginInstallationStore(store_root)
        manager = PluginLifecycleManager(store)

        installed = manager.install(PACKAGE)
        if installed["status"] != "completed" or installed["pluginId"] != PLUGIN_ID:
            raise RuntimeError(f"install did not complete: {installed}")

        registry = discover_plugin_registry(store)
        discovered = [item.manifest.plugin_id for item in registry.list()]
        if discovered != [PLUGIN_ID]:
            raise RuntimeError(f"discovery did not surface the plugin: {discovered}")

        run = _run_spec(registry, output_root, spec_path)
        result = run["result"]
        if result["status"] != "completed":
            raise RuntimeError(f"run did not complete: {result}")
        summary = result["result"]["summary"]
        ledger_path = output_root / run["runId"] / f"{run['runId']}.platform-ledger.json"
        if not ledger_path.is_file():
            raise RuntimeError("run did not publish a platform ledger")
        html_artifacts = list((output_root / run["runId"]).rglob("*.html"))
        if html_artifacts:
            raise RuntimeError(f"run published HTML output: {html_artifacts}")

        with tempfile.TemporaryDirectory() as bumped:
            upgraded_source = _bumped_copy(PACKAGE, Path(bumped) / "v110", "1.1.0")
            upgraded = manager.upgrade(upgraded_source)
        if upgraded["version"] != "1.1.0":
            raise RuntimeError(f"upgrade did not land: {upgraded}")

        rolled_back = manager.rollback(PLUGIN_ID)
        if rolled_back["version"] != "1.0.0":
            raise RuntimeError(f"rollback did not restore: {rolled_back}")

        uninstalled = manager.uninstall(PLUGIN_ID)
        if uninstalled["status"] != "completed":
            raise RuntimeError(f"uninstall did not complete: {uninstalled}")
        if manager.list():
            raise RuntimeError("uninstall left installed plugins behind")

        return {
            "schemaVersion": "1.0.0",
            "mode": "external_spec_package",
            "publishable": False,
            "status": "passed",
            "pluginId": PLUGIN_ID,
            "steps": [
                {"operation": "install", "version": installed["version"], "status": "completed"},
                {"operation": "discover", "plugins": discovered},
                {
                    "operation": "run",
                    "runId": run["runId"],
                    "terminalStatus": result["status"],
                    "summaryPhase": summary["phase"],
                    "readiness": summary["readiness"]["status"],
                    "candidateCount": summary["review"]["candidateCount"],
                },
                {"operation": "upgrade", "version": upgraded["version"],
                 "previousVersion": upgraded["previousVersion"]},
                {"operation": "rollback", "version": rolled_back["version"],
                 "previousVersion": rolled_back["previousVersion"]},
                {"operation": "uninstall", "status": uninstalled["status"]},
            ],
            "assertions": {
                "structuredSummary": True,
                "htmlOutput": False,
                "ledgerPublished": True,
                "installedCount": len(manager.list()),
            },
        }


def main() -> int:
    try:
        result = run()
    except Exception:
        result = {
            "schemaVersion": "1.0.0",
            "mode": "external_spec_package",
            "publishable": False,
            "status": "failed",
            "result": {
                "code": "SPEC_EXTERNAL_JOURNEY_FAILED",
                "message": "The external ass-spec exit-gate journey did not complete.",
            },
        }
    schema = json.loads(
        (ROOT / "schemas" / "spec-external-journey.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
