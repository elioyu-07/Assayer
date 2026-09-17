#!/usr/bin/env python3
"""Check authored repository text for non-English scripts.

Platform authored code and contracts are English. Locale belongs to a named
projection, so intentional non-English text is allowlisted by path rather than
tolerated globally. Generated output directories are never scanned.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
DEFAULT_EXTENSIONS = {".py", ".md", ".json", ".toml", ".txt", ".yaml", ".yml"}
# Generated run output is never authored text; assayer-output/ is ignored by
# git but still lives in a development checkout.
DEFAULT_EXCLUDES = {".git", "build", "dist", ".venv", "__pycache__", "assayer-output"}
# Locale belongs to a named projection, never to platform authored code. These
# paths intentionally contain non-English text and are allowlisted one by one:
#   - the frozen formal report format, the renderer that emits it, and its test;
#   - the SDK test that asserts non-ASCII identifiers are rejected;
#   - the historical Chinese lifecycle plan, retained for traceability only;
#   - approval records, which quote the approver name and the user's own words.
DEFAULT_ALLOWLIST = {
    "tests/fixtures",
    "tests/data",
    "examples/target-pages",
    "docs/audit-report-format-v1.md",
    "src/assayer_platform/audit_report.py",
    "tests/test_audit_report.py",
    "tests/test_plugin_sdk.py",
    "docs/codex-natural-language-plugin-lifecycle-plan.md",
    "design/changes",
}


def _is_allowed(relative: str, allowlist: set[str]) -> bool:
    return any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in allowlist)


def scan(root: Path, *, allowlist: set[str] | None = None) -> list[tuple[str, int, str]]:
    allowed = DEFAULT_ALLOWLIST | (allowlist or set())
    findings: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in DEFAULT_EXCLUDES for part in path.parts) or _is_allowed(relative, allowed):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if HAN_RE.search(line):
                findings.append((relative, number, line.strip()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject non-English authored repository text")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--allow", action="append", default=[], help="Additional relative file or directory allowlist entry")
    args = parser.parse_args()
    findings = scan(Path(args.root).resolve(), allowlist=set(args.allow))
    for path, line, text in findings:
        print(f"{path}:{line}: {text}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
