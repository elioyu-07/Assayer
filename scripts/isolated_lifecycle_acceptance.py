#!/usr/bin/env python3
"""Exercise the complete lifecycle stack against an isolated fake Codex CLI."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from assayer_host.codex_plugin_client import (  # noqa: E402
    CodexPluginClient,
    ReleaseAttestation,
    SubprocessCodexCommandRunner,
)
from assayer_host.lifecycle_product import LifecyclePlanStore, LifecycleProductController  # noqa: E402
from assayer_host.lifecycle_transaction import LifecycleJournalStore, PluginLifecycleTransaction  # noqa: E402
from assayer_host.runtime_cleanup import PrivateRuntimeCleaner  # noqa: E402


CURRENT = "assayer@release-010"
TARGET = "assayer@release-011"
VERSIONS = {
    CURRENT: "0.1.0+codex.release010",
    TARGET: "0.1.1+codex.release011",
}


_FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["ASSAYER_ACCEPTANCE_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
arguments = sys.argv[1:]
if arguments == ["plugin", "list", "--json"]:
    payload = {"installed": state["installed"], "available": state["available"]}
elif len(arguments) == 4 and arguments[:2] == ["plugin", "remove"] and arguments[3] == "--json":
    selector = arguments[2]
    state["installed"] = [item for item in state["installed"] if item["pluginId"] != selector]
    state["history"].append({"action": "remove", "selector": selector})
    payload = {"status": "removed", "pluginId": selector}
elif len(arguments) == 4 and arguments[:2] == ["plugin", "add"] and arguments[3] == "--json":
    selector = arguments[2]
    source = next((item for item in state["available"] if item["pluginId"] == selector), None)
    if source is None:
        raise SystemExit(2)
    state["installed"] = [item for item in state["installed"] if item["pluginId"] != selector]
    state["installed"].append({**source, "enabled": True})
    state["history"].append({"action": "add", "selector": selector})
    payload = {"status": "installed", "pluginId": selector}
else:
    raise SystemExit(2)
temporary = state_path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
temporary.replace(state_path)
print(json.dumps(payload, sort_keys=True))
'''


def _attestation(selector: str, version: str | None) -> ReleaseAttestation | None:
    expected = VERSIONS.get(selector)
    if expected is None or version not in {None, expected}:
        return None
    return ReleaseAttestation(selector, expected, True, True, True)


def _direction(operation: str, current, target) -> bool:
    pair = (current.selector, target.selector) if target is not None else None
    return (
        operation == "upgrade" and pair == (CURRENT, TARGET)
    ) or (
        operation == "rollback" and pair == (TARGET, CURRENT)
    )


def _controller(
    client: CodexPluginClient,
    root: Path,
    *,
    current_selector: str,
    target_selector: str | None,
) -> LifecycleProductController:
    journal_store = LifecycleJournalStore(root / "transactions")

    def transaction_factory() -> PluginLifecycleTransaction:
        return PluginLifecycleTransaction(
            client, journal_store, active_run_probe=lambda: False,
            direction_verifier=_direction,
        )

    return LifecycleProductController(
        client,
        LifecyclePlanStore(root / "plans.sqlite3"),
        transaction_factory,
        current_selector=current_selector,
        target_resolver=lambda operation, current: (
            client.snapshot(target_selector) if target_selector is not None else None
        ),
        direction_verifier=_direction,
        active_run_probe=lambda: False,
        # This is an isolated deterministic harness, not product authorization.
        external_authorizer=lambda authorization_ref, plan: True,
        transaction_loader=journal_store.load,
    )


def _execute(controller: LifecycleProductController, operation: str) -> tuple[dict, dict]:
    plan = controller.plan(operation)
    if plan["phase"] != "awaiting_confirmation":
        raise RuntimeError(f"isolated {operation} plan was not ready")
    result = controller.execute(plan["planToken"], confirmed=True)
    if result["phase"] != "terminal" or result["transaction"]["status"] != "completed":
        raise RuntimeError(f"isolated {operation} transaction did not complete")
    return plan, result


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="assayer-lifecycle-acceptance-") as directory:
        root = Path(directory)
        state_path = root / "codex-state.json"
        state_path.write_text(json.dumps({
            "installed": [{
                "pluginId": CURRENT, "version": VERSIONS[CURRENT], "enabled": True,
            }],
            "available": [
                {"pluginId": selector, "version": version}
                for selector, version in VERSIONS.items()
            ],
            "history": [],
        }), encoding="utf-8")
        executable = root / "codex"
        executable.write_text(_FAKE_CODEX, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        environment = dict(os.environ)
        environment["ASSAYER_ACCEPTANCE_STATE"] = str(state_path)
        client = CodexPluginClient(
            SubprocessCodexCommandRunner(
                executable=str(executable), environment=environment,
            ),
            release_attestation=_attestation,
            health_verifier=lambda installation: True,
            read_timeout_seconds=5,
            mutation_timeout_seconds=5,
        )

        upgrade_plan, upgrade = _execute(
            _controller(client, root, current_selector=CURRENT, target_selector=TARGET),
            "upgrade",
        )
        rollback_plan, rollback = _execute(
            _controller(client, root, current_selector=TARGET, target_selector=CURRENT),
            "rollback",
        )

        cache = root / "cache"
        runtime = cache / "assayer" / f"runtime-{VERSIONS[CURRENT]}"
        runtime.mkdir(parents=True)
        (runtime / "runtime-identity.json").write_text(json.dumps({
            "schemaVersion": "1.0.0",
            "pluginVersion": VERSIONS[CURRENT],
            "runtimeVersion": "0.1.0",
        }), encoding="utf-8")
        (runtime / "runtime-payload").write_text("isolated", encoding="utf-8")
        uninstall_plan, uninstall = _execute(
            _controller(client, root, current_selector=CURRENT, target_selector=None),
            "uninstall",
        )
        cleanup = PrivateRuntimeCleaner(cache).cleanup_after_uninstall(
            uninstall["transaction"],
        )
        if cleanup["status"] != "removed":
            raise RuntimeError("isolated private-runtime cleanup did not complete")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return {
            "schemaVersion": "1.0.0",
            "mode": "isolated_fake_codex",
            "publishable": False,
            "status": "passed",
            "steps": [
                {
                    "operation": operation,
                    "planStatus": plan["plan"]["status"],
                    "transactionStatus": result["transaction"]["status"],
                }
                for operation, plan, result in (
                    ("upgrade", upgrade_plan, upgrade),
                    ("rollback", rollback_plan, rollback),
                    ("uninstall", uninstall_plan, uninstall),
                )
            ],
            "cleanup": {
                "status": cleanup["status"],
                "runtimeRef": cleanup["runtimeRef"],
            },
            "catalog": {
                "installedCount": len(state["installed"]),
                "mutationCount": len(state["history"]),
            },
            "limitations": [
                "This acceptance uses an isolated fake Codex executable.",
                "It does not prove a real Codex user-approval boundary or mutate an installed plugin.",
            ],
        }


def main() -> int:
    try:
        result = run()
    except Exception:
        result = {
            "schemaVersion": "1.0.0",
            "mode": "isolated_fake_codex",
            "publishable": False,
            "status": "failed",
            "result": {
                "code": "ISOLATED_LIFECYCLE_ACCEPTANCE_FAILED",
                "message": "The isolated lifecycle acceptance did not complete.",
            },
        }
    schema = json.loads(
        (ROOT / "schemas" / "lifecycle-acceptance.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
