"""Build the split Assayer distributions and keep the tree clean.

The A-plan distribution split ships four independent wheels. Building with
setuptools stages metadata into ``src/*.egg-info`` and ``packages/*/build``;
left behind, the egg-info directories pollute
``importlib.metadata.entry_points()`` and produce duplicate ``assayer.*``
entry points.  This helper builds every distribution and then removes those
staging artifacts so a development checkout stays matchable to a clean venv.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Order is informational only: ``--no-deps`` makes each build independent.
DISTRIBUTIONS = (
    "assayer-plugin-sdk",
    "assayer-plugin-frontend-audit",
    "assayer-provider-markdown",
    "assayer-platform",
)


def _clean_staging() -> None:
    for egg_info in (ROOT / "src").glob("*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)
    for build in (ROOT / "packages").glob("*/build"):
        shutil.rmtree(build, ignore_errors=True)


def build(output: Path, *, python: str, isolated: bool) -> list[Path]:
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
        for distribution in DISTRIBUTIONS:
            subprocess.run(command + [str(ROOT / "packages" / distribution)], cwd=ROOT, check=True)
        built = sorted(output.glob("*.whl"))
    finally:
        _clean_staging()
    return built


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
    if len(wheels) != len(DISTRIBUTIONS):
        raise SystemExit(f"expected {len(DISTRIBUTIONS)} wheels, found {len(wheels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
