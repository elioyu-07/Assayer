"""User-facing Agent audit, Host-only smoke, and dynamic serving entry points."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

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
        f"{url}\nCall start_audit with outputDir=auto, browserProfile=default, "
        "authMode=anonymous, and ruleRegistryVersion=1.0.0. Complete the audit; do not run the smoke runner."
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
