"""Static dependency-direction checks for the platform/plugin boundary.

This checker is intentionally small and dependency-free.  It protects the
highest-risk import edges and prevents retired compatibility runtimes from
returning. It is not a general architectural linter.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.check_plugin_source_boundaries import find_violations as find_plugin_source_violations
except ModuleNotFoundError:  # direct execution from the scripts directory
    from check_plugin_source_boundaries import find_violations as find_plugin_source_violations


ROOT = Path(__file__).resolve().parents[1]

# Every retired tool name from both removed protocol generations: the vertical
# audit protocol (start_audit through complete_audit) and the plugin-run
# protocol (start_plugin_run through submit_domain_result). A retired name may
# only reappear as a quoted literal, which is how a protocol field or a
# tool-name table is written; matching the bare identifier would reject
# unrelated method names such as get_operation.
RETIRED_TOOL_NAMES = (
    "start_audit", "get_rule_contract", "get_audit_progress", "inspect_page",
    "discover_scope", "explore_entrypoint", "inspect_object", "begin_case",
    "perform_action", "restore_case", "inspect_source", "observe_page",
    "capture_evidence", "record_findings", "prepare_decision",
    "commit_decision", "get_operation", "complete_audit",
    "start_plugin_run", "advance_plugin_run", "resume_plugin_run",
    "get_plugin_progress", "submit_common_review", "submit_domain_result",
)
RETIRED_TOOL_LITERAL = re.compile(
    r"[\"'](" + "|".join(re.escape(name) for name in RETIRED_TOOL_NAMES) + r")(?:[\"'])"
)

# Every schema that shipped inside the platform or legacy-plugin wheels without
# a live reader was retired with the v1 vertical ledger surface and the legacy
# executable-plugin conformance chain. A retired schema may only return together
# with a live loader and an explicit author-facing registration.
RETIRED_SCHEMA_NAMES = (
    "action-attempt.schema.json",
    "audit-ledger.schema.json",
    "audit-object.schema.json",
    "derived-issues.schema.json",
    "dimension-finding.schema.json",
    "entrypoint.schema.json",
    "evidence.schema.json",
    "frontend-canonical-extension.schema.json",
    "issue.schema.json",
    "object-verification.schema.json",
    "observability-manifest.schema.json",
    "operation.schema.json",
    "page-candidate.schema.json",
    "page-element-judgement.schema.json",
    "page-state.schema.json",
    "pending-decision.schema.json",
    "performance-bill.schema.json",
    "plugin-conformance.schema.json",
    "plugin-fixture.schema.json",
    "plugin-release.schema.json",
    "plugin-release-acceptance.schema.json",
    "public-progress.schema.json",
    "request-observation.schema.json",
    "reverse-case.schema.json",
    "rule-assessment.schema.json",
    "rule-registry.schema.json",
    "run-diagnostics.schema.json",
    "runtime-event.schema.json",
    "scan-run.schema.json",
    "screenshot.schema.json",
)

# Schemas published as author-facing contracts that no Python validator loads.
# A schema that belongs here must be registered explicitly, which keeps a whole
# unreachable published surface from drifting back in unnoticed.
AUTHOR_FACING_SCHEMAS: frozenset[str] = frozenset()


def _repository_contract_violations(root: Path = ROOT) -> tuple[str, ...]:
    violations: list[str] = []
    violations.extend(
        f"{item.path.relative_to(root)}: {item.message}"
        for item in find_plugin_source_violations(root)
    )
    rules = root / "rules"
    if rules.exists() and any(path.is_file() for path in rules.rglob("*")):
        violations.append("platform repository contains root rules files; rules must be plugin-owned")
    pyprojects = [root / "pyproject.toml"]
    packages = root / "packages"
    if packages.is_dir():
        pyprojects.extend(sorted(packages.glob("*/pyproject.toml")))
    for pyproject in pyprojects:
        if not pyproject.exists():
            continue
        text = pyproject.read_text(encoding="utf-8")
        if "share/assayer/rules" in text or "assayer_host.harness" in text:
            violations.append(f"{pyproject.relative_to(root)} exposes removed rules or harness entry points")
    retired_modules = (
        "src/assayer_platform/plugin_registry.py",
        "src/assayer_platform/plugin_lifecycle.py",
        "src/assayer_platform/runner.py",
        "src/assayer_platform/interactive.py",
        "src/assayer_platform/conformance.py",
        "src/assayer_platform/installation_conformance.py",
        "src/assayer_platform/release_conformance.py",
        "src/assayer_platform/package_conformance.py",
        "src/assayer_plugin_sdk/registration.py",
        "src/assayer_plugin_sdk/agent_contract.py",
        "src/assayer_host/browser_session.py",
        "src/assayer_host/browser_readonly.py",
        "src/assayer_host/browser_action.py",
        "src/assayer_host/browser_recovery.py",
        "src/assayer_host/browser_evidence.py",
        "src/assayer_host/reporting.py",
        "src/assayer_host/observability.py",
        "src/assayer_host/page.py",
        "src/assayer_host/object_identity.py",
        "src/assayer_host/action_safety.py",
        "src/assayer_host/recovery.py",
        "src/assayer_host/evidence.py",
        "src/assayer_host/locale_terms.py",
        "src/assayer_agent",
        "packages/assayer-agent",
        "schemas/protocol",
    )
    for relative in retired_modules:
        if (root / relative).exists():
            violations.append(f"retired runtime path exists: {relative}")
    for source_root in _source_roots(root):
        for path in source_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            removed_names = (
                "HostCore", "RuntimeRouter", "BrowserHostRuntime",
                "JsonLineTransport", "McpToolTransport",
                "PluginRegistry", "PluginRegistration", "PlatformRunner",
                "InteractivePluginController", "DomainResultContract",
                "DerivedReportBuilder", "render_observability", "render_performance_bill",
            )
            for name in removed_names:
                if re.search(rf"\b{re.escape(name)}\b", text):
                    violations.append(
                        f"{path.relative_to(root)} retains removed architecture symbol {name}"
                    )
            for match in RETIRED_TOOL_LITERAL.finditer(text):
                violations.append(
                    f"{path.relative_to(root)} retains retired protocol tool name {match.group(1)}"
                )
            if re.search(r"\bdomainResult\b", text):
                violations.append(
                    f"{path.relative_to(root)} retains removed plugin protocol field domainResult"
                )
            for artifact in ("audit-ledger.json",):
                if artifact in text:
                    violations.append(
                        f"{path.relative_to(root)} retains retired v1 artifact name {artifact}"
                    )
            if "from .core import" in text or "assayer_host.core" in text:
                violations.append(f"{path.relative_to(root)} retains a deleted HostCore dependency")
            if "from .runtime_router import" in text or "assayer_host.runtime_router" in text:
                violations.append(f"{path.relative_to(root)} retains a deleted RuntimeRouter dependency")
    return tuple(violations)


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


def _source_roots(root: Path) -> tuple[Path, ...]:
    roots = [root / "src"]
    packages = root / "packages"
    if packages.is_dir():
        roots.extend(sorted(path for path in packages.glob("*/src") if path.is_dir()))
    return tuple(roots)


def _role_for(path: Path, *, root: Path = ROOT) -> str | None:
    relative = None
    for source_root in _source_roots(root):
        try:
            relative = path.resolve().relative_to(source_root.resolve())
            break
        except ValueError:
            continue
    if relative is None:
        return None
    parts = relative.parts
    if not parts:
        return None
    if parts[0] == "assayer_platform":
        return "platform"
    if parts[0] == "assayer_ordinary_plugin":
        return "plugin"
    if parts[0] == "assayer_host":
        return "host"
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
    if role == "plugin" and _starts_with(module, "assayer_host"):
        return "plugin must not depend on Host private implementation"
    if role == "plugin" and _starts_with(module, "mcp"):
        return "plugin must not depend on transport SDK"
    if role == "plugin" and _starts_with(module, "playwright"):
        return "plugin must not depend on browser implementation"
    if role == "plugin" and any(_starts_with(module, prefix) for prefix in (
        "assayer_platform.ledger", "assayer_platform.canonical_result",
        "assayer_platform.reporting", "assayer_platform.observability",
        "assayer_platform.interactive", "assayer_platform.result_delivery",
    )):
        return "plugin must use platform layer interfaces instead of owning delivery or lifecycle"
    return None


def scan_file(path: Path, *, root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    role = _role_for(path, root=root)
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


def _wheel_pyprojects(root: Path) -> tuple[Path, ...]:
    candidates = [root / "pyproject.toml"]
    for parent in ("packages", "plugins"):
        directory = root / parent
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*/pyproject.toml")))
    return tuple(path for path in candidates if path.exists())


def _data_file_violations(pyproject: Path, root: Path) -> tuple[str, ...]:
    """Reject a wheel declaration that ships a removed or missing path.

    Every distribution that installs ``share/assayer`` files must declare
    sources that still exist; a glob into a deleted directory is silently
    ignored by setuptools, so an artifact can keep a dangling declaration with
    every gate green.
    """
    violations: list[str] = []
    text = pyproject.read_text(encoding="utf-8")
    relative = pyproject.relative_to(root)
    if "share/assayer" not in text:
        return ()
    if "schemas/protocol" in text:
        violations.append(
            f"{relative} declares the retired protocol schema directory in its wheel data-files"
        )
    try:
        table = tomllib.loads(text).get("tool", {}).get("setuptools", {}).get("data-files", {})
    except tomllib.TOMLDecodeError:
        violations.append(f"{relative} is not valid TOML")
        return tuple(violations)
    for destination, sources in table.items():
        if not destination.startswith("share/assayer"):
            continue
        for source in sources:
            target = (pyproject.parent / source).resolve()
            has_glob = any(character in source for character in "*?[")
            exists = target.parent.is_dir() if has_glob else target.exists()
            if not exists:
                violations.append(
                    f"{relative} data-files source disappears: {source} -> {destination}"
                )
    return tuple(violations)


def _retired_surface_violations(root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    """Reject retired protocol vocabulary in the published artifact surface.

    Authored Python already fails through the source scan; this covers what a
    wheel ships without any Python reader, so a retired schema cannot ride
    along in a distribution simply because nothing imports it, and no wheel
    keeps a data-files declaration pointing at a path that no longer exists.
    """
    violations: list[BoundaryViolation] = []
    schemas = root / "schemas"
    if schemas.is_dir():
        for path in sorted(schemas.rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            for match in RETIRED_TOOL_LITERAL.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(BoundaryViolation(
                    path=path, line=line, role="repository", imported_module="",
                    reason=f"shipped schema retains retired protocol tool name {match.group(1)}",
                ))
        inventory = schemas / "README.md"
        if inventory.is_file():
            text = inventory.read_text(encoding="utf-8")
            for match in RETIRED_TOOL_LITERAL.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(BoundaryViolation(
                    path=inventory, line=line, role="repository", imported_module="",
                    reason=f"schema inventory retains retired protocol tool name {match.group(1)}",
                ))
    for pyproject in _wheel_pyprojects(root):
        for reason in _data_file_violations(pyproject, root):
            violations.append(BoundaryViolation(
                path=pyproject, line=1, role="repository", imported_module="", reason=reason,
            ))
    return tuple(violations)


def _schema_literal(name: str) -> re.Pattern[str]:
    return re.compile(r"""["'`]""" + re.escape(name) + r"""["'`]""")


