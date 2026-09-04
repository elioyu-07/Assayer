"""Read-only installation and feedback diagnostics for the Assayer product."""

from __future__ import annotations

import json
import os
import platform
import re
import sysconfig
from importlib import metadata
from pathlib import Path
from typing import Mapping


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_FIXED_DIAGNOSTIC_ARTIFACTS = (
    "canonical-result.json",
    "audit-summary.md",
    "run-diagnostics.md",
    "platform-run.log",
    "result-summary.json",
)
_IDENTITY_DOCUMENTS = (
    "canonical-result.json",
    "result-summary.json",
    "run-diagnostics.json",
    "audit-ledger.json",
)
_MAX_IDENTITY_DOCUMENT_BYTES = 2 * 1024 * 1024
_AUTO_PACKAGE_VERSION = object()


def _package_version() -> str | None:
    try:
        return metadata.version("assayer")
    except metadata.PackageNotFoundError:
        return None


def _valid_version(value: object) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def _safe_json(path: Path) -> object | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_IDENTITY_DOCUMENT_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _document_run_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    direct = value.get("runId")
    if isinstance(direct, str) and _ID.fullmatch(direct):
        return direct
    scan = value.get("scan")
    nested = scan.get("runId") if isinstance(scan, dict) else None
    if isinstance(nested, str) and _ID.fullmatch(nested):
        return nested
    run = value.get("run")
    canonical = run.get("runId") if isinstance(run, dict) else None
    return canonical if isinstance(canonical, str) and _ID.fullmatch(canonical) else None


