"""User-facing real-browser command entry point (B10)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .browser_runtime import BrowserHostRuntime
from .errors import HostError
from .transport import JsonLineTransport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-f", description="Run agent-f against a real Web URL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="启动真实 Chromium 并执行只读发现/Evidence 试跑")
    audit.add_argument("url")
    audit.add_argument("--output-dir", default="./agent-f-output")
    serve = subparsers.add_parser("serve", help="为一个真实 URL 启动 JSON Lines Host")
    serve.add_argument("url")
    serve.add_argument("--output-dir", default="./agent-f-output")
    args = parser.parse_args(argv)
    runtime = BrowserHostRuntime(args.url, Path(args.output_dir))
    try:
        if args.command == "audit":
            try:
                response = runtime.audit(args.url)
            except HostError as error:
                response = {
                    "protocolVersion": "1.0", "requestId": "runtime-audit",
                    "status": "failed",
                    "error": {"code": error.code, "message": error.message},
                    "evidenceRefs": [], "diagnosticRefs": [],
                }
            print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if response.get("status") == "ok" else 1
        JsonLineTransport(runtime).serve()
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
