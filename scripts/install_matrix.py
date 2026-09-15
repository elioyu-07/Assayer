"""Clean-venv install matrix for the split Assayer distributions.

Phase 2 of the A-plan proves that the six independent wheels install in
isolation without duplicating modules or ``assayer.*`` entry points.  For each
combination the harness builds a fresh virtual environment, installs only from
a local wheelhouse, and asserts:

* the expected ``assayer.plugins`` / ``assayer.providers`` entry points appear;
* each entry-point module resolves inside the venv's site-packages;
* the platform registry loads the expected plugins, or fails closed with
  ``PLUGIN_CONFLICT`` when two distributions export the same plugin id.

The wheelhouse bundles every third-party dependency, so the matrix itself runs
offline; only populating the wheelhouse needs network.

Boundary: this matrix proves installation, entry-point ownership, and registry
loading.  Executing a Check end to end belongs to the A-plan phase-3 bundle
acceptance, not here.

Phase 3 flipped the root ``assayer`` wheel to platform-only, so ``root-meta``
now installs ``0/0`` and the old ``root + split`` overlap is gone.  The
duplicate-entry-point negative case is preserved with a synthetic shadow
distribution that re-exports the frontend plugin id, keeping
``PLUGIN_CONFLICT`` coverage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_distributions import (  # noqa: E402
    ROOT,
    build as build_split,
    build_root,
    root_wheel,
)


THIRD_PARTY = ("jsonschema==4.26.0",)

# Synthetic distribution used only by the duplicate-entry-point negative case.
# It carries metadata and an ``assayer.plugins`` entry point that re-exports the
# real frontend plugin, so two installed distributions claim the same plugin id.
DUPLICATE_PLUGIN_DIST = "assayer-plugin-frontend-audit-shadow"


@dataclass(frozen=True)
class Case:
    name: str
    packages: tuple[str, ...]
    plugins: int
    providers: int
    conflict: str | None = None
    agent: bool = False
    platform: bool = True


CASES = (
    Case("platform-only", ("assayer-platform",), 0, 0),
    Case("platform+sdk", ("assayer-platform", "assayer-plugin-sdk"), 0, 0),
    Case("agent-only", ("assayer-agent",), 0, 0, agent=True, platform=False),
    Case(
        "platform+sdk+plugin",
        ("assayer-platform", "assayer-plugin-sdk", "assayer-plugin-frontend-audit"),
        1, 0,
    ),
    Case(
        "all-split",
        (
            "assayer-platform", "assayer-plugin-sdk",
            "assayer-agent",
            "assayer-plugin-frontend-audit", "assayer-provider-markdown",
            "assayer-provider-browser",
        ),
        1, 2, agent=True,
    ),
    Case("root-meta", ("assayer",), 0, 0),
    Case(
        "duplicate-plugin-conflict",
        (
            "assayer-platform", "assayer-plugin-sdk",
            "assayer-plugin-frontend-audit", DUPLICATE_PLUGIN_DIST,
        ),
        2, 0, conflict="PLUGIN_CONFLICT",
    ),
)


CHECK_SCRIPT = r"""
import importlib.util
import json
import sysconfig
from importlib import metadata
from assayer_plugin_sdk.resources import schema_root as sdk_schema_root


def _eps(group):
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=group))
    return list(discovered.get(group, ()))


plugins = _eps("assayer.plugins")
providers = _eps("assayer.providers")

expected_sdk_schemas = {
    "actionable-result.schema.json",
    "capability-provider.schema.json",
    "common.schema.json",
    "evaluation-corpus.schema.json",
    "evidence-claim.schema.json",
    "plugin-manifest.schema.json",
}
installed_sdk_schemas = {path.name for path in sdk_schema_root().glob("*.schema.json")}
if installed_sdk_schemas != expected_sdk_schemas:
    raise SystemExit(
        f"SDK schema ownership mismatch: expected {sorted(expected_sdk_schemas)}, "
        f"found {sorted(installed_sdk_schemas)}"
    )

origins = {}
for entry in plugins + providers:
    module = entry.value.split(":", 1)[0].split(".", 1)[0]
    spec = importlib.util.find_spec(module)
    origins[entry.name] = getattr(spec, "origin", None)

conflict = None
plugin_count = None
agent = importlib.util.find_spec("assayer_agent") is not None
platform = importlib.util.find_spec("assayer_platform") is not None
if platform:
    try:
        from assayer_platform import installed_plugin_registry
        from assayer_platform.registry import schema_store
        platform_schemas = schema_store()
        for required in (
            "common.schema.json",
            "plugin-manifest.schema.json",
            "platform-ledger.schema.json",
            "canonical-result.schema.json",
        ):
            if required not in platform_schemas:
                raise RuntimeError(f"combined platform schema store is missing {required}")
        plugin_count = len(installed_plugin_registry().list())
    except Exception as error:
        conflict = getattr(error, "code", error.__class__.__name__)
