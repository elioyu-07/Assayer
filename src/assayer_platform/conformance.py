"""Reusable package and registration conformance gates for audit plugins."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import SchemaError

from .contract import PlatformContractError, PluginManifest
from .registry import validate_plugin_manifest


RELEASE_DESCRIPTOR = "assayer-plugin-release.json"


@dataclass(frozen=True)
class PluginConformanceIssue:
    """One actionable violation of the frozen plugin contract."""

    code: str
    invariant: str
    message: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "invariant": self.invariant,
            "message": self.message,
            "nextAction": self.next_action,
        }


@dataclass(frozen=True)
class PluginConformanceReport:
    """Deterministic validation result suitable for release tooling."""

    plugin_id: str
    issues: tuple[PluginConformanceIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "pluginId": self.plugin_id,
            "status": "passed" if self.passed else "failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _issue(code: str, invariant: str, message: str, next_action: str) -> PluginConformanceIssue:
    return PluginConformanceIssue(code, invariant, message, next_action)


def _manifest_payload(manifest: PluginManifest) -> dict[str, Any]:
    profile = manifest.execution_profile
    return {
        "pluginId": manifest.plugin_id,
        "version": manifest.version,
        "platformApiVersion": manifest.platform_api_version,
        "domains": list(manifest.domains),
        "subjectKinds": list(manifest.subject_kinds),
        "checks": [{
            "checkId": check.check_id,
            "version": check.version,
            "subjectKinds": list(check.subject_kinds),
            "dimensions": list(check.dimensions),
            "decisionStates": list(check.decision_states),
            "requiredEvidenceKinds": list(check.required_evidence_kinds),
            "requiredCapabilities": list(check.required_capabilities),
            "capabilityMissingOutcome": check.capability_missing_outcome,
            "invalidationSignals": list(check.invalidation_signals),
        } for check in manifest.checks],
        "executionProfile": {
            "discoverBatching": profile.discover_batching,
            "inspectBatching": profile.inspect_batching,
            "decisionBatching": profile.decision_batching,
            "parallelism": profile.parallelism,
            "cacheReuse": profile.cache_reuse,
            "checkpoint": profile.checkpoint,
            "maxBatchSize": profile.max_batch_size,
            "ordering": profile.ordering,
            "failureSplitting": profile.failure_splitting,
        },
    }


def inspect_plugin_registration(
    registration: Any, *, construct_implementations: bool = False,
) -> PluginConformanceReport:
    """Inspect one registration, optionally constructing release implementations."""
    from .plugin_registry import PluginRegistration

    if not isinstance(registration, PluginRegistration):
        return PluginConformanceReport("unknown", (_issue(
            "INVALID_PLUGIN_REGISTRATION",
            "PCV1-REGISTRATION",
            "The release entry point did not provide a PluginRegistration.",
            "Export one PluginRegistration or a zero-argument factory that returns one.",
        ),))

    manifest = registration.manifest
    plugin_id = manifest.plugin_id if isinstance(manifest, PluginManifest) else "unknown"
    issues: list[PluginConformanceIssue] = []
    if not isinstance(manifest, PluginManifest):
        issues.append(_issue(
            "INVALID_PLUGIN_MANIFEST",
            "PCV1-MANIFEST",
            "The registration manifest is not a validated PluginManifest.",
            "Load the manifest with load_plugin_manifest before registration.",
        ))
        return PluginConformanceReport(plugin_id, tuple(issues))
    try:
        validate_plugin_manifest(_manifest_payload(manifest))
    except PlatformContractError as error:
        issues.append(_issue(
            error.code,
            "PCV1-MANIFEST",
            error.message,
            "Correct and reload the manifest before packaging the plugin.",
        ))

    if not callable(registration.plugin_factory):
        issues.append(_issue(
            "PLUGIN_RUNTIME_UNAVAILABLE",
            "PCV1-RUNTIME-FACTORY",
            "The registration does not expose a callable plugin factory.",
            "Provide plugin_factory that constructs the registered domain plugin.",
        ))
    if "batch" in registration.execution_modes and not callable(registration.decision_provider_factory):
        issues.append(_issue(
            "PLUGIN_DECISION_UNAVAILABLE",
            "PCV1-BATCH-DECISION",
            "A batch plugin must expose a callable semantic decision provider factory.",
            "Provide decision_provider_factory or remove batch from execution_modes.",
        ))

    if construct_implementations and callable(registration.plugin_factory):
        try:
            plugin = registration.create_plugin(None)
        except Exception as error:
            issues.append(_issue(
                "PLUGIN_INITIALIZATION_FAILED",
                "PCV1-RUNTIME-IMPLEMENTATION",
                f"The plugin factory failed during release validation: {error}",
                "Make plugin construction deterministic and defer live provider access until execution.",
            ))
        else:
            if getattr(plugin, "manifest", None) != manifest:
                issues.append(_issue(
                    "PLUGIN_IDENTITY_MISMATCH",
                    "PCV1-RUNTIME-IMPLEMENTATION",
                    "The plugin factory returned an implementation with different manifest identity.",
                    "Return the implementation declared by this registration.",
                ))
            missing_operations = [
                name for name in ("discover", "inspect")
                if not callable(getattr(plugin, name, None))
            ]
            if missing_operations:
                issues.append(_issue(
                    "PLUGIN_RUNTIME_INCOMPLETE",
                    "PCV1-RUNTIME-IMPLEMENTATION",
                    "The plugin implementation omits required operations: " + ", ".join(missing_operations),
                    "Implement callable discover and inspect operations.",
                ))
    if (
        construct_implementations
        and "batch" in registration.execution_modes
        and callable(registration.decision_provider_factory)
    ):
        try:
            provider = registration.create_decision_provider(None)
        except Exception as error:
            issues.append(_issue(
                "PLUGIN_DECISION_INITIALIZATION_FAILED",
                "PCV1-BATCH-DECISION",
                f"The decision provider factory failed during release validation: {error}",
                "Make decision-provider construction deterministic and independent of live inputs.",
            ))
        else:
            if not callable(getattr(provider, "decide", None)):
                issues.append(_issue(
                    "PLUGIN_DECISION_INCOMPLETE",
                    "PCV1-BATCH-DECISION",
                    "The batch decision provider does not implement decide.",
                    "Return a semantic decision provider with a callable decide operation.",
                ))

    required_capabilities = frozenset(
        capability
        for check in manifest.checks
        for capability in check.required_capabilities
    )
    missing_capabilities = sorted(required_capabilities - registration.capabilities)
    if missing_capabilities:
        issues.append(_issue(
            "PLUGIN_CAPABILITY_UNDECLARED",
            "PCV1-CAPABILITY-DECLARATION",
            "The registration omits required manifest capabilities: " + ", ".join(missing_capabilities),
            "Declare every required Check capability in the registration capability set.",
        ))

    schema = registration.scope_schema
    if not schema:
        issues.append(_issue(
            "PLUGIN_SCOPE_SCHEMA_MISSING",
            "PCV1-SCOPE-SCHEMA",
            "The registration does not publish a business-input scope schema.",
            "Provide a JSON Schema object that defines the plugin's accepted business input.",
        ))
    else:
        try:
            Draft202012Validator.check_schema(dict(schema))
        except SchemaError as error:
            issues.append(_issue(
                "PLUGIN_SCOPE_SCHEMA_INVALID",
                "PCV1-SCOPE-SCHEMA",
                f"The registration scope schema is invalid: {error.message}",
                "Correct the scope schema before packaging the plugin.",
            ))
        if schema.get("type") != "object":
            issues.append(_issue(
                "PLUGIN_SCOPE_SCHEMA_INVALID",
                "PCV1-SCOPE-SCHEMA",
                "The registration scope schema must describe a top-level object.",
                "Set scope_schema.type to object and define the accepted business fields.",
            ))

    profile = manifest.execution_profile
    if profile.failure_splitting == "allowed" and not profile.can_split_failed_inspection:
        issues.append(_issue(
            "PLUGIN_EXECUTION_PROFILE_INVALID",
            "PCV1-FAILURE-SPLITTING",
            "Failure splitting requires allowed inspection batching and independent ordering.",
            "Disable failure splitting or declare both safe inspection batching and independent ordering.",
        ))
    if profile.parallelism == "allowed" and profile.ordering != "independent":
        issues.append(_issue(
            "PLUGIN_EXECUTION_PROFILE_INVALID",
            "PCV1-PARALLELISM",
            "Parallel execution requires independent WorkItem ordering.",
            "Disable parallelism or declare independent ordering after proving isolation.",
        ))
    if profile.cache_reuse == "allowed":
        missing_invalidation = sorted(
            f"{check.check_id}@{check.version}"
            for check in manifest.checks
            if not check.invalidation_signals
        )
        if missing_invalidation:
            issues.append(_issue(
                "PLUGIN_CACHE_INVALIDATION_MISSING",
                "PCV1-CACHE-INVALIDATION",
                "Cache reuse lacks invalidation signals for: " + ", ".join(missing_invalidation),
                "Declare source-change invalidation signals for every cacheable Check.",
            ))

    return PluginConformanceReport(plugin_id, tuple(issues))


def require_plugin_registration_conformance(
    registration: Any, *, construct_implementations: bool = False,
) -> PluginConformanceReport:
    """Reject an incomplete registration with one stable actionable error."""
    report = inspect_plugin_registration(
        registration, construct_implementations=construct_implementations,
    )
    if not report.passed:
        first = report.issues[0]
        raise PlatformContractError(
            first.code,
            f"{first.message} Contract: {first.invariant}. Next action: {first.next_action}",
        )
    return report


def inspect_plugin_registrations(
    registrations: Iterable[Any], *, construct_implementations: bool = False,
) -> tuple[PluginConformanceReport, ...]:
    return tuple(
        inspect_plugin_registration(
            registration, construct_implementations=construct_implementations,
        )
        for registration in registrations
    )


def _schema_validator(filename: str) -> Draft202012Validator:
    from .registry import _schema_root

    root = _schema_root()
    schemas: dict[str, Any] = {}
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas[filename]
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


def _package_issue(
    code: str, message: str, next_action: str,
) -> PluginConformanceIssue:
    return _issue(code, "PCV1-PACKAGE-RESOURCES", message, next_action)


def _declared_path(
    root: Path, relative: str, *, kind: str, issues: list[PluginConformanceIssue],
) -> Path | None:
    candidate = root / relative
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        issues.append(_package_issue(
            "PLUGIN_PACKAGE_PATH_UNSAFE",
            f"The declared {kind} path escapes the plugin package: {relative}",
            "Use a normalized relative path contained inside the plugin package.",
        ))
        return None
    current = candidate
    while current != root:
        if current.is_symlink():
            issues.append(_package_issue(
                "PLUGIN_PACKAGE_PATH_UNSAFE",
                f"The declared {kind} path traverses a symbolic link: {relative}",
                "Package regular files and directories instead of symbolic links.",
            ))
            return None
        current = current.parent
    return resolved


def _read_json_resource(
    path: Path | None, *, kind: str, issues: list[PluginConformanceIssue],
) -> Any | None:
    if path is None:
        return None
    if not path.is_file():
        issues.append(_package_issue(
            "PLUGIN_PACKAGE_RESOURCE_MISSING",
            f"The declared {kind} file does not exist: {path.name}",
            f"Add the declared {kind} file to the release package.",
        ))
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        issues.append(_package_issue(
            "PLUGIN_PACKAGE_RESOURCE_INVALID",
            f"The declared {kind} file is not valid UTF-8 JSON: {error}",
            f"Correct the declared {kind} JSON before packaging.",
        ))
        return None


def inspect_plugin_package(package_root: str | Path) -> PluginConformanceReport:
    """Validate an independent plugin package without importing its code."""
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        return PluginConformanceReport(str(root), (_package_issue(
            "PLUGIN_PACKAGE_NOT_FOUND",
            "The plugin package root does not exist or is not a directory.",
            "Provide the directory containing assayer-plugin-release.json.",
        ),))
    descriptor_path = root / RELEASE_DESCRIPTOR
    issues: list[PluginConformanceIssue] = []
    descriptor = _read_json_resource(
        descriptor_path, kind="release descriptor", issues=issues,
    )
    if not isinstance(descriptor, dict):
        if descriptor is not None:
            issues.append(_package_issue(
                "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
                "The release descriptor must be a JSON object.",
                "Correct assayer-plugin-release.json to match plugin-release.schema.json.",
            ))
        return PluginConformanceReport(root.name, tuple(issues))
    schema_errors = sorted(
        _schema_validator("plugin-release.schema.json").iter_errors(descriptor),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    for error in schema_errors:
        location = "/".join(str(part) for part in error.absolute_path) or "root"
        issues.append(_package_issue(
            "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            f"The release descriptor is invalid at {location}: {error.message}",
            "Correct assayer-plugin-release.json to match plugin-release.schema.json.",
        ))
    plugin_id = descriptor.get("pluginId")
    if schema_errors or not isinstance(plugin_id, str):
        return PluginConformanceReport(plugin_id or root.name, tuple(issues))

    paths = {
        name: _declared_path(root, descriptor[name], kind=name, issues=issues)
        for name in ("manifest", "scopeSchema", "runtimeSource", "semanticReview", "packageMetadata")
    }
    fixture_paths = [
        _declared_path(root, item, kind="fixture", issues=issues)
        for item in descriptor["fixtures"]
    ]

    manifest_value = _read_json_resource(
        paths["manifest"], kind="manifest", issues=issues,
    )
    manifest = None
    if isinstance(manifest_value, dict):
        try:
            from .registry import load_plugin_manifest

            manifest = load_plugin_manifest(manifest_value)
        except PlatformContractError as error:
            issues.append(_package_issue(
                error.code,
                f"The packaged manifest is invalid: {error.message}",
                "Correct the manifest before packaging the plugin.",
            ))
        else:
            expected_identity = (
                descriptor["pluginId"], descriptor["pluginVersion"],
                descriptor["platformApiVersion"],
            )
            actual_identity = (
                manifest.plugin_id, manifest.version, manifest.platform_api_version,
            )
            if actual_identity != expected_identity:
                issues.append(_package_issue(
                    "PLUGIN_PACKAGE_IDENTITY_MISMATCH",
                    "The release descriptor and manifest plugin identity or version differ.",
                    "Use one plugin ID, plugin version, and platform API version throughout the package.",
                ))
    elif manifest_value is not None:
        issues.append(_package_issue(
            "INVALID_PLUGIN_MANIFEST",
            "The packaged manifest must be a JSON object.",
            "Correct the manifest before packaging the plugin.",
        ))

    scope_value = _read_json_resource(
        paths["scopeSchema"], kind="scope schema", issues=issues,
    )
    if isinstance(scope_value, dict):
        try:
            Draft202012Validator.check_schema(scope_value)
        except SchemaError as error:
            issues.append(_package_issue(
                "PLUGIN_SCOPE_SCHEMA_INVALID",
                f"The packaged scope schema is invalid: {error.message}",
                "Correct the scope schema before packaging the plugin.",
            ))
        if scope_value.get("type") != "object":
            issues.append(_package_issue(
                "PLUGIN_SCOPE_SCHEMA_INVALID",
                "The packaged scope schema must describe a top-level object.",
                "Set the packaged scope schema type to object.",
            ))
    elif scope_value is not None:
        issues.append(_package_issue(
            "PLUGIN_SCOPE_SCHEMA_INVALID",
            "The packaged scope schema must be a JSON object.",
            "Correct the scope schema before packaging the plugin.",
        ))

    runtime_root = paths["runtimeSource"]
    module_name, _, _attribute = descriptor["registration"].partition(":")
    if runtime_root is None or not runtime_root.is_dir():
        issues.append(_package_issue(
            "PLUGIN_RUNTIME_SOURCE_MISSING",
            "The declared runtime source directory is missing.",
            "Package the Python source directory containing the registration module.",
        ))
    else:
        module_path = runtime_root.joinpath(*module_name.split("."))
        if not module_path.with_suffix(".py").is_file() and not (module_path / "__init__.py").is_file():
            issues.append(_package_issue(
                "PLUGIN_REGISTRATION_SOURCE_MISSING",
                f"The registration module is absent from runtimeSource: {module_name}",
                "Package the module named by the registration entry point.",
            ))

    semantic_review = paths["semanticReview"]
    if semantic_review is None or not semantic_review.is_file():
        issues.append(_package_issue(
            "PLUGIN_SEMANTIC_REVIEW_MISSING",
            "The declared semantic-review instructions are missing.",
            "Package a nonempty Markdown semantic-review contract.",
        ))
    else:
        try:
            review_text = semantic_review.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            review_text = ""
            issues.append(_package_issue(
                "PLUGIN_SEMANTIC_REVIEW_INVALID",
                f"The semantic-review instructions cannot be read as UTF-8: {error}",
                "Store the semantic-review contract as UTF-8 Markdown.",
            ))
        if semantic_review.suffix.lower() != ".md" or not review_text.strip():
            issues.append(_package_issue(
                "PLUGIN_SEMANTIC_REVIEW_INVALID",
                "The semantic-review instructions must be nonempty Markdown.",
                "Provide a nonempty .md file describing the Agent decision boundary.",
            ))

    fixture_validator = _schema_validator("plugin-fixture.schema.json")
    fixture_ids: set[str] = set()
    declared_checks = {check.check_id for check in manifest.checks} if manifest else set()
    for fixture_path in fixture_paths:
        fixture = _read_json_resource(
            fixture_path, kind="fixture", issues=issues,
        )
        if not isinstance(fixture, dict):
            if fixture is not None:
                issues.append(_package_issue(
                    "PLUGIN_FIXTURE_INVALID",
                    "A deterministic fixture must be a JSON object.",
                    "Correct every fixture to match plugin-fixture.schema.json.",
                ))
            continue
        errors = list(fixture_validator.iter_errors(fixture))
        if errors:
            issues.append(_package_issue(
                "PLUGIN_FIXTURE_INVALID",
                f"A deterministic fixture is invalid: {errors[0].message}",
                "Correct every fixture to match plugin-fixture.schema.json.",
            ))
            continue
        fixture_id = fixture["fixtureId"]
        if fixture_id in fixture_ids:
            issues.append(_package_issue(
                "PLUGIN_FIXTURE_DUPLICATE",
                f"The fixture ID is duplicated: {fixture_id}",
                "Assign a stable unique fixtureId to every deterministic fixture.",
            ))
        fixture_ids.add(fixture_id)
        if manifest and fixture["checkId"] not in declared_checks:
            issues.append(_package_issue(
                "PLUGIN_FIXTURE_CHECK_UNKNOWN",
                f"Fixture {fixture_id} references an undeclared Check: {fixture['checkId']}",
                "Reference a Check declared by the packaged manifest.",
            ))

    metadata = paths["packageMetadata"]
    if metadata is None or not metadata.is_file():
        issues.append(_package_issue(
            "PLUGIN_PACKAGE_METADATA_MISSING",
            "The declared Python package metadata is missing.",
            "Package pyproject.toml with an assayer.plugins entry point.",
        ))
    else:
        try:
            project = tomllib.loads(metadata.read_text(encoding="utf-8"))["project"]
            entry_points = project["entry-points"]["assayer.plugins"]
            if not isinstance(project, dict) or not isinstance(entry_points, dict):
                raise TypeError("project and assayer.plugins entry points must be TOML tables")
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, AttributeError) as error:
            issues.append(_package_issue(
                "PLUGIN_PACKAGE_METADATA_INVALID",
                f"Python package metadata is invalid or lacks assayer.plugins: {error}",
                "Declare [project.entry-points.\"assayer.plugins\"] in pyproject.toml.",
            ))
        else:
            if project.get("version") != descriptor["pluginVersion"]:
                issues.append(_package_issue(
                    "PLUGIN_PACKAGE_VERSION_MISMATCH",
                    "The Python project version differs from the release descriptor.",
                    "Use the same plugin version in pyproject.toml, the descriptor, and the manifest.",
                ))
            dependencies = project.get("dependencies", [])
            if not isinstance(dependencies, list) or not any(
                isinstance(item, str)
                and re.match(r"^\s*assayer(?:\s|$|[<>=!~;\[])", item, re.IGNORECASE)
                for item in dependencies
            ):
                issues.append(_package_issue(
                    "PLUGIN_PLATFORM_DEPENDENCY_MISSING",
                    "Python package metadata does not declare an Assayer platform dependency.",
                    "Declare a compatible assayer version range in project.dependencies.",
                ))
            if descriptor["registration"] not in entry_points.values():
                issues.append(_package_issue(
                    "PLUGIN_ENTRY_POINT_MISSING",
                    "Python package metadata does not export the declared registration.",
                    "Add the declared registration to the assayer.plugins entry-point group.",
                ))

    return PluginConformanceReport(plugin_id, tuple(issues))


def _resolve_registrations(spec: str) -> tuple[Any, ...]:
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("Registration must use module:attribute format")
    value = getattr(importlib.import_module(module_name), attribute_name)
    from .plugin_registry import PluginRegistration, PluginRegistry

    if callable(value) and not isinstance(value, PluginRegistration):
        value = value()
    if not isinstance(value, PluginRegistration):
        value = getattr(value, "registration", value)
    if isinstance(value, PluginRegistry):
        return value.list()
    return (value,)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Assayer plugin registrations before packaging or publication",
    )
    parser.add_argument(
        "registration", nargs="+", metavar="MODULE:ATTRIBUTE",
        help="Import path for a PluginRegistration or zero-argument registration factory",
    )
    args = parser.parse_args(argv)
    reports: list[PluginConformanceReport] = []
    for spec in args.registration:
        try:
            reports.extend(
                inspect_plugin_registration(
                    registration, construct_implementations=True,
                )
                for registration in _resolve_registrations(spec)
            )
        except Exception as error:
            reports.append(PluginConformanceReport(spec, (_issue(
                "PLUGIN_RELEASE_LOAD_FAILED",
                "PCV1-REGISTRATION",
                f"The registration could not be loaded: {error}",
                "Fix the import path and registration initialization before packaging.",
            ),)))
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "plugins": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
