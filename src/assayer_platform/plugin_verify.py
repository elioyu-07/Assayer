"""One deterministic verification pipeline for ordinary plugins."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .conformance import inspect_plugin_package
from .contract import PlatformContractError
from .plugin_packaging import assemble_self_contained_wheel, sha256_hex
from .release_conformance import inspect_plugin_release
from .simple_plugin_compiler import compile_simple_plugin
from .plugin_source import stage_plugin_source


def _failure(code: str, message: str, stages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0", "status": "failed",
        "error": {"code": code, "message": message}, "stages": stages,
    }


def _uv_wheel(
    source: Path, destination: Path, *, timeout_seconds: int,
) -> tuple[Path | None, dict[str, str] | None]:
    uv = shutil.which("uv")
    if uv is None:
        return None, {
            "code": "PLUGIN_WHEEL_BUILDER_UNAVAILABLE",
            "message": "uv is required for isolated plugin wheel builds",
        }
    destination.mkdir(parents=True, exist_ok=True)
    try:
        built = subprocess.run(
            [
                uv, "build", "--wheel", "--out-dir", str(destination),
                str(source),
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=max(1, timeout_seconds), check=False,
        )
    except subprocess.TimeoutExpired:
        return None, {
            "code": "PLUGIN_WHEEL_BUILD_TIMEOUT",
            "message": "Isolated wheel build exceeded its budget",
        }
    wheels = tuple(destination.glob("*.whl"))
    if built.returncode != 0 or len(wheels) != 1:
        detail = " | ".join(built.stdout.strip().splitlines()[-8:])
        return None, {
            "code": "PLUGIN_WHEEL_BUILD_FAILED",
            "message": detail or f"Isolated build produced {len(wheels)} wheels",
        }
    return wheels[0], None


def verify_advanced_plugin(
    source: str | Path, *, output_dir: str | Path,
    python: str = sys.executable, build_timeout_seconds: int = 120,
    install_timeout_seconds: int = 120, fixture_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Build and verify an Advanced-SPI source without building in its repository."""
    source_root = Path(source).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    stages: list[dict[str, str]] = []
    if not source_root.is_dir():
        return _failure(
            "PLUGIN_SOURCE_UNREACHABLE",
            f"The plugin source is unreachable or does not exist: {source_root}",
            [{"name": "source_static", "status": "failed"}],
        )

    # Keep the staging root beside the source so explicitly declared relative
    # local build sources retain their meaning, while never invoking the build
    # backend inside the author's repository.
    try:
        build_parent = source_root.parent
        temporary_context = tempfile.TemporaryDirectory(
            prefix=f".{source_root.name}-assayer-build-", dir=build_parent,
        )
    except OSError:
        temporary_context = tempfile.TemporaryDirectory(
            prefix="assayer-plugin-build-",
        )
    with temporary_context as directory:
        staged_source = stage_plugin_source(source_root, Path(directory))
        static = inspect_plugin_package(staged_source)
        if not static.passed:
            first = static.issues[0]
            return _failure(
                first.code, first.message,
                [{"name": "source_static", "status": "failed"}],
            )
        stages.append({"name": "source_static", "status": "passed"})
        descriptor = json.loads(
            (staged_source / "assayer-plugin-release.json").read_text(encoding="utf-8"),
        )
        wheel, build_error = _uv_wheel(
            staged_source, Path(directory) / "wheel-build",
            timeout_seconds=build_timeout_seconds,
        )
        if build_error is not None or wheel is None:
            issue = build_error or {
                "code": "PLUGIN_WHEEL_BUILD_FAILED",
                "message": "No wheel artifact was produced",
            }
            return _failure(
                issue["code"], issue["message"],
                [*stages, {"name": "isolated_wheel_build", "status": "failed"}],
            )
        exact_bytes = assemble_self_contained_wheel(
            wheel.read_bytes(), staged_source, descriptor,
        )
        exact_wheel = Path(directory) / "exact" / wheel.name
        exact_wheel.parent.mkdir()
        exact_wheel.write_bytes(exact_bytes)
        stages.append({"name": "isolated_wheel_build", "status": "passed"})
        release = inspect_plugin_release(
            exact_wheel,
            python=python,
            package_source=staged_source,
            install_timeout_seconds=install_timeout_seconds,
            fixture_timeout_seconds=fixture_timeout_seconds,
        )
        if release["status"] != "passed":
            issues = release.get("issues", [])
            issue = issues[0] if issues else {
                "code": "PLUGIN_VERIFY_FAILED",
                "message": "Release verification failed",
            }
            return {
                **_failure(
                    str(issue.get("code", "PLUGIN_VERIFY_FAILED")),
                    str(issue.get("message", "Release verification failed")),
                    [*stages, {"name": "installed_lifecycle", "status": "failed"}],
                ),
                "release": release,
            }
        stages.append({"name": "installed_lifecycle", "status": "passed"})
        destination.mkdir(parents=True, exist_ok=True)
        published = destination / wheel.name
        published.write_bytes(exact_bytes)
        return {
            "schemaVersion": "1.0.0",
            "status": "passed",
            "pluginId": descriptor["pluginId"],
            "pluginVersion": descriptor["pluginVersion"],
            "wheel": str(published),
            "sha256": sha256_hex(exact_bytes),
            "stages": stages,
            "release": release,
        }


