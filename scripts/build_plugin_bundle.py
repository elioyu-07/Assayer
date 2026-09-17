"""Build a self-contained Assayer Codex Plugin release directory and zip."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
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


def _compiled_first_party_plugins() -> tuple[dict[str, object], ...]:
    """Compile every first-party declaration plugin into a verified artifact."""
    source_root = str(ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from assayer_platform.compiled_plugin_contract import load_compiled_plugin_contract
    from assayer_platform.declaration_compiler import compile_plugin_contract

    plugins_root = ROOT / "plugins"
    sources = tuple(
        path for path in sorted(plugins_root.iterdir())
        if path.is_dir() and (path / "plugin.yaml").is_file()
    )
    compiled: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for source in sources:
        if re.fullmatch(r"[A-Za-z0-9._-]+", source.name) is None:
            raise SystemExit(f"invalid first-party plugin directory name: {source.name}")
        with tempfile.TemporaryDirectory(prefix=f"assayer-{source.name}-gate-") as directory:
            generated = Path(directory) / "generated"
            compile_plugin_contract(source, generated)
            contract = load_compiled_plugin_contract(generated)
            if contract.plugin_id in seen_ids:
                raise SystemExit(f"duplicate first-party plugin identity: {contract.plugin_id}")
            seen_ids.add(contract.plugin_id)
            payload = (generated / "compiled-plugin.json").read_bytes()
        compiled.append({
            "pluginId": contract.plugin_id,
            "pluginVersion": contract.version,
            "file": f"{source.name}.compiled-plugin.json",
            "payload": payload,
        })
    return tuple(compiled)


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
    built SDK. Build the SDK and Agent first, then resolve the platform-only
    root wheel (plus Playwright and the MCP SDK). Concrete Providers are not
    shipped; ordinary plugins are bundled separately as data-only contracts.
    """
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from build_distributions import build as build_split, build_root

    build_split(
        wheel_dir, python=python, isolated=False,
        distributions=(
            "assayer-plugin-sdk",
            "assayer-agent",
        ),
    )
    build_root(
        wheel_dir, python=python, extras="mcp", find_links=wheel_dir,
    )


def build(output: Path, *, python: str) -> tuple[Path, Path]:
    compiled_plugins = _compiled_first_party_plugins()
    package_version, plugin_version = _versions(PLUGIN_SOURCE)
    release_root = output / f"assayer-plugin-{plugin_version}"
    archive = output / f"assayer-plugin-{plugin_version}.zip"
    if release_root.exists():
        shutil.rmtree(release_root)
    if archive.exists():
        archive.unlink()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN_SOURCE, release_root)

    plugin_contract_dir = release_root / "runtime" / "plugins"
    plugin_contract_dir.mkdir(parents=True, exist_ok=True)
    for item in compiled_plugins:
        (plugin_contract_dir / str(item["file"])).write_bytes(item["payload"])

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
        "pluginContracts": [
            {
                "pluginId": item["pluginId"],
                "pluginVersion": item["pluginVersion"],
                "file": item["file"],
                "sha256": hashlib.sha256(item["payload"]).hexdigest(),
            }
            for item in compiled_plugins
        ],
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
            f"assayer[mcp]=={package_version}",
        ])
        _run([
            str(venv_python),
            "-c",
            "from importlib import metadata; "
            "plugins=[e.name for e in metadata.entry_points().select(group='assayer.plugins')]; "
            "providers=[e.name for e in metadata.entry_points().select(group='assayer.providers')]; "
            "assert plugins==[], plugins; "
            "assert providers==[], providers; "
            "from assayer_platform import installed_provider_registry; "
            "assert installed_provider_registry().list()==(), installed_provider_registry().list(); "
            "from assayer_host.transport import create_compiled_mcp_server; "
            "assert callable(create_compiled_mcp_server)",
        ])
        # Exercise explicit runtime preparation followed by the lightweight
        # product launcher from a clean private cache. MCP receives EOF
        # immediately, so this validates both phases without running an audit.
        launcher_env = os.environ.copy()
        launcher_env["XDG_CACHE_HOME"] = str(Path(tmp) / "launcher-cache")
        subprocess.run(
            [str(release_root / "scripts" / "prepare_assayer_runtime")],
            cwd=release_root,
            env=launcher_env,
            timeout=60,
            check=True,
        )
        prepared_runtime = (
            Path(launcher_env["XDG_CACHE_HOME"])
            / "assayer"
            / f"runtime-{plugin_version}"
            / "venv"
        )
        _run([
            str(prepared_runtime / "bin" / "python"),
            "-c",
            "from importlib import metadata; "
            "providers={e.name for e in metadata.entry_points().select(group='assayer.providers')}; "
            "assert providers == set(), providers",
        ])
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
