"""Repository-local launcher used by the shared Codex MCP configuration."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assayer_host.transport import mcp_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(mcp_main())
