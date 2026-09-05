"""Isolated installation and deterministic fixture gate for provider releases."""

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
from typing import Any, Mapping

from .capability_negotiation import CapabilityNegotiator
from .contract import CapabilityProfile, CheckContract, WorkItem
from .provider_conformance import (
    ProviderConformanceIssue,
    ProviderConformanceReport,
    inspect_provider_registration,
)
from .provider_execution import BoundCapabilityProvider
from .provider_package_conformance import (
    PROVIDER_RELEASE_DESCRIPTOR,
    inspect_provider_package,
)
from .provider_registry import (
    ProviderRegistration,
    ProviderRegistry,
    load_provider_descriptor,
)


def _issue(
    code: str,
    message: str,
    next_action: str,
    *,
    invariant: str = "CPV1-ISOLATED-INSTALLATION",
) -> ProviderConformanceIssue:
    return ProviderConformanceIssue(code, invariant, message, next_action)


def _installed_registration(
    target: Path,
    registration_spec: str,
    provider_version: str,
) -> tuple[ProviderRegistration | None, list[ProviderConformanceIssue]]:
    candidates = [
        entry
        for distribution in metadata.distributions(path=[str(target)])
        for entry in distribution.entry_points
        if entry.group == "assayer.providers"
    ]
    if len(candidates) != 1 or candidates[0].value != registration_spec:
        return None, [_issue(
            "PROVIDER_INSTALLED_ENTRY_POINT_INVALID",
            "The isolated distribution does not expose exactly one matching assayer.providers entry point.",
            "Publish exactly one provider entry point matching the release descriptor.",
        )]
    if candidates[0].dist is None or candidates[0].dist.version != provider_version:
        return None, [_issue(
            "PROVIDER_INSTALLED_VERSION_MISMATCH",
            "The installed distribution version differs from the validated provider release.",
            "Publish one version consistently in package metadata and provider descriptors.",
        )]

    module_name = registration_spec.partition(":")[0]
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        spec = None
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    try:
        if origin is None:
            raise ValueError
        origin.relative_to(target)
    except ValueError:
        return None, [_issue(
            "PROVIDER_INSTALLED_SOURCE_MISMATCH",
            "The installed registration module did not resolve from the isolated provider target.",
            "Ensure the provider wheel installs its declared registration module.",
        )]

    try:
        value: Any = candidates[0].load()
        if callable(value) and not isinstance(value, ProviderRegistration):
            value = value()
        if isinstance(value, ProviderRegistry):
            values = value.list()
            if len(values) != 1:
                raise TypeError
            value = values[0]
        if not isinstance(value, ProviderRegistration):
            value = getattr(value, "registration", value)
        if not isinstance(value, ProviderRegistration):
            raise TypeError
    except Exception:
        return None, [_issue(
            "PROVIDER_INSTALLED_REGISTRATION_FAILED",
            "The isolated provider registration could not be loaded.",
            "Fix installed imports and return exactly one ProviderRegistration.",
        )]
    return value, []


