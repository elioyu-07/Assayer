"""Clean-venv install matrix for the split Assayer distributions.

Phase 2 of the A-plan proves that the four independent wheels install in
isolation without duplicating modules or ``assayer.*`` entry points.  For each
combination the harness builds a fresh virtual environment, installs only from
a local wheelhouse, and asserts:

* the expected ``assayer.plugins`` / ``assayer.providers`` entry points appear;
* each entry-point module resolves from exactly one location;
* the platform registry loads the expected plugins, or fails closed with
  ``PLUGIN_CONFLICT`` when a distribution overlaps another (the root meta wheel
  beside the split frontend plugin).

The wheelhouse bundles every third-party dependency, so the matrix itself runs
offline; only populating the wheelhouse needs network.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_distributions import ROOT, build as build_split  # noqa: E402


THIRD_PARTY = ("jsonschema==4.26.0",)


@dataclass(frozen=True)
class Case:
    name: str
    packages: tuple[str, ...]
    plugins: int
    providers: int
    conflict: str | None = None


CASES = (
    Case("platform-only", ("assayer-platform",), 0, 0),
    Case("platform+sdk", ("assayer-platform", "assayer-plugin-sdk"), 0, 0),
    Case(
        "platform+sdk+plugin",
        ("assayer-platform", "assayer-plugin-sdk", "assayer-plugin-frontend-audit"),
        1, 0,
    ),
    Case(
        "all-split",
        (
            "assayer-platform", "assayer-plugin-sdk",
            "assayer-plugin-frontend-audit", "assayer-provider-markdown",
        ),
        1, 1,
    ),
    Case("root-meta", ("assayer",), 1, 1),
    Case(
        "root+split-conflict",
        ("assayer", "assayer-plugin-frontend-audit"),
        2, 1, conflict="PLUGIN_CONFLICT",
    ),
)


CHECK_SCRIPT = r"""
import importlib.util
import json
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

conflict = None
plugin_count = None
try:
    from assayer_platform import installed_plugin_registry
    plugin_count = len(installed_plugin_registry().list())
except Exception as error:
    conflict = getattr(error, "code", error.__class__.__name__)

print(json.dumps({
    "plugins": [entry.name for entry in plugins],
    "providers": [entry.name for entry in providers],
    "origins": origins,
    "plugin_count": plugin_count,
    "conflict": conflict,
}))
"""


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_wheelhouse(wheelhouse: Path, *, python: str) -> None:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build_split(wheelhouse, python=python, isolated=False)
    try:
        _run([python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
              "--wheel-dir", str(wheelhouse), str(ROOT)])
    finally:
        for egg_info in (ROOT / "src").glob("*.egg-info"):
            shutil.rmtree(egg_info, ignore_errors=True)
    _run([python, "-m", "pip", "download", "--dest", str(wheelhouse), *THIRD_PARTY])


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run_case(case: Case, wheelhouse: Path, work: Path) -> dict:
    venv_dir = work / case.name
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = _venv_python(venv_dir)
    _run([str(python), "-m", "pip", "install", "--no-index",
          "--find-links", str(wheelhouse), *case.packages])
    result = subprocess.run(
        [str(python), "-c", CHECK_SCRIPT],
        capture_output=True, text=True, check=True,
    )
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    _assert_case(case, observed)
    return observed


def _assert_case(case: Case, observed: dict) -> None:
    if len(observed["plugins"]) != case.plugins:
        raise SystemExit(
            f"{case.name}: expected {case.plugins} plugin entry points, "
            f"observed {observed['plugins']}"
        )
    if len(observed["providers"]) != case.providers:
        raise SystemExit(
            f"{case.name}: expected {case.providers} provider entry points, "
            f"observed {observed['providers']}"
        )
    if case.conflict is None:
        if observed["conflict"] is not None:
            raise SystemExit(f"{case.name}: unexpected registry failure {observed['conflict']}")
        if observed["plugin_count"] != case.plugins:
            raise SystemExit(
                f"{case.name}: registry loaded {observed['plugin_count']} plugins, "
                f"expected {case.plugins}"
            )
    else:
        if observed["conflict"] != case.conflict:
            raise SystemExit(
                f"{case.name}: expected {case.conflict}, observed {observed['conflict']}"
            )
    origins = [origin for origin in observed["origins"].values() if origin]
    if len(origins) != len(set(origins)):
        raise SystemExit(f"{case.name}: entry-point modules resolve from overlapping origins")


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
    results = run_matrix(wheelhouse, keep=args.keep)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
