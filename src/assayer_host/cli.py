"""User-facing Agent audit, Host-only smoke, and dynamic serving entry points."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from assayer_platform import PlatformContractError
from assayer_platform.plugin_verify import verify_plugin_source

from .errors import HostError
from .plugin_intent import IntentResolutionError
from .plugin_lifecycle_router import route_plugin_request
from .plugin_lifecycle_ops import (
    DEFAULT_CATALOG_URL,
    add_from_catalog as _add_from_catalog,
    apply_local_source_change as _apply_local_source_change,
    execute_intent_step as _execute_intent_step,
    known_plugin_ids as _known_plugin_ids,
    lifecycle_manager as _lifecycle_manager,
    plugin_catalog as _plugin_catalog,
    store_index_entries as _store_index_entries,
    store_registry as _store_registry,
)
from .plugin_store_registry import default_store_root
from .readiness import ReadinessReport, collect_readiness, target_kind
from .transport import mcp_main


def _error(code: str, message: str) -> dict:
    return {"protocolVersion": "1.0", "requestId": "assayer-cli", "status": "failed",
            "error": {"code": code, "message": message}, "evidenceRefs": [], "diagnosticRefs": []}


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _audit_prompt(target: str, *, plugin_id: str | None = None) -> str:
    kind = target_kind(target)
    if kind == "web":
        return (
            "Use $assayer-audit to investigate this web URL through the assayer MCP tools: "
            f"{target}\nComplete the audit; supply only business inputs exposed by the tools, "
            "let Assayer maintain all protocol and runtime configuration, and do not run the smoke runner."
        )

    path = Path(target).expanduser().resolve()
    selected_plugin = plugin_id
    plugin_instruction = (
        f"Use the installed {selected_plugin} plugin. "
        if selected_plugin is not None else
        "Select an installed plugin whose declared scope matches the target. "
    )
    return (
        f"Use $assayer-plugin to review this {kind} target through the assayer MCP tools: {path}\n"
        f"{plugin_instruction}Complete exactly one plugin Run for the requested target. "
        "If the required plugin is missing or ambiguous, report that clearly instead of guessing or starting a web audit. "
        "Supply only business inputs exposed by the tools, let Assayer maintain protocol and runtime configuration, "
        "and do not start Chromium."
    )


def _run_agent_audit(target: str, output_root: Path, *, plugin_id: str | None = None) -> int:
    codex = shutil.which("codex")
    if codex is None:
        _report_agent_start_error(
            "AGENT_RUNTIME_UNAVAILABLE",
            "Codex CLI was not found; formal audit will not fall back to smoke.",
        )
        return 2
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if target_kind(target) == "web":
        command = sys.executable
        args = ["-m", "assayer_host.transport", "--output-root", str(output_root)]
    else:
        # File and directory audits require the domain-plugin lifecycle MCP,
        # not the browser Runtime Router exposed by ``transport --mcp``.
        # Use the console entry point adjacent to this exact Assayer runtime so
        # a dynamically configured Codex process cannot bind a stale/global
        # installation with the same server name.
        command = str(Path(sys.executable).with_name("assayer-mcp"))
        args = [
            "--output-root", str(output_root),
            "--store", str(default_store_root()),
        ]
    prompt = _audit_prompt(target, plugin_id=plugin_id)
    config_command = f"mcp_servers.assayer.command={_toml_string(command)}"
    config_args = f"mcp_servers.assayer.args={json.dumps(args, ensure_ascii=False)}"
    try:
        completed = subprocess.run(
            [
                codex, "exec", "--approve-for-me", "-C", str(Path.cwd()),
                "-c", config_command, "-c", config_args, prompt,
            ],
            check=False,
        )
    except OSError:
        _report_agent_start_error(
            "AGENT_RUNTIME_UNAVAILABLE",
            "Codex Agent Runtime failed to start; smoke fallback was not executed.",
        )
        return 2
    return completed.returncode


def _report_agent_start_error(code: str, message: str) -> None:
    """Use human wording interactively while preserving JSON automation output."""
    if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
        print("Assayer cannot start the audit.", file=sys.stderr)
        print(f"Reason: {message}", file=sys.stderr)
        print("Next step: assayer doctor", file=sys.stderr)
        return
    _print_json(_error(code, message))


def _print_json(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _print_error(code: str, message: str) -> None:
    _print_json(_error(code, message))


def _default_plugin_root() -> Path | None:
    configured = os.environ.get("ASSAYER_PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    cache_root = codex_home / "plugins" / "cache"
    candidates = [
        path for path in cache_root.glob("*/assayer/*")
        if (path / ".codex-plugin" / "plugin.json").is_file()
    ]
    if candidates:
        personal = [path for path in candidates if path.parents[1].name == "personal"]
        complete = [
            path for path in candidates
            if (path / "runtime" / "bundle-manifest.json").is_file()
        ]
        preferred = personal or complete or candidates
        # Cache directories are immutable version snapshots. The most recently
        # materialized one corresponds to the latest local installation even
        # when the SemVer build suffix is not lexically sortable.
        return max(preferred, key=lambda path: path.stat().st_mtime_ns).resolve()
    marketplace_source = Path.home() / "plugins" / "assayer"
    if (marketplace_source / ".codex-plugin" / "plugin.json").is_file():
        return marketplace_source.resolve()
    source_root = Path(__file__).resolve().parents[2] / "plugins" / "assayer"
    return source_root if source_root.is_dir() else None


def _print_readiness(report: ReadinessReport, *, as_json: bool) -> int:
    if as_json:
        _print_json(report.as_dict())
    else:
        print("Assayer readiness")
        print("")
        for check in report.checks:
            suffix = "required" if check.required else "optional"
            print(f"{check.check_id}: {check.status} ({suffix}) — {check.message}")
        if not report.ready:
            print("")
            print(f"Repair: {report.repair_command or 'read the failing check above'}")
    return 0 if report.ready else 2


def _runtime_can_be_prepared(report: ReadinessReport) -> bool:
    statuses = {check.check_id: check.status for check in report.checks}
    return statuses.get("assayer_bundle") == "ok" and statuses.get("private_runtime") != "ok"


def _prepare_private_runtime(plugin_root: Path, *, timeout_seconds: float = 600.0) -> tuple[bool, str | None]:
    preparer = plugin_root / "scripts" / "prepare_assayer_runtime"
    if not preparer.is_file():
        return False, "The Assayer runtime preparer is missing; reinstall the Assayer Codex Plugin."
    try:
        completed = subprocess.run(
            [str(preparer)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, "Runtime preparation timed out; reinstall the Assayer Codex Plugin and retry."
    except OSError:
        return False, "The Assayer runtime preparer could not be started; reinstall the Assayer Codex Plugin."
    if completed.returncode == 0:
        return True, None
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    if detail:
        # The launcher emits stable one-line error codes. Bound the forwarded
        # detail so doctor never turns a failed installer into a stack dump.
        return False, detail[-1][:1000]
    return False, "The Assayer private runtime could not be prepared."


def _print_doctor_fix_failure(report: ReadinessReport, message: str, *, as_json: bool) -> int:
    if as_json:
        payload = report.as_dict()
        payload["repair"] = {
            "status": "failed",
            "code": "RUNTIME_PREPARE_FAILED",
            "message": message,
        }
        _print_json(payload)
    else:
        print("Assayer could not prepare its private runtime.")
        print(f"Reason: {message}")
        print("Next step: reinstall the Assayer Codex Plugin, then run: assayer doctor --fix")
    return 2


def _run_doctor(
    *,
    target: str | None,
    plugin_id: str | None,
    store: str,
    plugin_root: str | None,
    as_json: bool,
    fix: bool,
) -> int:
    resolved_plugin_root = Path(plugin_root).expanduser().resolve() if plugin_root else _default_plugin_root()
    report = collect_readiness(
        target=target,
        plugin_id=plugin_id,
        store_root=store,
        plugin_root=resolved_plugin_root,
    )
    if fix and _runtime_can_be_prepared(report):
        assert resolved_plugin_root is not None
        prepared, error = _prepare_private_runtime(resolved_plugin_root)
        if not prepared:
            return _print_doctor_fix_failure(report, error or "Runtime preparation failed.", as_json=as_json)
        report = collect_readiness(
            target=target,
            plugin_id=plugin_id,
            store_root=store,
            plugin_root=resolved_plugin_root,
        )
    return _print_readiness(report, as_json=as_json)


def _prompt_confirmation(plan: list[dict]) -> bool:
    try:
        answer = input("Proceed with this operation? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _split_intent_args(argv: list[str]) -> tuple[str, str, bool, str, str | None]:
    store = str(default_store_root())
    output_root = "./assayer-output"
    index_arg: str | None = None
    yes = False
    words: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--yes":
            yes = True
        elif arg == "--store":
            store = argv[index + 1]
            index += 1
        elif arg.startswith("--store="):
            store = arg.split("=", 1)[1]
        elif arg == "--output-root":
            output_root = argv[index + 1]
            index += 1
        elif arg.startswith("--output-root="):
            output_root = arg.split("=", 1)[1]
        elif arg == "--index":
            index_arg = argv[index + 1]
            index += 1
        elif arg.startswith("--index="):
            index_arg = arg.split("=", 1)[1]
        else:
            words.append(arg)
        index += 1
    return " ".join(words), store, yes, output_root, index_arg


def _mutation_step(args) -> dict:
    step = {"operation": args.plugin_command}
    if args.plugin_command in {"install", "upgrade"}:
        step["package"] = args.package
    elif args.plugin_command == "downgrade":
        step["pluginId"] = args.plugin_id
        step["version"] = args.version
    else:
        step["pluginId"] = args.plugin_id
    return step


def _run_intent(text: str, store_root: str, *, yes: bool, output_root: str, confirm, index: str | None = None) -> int:
    catalog_index = index or DEFAULT_CATALOG_URL
    try:
        route = route_plugin_request(
            text, known_plugin_ids=_known_plugin_ids(store_root), cwd=Path.cwd(),
        )
    except IntentResolutionError as error:
        _print_error(error.code, error.message)
        return 2
    if route.workflow.startswith("product_"):
        _print_error("PRODUCT_LIFECYCLE_DEFERRED", route.message or "Use Codex Marketplace for Assayer lifecycle changes.")
        return 2
    plan = route.steps
    plan_payload = [step.as_dict() for step in plan]
    if any(step.dangerous for step in plan) and not yes:
        _print_json({"plan": plan_payload, "pending": "confirmation"})
        if not confirm(plan_payload):
            _print_json({"plan": plan_payload, "status": "aborted",
                         "reason": "confirmation required"})
            return 0
    results = [
        _execute_intent_step(step, store_root, output_root, catalog_index)
        for step in plan
    ]
    _print_json({"plan": plan_payload, "results": results})
    completed = all(result.get("status") in {"completed", "quarantined"} for result in results)
    return 0 if completed else 1


def _plugins_command(args, *, confirm) -> int:
    if args.plugin_command == "run":
        _print_error(
            "COMPILED_RUN_REQUIRED",
            "The legacy plugin run command is removed; use the compiled contract runtime.",
        )
        return 2
    if args.plugin_command == "info":
        manager = _lifecycle_manager(args.store)
        entry = manager.get(args.plugin_id)
        if entry is None:
            _print_error("UNKNOWN_PLUGIN", f"Plugin is not installed: {args.plugin_id}")
            return 2
        _print_json(entry)
        return 0
    if args.plugin_command == "build":
        try:
            result = verify_plugin_source(
                args.source,
                output_dir=Path(args.source).expanduser().resolve() / ".assayer" / "build",
            )
        except PlatformContractError as error:
            _print_error(error.code, error.message)
            return 2
        _print_json(result)
        return 0 if result.get("status") == "passed" else 1
    if args.plugin_command == "add":
        if not args.yes and not confirm([
            {"operation": "add", "plugin": args.plugin, "version": args.version},
        ]):
            _print_json({"operation": "add", "status": "aborted",
                         "reason": "confirmation required"})
            return 0
        try:
            result = _add_from_catalog(
                args.plugin, version=args.version, index=args.index, store_root=args.store,
            )
        except PlatformContractError as error:
            _print_error(error.code, error.message)
            return 2
        _print_json(result)
        return 0 if result.get("status") in {"completed", "quarantined"} else 1
    if args.plugin_command in {"install", "upgrade", "downgrade", "rollback", "uninstall"}:
        if not args.yes and not confirm([_mutation_step(args)]):
            _print_json({"operation": args.plugin_command, "status": "aborted",
                         "reason": "confirmation required"})
            return 0
        manager = _lifecycle_manager(args.store)
        try:
            if args.plugin_command == "install":
                result = _apply_local_source_change(
                    operation="install", source=args.package, store_root=args.store,
                )
            elif args.plugin_command == "upgrade":
                result = _apply_local_source_change(
                    operation="upgrade", source=args.package, store_root=args.store,
                )
            elif args.plugin_command == "downgrade":
                result = manager.downgrade(args.plugin_id, args.version)
            elif args.plugin_command == "rollback":
                result = manager.rollback(args.plugin_id)
            else:
                result = manager.uninstall(args.plugin_id)
        except PlatformContractError as error:
            _print_error(error.code, error.message)
            return 2
        _print_json(result)
        return 0
    catalog = _plugin_catalog(_store_registry(args.store))
    entries = _store_index_entries(args.store)
    quarantined = [entry for entry in entries if entry.get("state") == "dirty"]
    if args.as_json:
        payload = {"plugins": catalog}
        if quarantined:
            payload["quarantined"] = quarantined
        _print_json(payload)
    else:
        for plugin in catalog:
            checks = ", ".join(
                f"{item['checkId']}@{item['version']}" for item in plugin["checks"]
            )
            line = f"{plugin['pluginId']} {plugin['version']} [{checks}]"
            print(line)
        for entry in quarantined:
            print(f"{entry['pluginId']} (dirty: {entry.get('stateReason')})")
    return 0


def main(argv: list[str] | None = None, *, confirm=_prompt_confirmation) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"audit", "doctor", "smoke", "serve", "plugin", "plugins"} and not argv[0].startswith("-"):
        text, store, yes, output_root, index = _split_intent_args(argv)
        return _run_intent(text, store, yes=yes, output_root=output_root, confirm=confirm, index=index)
    parser = argparse.ArgumentParser(
        prog="assayer",
        description="Run Assayer reviews and check installation readiness",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="{audit,doctor}",
    )
    doctor = subparsers.add_parser(
        "doctor", help="Check Assayer readiness without starting an audit or browser",
    )
    doctor.add_argument("--target", default=None, help="Optional URL, file, or directory to check")
    doctor.add_argument("--plugin", default=None, dest="plugin_id", help="Domain plugin required by the target")
    doctor.add_argument("--store", default=str(default_store_root()), help=argparse.SUPPRESS)
    doctor.add_argument("--plugin-root", default=None, help=argparse.SUPPRESS)
    doctor.add_argument("--fix", action="store_true", help="Prepare the bundled private runtime, then check again")
    doctor.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable readiness JSON")
    audit = subparsers.add_parser("audit", help="Review a URL, file, or directory through the matching Assayer Skill")
    audit.add_argument("target")
    audit.add_argument("--plugin", default=None, dest="plugin_id",
                       help="Installed domain plugin to use for a file or directory")
    audit.add_argument("--output-root", default="./assayer-output")
    # Advanced/debug entry points remain callable but are intentionally absent
    # from the first-use help surface. Omitting ``help`` keeps argparse from
    # adding them to the displayed subcommand table; the explicit metavar
    # above keeps them out of usage as well.
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("url")
    smoke.add_argument("--output-dir", default="./assayer-output")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--output-root", default="./assayer-output")
    serve.add_argument("--max-runtimes", type=int, default=4)
    serve.add_argument("--lease-timeout", type=float, default=300.0)
    plugins = subparsers.add_parser("plugins")
    plugin = subparsers.add_parser("plugin")
    plugin_subparsers = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_verify = plugin_subparsers.add_parser(
        "verify", help="Compile and verify an ordinary plugin as one data-only contract",
    )
    plugin_verify.add_argument("source", help="Ordinary plugin source directory")
    plugin_verify.add_argument("--output-dir", default=".assayer/verified")
    plugin_verify.add_argument("--build-timeout-seconds", type=int, default=120)
    plugin_verify.add_argument("--install-timeout-seconds", type=int, default=120)
    plugin_verify.add_argument("--fixture-timeout-seconds", type=int, default=300)
    plugin_common = argparse.ArgumentParser(add_help=False)
    plugin_common.add_argument("--store", default=str(default_store_root()),
                               help="Plugin installation store directory")
    plugins_subparsers = plugins.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugins_subparsers.add_parser("list", parents=[plugin_common],
                                                help="List registered plugins and checks")
    plugin_list.add_argument("--json", action="store_true", dest="as_json")
    plugin_info = plugins_subparsers.add_parser("info", parents=[plugin_common],
                                                help="Show one installed plugin's version and state")
    plugin_info.add_argument("plugin_id")
    plugin_run = plugins_subparsers.add_parser("run", parents=[plugin_common],
                                               help="Run one registered plugin Check")
    plugin_run.add_argument("--plugin", required=True, dest="plugin_id")
    plugin_run.add_argument("--check", required=True, dest="check_id")
    plugin_run.add_argument("--check-version")
    plugin_run.add_argument("--scope-json", dest="scope_json")
    plugin_run.add_argument("--scope", dest="scope_file",
                            help="Path to a JSON scope file (alternative to --scope-json)")
    plugin_run.add_argument("--output-root", default="./assayer-output")
    plugin_mutation_common = argparse.ArgumentParser(add_help=False)
    plugin_mutation_common.add_argument("--yes", action="store_true", dest="yes",
                                        help="Skip the stop-and-confirm prompt for dangerous operations")
    plugin_build = plugins_subparsers.add_parser("build", parents=[plugin_common],
                                                 help="Compile a data-only plugin contract")
    plugin_build.add_argument("source", help="Plugin source directory")
    plugin_add = plugins_subparsers.add_parser("add", parents=[plugin_common, plugin_mutation_common],
                                               help="Download, verify, and install a compiled-plugin artifact from the catalog")
    plugin_add.add_argument("plugin", help="Plugin ID to resolve from the catalog")
    plugin_add.add_argument("--version", default=None, help="Pin a specific plugin version")
    plugin_add.add_argument("--index", default=DEFAULT_CATALOG_URL,
                            help="Compiled-artifact catalog location: an http(s) URL or local plugins.json path")
    plugin_install = plugins_subparsers.add_parser("install", parents=[plugin_common, plugin_mutation_common],
                                                   help="Install a plugin package into the store")
    plugin_install.add_argument("package")
    plugin_upgrade = plugins_subparsers.add_parser("upgrade", parents=[plugin_common, plugin_mutation_common],
                                                   help="Upgrade an installed plugin to a newer package")
    plugin_upgrade.add_argument("package")
    plugin_downgrade = plugins_subparsers.add_parser("downgrade", parents=[plugin_common, plugin_mutation_common],
                                                     help="Downgrade an installed plugin to a previous version")
    plugin_downgrade.add_argument("plugin_id")
    plugin_downgrade.add_argument("version")
    plugin_rollback = plugins_subparsers.add_parser("rollback", parents=[plugin_common, plugin_mutation_common],
                                                    help="Roll back an installed plugin to its previous version")
    plugin_rollback.add_argument("plugin_id")
    plugin_uninstall = plugins_subparsers.add_parser("uninstall", parents=[plugin_common, plugin_mutation_common],
                                                     help="Remove an installed plugin from the store")
    plugin_uninstall.add_argument("plugin_id")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor(
            target=args.target, plugin_id=args.plugin_id, store=args.store,
            plugin_root=args.plugin_root, as_json=args.as_json, fix=args.fix,
        )
    if args.command == "audit":
        return _run_agent_audit(args.target, Path(args.output_root), plugin_id=args.plugin_id)
    if args.command == "serve":
        return mcp_main(["--output-root", args.output_root])
    if args.command == "plugins":
        return _plugins_command(args, confirm=confirm)
    if args.command == "plugin":
        result = verify_plugin_source(args.source, output_dir=args.output_dir)
        _print_json(result)
        return 0 if result["status"] == "passed" else 1
    raise SystemExit(
        "The legacy browser smoke entry point was removed; run an installed plugin through the plugin lifecycle."
    )


if __name__ == "__main__":
    raise SystemExit(main())
