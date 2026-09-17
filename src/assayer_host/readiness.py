"""Human- and machine-readable readiness checks for the public CLI.

Readiness is deliberately separate from the audit Host.  It performs bounded,
side-effect-free checks so ``assayer doctor`` can explain what is missing
without starting Chromium, an MCP server, or an audit Run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .plugin_store_registry import default_store_root, store_backed_plugin_registry


@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    status: str
    required: bool
    message: str
    repair_command: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "id": self.check_id,
            "status": self.status,
            "required": self.required,
            "message": self.message,
        }
        if self.repair_command:
            value["repairCommand"] = self.repair_command
        if self.details:
            value["details"] = dict(self.details)
        return value


@dataclass(frozen=True)
class ReadinessReport:
    target: str | None
    target_kind: str
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(item.status in {"ok", "optional", "deferred"} for item in self.checks if item.required)

    @property
    def repair_command(self) -> str | None:
        return next((item.repair_command for item in self.checks if item.repair_command), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "target": self.target,
            "targetKind": self.target_kind,
            "checks": [item.as_dict() for item in self.checks],
            **({"repairCommand": self.repair_command} if self.repair_command else {}),
        }


def target_kind(target: str | None) -> str:
    if not target:
        return "unknown"
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return "web"
    path = Path(target).expanduser()
    if path.suffix.lower() in {".md", ".markdown", ".mdown"}:
        return "markdown"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "unknown"


def _codex_check(*, executable: str | None = None) -> ReadinessCheck:
    path = executable or shutil.which("codex")
    if path is None:
        return ReadinessCheck(
            "codex_cli", "missing", True,
            "Codex CLI was not found.",
            "Install Codex CLI, then run: assayer doctor",
        )
    return ReadinessCheck("codex_cli", "ok", True, f"Codex CLI found at {path}.", details={"path": path})


def _python_check() -> ReadinessCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 11):
        return ReadinessCheck(
            "python", "unsupported", True,
            f"Python {version} is unsupported; Python 3.11 or newer is required.",
            "Install the Python version required by the Assayer bundle.",
            details={"version": version},
        )
    return ReadinessCheck("python", "ok", True, f"Python {version} is supported.", details={"version": version})


def _plugin_check(plugin_id: str | None, store_root: str | Path) -> ReadinessCheck:
    if not plugin_id:
        return ReadinessCheck("domain_plugin", "not_checked", False, "No domain plugin was required for this check.")
    try:
        lifecycle = store_backed_plugin_registry(store_root)
        registration = lifecycle.get(plugin_id)
    except Exception as error:
        return ReadinessCheck(
            "domain_plugin", "broken", True,
            f"The plugin store could not be read: {type(error).__name__}.",
            "Run: assayer doctor --fix",
            details={"pluginId": plugin_id},
        )
    if registration is None:
        return ReadinessCheck(
            "domain_plugin", "missing", True,
            f"Required plugin is not installed: {plugin_id}.",
            f"Install {plugin_id} through the Assayer lifecycle, then run: assayer doctor",
            details={"pluginId": plugin_id},
        )
    return ReadinessCheck(
        "domain_plugin", "ok", True,
        f"Required plugin is installed: {plugin_id}.",
        details={"pluginId": plugin_id, "version": registration.get("activeVersion")},
    )


def _bundle_check(plugin_root: Path | None) -> ReadinessCheck:
    if plugin_root is None:
        return ReadinessCheck(
            "assayer_bundle", "not_checked", False,
            "The installed Codex Plugin location is not available to this CLI process.",
        )
    manifest_path = plugin_root / "runtime" / "bundle-manifest.json"
    wheel_dir = plugin_root / "runtime" / "wheels"
    if not manifest_path.is_file() or not wheel_dir.is_dir():
        return ReadinessCheck(
            "assayer_bundle", "missing", True,
            "The Assayer runtime bundle is incomplete.",
            "Reinstall the Assayer Codex Plugin, then run: assayer doctor --fix",
            details={"pluginRoot": str(plugin_root)},
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        runtime = manifest["runtime"]
        expected_python = str(runtime["pythonVersion"])
        expected_platform = str(runtime["platform"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return ReadinessCheck(
            "assayer_bundle", "broken", True,
            f"The runtime bundle manifest is invalid: {type(error).__name__}.",
            "Reinstall the Assayer Codex Plugin, then run: assayer doctor --fix",
        )
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    actual_platform = sysconfig.get_platform()
    if expected_python != actual_python or expected_platform != actual_platform:
        return ReadinessCheck(
            "assayer_bundle", "incompatible", True,
            f"Bundle requires Python {expected_python}/{expected_platform}; detected {actual_python}/{actual_platform}.",
            "Install the matching Assayer Codex Plugin bundle.",
            details={"expectedPython": expected_python, "expectedPlatform": expected_platform},
        )
    wheels = tuple(wheel_dir.glob("assayer-*.whl"))
    if not wheels:
        return ReadinessCheck(
            "assayer_bundle", "incomplete", True,
            "The Assayer bundle contains no platform wheelhouse.",
            "Reinstall the Assayer Codex Plugin, then run: assayer doctor --fix",
        )
    return ReadinessCheck(
        "assayer_bundle", "ok", True,
        "The Assayer bundle matches this Python/platform.",
        details={"python": actual_python, "platform": actual_platform},
    )


def _runtime_check(plugin_root: Path | None) -> ReadinessCheck:
    if os.environ.get("ASSAYER_BUNDLE_VERIFIED") == "1":
        return ReadinessCheck("private_runtime", "ok", True, "The Assayer private runtime is verified.")
    if plugin_root is None:
        return ReadinessCheck(
            "private_runtime", "not_checked", False,
            "The installed Codex Plugin runtime location is not available to this CLI process.",
        )
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        plugin_version = str(json.loads(manifest_path.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return ReadinessCheck("private_runtime", "unknown", True, "The private runtime version could not be resolved.")
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    runtime_root = cache_root / "assayer" / f"runtime-{plugin_version}"
    executable = runtime_root / "venv" / "bin" / "assayer-mcp"
    if executable.is_file() and (runtime_root / "bundle-ready").is_file():
        return ReadinessCheck(
            "private_runtime", "ok", True,
            "The Assayer private runtime is ready.",
            details={"runtimeRoot": str(runtime_root)},
        )
    return ReadinessCheck(
        "private_runtime", "missing", True,
        "The Assayer private runtime has not been prepared.",
        "Run: assayer doctor --fix",
        details={"runtimeRoot": str(runtime_root)},
    )


def _local_chromium_path() -> str | None:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ])
    elif os.name == "nt":
        for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(root_name)
            if root:
                candidates.extend([
                    Path(root) / "Google/Chrome/Application/chrome.exe",
                    Path(root) / "Microsoft/Edge/Application/msedge.exe",
                ])
    else:
        for name in ("google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable"):
            executable = shutil.which(name)
            if executable:
                return executable
    return next((str(path) for path in candidates if path.is_file()), None)


def _browser_check(required: bool) -> ReadinessCheck:
    if not required:
        return ReadinessCheck("chromium", "optional", False, "Chromium is not required for this target.")
    if importlib.util.find_spec("playwright") is None:
        return ReadinessCheck(
            "chromium", "missing", True,
            "Playwright is not installed; a web audit cannot start Chromium.",
            "Install the browser-enabled Assayer runtime, then run: assayer doctor --fix",
        )
    executable = _local_chromium_path()
    if executable is None:
        return ReadinessCheck(
            "chromium", "missing", True,
            "Google Chrome or Microsoft Edge was not found; a web audit cannot start.",
            "Install Google Chrome or Microsoft Edge, then run: assayer doctor",
        )
    return ReadinessCheck(
        "chromium", "deferred", True,
        "Playwright and a local Chromium browser are installed; launch is deferred until the web audit starts.",
        details={"path": executable},
    )


def collect_readiness(
    *,
    target: str | None = None,
    plugin_id: str | None = None,
    store_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    codex_executable: str | None = None,
) -> ReadinessReport:
    """Collect bounded checks without starting an audit or browser."""
    root = Path(plugin_root).expanduser().resolve() if plugin_root else None
    checks = (
        _python_check(),
        _codex_check(executable=codex_executable),
        _bundle_check(root),
        _runtime_check(root),
        _plugin_check(plugin_id, store_root or default_store_root()),
        _browser_check(target_kind(target) == "web"),
    )
    return ReadinessReport(target, target_kind(target), checks)


__all__ = ["ReadinessCheck", "ReadinessReport", "collect_readiness", "target_kind"]
