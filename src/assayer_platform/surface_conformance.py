"""Import-boundary conformance for Assayer plugins.

Enforces the Platform--Plugin Boundary Contract v1 section 5: a plugin may
import only names that belong to the public SDK surface declared in
``assayer_platform.public_surface``.

The check walks the plugin's Python source tree and flags any
``assayer_platform`` import that references a module or symbol outside the
whitelist. Symbol-level enforcement applies to ``from ... import ...``
statements; a bare ``import assayer_platform.<module>`` is checked at module
granularity, and a bare ``import assayer_platform`` is allowed.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from .conformance import PluginConformanceReport, _issue
from .public_surface import PUBLIC_SURFACE, PUBLIC_SURFACE_VERSION

_SURFACE_INVARIANT = "PBV1-PUBLIC-SURFACE"


def _surface_issue(
    code: str, message: str, next_action: str,
) -> "PluginConformanceIssue":
    return _issue(code, _SURFACE_INVARIANT, message, next_action)


def _module_issues(
    module_name: str, *, location: str,
) -> list["PluginConformanceIssue"]:
    if module_name in PUBLIC_SURFACE:
        return []
    return [_surface_issue(
        "PLUGIN_IMPORT_MODULE_NOT_PUBLIC",
        f"{location}: imports non-public platform module `{module_name}`.",
        "Import only modules listed in the public SDK surface, or promote the "
        "module with documented ownership, a version, and a conformance test.",
    )]


def _symbol_issues(
    module_name: str, names: list[str], *, location: str,
) -> list["PluginConformanceIssue"]:
    allowed = PUBLIC_SURFACE.get(module_name)
    if allowed is None:
        return _module_issues(module_name, location=location)
    issues: list["PluginConformanceIssue"] = []
    for name in names:
        if name == "*":
            issues.append(_surface_issue(
                "PLUGIN_IMPORT_WILDCARD",
                f"{location}: wildcard import from `{module_name}` is not allowed.",
                "Import explicit public names from the module.",
            ))
        elif name not in allowed:
            issues.append(_surface_issue(
                "PLUGIN_IMPORT_SYMBOL_NOT_PUBLIC",
                f"{location}: `{name}` is not a public symbol of `{module_name}`.",
                "Import a public name listed in the SDK surface, or promote the "
                "symbol with documented ownership, a version, and a conformance test.",
            ))
    return issues


def _file_issues(path: Path) -> list["PluginConformanceIssue"]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        return [_surface_issue(
            "PLUGIN_SOURCE_UNREADABLE",
            f"{path}: source could not be parsed: {error}",
            "Fix the Python source before running the surface check.",
        )]
    issues: list["PluginConformanceIssue"] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "assayer_platform":
                    continue
                if alias.name.startswith("assayer_platform."):
                    issues.extend(_module_issues(
                        alias.name, location=f"{path}:{node.lineno}",
                    ))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if node.module == "assayer_platform" or node.module.startswith("assayer_platform."):
                issues.extend(_symbol_issues(
                    node.module,
                    [alias.name for alias in node.names],
                    location=f"{path}:{node.lineno}",
                ))
    return issues


def _plugin_id(root: Path) -> str:
    for path in root.rglob("manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        plugin_id = data.get("pluginId")
        if isinstance(plugin_id, str) and plugin_id:
            return plugin_id
    return root.name


def inspect_plugin_surface(source_root: str | Path) -> PluginConformanceReport:
    """Validate a plugin source tree against the public SDK surface."""
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        return PluginConformanceReport(root.name, (_surface_issue(
            "PLUGIN_SURFACE_ROOT_MISSING",
            "The plugin source root does not exist or is not a directory.",
            "Provide the directory containing the plugin's Python source.",
        ),))
    issues: list["PluginConformanceIssue"] = []
    for path in sorted(root.rglob("*.py")):
        issues.extend(_file_issues(path))
    return PluginConformanceReport(_plugin_id(root), tuple(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate plugin imports against the public SDK surface",
    )
    parser.add_argument(
        "source", nargs="+", metavar="SOURCE_ROOT",
        help="Plugin source directory (containing the plugin's Python package)",
    )
    args = parser.parse_args(argv)
    reports = [inspect_plugin_surface(path) for path in args.source]
    payload = {
        "schemaVersion": "1.0.0",
        "surfaceVersion": PUBLIC_SURFACE_VERSION,
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "plugins": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
