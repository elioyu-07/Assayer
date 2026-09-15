"""Import-boundary conformance for Assayer plugins.

Enforces the Platform--Plugin Boundary Contract: a plugin may import only
the standalone SDK surface.  Platform and Host implementation modules are
not plugin dependencies.

The check walks the plugin's Python source tree and flags any
``assayer_platform`` imports are rejected.  Symbol-level enforcement applies
to SDK imports through the SDK's own public surface.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from .conformance import PluginConformanceIssue, PluginConformanceReport, _issue
from .public_surface import PUBLIC_SURFACE, PUBLIC_SURFACE_VERSION

_SURFACE_INVARIANT = "PBV1-PUBLIC-SURFACE"

# Any ``assayer*`` module other than the SDK is a platform or provider
# implementation that a plugin must reach through the Host binding.
_ALLOWED_ASSAYER_ROOTS = frozenset({"assayer_plugin_sdk"})


def _surface_issue(
    code: str, message: str, next_action: str,
) -> "PluginConformanceIssue":
    return _issue(code, _SURFACE_INVARIANT, message, next_action)


def _provider_import_issues(
    path: Path, root: Path, modules: list[str], *, location: str,
) -> list["PluginConformanceIssue"]:
    issues: list["PluginConformanceIssue"] = []
    for module_name in modules:
        top = module_name.split(".", 1)[0]
        if top == "assayer_platform":
            continue
        if not top.startswith("assayer") or top in _ALLOWED_ASSAYER_ROOTS:
            continue
        if (root / top).is_dir() or (root / f"{top}.py").is_file():
            continue
        issues.append(_surface_issue(
            "PLUGIN_IMPORT_PROVIDER_IMPLEMENTATION",
            f"{location}: imports concrete implementation `{module_name}`.",
            "Depend only on assayer-plugin-sdk and consume capabilities through "
            "the Host-bound CapabilityAccess contract.",
        ))
    return issues


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


def _file_issues(path: Path, root: Path) -> list["PluginConformanceIssue"]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        return [_surface_issue(
            "PLUGIN_SOURCE_UNREADABLE",
            f"{path}: source could not be parsed: {error}",
            "Fix the Python source before running the surface check.",
        )]
    issues: list["PluginConformanceIssue"] = []
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
                if alias.name == "assayer_platform" or alias.name.startswith("assayer_platform."):
                    issues.append(_surface_issue(
                        "PLUGIN_IMPORT_PLATFORM_IMPLEMENTATION",
                        f"{path}:{node.lineno}: imports platform implementation `{alias.name}`.",
                        "Import the equivalent contract from assayer_plugin_sdk.",
                    ))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            imported_modules.append(node.module)
            if node.module == "assayer_platform" or node.module.startswith("assayer_platform."):
                issues.append(_surface_issue(
                    "PLUGIN_IMPORT_PLATFORM_IMPLEMENTATION",
                    f"{path}:{node.lineno}: imports platform implementation `{node.module}`.",
                    "Import the equivalent contract from assayer_plugin_sdk.",
                ))
    issues.extend(_provider_import_issues(
        path, root, imported_modules, location=f"{path}",
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
        issues.extend(_file_issues(path, root))
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
