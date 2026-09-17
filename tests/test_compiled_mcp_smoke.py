"""Smoke-test the compiled MCP server through the real MCP SDK.

The full profile must prove that the optional MCP dependency is usable, not only
importable. This drives create_compiled_mcp_server through the real FastMCP
server: the SDK lists the lifecycle tools and one read-only tool is invoked
through the SDK call path, so an SDK integration regression fails the profile.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE_SRCS = tuple(sorted(
    path for path in (ROOT / "packages").glob("*/src") if path.is_dir()
))
for source_root in (ROOT, SRC, *PACKAGE_SRCS):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

try:
    import mcp  # noqa: F401
except ImportError:  # pragma: no cover - the full preflight guarantees this
    mcp = None


class CompiledMcpServerSmokeTest(unittest.TestCase):
    def _server(self):
        from assayer_host.transport import create_compiled_mcp_server

        directory = tempfile.mkdtemp(prefix="assayer-mcp-smoke-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return create_compiled_mcp_server(output_root=directory, store_root=directory)

    @unittest.skipIf(mcp is None, "MCP SDK is not installed")
    def test_sdk_lists_the_compiled_lifecycle_tools(self):
        server = self._server()
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertTrue(
            {
                "list_plugins",
                "start_compiled_run",
                "finalize_compiled_run",
                "get_compiled_result",
            }.issubset(names),
            sorted(names),
        )

    @unittest.skipIf(mcp is None, "MCP SDK is not installed")
    def test_sdk_invokes_a_read_only_tool(self):
        server = self._server()

        async def invoke():
            return await server.call_tool("list_plugins", {})

        blocks = asyncio.run(invoke())
        payload = json.loads(blocks[0].text)
        self.assertFalse(payload["isError"])
        self.assertEqual(payload["structuredContent"]["status"], "ok")
        self.assertEqual(payload["structuredContent"]["result"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