def _run_fixture(
    registration: ProviderRegistration,
    fixture: Mapping[str, Any],
) -> list[ProviderConformanceIssue]:
    fixture_id = fixture["fixtureId"]
    capability = fixture["capability"]
    expected = fixture["expected"]
    check = CheckContract(
        "CPV-001",
        "1.0.0",
        ("provider_fixture",),
        ("fact_available",),
        ("scanned_no_issue", "needs_review"),
        tuple(expected.get("evidenceKinds", ())) or ("provider_failure",),
        (capability,),
        "blocked",
        ("source_digest",),
    )
    profile = CapabilityProfile(frozenset({capability}))
    try:
        negotiation = CapabilityNegotiator().negotiate(
            registration,
            (capability,),
            profile,
            user_profile=profile,
            scope=fixture["scope"],
        )
        bound = BoundCapabilityProvider(
            registration,
            negotiation,
            run_id=f"conformance:{fixture_id}",
            scope=fixture["scope"],
        )
        try:
            result = bound.collect(
                WorkItem(
                    f"fixture:{fixture_id}",
                    "provider_fixture",
                    fixture["sourceIdentity"],
                    fixture["stateDigest"],
                ),
                check,
                capability,
            )
        finally:
            bound.close()
    except Exception:
        return [_issue(
            "PROVIDER_FIXTURE_EXECUTION_FAILED",
            "A deterministic provider fixture failed outside the provider result contract.",
            "Make fixture execution return a bounded fact or classified failure.",
            invariant="CPV1-DETERMINISTIC-FIXTURE",
        )]

    if expected["status"] == "succeeded":
        actual_status = "succeeded" if result.evidence and result.failure is None else "failed"
        actual = sorted(set(item.kind for item in result.evidence))
        wanted = sorted(expected["evidenceKinds"])
        matches = (
            actual_status == "succeeded"
            and actual == wanted
            and len(result.evidence) == expected["evidenceCount"]
        )
    else:
        actual_status = "failed" if result.failure is not None else "succeeded"
        actual = result.failure.code if result.failure is not None else None
        wanted = expected["failureCode"]
        matches = actual_status == "failed" and actual == wanted
    if not matches:
        return [_issue(
            "PROVIDER_FIXTURE_EXPECTATION_MISMATCH",
            "A deterministic provider fixture did not match its declared outcome.",
            "Correct provider behavior or update the reviewed fixture expectation.",
            invariant="CPV1-DETERMINISTIC-FIXTURE",
        )]
    return []


def _worker(target: Path, package_root: Path, result_path: Path) -> int:
    sys.path.insert(0, str(target))
    release = json.loads(
        (package_root / PROVIDER_RELEASE_DESCRIPTOR).read_text(encoding="utf-8")
    )
    issues: list[ProviderConformanceIssue] = []
    registration, load_issues = _installed_registration(
        target,
        release["registration"],
        release["providerVersion"],
    )
    issues.extend(load_issues)
    if registration is not None:
        report = inspect_provider_registration(
            registration,
            construct_implementation=True,
        )
        issues.extend(report.issues)
        descriptor = load_provider_descriptor(
            package_root / release["providerDescriptor"]
        )
        if registration.descriptor != descriptor:
            issues.append(_issue(
                "PROVIDER_INSTALLED_DESCRIPTOR_MISMATCH",
                "The installed registration descriptor differs from the statically validated descriptor.",
                "Build the runtime registration from the packaged provider descriptor.",
            ))
        if not issues:
            for relative in release["fixtures"]:
                fixture = json.loads((package_root / relative).read_text(encoding="utf-8"))
                issues.extend(_run_fixture(registration, fixture))

    payload = ProviderConformanceReport(release["providerId"], tuple(issues)).as_dict()
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if not issues else 1


