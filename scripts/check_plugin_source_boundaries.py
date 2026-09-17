#!/usr/bin/env python3
"""Check plugin source boundaries for ordinary plugins and the platform bundle.

Ordinary plugin source must stay declaration-only. The bundled platform plugin
(``plugins/assayer``) is not an ordinary domain package, but its user-facing
text is what an Agent actually reads, so it must name only the tools the
compiled Host exposes and must not carry retired protocol or decision tokens.
"""

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

# The platform bundle's user-facing text: product Skills, the Codex plugin
# manifest, and the local MCP configuration. Generated runtime material and
# local build state are not authored text.
PLATFORM_BUNDLE = "assayer"
PLATFORM_BUNDLE_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml"})
PLATFORM_BUNDLE_SKIP_PARTS = frozenset({".assayer", "__pycache__", "runtime"})

# Every tool the compiled Host transports expose: the compiled Run workflow and
# the plugin lifecycle. Frozen here so authored text can be checked without
# importing the runtime; tests/test_plugin_source_boundaries.py asserts this set
# still equals the live MCP surface.
LIVE_TOOLS = frozenset({
    "apply_plugin_change",
    "bind_provider",
    "collect_evidence",
    "discover_sources",
    "execute_plugin_change",
    "finalize_compiled_run",
    "get_compiled_result",
    "get_plugin_info",
    "list_compiled_plugins",
    "list_plugins",
    "plan_plugin_change",
    "plan_review_batches",
    "start_compiled_run",
    "submit_review_batch",
    "verify_plugin_source",
})
TOOL_PREFIXES = (
    "advance_", "apply_", "bind_", "collect_", "commit_", "complete_",
    "discover_", "execute_", "expand_", "finalize_", "get_", "investigate_",
    "list_", "plan_", "prepare_", "record_", "resume_", "scan_", "start_",
    "submit_", "verify_",
)
# Unmistakable retired vocabulary may not appear at all.
RETIRED_TOKENS = (
    re.compile(r"\b(?:domainResult|domainResultContract|auditReport)\b"),
    re.compile(r"\b(?:issue_found|scanned_no_issue|needs_review)\b"),
)
TOOL_TOKEN = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


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


def find_platform_bundle_violations(root: Path) -> tuple[PluginSourceViolation, ...]:
    """Reject authored platform-bundle text that names a non-live tool surface."""
    bundle = root / "plugins" / PLATFORM_BUNDLE
    if not bundle.is_dir():
        return ()
    violations: list[PluginSourceViolation] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in PLATFORM_BUNDLE_TEXT_SUFFIXES:
            continue
        relative = path.relative_to(bundle)
        if any(part in PLATFORM_BUNDLE_SKIP_PARTS for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            violations.append(PluginSourceViolation(path, f"platform bundle text is unreadable: {error}"))
            continue
        for token in sorted(set(TOOL_TOKEN.findall(text))):
            if token not in LIVE_TOOLS and token.startswith(TOOL_PREFIXES):
                violations.append(PluginSourceViolation(
                    path, f"platform bundle text names a tool the compiled Host does not expose: {token}",
                ))
        for pattern in RETIRED_TOKENS:
            found = pattern.search(text)
            if found:
                violations.append(PluginSourceViolation(
                    path, f"platform bundle text carries retired vocabulary: {found.group(0)}",
                ))
    return tuple(violations)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = find_violations(root) + find_platform_bundle_violations(root)
    if violations:
        print("Plugin source boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.path.relative_to(root)}: {violation.message}", file=sys.stderr)
        return 1
    print("Plugin source boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
