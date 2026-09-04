"""Static, import-free release checks for capability-provider packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib
from typing import Any

from jsonschema import Draft202012Validator

from .conformance import _schema_validator
from .contract import PlatformContractError
from .provider_conformance import (
    ProviderConformanceIssue,
    ProviderConformanceReport,
    inspect_provider_registration,
)
from .provider_registry import ProviderRegistration, load_provider_descriptor


PROVIDER_RELEASE_DESCRIPTOR = "assayer-provider-release.json"


def _issue(
    code: str, message: str, next_action: str,
    *, invariant: str = "CPV1-PACKAGE-RESOURCES",
) -> ProviderConformanceIssue:
    return ProviderConformanceIssue(code, invariant, message, next_action)


def _declared_path(
    root: Path,
    relative: str,
    *,
    kind: str,
    issues: list[ProviderConformanceIssue],
) -> Path | None:
    candidate = root / relative
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        issues.append(_issue(
            "PROVIDER_PACKAGE_PATH_UNSAFE",
            f"The declared {kind} path escapes the provider package.",
            "Use a normalized relative path contained inside the provider package.",
        ))
        return None
    current = candidate
    while current != root:
        if current.is_symlink():
            issues.append(_issue(
                "PROVIDER_PACKAGE_PATH_UNSAFE",
                f"The declared {kind} path traverses a symbolic link.",
                "Package regular files and directories instead of symbolic links.",
            ))
            return None
        current = current.parent
    return resolved


def _read_json(
    path: Path | None,
    *,
    kind: str,
    issues: list[ProviderConformanceIssue],
) -> Any | None:
    if path is None:
        return None
    if not path.is_file():
        issues.append(_issue(
            "PROVIDER_PACKAGE_RESOURCE_MISSING",
            f"The declared {kind} file does not exist.",
            f"Add the declared {kind} file to the provider release package.",
        ))
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        issues.append(_issue(
            "PROVIDER_PACKAGE_RESOURCE_INVALID",
            f"The declared {kind} file is not valid UTF-8 JSON.",
            f"Correct the declared {kind} JSON before packaging.",
        ))
        return None


def inspect_provider_package(package_root: str | Path) -> ProviderConformanceReport:
    """Validate an independent provider package without importing its code."""
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        return ProviderConformanceReport(root.name or "unknown", (_issue(
            "PROVIDER_PACKAGE_NOT_FOUND",
            "The provider package root does not exist or is not a directory.",
            "Provide the directory containing assayer-provider-release.json.",
        ),))

    issues: list[ProviderConformanceIssue] = []
    release = _read_json(
        root / PROVIDER_RELEASE_DESCRIPTOR,
        kind="release descriptor",
        issues=issues,
    )
    if not isinstance(release, dict):
        if release is not None:
            issues.append(_issue(
                "PROVIDER_RELEASE_DESCRIPTOR_INVALID",
                "The provider release descriptor must be a JSON object.",
                "Correct assayer-provider-release.json to match provider-release.schema.json.",
            ))
        return ProviderConformanceReport(root.name, tuple(issues))

    schema_errors = sorted(
        _schema_validator("provider-release.schema.json").iter_errors(release),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    for error in schema_errors:
        location = "/".join(str(part) for part in error.absolute_path) or "root"
        issues.append(_issue(
            "PROVIDER_RELEASE_DESCRIPTOR_INVALID",
            f"The provider release descriptor is invalid at {location}: {error.message}",
            "Correct assayer-provider-release.json to match provider-release.schema.json.",
        ))
    provider_id = release.get("providerId")
    if schema_errors or not isinstance(provider_id, str):
        return ProviderConformanceReport(provider_id or root.name, tuple(issues))

    paths = {
        name: _declared_path(root, release[name], kind=name, issues=issues)
        for name in ("providerDescriptor", "runtimeSource", "packageMetadata")
    }
    fixture_paths = tuple(
        _declared_path(root, relative, kind="fixture", issues=issues)
        for relative in release["fixtures"]
    )

    descriptor_value = _read_json(
        paths["providerDescriptor"], kind="provider descriptor", issues=issues,
    )
    descriptor = None
    if isinstance(descriptor_value, dict):
        try:
            descriptor = load_provider_descriptor(descriptor_value)
        except PlatformContractError as error:
            issues.append(_issue(
                error.code,
                "The packaged provider descriptor is invalid.",
                "Correct the provider descriptor before packaging.",
            ))
        else:
            descriptor_report = inspect_provider_registration(ProviderRegistration(
                descriptor,
                provider_factory=lambda runtime=None: None,
            ))
            issues.extend(descriptor_report.issues)
            expected = (
                release["providerId"],
                release["providerVersion"],
                release["platformApiVersion"],
            )
            actual = (
                descriptor.provider_id,
                descriptor.version,
                descriptor.platform_api_version,
            )
            if actual != expected:
                issues.append(_issue(
                    "PROVIDER_PACKAGE_IDENTITY_MISMATCH",
                    "The release and provider descriptors use different identity or versions.",
                    "Use one provider ID, provider version, and platform API version throughout the package.",
                ))
    elif descriptor_value is not None:
        issues.append(_issue(
            "INVALID_PROVIDER_DESCRIPTOR",
            "The packaged provider descriptor must be a JSON object.",
            "Correct the provider descriptor before packaging.",
        ))

    runtime_root = paths["runtimeSource"]
    module_name, _, _attribute = release["registration"].partition(":")
    if runtime_root is None or not runtime_root.is_dir():
        issues.append(_issue(
            "PROVIDER_RUNTIME_SOURCE_MISSING",
            "The declared provider runtime source directory is missing.",
            "Package the Python source directory containing the provider registration module.",
        ))
    else:
        module_path = runtime_root.joinpath(*module_name.split("."))
        if not module_path.with_suffix(".py").is_file() and not (module_path / "__init__.py").is_file():
            issues.append(_issue(
                "PROVIDER_REGISTRATION_SOURCE_MISSING",
                "The declared provider registration module is absent from runtimeSource.",
                "Package the module named by the provider registration entry point.",
            ))

    fixture_validator = _schema_validator("provider-fixture.schema.json")
    fixture_ids: set[str] = set()
    fixture_statuses: set[str] = set()
    successful_capabilities: set[str] = set()
    capabilities = {
        capability.name: set(capability.evidence_kinds)
        for capability in descriptor.capabilities
    } if descriptor is not None else {}
    for fixture_path in fixture_paths:
        fixture = _read_json(fixture_path, kind="fixture", issues=issues)
        if not isinstance(fixture, dict):
            if fixture is not None:
                issues.append(_issue(
                    "PROVIDER_FIXTURE_INVALID",
                    "A provider fixture must be a JSON object.",
                    "Correct every fixture to match provider-fixture.schema.json.",
                ))
            continue
        errors = list(fixture_validator.iter_errors(fixture))
        if errors:
            issues.append(_issue(
                "PROVIDER_FIXTURE_INVALID",
                f"A provider fixture is invalid: {errors[0].message}",
                "Correct every fixture to match provider-fixture.schema.json.",
            ))
            continue
        fixture_id = fixture["fixtureId"]
        fixture_statuses.add(fixture["expected"]["status"])
        if fixture["expected"]["status"] == "succeeded":
            successful_capabilities.add(fixture["capability"])
        if fixture_id in fixture_ids:
            issues.append(_issue(
                "PROVIDER_FIXTURE_DUPLICATE",
                "A provider fixture ID is duplicated.",
                "Assign a stable unique fixtureId to every provider fixture.",
            ))
        fixture_ids.add(fixture_id)
        capability = fixture["capability"]
        if descriptor is not None and capability not in capabilities:
            issues.append(_issue(
                "PROVIDER_FIXTURE_CAPABILITY_UNKNOWN",
                "A provider fixture references an undeclared capability.",
                "Reference a capability declared by the packaged provider descriptor.",
            ))
        elif (
            descriptor is not None
            and fixture["expected"]["status"] == "succeeded"
            and not set(fixture["expected"]["evidenceKinds"]).issubset(capabilities[capability])
        ):
            issues.append(_issue(
                "PROVIDER_FIXTURE_EVIDENCE_KIND_UNKNOWN",
                "A provider fixture expects an Evidence kind outside its capability declaration.",
                "Use only Evidence kinds declared for the fixture capability.",
            ))
        if descriptor is not None:
            scope_error = next(
                Draft202012Validator(descriptor.scope_schema).iter_errors(fixture["scope"]),
                None,
            )
            if scope_error is not None:
                issues.append(_issue(
                    "PROVIDER_FIXTURE_SCOPE_INVALID",
                    "A provider fixture scope does not satisfy the packaged provider schema.",
                    "Correct the fixture scope or provider scope schema.",
                ))

    if descriptor is not None and fixture_statuses != {"succeeded", "failed"}:
        issues.append(_issue(
            "PROVIDER_FIXTURE_COVERAGE_INCOMPLETE",
            "Provider release fixtures must cover both a successful fact response and a classified failure.",
            "Add deterministic succeeded and failed provider fixtures.",
        ))
    untested_capabilities = set(capabilities) - successful_capabilities
    if descriptor is not None and untested_capabilities:
        issues.append(_issue(
            "PROVIDER_FIXTURE_CAPABILITY_COVERAGE_INCOMPLETE",
            "Provider release fixtures do not prove every declared capability.",
            "Add one deterministic successful fixture for each declared capability.",
        ))

    metadata_path = paths["packageMetadata"]
    if metadata_path is None or not metadata_path.is_file():
        issues.append(_issue(
            "PROVIDER_PACKAGE_METADATA_MISSING",
            "The declared Python package metadata is missing.",
            "Package pyproject.toml with an assayer.providers entry point.",
        ))
    else:
        try:
            project = tomllib.loads(metadata_path.read_text(encoding="utf-8"))["project"]
            entry_points = project["entry-points"]["assayer.providers"]
            if not isinstance(project, dict) or not isinstance(entry_points, dict):
                raise TypeError("project and provider entry points must be TOML tables")
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, AttributeError):
            issues.append(_issue(
                "PROVIDER_PACKAGE_METADATA_INVALID",
                "Python package metadata is invalid or lacks assayer.providers.",
                "Declare [project.entry-points.\"assayer.providers\"] in pyproject.toml.",
            ))
        else:
            if project.get("version") != release["providerVersion"]:
                issues.append(_issue(
                    "PROVIDER_PACKAGE_VERSION_MISMATCH",
                    "The Python project version differs from the provider release descriptor.",
                    "Use the same provider version in package metadata and both descriptors.",
                ))
            dependencies = project.get("dependencies", [])
            if not isinstance(dependencies, list) or not any(
                isinstance(item, str)
                and re.match(r"^\s*assayer(?:\s|$|[<>=!~;\[])", item, re.IGNORECASE)
                for item in dependencies
            ):
                issues.append(_issue(
                    "PROVIDER_PLATFORM_DEPENDENCY_MISSING",
                    "Python package metadata does not declare an Assayer platform dependency.",
                    "Declare a compatible assayer version range in project.dependencies.",
                ))
            if release["registration"] not in entry_points.values():
                issues.append(_issue(
                    "PROVIDER_ENTRY_POINT_MISSING",
                    "Python package metadata does not export the declared provider registration.",
                    "Add the registration to the assayer.providers entry-point group.",
                ))

    return ProviderConformanceReport(provider_id, tuple(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an Assayer provider package without importing provider code",
    )
    parser.add_argument("package", nargs="+", metavar="PACKAGE_ROOT")
    args = parser.parse_args(argv)
    reports = [inspect_provider_package(path) for path in args.package]
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "providers": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PROVIDER_RELEASE_DESCRIPTOR",
    "inspect_provider_package",
    "main",
]
