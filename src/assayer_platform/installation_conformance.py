"""Isolated installation and deterministic fixture gate for plugin releases."""

from __future__ import annotations

import argparse
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from .conformance import (
    RELEASE_DESCRIPTOR,
    PluginConformanceIssue,
    PluginConformanceReport,
    _package_issue,
    inspect_plugin_package,
    inspect_plugin_registration,
)
from .contract import (
    CheckContract,
    DecisionProposal,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
)
from .kernel import PlatformKernel
from .plugin_registry import PluginRegistration, PluginRegistry
from .registry import load_plugin_manifest
from .result_conformance import inspect_result_conformance


def _fixture_issue(code: str, message: str, next_action: str) -> PluginConformanceIssue:
    return PluginConformanceIssue(
        code, "PCV1-DETERMINISTIC-FIXTURE", message, next_action,
    )


class _PacketDecisionProvider:
    """Test-only provider that preserves packet states without business inference."""

    def decide(
        self,
        packets: Sequence[InvestigationPacket],
        check: CheckContract,
        context: PlatformContext,
    ) -> Sequence[DecisionProposal]:
        del context
        proposals = []
        for packet in packets:
            statuses = [dimension.candidate_status for dimension in packet.dimensions]
            if statuses and all(status == "satisfied" for status in statuses):
                result = "scanned_no_issue"
            elif "violated" in statuses:
                result = "issue_found"
            else:
                result = "needs_review"
            findings = tuple(Finding(
                dimension.name,
                dimension.candidate_status,
                dimension.observations[0] if dimension.observations else "No observation was supplied.",
            ) for dimension in packet.dimensions)
            proposals.append(DecisionProposal(
                packet.work_item.work_item_id,
                check.check_id,
                check.version,
                result,
                findings,
                "Deterministic conformance fixture preserved the plugin packet states.",
                {"authority": "conformance_fixture"},
            ))
        return tuple(proposals)


def _entry_point_registration(
    target: Path, registration_spec: str, plugin_version: str,
) -> tuple[PluginRegistration | None, list[PluginConformanceIssue]]:
    issues: list[PluginConformanceIssue] = []
    candidates = [
        entry
        for distribution in metadata.distributions(path=[str(target)])
        for entry in distribution.entry_points
        if entry.group == "assayer.plugins"
    ]
    if len(candidates) != 1 or candidates[0].value != registration_spec:
        issues.append(_package_issue(
            "PLUGIN_INSTALLED_ENTRY_POINT_INVALID",
            f"Expected one installed assayer.plugins entry point for {registration_spec}; found {len(candidates)} total.",
            "Publish exactly one matching entry point in the installed distribution.",
        ))
        return None, issues
    if candidates[0].dist is None or candidates[0].dist.version != plugin_version:
        issues.append(_package_issue(
            "PLUGIN_INSTALLED_VERSION_MISMATCH",
            "The installed distribution version differs from the validated plugin release.",
            "Publish one version consistently in package metadata, descriptor, manifest, and wheel metadata.",
        ))
        return None, issues

    module_name = registration_spec.partition(":")[0]
    spec = importlib.util.find_spec(module_name)
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    try:
        if origin is None:
            raise ValueError("module origin is unavailable")
        origin.relative_to(target)
    except ValueError:
        issues.append(_package_issue(
            "PLUGIN_INSTALLED_SOURCE_MISMATCH",
            "The installed registration module did not resolve from the isolated plugin target.",
            "Ensure the wheel installs the declared registration module into its own distribution.",
        ))
        return None, issues

    try:
        value: Any = candidates[0].load()
        if callable(value) and not isinstance(value, PluginRegistration):
            value = value()
        if isinstance(value, PluginRegistry):
            matches = [item for item in value.list()]
            if len(matches) != 1:
                raise TypeError("the release entry point registry must contain exactly one plugin")
            value = matches[0]
        if not isinstance(value, PluginRegistration):
            value = getattr(value, "registration", value)
        if not isinstance(value, PluginRegistration):
            raise TypeError("entry point did not provide PluginRegistration")
    except Exception as error:
        issues.append(_package_issue(
            "PLUGIN_INSTALLED_REGISTRATION_FAILED",
            f"The isolated registration could not be loaded: {error}",
            "Fix installed imports and return exactly one PluginRegistration.",
        ))
        return None, issues
    return value, issues


