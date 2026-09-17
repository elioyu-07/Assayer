"""Clean-venv install matrix for platform, SDK, and Agent.

Ordinary plugins are compiled JSON artifacts, never Python distributions. The
matrix asserts that no ``assayer.plugins`` entry points are present, while
The default distributions ship no concrete Provider entry points.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_distributions import ROOT, build as build_split, build_root, root_wheel  # noqa: E402


THIRD_PARTY = ("jsonschema==4.26.0",)


@dataclass(frozen=True)
class Case:
    name: str
    packages: tuple[str, ...]
    ordinary_plugins: int
    providers: int
    platform: bool = True


CASES = (
    Case("platform-only", ("assayer-platform",), 0, 0),
    Case("platform+sdk", ("assayer-platform", "assayer-plugin-sdk"), 0, 0),
    Case(
        "all-split",
        (
            "assayer-platform", "assayer-plugin-sdk",
        ),
        0, 0,
    ),
    Case("root-meta", ("assayer",), 0, 0),
)


CHECK_SCRIPT = r"""
import importlib.util
import json
import sysconfig
from importlib import metadata


def _eps(group):
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=group))
    return list(discovered.get(group, ()))


plugins = _eps("assayer.plugins")
providers = _eps("assayer.providers")
origins = {}
for entry in plugins + providers:
    module = entry.value.split(":", 1)[0].split(".", 1)[0]
    spec = importlib.util.find_spec(module)
    origins[entry.name] = getattr(spec, "origin", None)

print(json.dumps({
    "ordinaryPlugins": [entry.name for entry in plugins],
    "providers": [entry.name for entry in providers],
    "origins": origins,
    "purelib": sysconfig.get_paths()["purelib"],
    "platform": importlib.util.find_spec("assayer_platform") is not None,
}))
"""


PLATFORM_TOP_LEVEL = frozenset({"assayer_platform", "assayer_host"})
SDK_OWNED_SCHEMAS = frozenset({
    "actionable-result.schema.json", "capability-provider.schema.json",
    "common.schema.json", "evaluation-corpus.schema.json",
    "evidence-claim.schema.json", "plugin-manifest.schema.json",
})


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def _assert_no_sdk_schema_leak(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        leaked = {
            Path(name).name for name in archive.namelist()
            if Path(name).name in SDK_OWNED_SCHEMAS
        }
    if leaked:
        raise SystemExit(f"{wheel.name} leaks SDK-owned schemas: {sorted(leaked)}")


def assert_root_wheel_is_platform_only(wheelhouse: Path) -> None:
    wheel = root_wheel(wheelhouse)
    with zipfile.ZipFile(wheel) as archive:
        top_level = {name.split("/", 1)[0] for name in archive.namelist()}
    modules = {
        name for name in top_level
        if "." not in name and not name.endswith((".dist-info", ".data"))
    }
    leaked = modules - PLATFORM_TOP_LEVEL
    if leaked:
        raise SystemExit(f"{wheel.name} leaks non-platform top-level modules: {sorted(leaked)}")
    _assert_no_sdk_schema_leak(wheel)


def assert_platform_wheel_owns_only_platform_schemas(wheelhouse: Path) -> None:
    matches = sorted(wheelhouse.glob("assayer_platform-*.whl"))
    if len(matches) != 1:
        raise SystemExit(f"expected one assayer-platform wheel in {wheelhouse}, found {matches}")
    _assert_no_sdk_schema_leak(matches[0])


def reset_wheelhouse(wheelhouse: Path) -> None:
    """Clear the wheelhouse so only this build's wheels can be installed.

    The wheelhouse is a persistent directory by default, so without this a
    retired or stale distribution can live there indefinitely and still be
    resolvable by ``--no-index --find-links``.
    """
    shutil.rmtree(wheelhouse, ignore_errors=True)
    wheelhouse.mkdir(parents=True, exist_ok=True)


def build_wheelhouse(wheelhouse: Path, *, python: str) -> None:
    reset_wheelhouse(wheelhouse)
    build_split(wheelhouse, python=python, isolated=False)
    build_root(wheelhouse, python=python, isolated=False, no_deps=True)
    assert_root_wheel_is_platform_only(wheelhouse)
    assert_platform_wheel_owns_only_platform_schemas(wheelhouse)
    _run([python, "-m", "pip", "download", "--dest", str(wheelhouse), *THIRD_PARTY])


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run_case(case: Case, wheelhouse: Path, work: Path) -> dict:
    venv_dir = work / case.name
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = _venv_python(venv_dir)
    _run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), *case.packages])
    result = subprocess.run([str(python), "-c", CHECK_SCRIPT], capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    _assert_case(case, observed)
    return observed


def _assert_case(case: Case, observed: dict) -> None:
    if len(observed["ordinaryPlugins"]) != case.ordinary_plugins:
        raise SystemExit(f"{case.name}: ordinary Python plugins were installed: {observed['ordinaryPlugins']}")
    if len(observed["providers"]) != case.providers:
        raise SystemExit(f"{case.name}: expected {case.providers} providers, observed {observed['providers']}")
    if bool(observed.get("platform", True)) != case.platform:
        raise SystemExit(f"{case.name}: expected platform package={case.platform}")
    purelib = observed.get("purelib") or ""
    for name, origin in observed["origins"].items():
        if not origin:
            raise SystemExit(f"{case.name}: entry point {name} did not resolve to a module")
        if purelib and not Path(origin).is_relative_to(purelib):
            raise SystemExit(f"{case.name}: entry point {name} resolves outside the venv: {origin}")


def run_matrix(wheelhouse: Path, *, keep: bool = False) -> list[dict]:
    results = []
    if keep:
        work = ROOT / "dist" / "install-matrix"
        work.mkdir(parents=True, exist_ok=True)
        for case in CASES:
            results.append({"case": case.name, **run_case(case, wheelhouse, work)})
        return results
    with tempfile.TemporaryDirectory(prefix="assayer-install-matrix-") as directory:
        work = Path(directory)
        for case in CASES:
            results.append({"case": case.name, **run_case(case, wheelhouse, work)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, default=ROOT / "dist" / "wheelhouse")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--keep", action="store_true", help="keep the per-case virtual environments")
    args = parser.parse_args()
    wheelhouse = args.wheelhouse.resolve()
    build_wheelhouse(wheelhouse, python=args.python)
    print(json.dumps(run_matrix(wheelhouse, keep=args.keep), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
