#!/usr/bin/env python3
"""Run one compiled end-to-end acceptance inside a clean, isolated venv.

The acceptance baseline requires that a clean environment installs the split
distributions from a wheelhouse with ``--no-index`` and then runs one compiled
Run end to end, producing and verifying one artifact.

This script owns that run. It builds the wheelhouse, creates an isolated venv,
installs the split distributions plus the generic markdown capability provider,
and drives one compiled Run through the Host transport using only the installed
packages. The driver reports every module origin, and the caller rejects any
origin outside the venv, so an editable or stale local install cannot satisfy
the acceptance.

The Run is driven with a non-business fixture (``tests/fixtures/plugins/
policy-pack``): a declarative markdown plugin with no Python. The verified
artifact is the terminal compiled result, checked for its content digest, its
terminal status, and its five-state review atoms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_distributions import build as build_distributions  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "plugins" / "policy-pack"

# The compiled runtime needs the platform, the SDK it depends on, the Agent
# distribution, and one capability provider. Ordinary plugins are compiled JSON
# artifacts, so they are never Python distributions here.
DISTRIBUTIONS = (
    "assayer-platform",
    "assayer-plugin-sdk",
    "assayer-agent",
    "assayer-provider-markdown",
)
THIRD_PARTY = ("jsonschema==4.26.0",)
MODULES = ("assayer_platform", "assayer_host", "assayer_document_navigation")
PLUGIN_ID = "test.policy-pack"
CHECK_ID = "POLICY-001"
FIVE_STATE_DECISIONS = (
    "satisfied", "violated", "not_applicable", "unknown", "blocked",
)
TERMINAL_RUN_STATUSES = ("completed", "partial", "failed")


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def distribution_versions() -> dict[str, str | None]:
    """Report the installed version of every distribution the Run needs.

    A development checkout imports from the source tree without installing the
    split distributions, so a missing distribution is reported as ``None`` and
    the caller rejects it instead of the driver inventing a version.
    """
    import importlib.metadata as metadata

    versions: dict[str, str | None] = {}
    for name in DISTRIBUTIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def fixture_digest(fixture: Path) -> str:
    """Digest the declared fixture exactly as read, for line-by-line review."""
    digest = hashlib.sha256()
    for path in sorted(item for item in fixture.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(fixture)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _validate_schema(name: str, payload: Any) -> None:
    from jsonschema import Draft202012Validator, RefResolver

    from assayer_platform.registry import schema_store

    schemas = schema_store()
    schema = schemas[name]
    validator = Draft202012Validator(
        schema, resolver=RefResolver(schema["$id"], schema, store=schemas),
    )
    error = next(validator.iter_errors(payload), None)
    if error is not None:
        location = "/".join(str(item) for item in error.absolute_path)
        raise SystemExit(f"{name} rejected the artifact at {location}: {error.message}")


def run_driver(fixture: Path, work: Path, *, provider_registry: Any = None) -> dict[str, Any]:
    """Drive one compiled Run with installed packages only, and verify it.

    Runs inside the isolated venv (``--driver``). Prints nothing else, so the
    caller can read the evidence object from stdout. The capability provider is
    discovered from installed entry points unless a caller injects one, which
    keeps the acceptance itself on the installed surface.
    """
    import importlib.metadata as metadata
    import importlib.util

    from assayer_host.transport import CompiledPlatformMcpToolTransport
    from assayer_platform import (
        CapabilityProfile,
        CompiledPluginLifecycleManager,
    )
    from assayer_platform.declaration_compiler import compile_plugin_contract
    from assayer_platform.incremental_review import JsonCoverageLedgerStore
    from assayer_platform.plugin_installation import PluginInstallationStore
    from assayer_platform.provider_registry import ProviderRegistry

    work.mkdir(parents=True, exist_ok=True)
    artifact_dir = work / "artifact"
    compile_plugin_contract(fixture, artifact_dir)
    contract_path = artifact_dir / "compiled-plugin.json"
    _validate_schema("compiled-plugin-contract.schema.json", json.loads(contract_path.read_text(encoding="utf-8")))

    store_root = work / "store"
    runs_root = work / "runs"
    document = work / "input.md"
    document.write_text(
        "# Delivery notes\n\nThe document contains implementation details but no introductory summary.\n",
        encoding="utf-8",
    )
    CompiledPluginLifecycleManager(PluginInstallationStore(store_root)).install(contract_path)

    transport = CompiledPlatformMcpToolTransport(
        runs_root,
        store_root=store_root,
        # Installed entry points must supply the provider: nothing from the
        # checkout is on the path, so this also proves the shipped entry point.
        provider_registry=provider_registry if provider_registry is not None else ProviderRegistry.from_entry_points(),
        platform_profile=CapabilityProfile(
            frozenset({"document_navigation"}), limits={"maxItems": 1},
        ),
    )
    trace: list[str] = []

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        trace.append(name)
        response = transport.call_tool(name, arguments)
        if response.get("isError"):
            raise SystemExit(f"{name} failed: {json.dumps(response, ensure_ascii=False, sort_keys=True)}")
        payload = response["structuredContent"]["result"]
        if payload.get("status") in {"failed", "blocked"}:
            raise SystemExit(f"{name} returned {payload['status']}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")
        return payload

    try:
        installed = call("list_compiled_plugins", {})
        if PLUGIN_ID not in {item.get("pluginId") for item in installed.get("plugins", ())}:
            raise SystemExit(f"the installed compiled plugin {PLUGIN_ID} is not listed: {installed}")

        started = call("start_compiled_run", {
            "pluginId": PLUGIN_ID, "checkId": CHECK_ID,
            "scope": {"files": [str(document)]},
        })
        run_id = started["runId"]
        call("bind_provider", {"runId": run_id})
        discovered = call("discover_sources", {"runId": run_id})
        collected = call("collect_evidence", {"runId": run_id})
        plan = call("plan_review_batches", {"runId": run_id, "maxBatchItems": 1})
        atoms = {atom["atomId"]: atom for atom in plan["coverage"]["atoms"]}
        for batch in plan["coverage"]["batches"]:
            call("submit_review_batch", {
                "runId": run_id, "batchId": batch["batchId"],
                "decisions": [{
                    "atomId": atom_id,
                    "state": "satisfied",
                    "rationale": "The frozen source element has no equivalent overview.",
                    "evidenceRefs": atoms[atom_id]["payload"]["evidenceRefs"],
                } for atom_id in batch["atomIds"]],
            })
        finalized = call("finalize_compiled_run", {"runId": run_id})
        restored = call("get_compiled_result", {"runId": run_id})
    finally:
        transport.close()

    if restored != finalized:
        raise SystemExit("the persisted compiled result differs from the finalized result")

    ledger = JsonCoverageLedgerStore(runs_root / "coverage").load(run_id)
    if ledger is None or ledger.terminal_status is None:
        raise SystemExit("the compiled Run has no terminal coverage ledger")
    coverage_digest = _digest_bytes(ledger.canonical_bytes())
    reported_digest = restored.get("trace", {}).get("coverageDigest")
    if reported_digest != coverage_digest:
        raise SystemExit(
            f"the reported coverage digest {reported_digest} does not match the ledger {coverage_digest}"
        )
    verdicts = [
        {"atomId": decision.get("atomId"), "state": decision.get("state")}
        for verdict in ledger.verdicts for decision in verdict.decisions
    ]
    outside = sorted({item["state"] for item in verdicts} - set(FIVE_STATE_DECISIONS))
    if outside:
        raise SystemExit(f"review atoms reported decisions outside the five-state vocabulary: {outside}")
    if ledger.terminal_status not in TERMINAL_RUN_STATUSES:
        raise SystemExit(f"terminal status {ledger.terminal_status!r} is not a compiled Run status")

    artifact_path = work / "verified-artifact.json"
    artifact_path.write_text(json.dumps({
        "schemaVersion": "1.0.0",
        "kind": "compiled-run-result",
        "runId": run_id,
        "status": ledger.terminal_status,
        "pluginId": PLUGIN_ID,
        "checkId": CHECK_ID,
        "coverageDigest": coverage_digest,
        "verdicts": verdicts,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifact_digest = _digest_bytes(artifact_path.read_bytes())

    origins: dict[str, str | None] = {}
    for module in MODULES:
        spec = importlib.util.find_spec(module)
        origins[module] = getattr(spec, "origin", None)
    return {
        "schemaVersion": "1.0.0",
        "acceptance": "compiled-end-to-end-clean-venv",
        "status": "passed",
        "python": sys.version.split()[0],
        "prefix": sys.prefix,
        "sysPath": [str(item) for item in sys.path],
        "moduleOrigins": origins,
        "providerRegistry": "injected" if provider_registry is not None else "entry-points",
        "distributions": distribution_versions(),
        "schemaRoot": str(Path(sys.prefix) / "share" / "assayer" / "schemas"),
        "fixtureDigest": fixture_digest(fixture),
        "runId": run_id,
        "runStatus": ledger.terminal_status,
        "atoms": len(ledger.atoms),
        "batches": len(ledger.batches),
        "workItems": len(discovered["workItems"]),
        "collections": len(collected["collections"]),
        "verdicts": verdicts,
        "coverageDigest": coverage_digest,
        "artifactPath": str(artifact_path),
        "artifactDigest": artifact_digest,
        "trace": trace,
        "finalizedStatus": finalized.get("status"),
    }


def verify_evidence(evidence: dict[str, Any], *, venv_dir: Path, repo_root: Path) -> None:
    """Reject evidence that did not come from the clean venv or does not verify."""
    if evidence.get("status") != "passed":
        raise SystemExit(f"the driver did not pass: {evidence.get('status')!r}")
    for name, version in sorted(evidence["distributions"].items()):
        if not version:
            raise SystemExit(f"{name} was not installed in the acceptance venv")
    purelib = (venv_dir / "lib").glob("python*/site-packages")
    roots = [str(item.resolve()) for item in purelib]
    for module, origin in sorted(evidence["moduleOrigins"].items()):
        if not origin:
            raise SystemExit(f"{module} did not resolve inside the venv")
        if not any(Path(origin).resolve().is_relative_to(root) for root in roots):
            raise SystemExit(f"{module} resolved outside the venv: {origin}")
    for entry in evidence["sysPath"]:
        resolved = str(Path(entry).resolve()) if entry else entry
        for source in [repo_root / "src", *(repo_root / "packages").glob("*/src")]:
            if resolved == str(source):
                raise SystemExit(f"the driver imported from the checkout: {resolved}")
    if evidence["runStatus"] not in TERMINAL_RUN_STATUSES:
        raise SystemExit(f"unexpected compiled Run status: {evidence['runStatus']!r}")
    if not evidence["verdicts"]:
        raise SystemExit("the compiled Run reported no review verdicts")
    if not Path(evidence["artifactPath"]).is_file():
        raise SystemExit(f"the verified artifact is missing: {evidence['artifactPath']}")
    if _digest_bytes(Path(evidence["artifactPath"]).read_bytes()) != evidence["artifactDigest"]:
        raise SystemExit("the verified artifact digest does not match its content")


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_wheelhouse(wheelhouse: Path, *, python: str) -> None:
    """Build only this acceptance's distributions into a cleared wheelhouse."""
    shutil.rmtree(wheelhouse, ignore_errors=True)
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build_distributions(wheelhouse, python=python, isolated=False, distributions=DISTRIBUTIONS)
    _run([python, "-m", "pip", "download", "--dest", str(wheelhouse), *THIRD_PARTY])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--wheel-python", default=sys.executable)
    parser.add_argument(
        "--driver", action="store_true",
        help="run only the in-venv driver and print its evidence as JSON",
    )
    args = parser.parse_args()
    fixture = args.fixture.resolve()

    if args.driver:
        work = (args.workdir or Path(tempfile.mkdtemp(prefix="assayer-acceptance-run-"))).resolve()
        print(json.dumps(run_driver(fixture, work), ensure_ascii=False, sort_keys=True))
        return 0

    with tempfile.TemporaryDirectory(prefix="assayer-compiled-acceptance-") as directory:
        root = Path(directory)
        wheelhouse = (args.wheelhouse or root / "wheelhouse").resolve()
        venv_dir = root / "venv"
        build_wheelhouse(wheelhouse, python=args.wheel_python)
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = _venv_python(venv_dir)
        _run([
            str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse),
            *DISTRIBUTIONS,
        ])
        result = subprocess.run(
            [
                str(python), str(Path(__file__).resolve()), "--driver",
                "--fixture", str(fixture), "--workdir", str(root / "run"),
            ],
            capture_output=True, text=True, cwd=root,
        )
        if result.returncode != 0:
            raise SystemExit(result.stderr.strip() or result.stdout.strip() or "the driver failed")
        if not result.stdout.strip():
            raise SystemExit("the driver produced no evidence")
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        verify_evidence(evidence, venv_dir=venv_dir, repo_root=ROOT)
        if args.evidence is not None:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