def _run_fixture(
    registration: PluginRegistration,
    fixture: Mapping[str, Any],
) -> list[PluginConformanceIssue]:
    fixture_id = fixture["fixtureId"]
    check_id = fixture["checkId"]
    scope = dict(fixture["scope"])
    try:
        Draft202012Validator(registration.scope_schema).validate(scope)
    except Exception as error:
        return [_fixture_issue(
            "PLUGIN_FIXTURE_SCOPE_INVALID",
            f"Fixture {fixture_id} does not satisfy the runtime scope schema: {error}",
            "Correct the fixture scope or the registered business-input schema.",
        )]
    try:
        plugin = registration.create_plugin(None)
        provider = (
            registration.create_decision_provider(None)
            if "batch" in registration.execution_modes
            else _PacketDecisionProvider()
        )
        result = PlatformKernel().run(
            plugin,
            scope,
            check_id,
            provider,
            PlatformContext(
                f"conformance:{fixture_id}", registration.capabilities,
            ),
        )
    except Exception as error:
        return [_fixture_issue(
            "PLUGIN_FIXTURE_EXECUTION_FAILED",
            f"Fixture {fixture_id} raised outside the platform result contract: {error}",
            "Make fixture execution return a deterministic platform result.",
        )]

    conformance = inspect_result_conformance(result)
    if not conformance.passed:
        first = conformance.issues[0]
        return [_fixture_issue(
            "PLUGIN_FIXTURE_RESULT_CONFORMANCE_FAILED",
            f"Fixture {fixture_id} violated {first.invariant}: {first.message}",
            first.next_action,
        )]

    expected = fixture["expected"]
    mismatches = []
    if result.status != expected["terminalStatus"]:
        mismatches.append(
            f"terminal status {result.status!r} != {expected['terminalStatus']!r}"
        )
    if result.status == "failed":
        actual = sorted(failure.code for failure in result.failures)
        wanted = sorted(expected.get("failureCodes", []))
    else:
        actual = sorted(decision.result for decision in result.decisions)
        wanted = sorted(expected.get("decisionResults", []))
    if actual != wanted:
        mismatches.append(f"outcomes {actual!r} != {wanted!r}")
    if mismatches:
        return [_fixture_issue(
            "PLUGIN_FIXTURE_EXPECTATION_MISMATCH",
            f"Fixture {fixture_id} did not match its declared result: {'; '.join(mismatches)}",
            "Correct the plugin behavior or update the fixture expectation through review.",
        )]
    return []


def _worker(target: Path, package_root: Path, result_path: Path) -> int:
    sys.path.insert(0, str(target))
    descriptor = json.loads((package_root / RELEASE_DESCRIPTOR).read_text(encoding="utf-8"))
    issues: list[PluginConformanceIssue] = []
    registration, load_issues = _entry_point_registration(
        target, descriptor["registration"], descriptor["pluginVersion"],
    )
    issues.extend(load_issues)
    if registration is not None:
        report = inspect_plugin_registration(
            registration, construct_implementations=True,
        )
        issues.extend(report.issues)
        manifest = load_plugin_manifest(package_root / descriptor["manifest"])
        scope_schema = json.loads(
            (package_root / descriptor["scopeSchema"]).read_text(encoding="utf-8")
        )
        if registration.manifest != manifest:
            issues.append(_package_issue(
                "PLUGIN_INSTALLED_MANIFEST_MISMATCH",
                "The installed registration manifest differs from the statically validated manifest.",
                "Build the runtime registration from the packaged manifest without hidden overrides.",
            ))
        if dict(registration.scope_schema) != scope_schema:
            issues.append(_package_issue(
                "PLUGIN_INSTALLED_SCOPE_SCHEMA_MISMATCH",
                "The installed registration scope schema differs from the packaged scope schema.",
                "Publish the same business-input schema in the descriptor and registration.",
            ))
        review_payload_path = descriptor.get("reviewPayloadSchema")
        if review_payload_path:
            review_payload_schema = json.loads(
                (package_root / review_payload_path).read_text(encoding="utf-8")
            )
            if dict(registration.review_payload_schema) != review_payload_schema:
                issues.append(_package_issue(
                    "PLUGIN_INSTALLED_REVIEW_PAYLOAD_SCHEMA_MISMATCH",
                    "The installed registration review payload schema differs from the packaged review payload schema.",
                    "Publish the same checkpoint payload schema in the descriptor and registration.",
                ))
        if not issues:
            for relative in descriptor["fixtures"]:
                fixture = json.loads((package_root / relative).read_text(encoding="utf-8"))
                issues.extend(_run_fixture(registration, fixture))

    payload = PluginConformanceReport(descriptor["pluginId"], tuple(issues)).as_dict()
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if not issues else 1


