"""Deterministic B09 resilience gate for the Python Host.

This is intentionally a small mechanical scan, not an LLM verdict. It emits
JSON so CI can fail on blocking patterns without producing an HTML report.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path


def scan(root: Path) -> dict:
    findings = []
    inventory = {"goto": 0, "launch": 0, "screenshot": 0, "sleep": 0}
    for path in sorted((root / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Dynamic Playwright methods are intentionally obtained through
        # getattr() to keep the browser protocol optional; include them in the
        # external-call inventory even though AST cannot see a direct call.
        inventory["goto"] += len(re.findall(r'getattr\([^\n]+["\']goto["\']', source))
        inventory["screenshot"] += len(re.findall(r'getattr\([^\n]+["\']screenshot["\']', source))
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    findings.append({"tier": "block", "rule": "S-BARE-EXCEPT", "loc": f"{path}:{node.lineno}", "note": "bare except"})
                if not node.body or all(isinstance(item, ast.Pass) for item in node.body):
                    findings.append({"tier": "block", "rule": "S-EMPTY-CATCH", "loc": f"{path}:{node.lineno}", "note": "empty exception handler"})
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                findings.append({"tier": "block", "rule": "S-UNBOUNDED-LOOP", "loc": f"{path}:{node.lineno}", "note": "unbounded while True"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if name in inventory:
                    inventory[name] += 1
                    if name in {"goto", "launch"} and path.name != "browser_session.py" and not any(keyword.arg == "timeout" for keyword in node.keywords):
                        findings.append({"tier": "block", "rule": "S-MISSING-TIMEOUT", "loc": f"{path}:{node.lineno}", "note": f"{name} lacks explicit timeout"})
    return {"schemaVersion": "1.0.0", "scope": str(root / "src"), "inventory": inventory,
            "findings": findings, "blockingCount": sum(1 for item in findings if item["tier"] == "block"),
            "status": "pass" if not findings else "fail"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic agent-f resilience scan")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", help="optional JSON output path")
    args = parser.parse_args(argv)
    result = scan(Path(args.root).resolve())
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
