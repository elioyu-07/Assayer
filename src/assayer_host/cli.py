"""User-facing Agent audit, Host-only smoke, and dynamic serving entry points."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from assayer_platform import PlatformContractError, PlatformRunner
from assayer_platform.builtin_plugins import installed_plugin_registry

from .browser_runtime import BrowserHostRuntime
from .errors import HostError
from .runtime_router import RuntimeRouter
from .transport import JsonLineTransport


def _error(code: str, message: str) -> dict:
    return {"protocolVersion": "1.0", "requestId": "assayer-cli", "status": "failed",
            "error": {"code": code, "message": message}, "evidenceRefs": [], "diagnosticRefs": []}


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _run_agent_audit(url: str, output_root: Path) -> int:
    codex = shutil.which("codex")
    if codex is None:
        print(json.dumps(_error("AGENT_RUNTIME_UNAVAILABLE", "Codex CLI was not found; formal audit will not fall back to smoke"),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    command = sys.executable
    args = ["-m", "assayer_host.transport", "--mcp", "--output-root", str(output_root)]
    prompt = (
        "Use $assayer-audit to investigate this web URL through the assayer MCP tools: "
        f"{url}\nComplete the audit; supply only business inputs exposed by the tools, "
        "let Assayer maintain all protocol and runtime configuration, and do not run the smoke runner."
    )
    config_command = f"mcp_servers.assayer.command={_toml_string(command)}"
    config_args = f"mcp_servers.assayer.args={json.dumps(args, ensure_ascii=False)}"
    try:
        completed = subprocess.run(
            [codex, "exec", "-C", str(Path.cwd()), "-c", config_command, "-c", config_args, prompt],
            check=False,
        )
    except OSError:
        print(json.dumps(_error("AGENT_RUNTIME_UNAVAILABLE", "Codex Agent Runtime failed to start; smoke fallback was not executed"),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    return completed.returncode


def _plugin_catalog() -> list[dict]:
    catalog = []
    for registration in installed_plugin_registry().list():
        manifest = registration.manifest
        catalog.append({
            "pluginId": manifest.plugin_id,
            "version": manifest.version,
            "platformApiVersion": manifest.platform_api_version,
            "domains": list(manifest.domains),
            "subjectKinds": list(manifest.subject_kinds),
            "capabilities": sorted(registration.capabilities),
            "executionModes": sorted(registration.execution_modes),
            "supportsCommit": registration.committer_factory is not None,
            "scopeSchema": dict(registration.scope_schema),
            "checks": [
                {"checkId": check.check_id, "version": check.version}
                for check in manifest.checks
            ],
        })
    return sorted(catalog, key=lambda item: item["pluginId"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assayer", description="Run Assayer Agent audits and Host smoke checks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="Run a formal Agent audit through the Codex Skill and dynamic MCP")
    audit.add_argument("url")
    audit.add_argument("--output-root", default="./assayer-output")
    smoke = subparsers.add_parser("smoke", help="Run a non-publishable real Chromium Host fact diagnostic")
    smoke.add_argument("url")
    smoke.add_argument("--output-dir", default="./assayer-output")
    serve = subparsers.add_parser("serve", help="Start the dynamic JSON Lines Runtime Router")
    serve.add_argument("--output-root", default="./assayer-output")
    serve.add_argument("--max-runtimes", type=int, default=4)
    serve.add_argument("--lease-timeout", type=float, default=300.0)
    plugins = subparsers.add_parser("plugins", help="Inspect platform plugins available to this installation")
    plugins_subparsers = plugins.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugins_subparsers.add_parser("list", help="List registered plugins and checks")
    plugin_list.add_argument("--json", action="store_true", dest="as_json")
    plugin_run = plugins_subparsers.add_parser("run", help="Run one registered plugin Check")
    plugin_run.add_argument("--plugin", required=True, dest="plugin_id")
    plugin_run.add_argument("--check", required=True, dest="check_id")
    plugin_run.add_argument("--check-version")
    plugin_run.add_argument("--scope-json", required=True)
    plugin_run.add_argument("--output-root", default="./assayer-output")
    args = parser.parse_args(argv)
    if args.command == "audit":
        return _run_agent_audit(args.url, Path(args.output_root))
    if args.command == "serve":
        router = RuntimeRouter(args.output_root, max_runtimes=args.max_runtimes,
                               lease_timeout_seconds=args.lease_timeout)
        try:
            JsonLineTransport(router).serve()
            return 0
        finally:
            router.close()
    if args.command == "plugins":
        if args.plugin_command == "run":
            try:
                scope = json.loads(args.scope_json)
            except json.JSONDecodeError:
                print(json.dumps(_error("INVALID_SCOPE", "Plugin scope must be valid JSON"),
                                 ensure_ascii=False, indent=2, sort_keys=True))
                return 2
            try:
                result = PlatformRunner(
                    installed_plugin_registry(), args.output_root,
                ).run(
                    plugin_id=args.plugin_id, check_id=args.check_id,
                    check_version=args.check_version, scope=scope,
                )
            except PlatformContractError as error:
                print(json.dumps(_error(error.code, error.message),
                                 ensure_ascii=False, indent=2, sort_keys=True))
                return 2
            payload = {
                "runId": result.run_id,
                "status": result.status,
                "pluginId": args.plugin_id,
                "checkId": args.check_id,
                "decisions": [decision.result for decision in result.decisions],
                "failures": [
                    {"code": failure.code, "message": failure.message,
                     "workItemId": failure.work_item_id}
                    for failure in result.failures
                ],
                "outputDir": str((Path(args.output_root).expanduser().resolve() / result.run_id)),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result.status in {"completed", "partial"} else 1
        catalog = _plugin_catalog()
        if args.as_json:
            print(json.dumps({"plugins": catalog}, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for plugin in catalog:
                checks = ", ".join(
                    f"{item['checkId']}@{item['version']}" for item in plugin["checks"]
                )
                print(f"{plugin['pluginId']} {plugin['version']} [{checks}]")
        return 0
    runtime = BrowserHostRuntime(args.url, Path(args.output_dir))
    try:
        try:
            response = runtime.smoke(args.url)
        except HostError as error:
            response = _error(error.code, error.message)
        print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if response.get("status") == "ok" else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