def _cleanup_build_artifacts(root: Path) -> None:
    """Remove transient artifacts a local non-isolated install leaves behind.

    ``pip install --no-build-isolation <package>`` builds the wheel in place,
    leaving ``build/`` and ``*.egg-info`` inside the package source.  The
    release gate must be side-effect-free, so remove them before returning.
    """
    build_dir = root / "build"
    if build_dir.is_dir():
        shutil.rmtree(build_dir, ignore_errors=True)
    for parent in (root, root / "src"):
        if not parent.is_dir():
            continue
        for entry in parent.glob("*.egg-info"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def inspect_plugin_installation(
    package_root: str | Path, *, python: str = sys.executable,
    install_timeout_seconds: int = 120,
    fixture_timeout_seconds: int = 300,
) -> PluginConformanceReport:
    """Install a statically valid package in a temporary target and run fixtures."""
    root = Path(package_root).expanduser().resolve()
    _cleanup_build_artifacts(root)
    static_report = inspect_plugin_package(root)
    if not static_report.passed:
        return static_report
    with tempfile.TemporaryDirectory(prefix="assayer-plugin-install-check-") as directory:
        temporary = Path(directory)
        target = temporary / "site"
        result_path = temporary / "result.json"
        isolated_env = os.environ.copy()
        isolated_env.pop("ASSAYER_RESOURCE_ROOT", None)
        isolated_env.pop("PYTHONPATH", None)
        # The worker is intentionally isolated from the caller's environment,
        # but it still needs the installed Assayer platform harness to load
        # ``assayer_platform.installation_conformance``.  Point it at the
        # platform distribution that is executing this gate (the plugin under
        # test remains first on sys.path inside the worker).
        isolated_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        isolated_env.update({
            "PYTHONNOUSERSITE": "1",
            "UV_CACHE_DIR": str(temporary / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
        })
        pip_probe = subprocess.run(
            [python, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if pip_probe.returncode == 0:
            command = [
                python, "-m", "pip", "install", "--disable-pip-version-check",
                "--isolated", "--no-input", "--no-deps", "--no-index",
                "--no-build-isolation", "--target", str(target), str(root),
            ]
        else:
            version_probe = subprocess.run(
                [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False,
            )
            python_version = version_probe.stdout.strip()
            external_pip = None
            for name in ("pip3", "pip"):
                candidate = shutil.which(name)
                if candidate is None:
                    continue
                probe = subprocess.run(
                    [candidate, "--version"], text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    check=False,
                )
                match = re.search(r"\(python ([0-9]+\.[0-9]+)\)", probe.stdout)
                if probe.returncode == 0 and match and match.group(1) == python_version:
                    external_pip = candidate
                    break
            if external_pip is not None:
                command = [
                    external_pip, "install", "--disable-pip-version-check",
                    "--isolated", "--no-input", "--no-deps", "--no-index",
                    "--no-build-isolation", "--target", str(target), str(root),
                ]
            else:
                uv = shutil.which("uv")
                if uv is not None:
                    command = [
                        uv, "pip", "install", "--python", python,
                        "--no-python-downloads", "--no-deps", "--no-index",
                        "--no-build-isolation", "--target", str(target), str(root),
                    ]
                else:
                    return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                        "PLUGIN_INSTALLER_UNAVAILABLE",
                        "No pip compatible with the selected Python and no uv installer are available.",
                        "Provide pip for the selected Python or install the supported uv package installer.",
                    ),))
        try:
            try:
                installed = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=isolated_env,
                    timeout=max(1, install_timeout_seconds),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                    "PLUGIN_ISOLATED_INSTALL_TIMEOUT",
                    "The local isolated package installation exceeded its release-gate budget.",
                    "Make package builds bounded or raise the explicit release validation budget.",
                ),))
            if installed.returncode != 0:
                detail = installed.stdout.strip().splitlines()
                tail = " | ".join(detail[-8:]) if detail else "installer returned no diagnostic output"
                return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                    "PLUGIN_ISOLATED_INSTALL_FAILED",
                    f"The plugin could not be installed into the isolated target: {tail}",
                    "Fix package build metadata and declared dependencies before publication.",
                ),))
        finally:
            _cleanup_build_artifacts(root)
        try:
            worker = subprocess.run(
                [
                    python, "-m", "assayer_platform.installation_conformance",
                    "--worker-target", str(target),
                    "--worker-package", str(root),
                    "--worker-result", str(result_path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=root,
                env=isolated_env,
                timeout=max(1, fixture_timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PluginConformanceReport(static_report.plugin_id, (_fixture_issue(
                "PLUGIN_FIXTURE_TIMEOUT",
                "The isolated fixture worker exceeded its release-gate budget.",
                "Make deterministic fixtures bounded or raise the explicit release validation budget.",
            ),))
        if not result_path.is_file():
            detail = worker.stdout.strip().splitlines()
            tail = detail[-1] if detail else "worker returned no diagnostic output"
            return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                "PLUGIN_ISOLATED_VALIDATION_FAILED",
                f"The isolated validation worker did not publish a result: {tail}",
                "Fix plugin import-time failures and retry the release gate.",
            ),))
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        try:
            from .conformance import _schema_validator

            _schema_validator("plugin-conformance.schema.json").validate({
                "schemaVersion": "1.0.0",
                "status": "passed" if not payload["issues"] else "failed",
                "plugins": [payload],
            })
        except Exception as error:
            return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                "PLUGIN_ISOLATED_RESULT_INVALID",
                f"The isolated validation result is malformed: {error}",
                "Repair the installation worker result contract before publication.",
            ),))
        if payload["pluginId"] != static_report.plugin_id:
            return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                "PLUGIN_ISOLATED_RESULT_INVALID",
                "The isolated validation result changed the statically verified plugin identity.",
                "Keep one plugin identity across static and isolated validation.",
            ),))
        expected_worker_status = 0 if not payload["issues"] else 1
        if worker.returncode != expected_worker_status:
            return PluginConformanceReport(static_report.plugin_id, (_package_issue(
                "PLUGIN_ISOLATED_RESULT_INVALID",
                "The isolated worker exit status disagrees with its conformance result.",
                "Repair the worker lifecycle so one durable result determines its exit status.",
            ),))
        issues = tuple(PluginConformanceIssue(
            item["code"], item["invariant"], item["message"], item["nextAction"],
        ) for item in payload["issues"])
        return PluginConformanceReport(payload["pluginId"], issues)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install and execute deterministic conformance fixtures for an Assayer plugin package",
    )
    parser.add_argument("package", nargs="*", metavar="PACKAGE_ROOT")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--install-timeout-seconds", type=int, default=120)
    parser.add_argument("--fixture-timeout-seconds", type=int, default=300)
    parser.add_argument("--worker-target", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-package", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_target:
        if not args.worker_package or not args.worker_result:
            parser.error("worker package and result paths are required")
        return _worker(
            args.worker_target.resolve(), args.worker_package.resolve(),
            args.worker_result.resolve(),
        )
    if not args.package:
        parser.error("at least one PACKAGE_ROOT is required")
    reports = [
        inspect_plugin_installation(
            path,
            python=args.python,
            install_timeout_seconds=args.install_timeout_seconds,
            fixture_timeout_seconds=args.fixture_timeout_seconds,
        )
        for path in args.package
    ]
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "plugins": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
