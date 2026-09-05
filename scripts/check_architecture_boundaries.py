"""Static dependency-direction checks for the platform/plugin boundary.

This checker is intentionally small and dependency-free.  It protects the
highest-risk import edges while the repository still contains documented
compatibility bridges.  It is not a general architectural linter.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@dataclass(frozen=True)
class BoundaryViolation:
    path: Path
    line: int
    role: str
    imported_module: str
    reason: str

    def format(self, root: Path = ROOT) -> str:
        try:
            display = self.path.relative_to(root)
        except ValueError:
            display = self.path
        return f"{display}:{self.line}: {self.role} imports {self.imported_module} ({self.reason})"


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name
    return node.module or ""


def _starts_with(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _role_for(path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(SRC.resolve())
    except ValueError:
        return None
    parts = relative.parts
    if not parts:
        return None
    if parts[0] == "assayer_platform":
        return "platform_plugin_bundle" if len(parts) > 1 and parts[1] == "builtin_plugins" else "platform"
    if parts[0] == "assayer_host":
        return "host"
    if parts[0] == "assayer_agent":
        return "agent"
    if parts[0] == "assayer_document_navigation":
        return "capability_provider"
    return None


def _reason(role: str, module: str, path: Path) -> str | None:
    # These are the forbidden edges for new code.  Existing exceptions are
    # listed explicitly below so that adding another occurrence fails the gate.
    if role == "platform" and _starts_with(module, "assayer_host"):
        return "platform kernel must not depend on Host implementation"
    if role == "platform" and _starts_with(module, "playwright"):
        return "platform kernel must not depend on browser implementation"
    if role == "platform" and _starts_with(module, "mcp"):
        return "platform kernel must not depend on transport SDK"
    if role == "platform" and _starts_with(module, "assayer_platform.builtin_plugins"):
        # The package root is a documented built-in registry compatibility
        # bridge.  No other platform module may import a concrete plugin.
        if path.resolve() != (SRC / "assayer_platform" / "__init__.py").resolve():
            return "platform kernel must not depend on concrete plugin implementation"
    if role == "platform_plugin_bundle" and _starts_with(module, "assayer_host"):
        return "plugin must not depend on Host private implementation"
    if role == "platform_plugin_bundle" and _starts_with(module, "mcp"):
        return "plugin must not depend on transport SDK"
    if role == "platform_plugin_bundle" and _starts_with(module, "playwright"):
        return "plugin must not depend on browser implementation"
    if role == "platform_plugin_bundle" and any(_starts_with(module, prefix) for prefix in (
        "assayer_platform.ledger", "assayer_platform.canonical_result",
        "assayer_platform.reporting", "assayer_platform.observability",
        "assayer_platform.interactive", "assayer_platform.result_delivery",
    )):
        return "plugin must use platform layer interfaces instead of owning delivery or lifecycle"
    if role == "capability_provider" and _starts_with(module, "assayer_host"):
        return "capability provider must not depend on Host implementation"
    if role == "capability_provider" and _starts_with(module, "assayer_platform.builtin_plugins"):
        return "capability provider must not depend on domain plugin"
    if role == "agent" and _starts_with(module, "assayer_host"):
        return "Agent adapter must not mutate or import Host implementation"
    return None


def scan_file(path: Path, *, root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    role = _role_for(path)
    if role is None or path.suffix != ".py":
        return ()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        return (BoundaryViolation(path, 1, role, "<unreadable>", f"cannot inspect source: {error}"),)
    violations: list[BoundaryViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        module = _module_name(node)
        reason = _reason(role, module, path)
        if reason:
            violations.append(BoundaryViolation(path, node.lineno, role, module, reason))
    return tuple(violations)


def find_violations(root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    source_root = root / "src"
    violations: list[BoundaryViolation] = []
    for path in sorted(source_root.rglob("*.py")):
        violations.extend(scan_file(path, root=root))
    return tuple(violations)


def main(argv: list[str] | None = None) -> int:
    del argv
    violations = find_violations()
    if violations:
        print("Architecture boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.format()}", file=sys.stderr)
        return 1
    print("Architecture boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
