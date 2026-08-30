"""Transport-only JSON and MCP-shaped adapters for the Host Core (B08).

These adapters deliberately do not create IDs, inspect browsers, interpret
rules, or persist state.  They only frame a complete protocol envelope and
delegate it to one ``HostCore`` instance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TextIO

from .core import HostCore, TOOL_KINDS
from .errors import HostError


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_TOOLS = tuple(TOOL_KINDS)


class JsonLineTransport:
    """Invoke HostCore using one JSON request/response per input line."""

    def __init__(self, core: HostCore):
        self.core = core

    @staticmethod
    def _safe_request_id(value: object) -> str:
        return value if isinstance(value, str) and _ID.fullmatch(value) else "transport-error"

    def invoke(self, request: object) -> dict:
        request_id = self._safe_request_id(request.get("requestId") if isinstance(request, dict) else None)
        try:
            if not isinstance(request, dict):
                raise HostError("INVALID_REQUEST", "JSON 请求必须是对象")
            return self.core.handle(request)
        except HostError as error:
            return self._error_response(request if isinstance(request, dict) else {}, request_id, error)
        except Exception:
            # Do not expose parser/adapter stack traces or request contents over
            # an untrusted transport boundary.
            return self._error_response(request if isinstance(request, dict) else {}, request_id,
                                        HostError("INTERNAL_FAILURE", "Host 处理请求时发生内部故障"))

    def _error_response(self, request: dict, request_id: str, error: HostError) -> dict:
        status = "failed" if error.code in {"INTERNAL_FAILURE", "CREDENTIAL_CHANNEL_FAILED"} else "rejected"
        scan_id = request.get("scanId")
        run_id = request.get("runId")
        if isinstance(scan_id, str) and isinstance(run_id, str) and _ID.fullmatch(scan_id) and _ID.fullmatch(run_id):
            revision = 0
            try:
                scan = self.core._store.get_scan(scan_id)
                if scan and scan.get("run_id") == run_id:
                    revision = int(scan.get("run_revision", 0))
            except Exception:
                revision = 0
            return {"protocolVersion": request.get("protocolVersion", "1.0"), "requestId": request_id,
                    "scanId": scan_id, "runId": run_id, "runRevision": max(0, revision),
                    "status": status, "error": error.as_dict(),
                    "evidenceRefs": [], "diagnosticRefs": []}
        return {"protocolVersion": request.get("protocolVersion", "1.0"), "requestId": request_id,
                "status": status, "error": error.as_dict(),
                "evidenceRefs": [], "diagnosticRefs": []}

    def serve(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> None:
        """Serve newline-delimited JSON until EOF; malformed lines stay isolated."""
        for line in input_stream:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                response = self._error_response({}, "transport-error", HostError("INVALID_REQUEST", "JSON 请求格式无效"))
            else:
                response = self.invoke(request)
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()


class McpToolTransport:
    """MCP-shaped tool listing/call adapter over the same JSON envelope.

    ``arguments`` must contain the complete agent-f protocol request.  This
    keeps IDs and lifecycle fields caller-owned and prevents MCP from becoming
    a second business-logic implementation.
    """

    def __init__(self, core: HostCore):
        self._json = JsonLineTransport(core)

    def list_tools(self) -> list[dict]:
        return [{"name": name, "description": f"agent-f Host {name}（仅传输适配）",
                 "inputSchema": {"type": "object"}} for name in _TOOLS]

    def call_tool(self, name: str, arguments: object) -> dict:
        if name not in _TOOLS:
            raise HostError("UNKNOWN_TOOL", f"工具 {name} 不存在")
        if not isinstance(arguments, dict) or arguments.get("tool") != name:
            raise HostError("INVALID_REQUEST", "MCP arguments 必须是匹配工具名的完整协议封套")
        response = self._json.invoke(arguments)
        return {"structuredContent": response,
                "content": [{"type": "text", "text": json.dumps(response, ensure_ascii=False, separators=(",", ":"))}],
                "isError": response.get("status") != "ok"}


def create_mcp_server(core: HostCore):
    """Create an optional official-SDK stdio server without making MCP required."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError("MCP SDK 未安装；请安装 agent-f-host[mcp]") from error
    server = FastMCP("agent-f")
    adapter = McpToolTransport(core)
    for item in adapter.list_tools():
        name = item["name"]

        def invoke(request: dict, _name=name) -> dict:
            return adapter.call_tool(_name, request)

        invoke.__name__ = f"agent_f_{name}"
        server.tool(name=name, description=item["description"])(invoke)
    return server


def mcp_main() -> int:
    core = HostCore()
    try:
        create_mcp_server(core).run(transport="stdio")
    finally:
        core.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-f JSON-lines Host transport")
    parser.add_argument("--stdio", action="store_true", help="从 stdin 读取 JSON 请求并向 stdout 输出响应")
    args = parser.parse_args(argv)
    if not args.stdio:
        parser.error("必须指定 --stdio")
    core = HostCore()
    try:
        JsonLineTransport(core).serve()
    finally:
        core.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
