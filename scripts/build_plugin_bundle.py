"""Build a self-contained Assayer Codex Plugin release directory and zip."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugins" / "assayer"


def _validate_plugin_releases() -> None:
    source_root = str(ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from assayer_platform import installed_plugin_registry
    from assayer_platform.conformance import inspect_plugin_registrations

    registrations = installed_plugin_registry().list()
    if not registrations:
        raise SystemExit(
            "no ``assayer.plugins`` entry points are installed; install the split "
            "plugin distributions (packages/assayer-plugin-frontend-audit) before building"
        )
    reports = inspect_plugin_registrations(
        registrations, construct_implementations=True,
    )
    failed = [report.as_dict() for report in reports if not report.passed]
    if failed:
        raise SystemExit(
            "installed plugin conformance failed: "
            + json.dumps(failed, sort_keys=True)
        )


def _versions(path: Path) -> tuple[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    manifest = json.loads((path / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    package_version = project["version"]
    plugin_version = manifest["version"]
    # Codex development installs append a cachebuster (for example,
    # ``0.1.0+codex.local``).  It must not change the Python package version
    # used for wheel selection or isolated installation checks.
    if package_version != plugin_version.split("+", 1)[0]:
        raise SystemExit(
            f"package/plugin version mismatch: {package_version} != {plugin_version}"
        )
    return package_version, plugin_version


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def _build_wheelhouse(wheel_dir: Path, *, python: str) -> None:
    """Populate the bundle wheelhouse with the split runtime dependencies.

    The root ``assayer`` wheel is platform-only and depends on the separately
    built SDK; the plugins and providers ship as their own wheels.  Build the
    three split wheels first, then resolve the root wheel (plus Playwright and
    the MCP SDK) against them so no unpublished distribution is fetched.
    """
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from build_distributions import build as build_split

    build_split(
        wheel_dir, python=python, isolated=False,
        distributions=(
            "assayer-plugin-sdk",
            "assayer-plugin-frontend-audit",
            "assayer-provider-markdown",
        ),
    )
    try:
        _run([
            python, "-m", "pip", "wheel", ".[browser,mcp]",
            "--find-links", str(wheel_dir),
            "--wheel-dir", str(wheel_dir),
        ])
    finally:
        for egg_info in (ROOT / "src").glob("*.egg-info"):
            shutil.rmtree(egg_info, ignore_errors=True)
        shutil.rmtree(ROOT / "build", ignore_errors=True)


def build(output: Path, *, python: str) -> tuple[Path, Path]:
    _validate_plugin_releases()
    package_version, plugin_version = _versions(PLUGIN_SOURCE)
    release_root = output / f"assayer-plugin-{plugin_version}"
    archive = output / f"assayer-plugin-{plugin_version}.zip"
    if release_root.exists():
        shutil.rmtree(release_root)
    if archive.exists():
        archive.unlink()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN_SOURCE, release_root)

    wheel_dir = release_root / "runtime" / "wheels"
    shutil.rmtree(wheel_dir)
    wheel_dir.mkdir(parents=True)
    _build_wheelhouse(wheel_dir, python=python)

    wheels = []
    for wheel in sorted(wheel_dir.glob("*.whl")):
        wheels.append({"file": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
    if not any(item["file"].startswith(f"assayer-{package_version}-") for item in wheels):
        raise SystemExit("wheelhouse does not contain the matching Assayer wheel")
    bundle_manifest = {
        "schemaVersion": "1.0",
        "pluginVersion": plugin_version,
        "runtime": {
            "implementation": platform.python_implementation(),
            "pythonVersion": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sysconfig.get_platform(),
        },
        "wheels": wheels,
    }
    (release_root / "runtime" / "bundle-manifest.json").write_text(
        json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with tempfile.TemporaryDirectory(prefix="assayer-bundle-check-") as tmp:
        venv = Path(tmp) / "venv"
        _run([python, "-m", "venv", str(venv)])
        venv_python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run([
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheel_dir),
            f"assayer[browser,mcp]=={package_version}",
            "assayer-plugin-frontend-audit",
            "assayer-provider-markdown",
        ])
        _run([
            str(venv_python),
            "-c",
            "from importlib import metadata; "
            "plugins=[e.name for e in metadata.entry_points().select(group='assayer.plugins')]; "
            "providers=[e.name for e in metadata.entry_points().select(group='assayer.providers')]; "
            "assert plugins==['assayer.frontend-audit'], plugins; "
            "assert providers==['markdown'], providers; "
            "from assayer_platform import installed_plugin_registry, installed_provider_registry; "
            "installed_plugin_registry().select(plugin_id='assayer.frontend-audit'); "
            "installed_provider_registry().select(capability='document_navigation'); "
            "from assayer_host.core import HostCore; from assayer_host.transport import McpToolTransport; "
            "h=HostCore(); assert len(McpToolTransport(h).list_tools()) == 17",
        ])
        # Exercise the exact product launcher from a clean private cache. MCP
        # receives EOF immediately, so this validates bundle metadata,
        # integrity, offline runtime creation, and stdio startup without
        # opening a browser or running an audit.
        launcher_env = os.environ.copy()
        launcher_env["XDG_CACHE_HOME"] = str(Path(tmp) / "launcher-cache")
        subprocess.run(
            [str(release_root / "scripts" / "launch_assayer_mcp")],
            cwd=release_root,
            env=launcher_env,
            input=b"",
            timeout=60,
            check=True,
        )

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(release_root.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(release_root.name) / path.relative_to(release_root))
    return release_root, archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    release_root, archive = build(args.output.resolve(), python=args.python)
    print(json.dumps({"pluginDir": str(release_root), "archive": str(archive)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