def _install_command(python: str, root: Path, target: Path) -> list[str] | None:
    pip_probe = subprocess.run(
        [python, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if pip_probe.returncode == 0:
        return [
            python, "-m", "pip", "install", "--disable-pip-version-check",
            "--isolated", "--no-input", "--no-deps", "--no-index",
            "--no-build-isolation", "--target", str(target), str(root),
        ]
    version_probe = subprocess.run(
        [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    python_version = version_probe.stdout.strip()
    for name in ("pip3", "pip"):
        candidate = shutil.which(name)
        if candidate is None:
            continue
        probe = subprocess.run(
            [candidate, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        match = re.search(r"\(python ([0-9]+\.[0-9]+)\)", probe.stdout)
        if probe.returncode == 0 and match and match.group(1) == python_version:
            return [
                candidate, "install", "--disable-pip-version-check", "--isolated",
                "--no-input", "--no-deps", "--no-index", "--no-build-isolation",
                "--target", str(target), str(root),
            ]
    uv = shutil.which("uv")
    if uv is not None:
        return [
            uv, "pip", "install", "--python", python, "--no-python-downloads",
            "--no-deps", "--no-index", "--no-build-isolation", "--target",
            str(target), str(root),
        ]
    return None


def inspect_provider_installation(
    package_root: str | Path,
    *,
    python: str = sys.executable,
    install_timeout_seconds: int = 120,
    fixture_timeout_seconds: int = 300,
) -> ProviderConformanceReport:
    """Install a validated provider package temporarily and execute its fixtures."""
    root = Path(package_root).expanduser().resolve()
    static_report = inspect_provider_package(root)
    if not static_report.passed:
        return static_report
    with tempfile.TemporaryDirectory(prefix="assayer-provider-install-check-") as directory:
        temporary = Path(directory)
        target = temporary / "site"
        result_path = temporary / "result.json"
        environment = os.environ.copy()
        environment.pop("ASSAYER_RESOURCE_ROOT", None)
        environment.pop("PYTHONPATH", None)
        # Keep the provider target isolated while making the Assayer platform
        # worker importable from the distribution executing this gate.
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        environment.update({
            "PYTHONNOUSERSITE": "1",
            "UV_CACHE_DIR": str(temporary / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
        })
        command = _install_command(python, root, target)
        if command is None:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_INSTALLER_UNAVAILABLE",
                "No supported local package installer is available for the selected Python.",
                "Provide compatible pip or the supported uv package installer.",
            ),))
        try:
            installed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=max(1, install_timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_INSTALL_TIMEOUT",
                "The local provider installation exceeded its explicit release-gate budget.",
                "Make package builds bounded or raise the release validation budget.",
            ),))
        if installed.returncode != 0:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_INSTALL_FAILED",
                "The provider package could not be installed into the isolated target.",
                "Fix local package build metadata before publication.",
            ),))
        try:
            worker = subprocess.run(
                [
                    python, "-m", "assayer_platform.provider_installation_conformance",
                    "--worker-target", str(target),
                    "--worker-package", str(root),
                    "--worker-result", str(result_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=root,
                env=environment,
                timeout=max(1, fixture_timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_FIXTURE_TIMEOUT",
                "The provider fixture worker exceeded its explicit release-gate budget.",
                "Make deterministic fixtures bounded or raise the validation budget.",
                invariant="CPV1-DETERMINISTIC-FIXTURE",
            ),))
        if not result_path.is_file():
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_VALIDATION_FAILED",
                "The isolated provider worker did not publish a conformance result.",
                "Fix provider import or worker failures and retry the release gate.",
            ),))
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            from .conformance import _schema_validator
            _schema_validator("provider-conformance.schema.json").validate({
                "schemaVersion": "1.0.0",
                "status": "passed" if not payload["issues"] else "failed",
                "providers": [payload],
            })
        except Exception:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_RESULT_INVALID",
                "The isolated provider conformance result is malformed.",
                "Repair the installation worker result contract before publication.",
            ),))
        if payload["providerId"] != static_report.provider_id:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_RESULT_INVALID",
                "The isolated result changed the statically verified provider identity.",
                "Keep one provider identity across static and isolated validation.",
            ),))
        expected_status = 0 if not payload["issues"] else 1
        if worker.returncode != expected_status:
            return ProviderConformanceReport(static_report.provider_id, (_issue(
                "PROVIDER_ISOLATED_RESULT_INVALID",
                "The worker exit status disagrees with its conformance result.",
                "Repair the worker lifecycle before publication.",
            ),))
        return ProviderConformanceReport(
            payload["providerId"],
            tuple(ProviderConformanceIssue(
                item["code"], item["invariant"], item["message"], item["nextAction"],
            ) for item in payload["issues"]),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install and execute deterministic fixtures for an Assayer provider package",
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
            args.worker_target.resolve(),
            args.worker_package.resolve(),
            args.worker_result.resolve(),
        )
    if not args.package:
        parser.error("at least one PACKAGE_ROOT is required")
    reports = [
        inspect_provider_installation(
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
        "providers": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["inspect_provider_installation", "main"]