class InstallationStatusBuilder:
    """Build a bounded status document without starting or mutating a Run."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
        package_version: str | None | object = _AUTO_PACKAGE_VERSION,
        python_identity: Mapping[str, str] | None = None,
    ) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        self._environ = dict(os.environ if environ is None else environ)
        discovered_package_version = (
            _package_version() if package_version is _AUTO_PACKAGE_VERSION
            else package_version
        )
        self._package_version = _valid_version(discovered_package_version)
        self._python_identity = dict(python_identity or {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "platform": sysconfig.get_platform(),
        })

    @staticmethod
    def _check(code: str, status: str, message: str) -> dict[str, str]:
        return {"code": code, "status": status, "message": message}

    def build(self, *, max_recent_runs: int = 5) -> dict:
        if not isinstance(max_recent_runs, int) or isinstance(max_recent_runs, bool) or not 1 <= max_recent_runs <= 10:
            raise ValueError("max_recent_runs must be an integer from 1 through 10")
        raw_plugin_version = self._environ.get("ASSAYER_PLUGIN_VERSION") or None
        raw_runtime_version = self._environ.get("ASSAYER_RUNTIME_VERSION") or None
        plugin_version = _valid_version(raw_plugin_version)
        runtime_version = _valid_version(raw_runtime_version)
        bundle_verified = self._environ.get("ASSAYER_BUNDLE_VERIFIED")
        checks: list[dict[str, str]] = []

        metadata_supplied = raw_plugin_version is not None or raw_runtime_version is not None
        metadata_complete = plugin_version is not None and runtime_version is not None
        if self._package_version:
            checks.append(self._check(
                "PACKAGE_IDENTITY_AVAILABLE", "pass",
                "The running Assayer package version is identifiable.",
            ))
        else:
            checks.append(self._check(
                "PACKAGE_IDENTITY_MISSING", "fail" if metadata_complete else "warning",
                "The running Assayer package version could not be identified.",
            ))

        if metadata_complete:
            checks.append(self._check(
                "LAUNCHER_METADATA_AVAILABLE", "pass",
                "The release launcher supplied plugin and private-runtime identity.",
            ))
        elif metadata_supplied:
            checks.append(self._check(
                "LAUNCHER_METADATA_INVALID", "fail",
                "Release launcher identity is incomplete or invalid.",
            ))
        else:
            checks.append(self._check(
                "LAUNCHER_METADATA_MISSING", "warning",
                "Release launcher identity is unavailable; this is expected for a source or development run.",
            ))

        if bundle_verified == "1":
            checks.append(self._check(
                "BUNDLE_INTEGRITY_VERIFIED", "pass",
                "The release launcher verified bundle metadata and wheel integrity before startup.",
            ))
        elif bundle_verified is None:
            checks.append(self._check(
                "BUNDLE_INTEGRITY_UNVERIFIED", "warning",
                "Bundle integrity metadata is unavailable because the release launcher was not observed.",
            ))
        else:
            checks.append(self._check(
                "BUNDLE_INTEGRITY_INVALID", "fail",
                "The current process does not carry a verified release-bundle marker.",
            ))

        if metadata_complete and self._package_version:
            aligned = (
                plugin_version.split("+", 1)[0] == self._package_version
                and runtime_version == self._package_version
            )
            checks.append(self._check(
                "VERSION_IDENTITY_ALIGNED" if aligned else "VERSION_IDENTITY_MISMATCH",
                "pass" if aligned else "fail",
                (
                    "Package, plugin, and private-runtime versions are aligned."
                    if aligned else
                    "Package, plugin, and private-runtime versions do not describe one release."
                ),
            ))
        else:
            checks.append(self._check(
                "VERSION_IDENTITY_UNVERIFIED", "warning",
                "Version alignment cannot be verified without complete launcher and package identity.",
            ))

        check_states = {item["status"] for item in checks}
        if "fail" in check_states:
            status = "degraded"
            message = "The Assayer installation is inconsistent and should not be used for a formal audit."
            next_action = "Reinstall one complete Assayer release bundle, then request installation status again."
        elif "warning" in check_states:
            status = "limited"
            message = "Assayer is running, but release-bundle identity cannot be fully verified."
            next_action = "For release verification, start Assayer through the installed Codex plugin launcher."
        else:
            status = "healthy"
            message = "The running Assayer package, plugin, and private runtime are aligned and verified."
            next_action = "Assayer is ready for an audit."

        return {
            "schemaVersion": "1.0.0",
            "status": status,
            "message": message,
            "identity": {
                "packageVersion": self._package_version,
                "pluginVersion": plugin_version,
                "runtimeVersion": runtime_version,
            },
            "python": self._python_identity,
            "checks": checks,
            "recentRuns": self._recent_runs(max_recent_runs),
            "feedback": {
                "message": "Share the Run ID and only the listed diagnostic artifacts when reporting a problem.",
                "include": ["runId", "artifacts"],
            },
            "nextAction": next_action,
        }

    def _recent_runs(self, limit: int) -> list[dict]:
        try:
            directories = [
                item for item in self._output_root.iterdir()
                if item.is_dir() and not item.is_symlink()
            ]
        except OSError:
            return []
        directories.sort(key=self._modified_time, reverse=True)
        runs: list[dict] = []
        seen: set[str] = set()
        for directory in directories[:100]:
            run_id = self._run_id(directory)
            if run_id is None or run_id in seen:
                continue
            artifacts = self._diagnostic_artifacts(directory, run_id)
            if not artifacts:
                continue
            runs.append({"runId": run_id, "artifacts": artifacts})
            seen.add(run_id)
            if len(runs) >= limit:
                break
        return runs

    @staticmethod
    def _modified_time(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _run_id(directory: Path) -> str | None:
        if _ID.fullmatch(directory.name) and directory.name.startswith("run-"):
            return directory.name
        for name in _IDENTITY_DOCUMENTS:
            run_id = _document_run_id(_safe_json(directory / name))
            if run_id is not None:
                return run_id
        for path in directory.glob("*.platform-ledger.json"):
            suffix = ".platform-ledger.json"
            candidate = path.name[:-len(suffix)]
            if _ID.fullmatch(candidate):
                return candidate
        return None

    @staticmethod
    def _diagnostic_artifacts(directory: Path, run_id: str) -> list[str]:
        artifacts = [
            name for name in _FIXED_DIAGNOSTIC_ARTIFACTS
            if (directory / name).is_file() and not (directory / name).is_symlink()
        ]
        platform_diary = f"{run_id}.platform-run.log"
        if (directory / platform_diary).is_file() and not (directory / platform_diary).is_symlink():
            artifacts.append(platform_diary)
        return artifacts
