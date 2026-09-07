"""Single fail-fast command for the complete plugin release contract gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .conformance import (
    PluginConformanceIssue,
    PluginConformanceReport,
    _package_issue,
    inspect_plugin_package,
)
from .installation_conformance import inspect_plugin_installation
from .surface_conformance import inspect_plugin_surface


def _stage(name: str, status: str, report: PluginConformanceReport | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name, "status": status}
    if report is not None and report.issues:
        value["issues"] = [item.as_dict() for item in report.issues]
    return value


def _failed_report(plugin_id: str, issue: PluginConformanceIssue) -> PluginConformanceReport:
    return PluginConformanceReport(plugin_id, (issue,))


def _build_wheel(
    source: Path, destination: Path, *, python: str, timeout_seconds: int,
) -> tuple[Path | None, PluginConformanceReport | None]:
    plugin_id = inspect_plugin_package(source).plugin_id
    pip_probe = subprocess.run(
        [python, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if pip_probe.returncode == 0:
        command = [
            python, "-m", "pip", "wheel", "--disable-pip-version-check",
            "--no-deps", "--no-build-isolation", "--wheel-dir", str(destination),
            str(source),
        ]
    else:
        uv = shutil.which("uv")
        if uv is None:
            return None, _failed_report(plugin_id, _package_issue(
                "PLUGIN_WHEEL_BUILDER_UNAVAILABLE",
                "The selected Python has no pip and the uv wheel builder is unavailable.",
                "Provide pip for the selected Python or install uv before running the release gate.",
            ))
        command = [
            uv, "build", "--wheel", "--no-build-isolation", "--out-dir",
            str(destination), str(source),
        ]
    try:
        built = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=max(1, timeout_seconds), check=False,
        )
    except subprocess.TimeoutExpired:
        return None, _failed_report(plugin_id, _package_issue(
            "PLUGIN_WHEEL_BUILD_TIMEOUT",
            "Building the plugin wheel exceeded the release-gate budget.",
            "Make the wheel build bounded or raise the explicit build timeout.",
        ))
    if built.returncode != 0:
        lines = built.stdout.strip().splitlines()
        detail = " | ".join(lines[-8:]) if lines else "wheel builder returned no diagnostic output"
        return None, _failed_report(plugin_id, _package_issue(
            "PLUGIN_WHEEL_BUILD_FAILED",
            f"The plugin wheel could not be built: {detail}",
            "Fix package build metadata before running installation or lifecycle checks.",
        ))
    wheels = tuple(destination.glob("*.whl"))
    if len(wheels) != 1:
        return None, _failed_report(plugin_id, _package_issue(
            "PLUGIN_WHEEL_BUILD_AMBIGUOUS",
            f"The wheel build produced {len(wheels)} artifacts instead of exactly one.",
            "Make one source release produce exactly one wheel artifact.",
        ))
    return wheels[0], None


def inspect_plugin_release(
    package: str | Path, *, python: str = sys.executable,
    package_source: str | Path | None = None,
    build_timeout_seconds: int = 120,
    install_timeout_seconds: int = 120,
    fixture_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Build and exercise one exact wheel, stopping at the first failed stage."""
    candidate = Path(package).expanduser().resolve()
    supplied_wheel = candidate.suffix.lower() == ".whl"
    source = (
        Path(package_source).expanduser().resolve()
        if package_source is not None
        else (None if supplied_wheel else candidate)
    )
    if source is None:
        report = _failed_report(candidate.name, _package_issue(
            "PLUGIN_WHEEL_SOURCE_REQUIRED",
            "The complete gate needs the reviewed release source associated with this wheel.",
            "Pass --source PACKAGE_ROOT when validating a prebuilt wheel.",
        ))
        return {
            **report.as_dict(), "stages": [
                _stage("source_static", "failed", report),
                _stage("public_surface", "not_run"),
                _stage("wheel_build", "not_run"),
                _stage("installed_lifecycle", "not_run"),
            ],
        }

    static = inspect_plugin_package(source)
    if not static.passed:
        return {
            **static.as_dict(), "stages": [
                _stage("source_static", "failed", static),
                _stage("public_surface", "not_run"),
                _stage("wheel_build", "not_run"),
                _stage("installed_lifecycle", "not_run"),
            ],
        }

    descriptor = json.loads((source / "assayer-plugin-release.json").read_text(encoding="utf-8"))
    surface = inspect_plugin_surface(source / descriptor["runtimeSource"])
    if not surface.passed:
        return {
            **surface.as_dict(), "stages": [
                _stage("source_static", "passed"),
                _stage("public_surface", "failed", surface),
                _stage("wheel_build", "not_run"),
                _stage("installed_lifecycle", "not_run"),
            ],
        }

    with tempfile.TemporaryDirectory(prefix="assayer-plugin-release-check-") as directory:
        temporary = Path(directory)
        if supplied_wheel:
            wheel = candidate
            build_report = None
            build_stage_name = "wheel_artifact"
        else:
            wheel, build_report = _build_wheel(
                source, temporary / "dist", python=python,
                timeout_seconds=build_timeout_seconds,
            )
            build_stage_name = "wheel_build"
        if build_report is not None or wheel is None:
            failed = build_report or _failed_report(static.plugin_id, _package_issue(
                "PLUGIN_WHEEL_BUILD_FAILED", "No wheel artifact was produced.",
                "Fix the package wheel build before continuing.",
            ))
            return {
                **failed.as_dict(), "stages": [
                    _stage("source_static", "passed"),
                    _stage("public_surface", "passed"),
                    _stage(build_stage_name, "failed", failed),
                    _stage("installed_lifecycle", "not_run"),
                ],
            }
        if not wheel.is_file():
            missing = _failed_report(static.plugin_id, _package_issue(
                "PLUGIN_WHEEL_NOT_FOUND", "The declared wheel artifact does not exist.",
                "Build or provide the exact wheel before running the release gate.",
            ))
            return {
                **missing.as_dict(), "stages": [
                    _stage("source_static", "passed"),
                    _stage("public_surface", "passed"),
                    _stage(build_stage_name, "failed", missing),
                    _stage("installed_lifecycle", "not_run"),
                ],
            }
        artifact = {
            "filename": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }
        installed = inspect_plugin_installation(
            wheel, python=python, package_source=source,
            install_timeout_seconds=install_timeout_seconds,
            fixture_timeout_seconds=fixture_timeout_seconds,
        )
        return {
            **installed.as_dict(),
            "artifact": artifact,
            "stages": [
                _stage("source_static", "passed"),
                _stage("public_surface", "passed"),
                _stage(build_stage_name, "passed"),
                _stage(
                    "installed_lifecycle",
                    "passed" if installed.passed else "failed",
                    installed,
                ),
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static and isolated lifecycle gates for Assayer plugins")
    parser.add_argument("package", nargs="+", metavar="PACKAGE_ROOT")
    parser.add_argument("--source", default=None, help="Reviewed source root for one prebuilt wheel")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--build-timeout-seconds", type=int, default=120)
    parser.add_argument("--install-timeout-seconds", type=int, default=120)
    parser.add_argument("--fixture-timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.source and len(args.package) != 1:
        parser.error("--source can only be used with one prebuilt wheel")
    reports = [inspect_plugin_release(
        path, python=args.python, package_source=args.source,
        build_timeout_seconds=args.build_timeout_seconds,
        install_timeout_seconds=args.install_timeout_seconds,
        fixture_timeout_seconds=args.fixture_timeout_seconds,
    ) for path in args.package]
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(item["status"] == "passed" for item in reports) else "failed",
        "plugins": reports,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
