"""Build the split Assayer distributions and keep the tree clean.

The platform distribution split ships five independent wheels. Ordinary
plugins are data-only contracts and are not Python distributions. Building with
setuptools stages metadata into ``src/*.egg-info`` and ``packages/*/build``;
left behind, the egg-info directories pollute
``importlib.metadata.entry_points()`` and produce duplicate ``assayer.*``
entry points.  This helper builds every distribution and then removes those
staging artifacts so a development checkout stays matchable to a clean venv.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Matches ``assayer-0.1.2-...whl`` but not ``assayer-plugin-sdk-...`` or
# ``assayer_plugin_sdk-...``.
_ROOT_WHEEL = re.compile(r"^assayer-\d")

# Order is informational only: ``--no-deps`` makes each build independent.
DISTRIBUTIONS = (
    "assayer-plugin-sdk",
    "assayer-platform",
)

def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def assert_built_wheels(
    output: Path, distributions: tuple[str, ...] = DISTRIBUTIONS
) -> list[Path]:
    """Assert the build output holds exactly the requested distributions.

    Every caller shares this guard, so an install/acceptance path cannot skip
    it. Matching is by normalized distribution name, not by wheel count alone,
    so a stale or retired ``assayer_*`` wheel sitting in a reused output
    directory is rejected instead of silently surviving.
    """
    wheels = sorted(output.glob("*.whl"))
    requested = {_normalized(name) for name in distributions}
    observed: dict[str, list[Path]] = {}
    for wheel in wheels:
        observed.setdefault(_normalized(wheel.name.split("-", 1)[0]), []).append(wheel)
    missing = sorted(requested - observed.keys())
    if missing:
        raise SystemExit(f"missing wheels for requested distributions: {missing}")
    duplicated = {
        name: [wheel.name for wheel in matches]
        for name, matches in observed.items()
        if name in requested and len(matches) != 1
    }
    if duplicated:
        raise SystemExit(f"expected exactly one wheel per requested distribution: {duplicated}")
    # ``assayer`` is the root meta wheel, built separately by ``build_root``.
    unexpected = sorted(
        name for name in observed
        if name not in requested and name.startswith("assayer") and name != "assayer"
    )
    if unexpected:
        raise SystemExit(f"unexpected assayer wheels in build output: {unexpected}")
    return [observed[_normalized(name)][0] for name in distributions]


def clean_staging(root: Path = ROOT) -> None:
    """Remove only known setuptools staging paths below an explicit root."""
    root = root.resolve()
    for egg_info in (root / "src").glob("*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)
    for egg_info in (root / "packages").glob("*/src/*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)
    for build in (root / "packages").glob("*/build"):
        shutil.rmtree(build, ignore_errors=True)
    # The root ``build/lib`` is reused across builds; left behind it can leak
    # stale modules into the platform-only root wheel.
    shutil.rmtree(root / "build", ignore_errors=True)


def build(
    output: Path, *, python: str, isolated: bool,
    distributions: tuple[str, ...] = DISTRIBUTIONS,
) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        python, "-m", "pip", "wheel",
        "--no-deps",
        "--wheel-dir", str(output),
    ]
    if not isolated:
        command.append("--no-build-isolation")
    built: list[Path] = []
    try:
        for distribution in distributions:
            subprocess.run(command + [str(ROOT / "packages" / distribution)], cwd=ROOT, check=True)
        built = assert_built_wheels(output, distributions)
    finally:
        clean_staging()
    return built


def root_wheel(output: Path) -> Path:
    """Return the single root ``assayer`` wheel in ``output``."""
    matches = [wheel for wheel in output.glob("assayer-*.whl") if _ROOT_WHEEL.match(wheel.name)]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one root assayer wheel in {output}, found {matches}")
    return matches[0]


def build_root(
    output: Path, *, python: str, extras: str | None = None,
    find_links: Path | None = None, isolated: bool = True, no_deps: bool = False,
) -> Path:
    """Build the root ``assayer`` wheel with explicit staging cleanup.

    Setuptools reuses ``build/lib`` across runs, so a stale meta-package build
    leaks plugin/provider modules into the platform-only root wheel.  Clean
    before and after so every caller shares the same guard instead of relying on
    a neighbouring build having cleaned up.
    """
    requirement = f".[{extras}]" if extras else "."
    command = [python, "-m", "pip", "wheel", requirement, "--wheel-dir", str(output)]
    if no_deps:
        command.append("--no-deps")
    if not isolated:
        command.append("--no-build-isolation")
    if find_links is not None:
        command += ["--find-links", str(find_links)]
    clean_staging()
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    finally:
        clean_staging()
    return root_wheel(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "split")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--isolated", action="store_true",
        help="use build isolation (needs network); default reuses the current environment",
    )
    args = parser.parse_args()
    wheels = build(args.output.resolve(), python=args.python, isolated=args.isolated)
    for wheel in wheels:
        print(wheel.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