else:
    plugin_count = 0

print(json.dumps({
    "plugins": [entry.name for entry in plugins],
    "providers": [entry.name for entry in providers],
    "origins": origins,
    "purelib": sysconfig.get_paths()["purelib"],
    "plugin_count": plugin_count,
    "conflict": conflict,
    "agent": agent,
    "platform": platform,
}))
"""


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_duplicate_plugin_wheel(wheelhouse: Path) -> Path:
    """Write a metadata-only wheel that re-exports an installed plugin id.

    Combined with the real frontend-audit wheel, two ``assayer.plugins`` entry
    points resolve to the same plugin id, which the platform registry must
    reject with ``PLUGIN_CONFLICT``.
    """
    module = "assayer_plugin_frontend_audit_shadow"
    version = "0.0.0"
    info = f"{module}-{version}.dist-info"
    files = {
        f"{info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {DUPLICATE_PLUGIN_DIST}\n"
            f"Version: {version}\n"
            "Requires-Python: >=3.11\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: assayer-install-matrix\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        f"{info}/entry_points.txt": (
            "[assayer.plugins]\n"
            "assayer.frontend-audit-shadow = assayer_frontend_audit:registration\n"
        ),
    }
    files[f"{info}/RECORD"] = "".join(f"{path},,\n" for path in (*files, f"{info}/RECORD"))
    wheel = wheelhouse / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return wheel


PLATFORM_TOP_LEVEL = frozenset({"assayer_platform", "assayer_host"})
SDK_OWNED_SCHEMAS = frozenset({
    "actionable-result.schema.json",
    "capability-provider.schema.json",
    "common.schema.json",
    "evaluation-corpus.schema.json",
    "evidence-claim.schema.json",
    "plugin-manifest.schema.json",
})


def _assert_no_sdk_schema_leak(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        leaked = {
            Path(name).name for name in archive.namelist()
            if Path(name).name in SDK_OWNED_SCHEMAS
        }
    if leaked:
        raise SystemExit(
            f"{wheel.name} leaks SDK-owned schemas: {sorted(leaked)}"
        )


def assert_root_wheel_is_platform_only(wheelhouse: Path) -> None:
    """Guard the platform-only root wheel against stale ``build/lib`` leakage.

    The install matrix builds the real root wheel, so this is the release-gate
    check that a stale meta-package build cannot silently ship plugin/provider
    modules inside the platform-only ``assayer`` distribution.
    """
    wheel = root_wheel(wheelhouse)
    with zipfile.ZipFile(wheel) as archive:
        top_level = {name.split("/", 1)[0] for name in archive.namelist()}
    modules = {
        name for name in top_level
        if "." not in name and not name.endswith((".dist-info", ".data"))
    }
    leaked = modules - PLATFORM_TOP_LEVEL
    if leaked:
        raise SystemExit(
            f"{wheel.name} leaks non-platform top-level modules: {sorted(leaked)}"
        )
    _assert_no_sdk_schema_leak(wheel)


def assert_platform_wheel_owns_only_platform_schemas(wheelhouse: Path) -> None:
    matches = sorted(wheelhouse.glob("assayer_platform-*.whl"))
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one assayer-platform wheel in {wheelhouse}, found {matches}"
        )
    _assert_no_sdk_schema_leak(matches[0])


def build_wheelhouse(wheelhouse: Path, *, python: str) -> None:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build_split(wheelhouse, python=python, isolated=False)
    build_root(wheelhouse, python=python, isolated=False, no_deps=True)
    assert_root_wheel_is_platform_only(wheelhouse)
    assert_platform_wheel_owns_only_platform_schemas(wheelhouse)
    _run([python, "-m", "pip", "download", "--dest", str(wheelhouse), *THIRD_PARTY])
    build_duplicate_plugin_wheel(wheelhouse)


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
    if bool(observed.get("agent")) != case.agent:
        raise SystemExit(
            f"{case.name}: expected agent package={case.agent}, observed {observed.get('agent')}"
        )
    if bool(observed.get("platform", True)) != case.platform:
        raise SystemExit(
            f"{case.name}: expected platform package={case.platform}, observed {observed.get('platform')}"
        )
    purelib = observed.get("purelib") or ""
    for name, origin in observed["origins"].items():
        if not origin:
            raise SystemExit(f"{case.name}: entry point {name} did not resolve to a module")
        if purelib and not Path(origin).is_relative_to(purelib):
            raise SystemExit(
                f"{case.name}: entry point {name} resolves outside the venv site-packages: {origin}"
            )


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