def verify_plugin_source(
    source: str | Path, *, output_dir: str | Path,
    python: str = sys.executable, build_timeout_seconds: int = 120,
    install_timeout_seconds: int = 120, fixture_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Verify either an ordinary declaration or an Advanced-SPI source tree."""
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        return _failure(
            "PLUGIN_SOURCE_UNREACHABLE",
            f"The plugin source is unreachable or does not exist: {root}",
            [{"name": "source_static", "status": "failed"}],
        )
    options = {
        "output_dir": output_dir,
        "python": python,
        "build_timeout_seconds": build_timeout_seconds,
        "install_timeout_seconds": install_timeout_seconds,
        "fixture_timeout_seconds": fixture_timeout_seconds,
    }
    if (root / "assayer-plugin-release.json").is_file():
        return verify_advanced_plugin(root, **options)
    if (root / "plugin.yaml").is_file():
        return verify_simple_plugin(root, **options)
    return _failure(
        "PLUGIN_PACKAGE_NOT_FOUND",
        "The plugin source contains neither plugin.yaml nor assayer-plugin-release.json.",
        [{"name": "source_static", "status": "failed"}],
    )


def verify_simple_plugin(
    source: str | Path, *, output_dir: str | Path,
    python: str = sys.executable, build_timeout_seconds: int = 120,
    install_timeout_seconds: int = 120, fixture_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Compile, build, install, and exercise the same exact wheel artifact."""
    source_root = Path(source).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    stages: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="assayer-plugin-verify-") as directory:
        temporary = Path(directory)
        generated = temporary / "generated"
        try:
            compiled = compile_simple_plugin(source_root, generated)
        except PlatformContractError as error:
            return _failure(error.code, error.message, [{"name": "compile", "status": "failed"}])
        stages.append({"name": "compile", "status": "passed"})
        static = inspect_plugin_package(generated)
        if not static.passed:
            first = static.issues[0]
            return _failure(
                first.code, first.message,
                [*stages, {"name": "generated_contracts", "status": "failed"}],
            )
        stages.append({"name": "generated_contracts", "status": "passed"})
        wheel_root = temporary / "wheel"
        wheel_root.mkdir()
        wheel, build_error = _uv_wheel(
            generated, wheel_root, timeout_seconds=build_timeout_seconds,
        )
        if build_error is not None or wheel is None:
            issue = build_error or {
                "code": "PLUGIN_WHEEL_BUILD_FAILED",
                "message": "No wheel artifact was produced",
            }
            return _failure(
                issue["code"], issue["message"],
                [*stages, {"name": "isolated_wheel_build", "status": "failed"}],
            )
        descriptor = json.loads(
            (generated / "assayer-plugin-release.json").read_text(encoding="utf-8"),
        )
        exact_bytes = assemble_self_contained_wheel(
            wheel.read_bytes(), generated, descriptor,
        )
        exact_root = temporary / "exact"
        exact_root.mkdir()
        exact_wheel = exact_root / wheel.name
        exact_wheel.write_bytes(exact_bytes)
        stages.append({"name": "isolated_wheel_build", "status": "passed"})
        release = inspect_plugin_release(
            exact_wheel,
            python=python,
            package_source=generated,
            install_timeout_seconds=install_timeout_seconds,
            fixture_timeout_seconds=fixture_timeout_seconds,
        )
        if release["status"] != "passed":
            issues = release.get("issues", [])
            issue = issues[0] if issues else {
                "code": "PLUGIN_VERIFY_FAILED", "message": "Release verification failed",
            }
            return {
                **_failure(
                    str(issue.get("code", "PLUGIN_VERIFY_FAILED")),
                    str(issue.get("message", "Release verification failed")),
                    [*stages, {"name": "installed_lifecycle", "status": "failed"}],
                ),
                "release": release,
            }
        stages.append({"name": "installed_lifecycle", "status": "passed"})
        destination.mkdir(parents=True, exist_ok=True)
        published = destination / wheel.name
        published.write_bytes(exact_bytes)
        return {
            "schemaVersion": "1.0.0", "status": "passed",
            "pluginId": compiled.plugin_id, "pluginVersion": compiled.version,
            "wheel": str(published), "sha256": sha256_hex(exact_bytes),
            "stages": stages,
            "acceptance": {
                "isolatedInstall": True, "resume": True,
                "pagination": True, "replay": True,
            },
        }


__all__ = [
    "verify_advanced_plugin",
    "verify_plugin_source",
    "verify_simple_plugin",
]
