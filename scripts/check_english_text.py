#!/usr/bin/env python3
"""Check authored repository text for non-English scripts.

Target-page locale samples and recognition resources are intentionally
allowlisted. Generated output directories are never scanned.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
DEFAULT_EXTENSIONS = {".py", ".md", ".json", ".toml", ".txt", ".yaml", ".yml"}
DEFAULT_EXCLUDES = {".git", "build", "dist", ".venv", "__pycache__"}
DEFAULT_ALLOWLIST = {
    "src/assayer_host/locale_terms.py",
    "tests/fixtures",
    "tests/data",
    "examples/target-pages",
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