def _referenced_schema_names(root: Path) -> set[str]:
    """Names of shipped platform schemas a live code path actually loads.

    A schema is referenced when an authored Python literal names it, or when a
    referenced schema reaches it through a cross-file ``$ref``.
    """
    directory = root / "schemas"
    if not directory.is_dir():
        return set()
    shipped = {path.name for path in directory.rglob("*.schema.json")}
    referenced: set[str] = set()
    plugins = root / "plugins"
    plugin_sources = sorted(plugins.glob("*/src")) if plugins.is_dir() else []
    source_roots = [*_source_roots(root), root / "scripts", *plugin_sources]
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in shipped - referenced:
                if _schema_literal(name).search(text):
                    referenced.add(name)
    frontier = list(referenced)
    while frontier:
        name = frontier.pop()
        source = directory / name
        if not source.is_file():
            continue
        for match in re.finditer(r'"\$ref"\s*:\s*"([^"]+)"', source.read_text(encoding="utf-8")):
            target = match.group(1).split("#", 1)[0].split("/")[-1]
            if target in shipped and target not in referenced:
                referenced.add(target)
                frontier.append(target)
    return referenced


def _schema_surface_violations(root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    """Reject a retired or unreachable schema in a published wheel.

    Deleted Python already fails through the source scan; a schema ships without
    any reader, so nothing else notices when a retired or orphaned schema rides
    along. Every schema in the platform resource directory must be reachable
    from an authored literal (or a ``$ref`` chain) unless it is registered as an
    explicit author-facing contract.
    """
    directory = root / "schemas"
    if not directory.is_dir():
        return ()
    shipped = sorted(path.name for path in directory.rglob("*.schema.json"))
    referenced = _referenced_schema_names(root)
    violations: list[BoundaryViolation] = []
    for name in shipped:
        path = directory / name
        if name in RETIRED_SCHEMA_NAMES:
            reason = f"retired v1 or legacy-plugin schema {name} is present"
        elif name not in referenced and name not in AUTHOR_FACING_SCHEMAS:
            reason = (
                f"schema {name} ships without a live reader or an explicit "
                "author-facing registration"
            )
        else:
            continue
        violations.append(BoundaryViolation(
            path=path, line=1, role="repository", imported_module="", reason=reason,
        ))
    return tuple(violations)


def find_violations(root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    violations: list[BoundaryViolation] = []
    for source_root in _source_roots(root):
        for path in sorted(source_root.rglob("*.py")):
            violations.extend(scan_file(path, root=root))
    violations.extend(_retired_surface_violations(root))
    violations.extend(_schema_surface_violations(root))
    return tuple(violations)


def main(argv: list[str] | None = None) -> int:
    del argv
    contract_violations = _repository_contract_violations()
    if contract_violations:
        print("Repository contract violations:", file=sys.stderr)
        for violation in contract_violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
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
