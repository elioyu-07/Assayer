#!/usr/bin/env python3
"""Check that ordinary plugin source remains declaration-only."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_FILES = frozenset({
    "plugin.yaml",
    "checks.yaml",
    "semantic-review.md",
    "README.md",
})
ALLOWED_DIRECTORIES = frozenset({"cases", ".assayer"})
FORBIDDEN_SOURCE = (
    re.compile(r"\bplugin\.py\b", re.IGNORECASE),
    re.compile(r"\b(?:scan|relations)\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:DomainResultContract|PluginRegistration|Advanced SPI)\b"),
    re.compile(r"assayer_(?:platform|host|plugin_sdk\.advanced)"),
)


@dataclass(frozen=True)
class PluginSourceViolation:
    path: Path
    message: str


def _ordinary_plugin_roots(root: Path) -> tuple[Path, ...]:
    plugins = root / "plugins"
    if not plugins.is_dir():
        return ()
    # The bundled product plugin is not an ordinary domain package.  It owns
    # product Skills and lifecycle transport, so its source is governed by the
    # platform bundle gate rather than the ordinary author surface.
    return tuple(sorted(path for path in plugins.iterdir() if path.is_dir() and path.name != "assayer"))


def find_violations(root: Path) -> tuple[PluginSourceViolation, ...]:
    violations: list[PluginSourceViolation] = []
    for plugin_root in _ordinary_plugin_roots(root):
        for path in sorted(plugin_root.rglob("*")):
            relative = path.relative_to(plugin_root)
            if any(part in {".git", ".venv", "__pycache__"} for part in relative.parts):
                continue
            if path.is_dir():
                if len(relative.parts) == 1 and relative.name not in ALLOWED_DIRECTORIES:
                    violations.append(PluginSourceViolation(path, "ordinary plugin has an undeclared directory"))
                continue
            if ".assayer" in relative.parts:
                continue
            if len(relative.parts) == 1 and path.name not in ALLOWED_FILES:
                violations.append(PluginSourceViolation(path, "ordinary plugin has an undeclared author file"))
            if path.suffix.lower() in {".py", ".yaml", ".yml", ".json", ".md"}:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as error:
                    violations.append(PluginSourceViolation(path, f"ordinary plugin source is unreadable: {error}"))
                    continue
                for pattern in FORBIDDEN_SOURCE:
                    if pattern.search(text):
                        violations.append(
                            PluginSourceViolation(path, f"ordinary plugin source contains forbidden surface: {pattern.pattern}")
                        )
                        break
    return tuple(violations)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = find_violations(root)
    if violations:
        print("Ordinary plugin source boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.path.relative_to(root)}: {violation.message}", file=sys.stderr)
        return 1
    print("Ordinary plugin source boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
