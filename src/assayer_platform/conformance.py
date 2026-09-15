"""Reusable package and registration conformance gates for audit plugins."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import SchemaError

from .contract import PlatformContractError, PluginManifest
from .registry import schema_validator, validate_plugin_manifest


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
        **({"compatibility": {
            "protocolMinVersion": manifest.compatibility.protocol_min_version,
            "protocolMaxVersion": manifest.compatibility.protocol_max_version,
            "sdkMinVersion": manifest.compatibility.sdk_min_version,
            "sdkMaxVersion": manifest.compatibility.sdk_max_version,
            "capabilities": sorted(manifest.compatibility.capabilities),
            **({"domainContractVersion": manifest.compatibility.domain_contract_version}
               if manifest.compatibility.domain_contract_version is not None else {}),
        }} if manifest.compatibility is not None else {}),
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
            "maxBatchSize": profile.max_batch_size,
            "ordering": profile.ordering,
            "failureSplitting": profile.failure_splitting,
        },
    }


def _schema_refs(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        for item in value.values():
            yield from _schema_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _schema_refs(item)


def _inspect_domain_result_schema(
    schema: dict[str, Any], *, label: str,
) -> list[PluginConformanceIssue]:
    issues: list[PluginConformanceIssue] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        issues.append(_issue(
            "PLUGIN_DOMAIN_RESULT_SCHEMA_INVALID",
            "PDSV2-DOMAIN-RESULT-SCHEMA",
            f"The {label} is not a valid Draft 2020-12 JSON Schema: {error.message}",
            "Correct the boundary schema before registering or packaging the plugin.",
        ))
        return issues
    if schema.get("type") != "object":
        issues.append(_issue(
            "PLUGIN_DOMAIN_RESULT_SCHEMA_INVALID",
            "PDSV2-DOMAIN-RESULT-SCHEMA",
            f"The {label} must explicitly describe a top-level object.",
            "Set the boundary schema type to object and declare its accepted fields.",
        ))
    resolver = RefResolver.from_schema(schema)
    for reference in sorted(set(_schema_refs(schema))):
        if not reference.startswith("#"):
            issues.append(_issue(
                "PLUGIN_DOMAIN_RESULT_SCHEMA_REFERENCE_INVALID",
                "PDSV2-DOMAIN-RESULT-SCHEMA",
                f"The {label} contains a non-bundled schema reference: {reference}",
                "Resolve package-local references into the registered bundle and remove remote references.",
            ))
            continue
        try:
            resolver.resolve(reference)
        except Exception:
            issues.append(_issue(
                "PLUGIN_DOMAIN_RESULT_SCHEMA_REFERENCE_INVALID",
                "PDSV2-DOMAIN-RESULT-SCHEMA",
                f"The {label} contains an unresolved local schema reference: {reference}",
                "Define the referenced fragment inside the same boundary schema.",
            ))
    return issues


def _inspect_domain_result_contracts(registration: Any) -> list[PluginConformanceIssue]:
    """Validate the target domain-only contract surface when published."""
    contracts = registration.domain_result_contracts
    issues: list[PluginConformanceIssue] = []
    common_review = "common_review" in registration.result_features
    if common_review and "interactive" not in registration.execution_modes:
        issues.append(_issue(
            "PLUGIN_COMMON_REVIEW_MODE_INVALID",
            "PDSV2-COMMON-REVIEW-CONTRACT",
            "Common review requires interactive execution mode.",
            "Declare interactive execution or remove the common-review feature.",
        ))
    if common_review and contracts:
        issues.append(_issue(
            "PLUGIN_SEMANTIC_CONTRACT_CONFLICT",
            "PDSV2-COMMON-REVIEW-CONTRACT",
            "A common-review registration cannot also publish a complete DomainResultContract.",
            "Remove the handwritten DomainResultContract and use the Host common-review model.",
        ))
    if "interactive" in registration.execution_modes and not contracts and not common_review:
        issues.append(_issue(
            "PLUGIN_DOMAIN_RESULT_CONTRACT_REQUIRED",
            "PDSV2-DOMAIN-RESULT-CONTRACT",
            "Interactive registrations must publish one DomainResultContract per Check.",
            "Publish a DomainResultContract for every interactive Check before registering the plugin.",
        ))
    if not contracts:
        return issues
    if "interactive" not in registration.execution_modes:
        issues.append(_issue(
            "PLUGIN_DOMAIN_RESULT_CONTRACT_MODE_INVALID",
            "PDSV2-DOMAIN-RESULT-CONTRACT",
            "The registration publishes domain-result contracts without interactive execution mode.",
            "Declare interactive execution mode or remove the domain-result contracts.",
        ))
    declared_refs = {check.ref for check in registration.manifest.checks}
    seen_refs: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for contract in contracts:
        payload = contract.as_dict()
        schema = payload.get("resultSchema")
        issues.extend(_inspect_domain_result_schema(
            schema,
            label=f"domain result schema for {contract.check_id}@{contract.check_version}",
        ))
        if contract.check_ref in seen_refs:
            issues.append(_issue(
                "PLUGIN_DOMAIN_RESULT_CONTRACT_DUPLICATE",
                "PDSV2-DOMAIN-RESULT-COVERAGE",
                f"More than one domain-result contract targets {contract.check_id}@{contract.check_version}.",
                "Publish exactly one domain-result contract per interactive Check.",
            ))
        seen_refs.add(contract.check_ref)
        if contract.contract_id in seen_ids:
            issues.append(_issue(
                "PLUGIN_DOMAIN_RESULT_CONTRACT_DUPLICATE",
                "PDSV2-DOMAIN-RESULT-CONTRACT",
                f"The domain-result contract ID is duplicated: {contract.contract_id}",
                "Assign one globally namespaced contractId to each Check contract.",
            ))
        seen_ids.add(contract.contract_id)
        if contract.check_ref not in declared_refs:
            issues.append(_issue(
                "PLUGIN_DOMAIN_RESULT_CONTRACT_CHECK_UNKNOWN",
                "PDSV2-DOMAIN-RESULT-COVERAGE",
                f"Domain-result contract references an undeclared Check: {contract.check_id}@{contract.check_version}",
                "Bind each domain-result contract to a Check declared by the plugin manifest.",
            ))
    missing_refs = sorted(declared_refs - set(seen_refs))
    if missing_refs:
        issues.append(_issue(
            "PLUGIN_DOMAIN_RESULT_CONTRACT_CHECK_COVERAGE_INCOMPLETE",
            "PDSV2-DOMAIN-RESULT-COVERAGE",
            "Domain-result contracts do not cover declared Checks: "
            + ", ".join(f"{check_id}@{version}" for check_id, version in missing_refs),
            "Publish exactly one domain-result contract for every interactive Check.",
        ))
    declared_version = getattr(registration.compatibility, "domain_contract_version", None)
    actual_versions = {contract.contract_version for contract in contracts}
    if declared_version is None:
        issues.append(_issue(
            "PLUGIN_DOMAIN_CONTRACT_VERSION_REQUIRED",
            "PDSV2-DOMAIN-RESULT-CONTRACT",
            "Interactive registrations must declare domainContractVersion in manifest compatibility.",
            "Set domainContractVersion to the exact registered DomainResultContract contractVersion.",
        ))
    elif actual_versions != {declared_version}:
        issues.append(_issue(
            "PLUGIN_DOMAIN_CONTRACT_VERSION_MISMATCH",
            "PDSV2-DOMAIN-RESULT-CONTRACT",
            "The manifest domainContractVersion does not match the registered DomainResultContract version(s).",
            "Set domainContractVersion to the exact registered DomainResultContract contractVersion.",
        ))
    return issues


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
    if manifest.compatibility is None:
        issues.append(_issue(
            "PLUGIN_COMPATIBILITY_REQUIRED",
            "PCV1-COMPATIBILITY",
            "The registration manifest must declare the exact current protocol and SDK contract.",
            "Add the compatibility block to the manifest and reload it.",
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
            simple_runtime = (
                "common_review" in registration.result_features
                and isinstance(getattr(plugin, "_assayer_simple_declaration", None), Mapping)
                and callable(getattr(plugin, "_assayer_compile_scan", None))
            )
            if missing_operations and not simple_runtime:
                issues.append(_issue(
                    "PLUGIN_RUNTIME_INCOMPLETE",
                    "PCV1-RUNTIME-IMPLEMENTATION",
                    "The plugin implementation omits required operations: " + ", ".join(missing_operations),
                    "Implement callable discover and inspect operations.",
                ))
            if "evidence_graph" in registration.result_features and getattr(
                plugin, "evidence_graph_enabled", False,
            ) is not True:
                issues.append(_issue(
                    "PLUGIN_RESULT_FEATURE_UNIMPLEMENTED",
                    "PCV1-RESULT-FEATURE",
                    "The registration declares evidence_graph but the runtime does not mark it as enabled.",
                    "Expose evidence_graph_enabled = True only after emitting and validating the platform projection.",
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
    provider_source_capabilities = frozenset(
        getattr(registration, "provider_source_capabilities", ())
    )
    unused_source_capabilities = sorted(
        provider_source_capabilities - required_capabilities
    )
    if unused_source_capabilities:
        issues.append(_issue(
            "PLUGIN_PROVIDER_SOURCE_CAPABILITY_UNUSED",
            "PCV1-PROVIDER-SOURCE-CAPABILITY",
            "The registration declares provider-owned source discovery for capabilities not required by any Check: "
            + ", ".join(unused_source_capabilities),
            "Declare source discovery only for capabilities required by the plugin's Checks.",
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

    issues.extend(_inspect_domain_result_contracts(registration))

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


def inspect_plugin_lifecycle(
    registration: Any,
    scope: Any,
    check_id: str,
    context: Any,
    *,
    check_version: str | None = None,
    decision_provider: Any = None,
    committer: Any = None,
    review_builder: Any = None,
) -> PluginConformanceReport:
    """Run the domain-neutral plugin lifecycle gate.

    This deliberately validates only platform invariants.  Domain semantics
    remain owned by the plugin's own tests and schemas.
    """
    base = inspect_plugin_registration(registration, construct_implementations=True)
    if not base.passed:
        return base
    from .kernel import PlatformKernel
    from .plugin_registry import PluginRegistry
    from .result_conformance import inspect_result_conformance

    registry = PluginRegistry((registration,))
    scope_error = next(Draft202012Validator(dict(registration.scope_schema)).iter_errors(scope), None)
    if scope_error is not None:
        return PluginConformanceReport(registration.manifest.plugin_id, (_issue(
            "INVALID_SCOPE",
            "PCV1-LIFECYCLE-SCOPE",
            f"The lifecycle fixture does not satisfy the registered scope schema: {scope_error.message}",
            "Provide a valid business scope fixture for the plugin lifecycle gate.",
        ),))
    kernel = PlatformKernel()
    from .provider_binding import bind_capability_provider, check_for
    check = check_for(registration, check_id, check_version)
    bound = (
        bind_capability_provider(
            registration, check, scope, f"lifecycle:{registration.manifest.plugin_id}",
        )
        if check is not None else None
    )
    run_context = bound.context if bound is not None else context
    expectation = bound.evidence_expectation if bound is not None else None
    result = kernel.run_registered(
        registry, scope, check_id, run_context, plugin_id=registration.manifest.plugin_id,
        check_version=check_version, decision_provider=decision_provider,
        committer=committer, provider_evidence_expectation=expectation,
    )
    result_report = inspect_result_conformance(result)
    issues = list(base.issues)
    if review_builder is not None:
        if not callable(review_builder):
            issues.append(_issue(
                "INVALID_REVIEW_BUILDER", "PCV1-LIFECYCLE-REVIEW",
                "The lifecycle review_builder is not callable.",
                "Provide a callable that returns the assembled platform decisions.",
            ))
        else:
            try:
                assembled = tuple(review_builder(result))
            except Exception as error:
                issues.append(_issue(
                    "REVIEW_ASSEMBLY_FAILED", "PCV1-LIFECYCLE-REVIEW",
                    f"The review builder failed for the terminal result: {error}",
                    "Make review assembly deterministic and return platform DecisionProposal objects.",
                ))
            else:
                if assembled != tuple(result.decisions):
                    issues.append(_issue(
                        "REVIEW_ASSEMBLY_MISMATCH", "PCV1-LIFECYCLE-REVIEW",
                        "Assembled review decisions differ from decisions committed by the platform run.",
                        "Commit exactly the decisions produced by the review assembly stage.",
                    ))
    replay = kernel.run_registered(
        registry, scope, check_id, run_context, plugin_id=registration.manifest.plugin_id,
        check_version=check_version, decision_provider=decision_provider,
        committer=committer, provider_evidence_expectation=expectation,
    )
    replay_report = inspect_result_conformance(replay)
    if replay.status != result.status or tuple(replay.failures) != tuple(result.failures):
        issues.append(_issue(
            "LIFECYCLE_REPLAY_MISMATCH", "PCV1-LIFECYCLE-REPLAY",
            "Replaying the same Run input produced a different status or failure set.",
            "Make discovery, inspection, decision, and commit replay deterministic and idempotent.",
        ))
    if len(replay.receipts) != len(result.receipts):
        issues.append(_issue(
            "LIFECYCLE_REPLAY_RECEIPT_MISMATCH", "PCV1-LIFECYCLE-REPLAY",
            "Replaying the same Run input produced a different receipt count.",
            "Return the original commit receipts for an idempotent replay.",
        ))
    issues.extend(_issue(
        item.code, item.invariant, item.message, item.next_action,
    ) for item in replay_report.issues)
    issues.extend(_issue(
        item.code, item.invariant, item.message, item.next_action,
    ) for item in result_report.issues)
    if bound is not None:
        bound.close()
    return PluginConformanceReport(registration.manifest.plugin_id, tuple(issues))


def inspect_plugin_registrations(
    registrations: Iterable[Any], *, construct_implementations: bool = False,
) -> tuple[PluginConformanceReport, ...]:
    return tuple(
        inspect_plugin_registration(
            registration, construct_implementations=construct_implementations,
        )
        for registration in registrations
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
        schema_validator("plugin-release.schema.json").iter_errors(descriptor),
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
            manifest_compatibility = manifest.compatibility
            declared_compatibility = descriptor.get("compatibility")
            if manifest_compatibility is None:
                issues.append(_package_issue(
                    "PLUGIN_COMPATIBILITY_REQUIRED",
                    "The packaged manifest must declare the exact current protocol and SDK contract.",
                    "Add protocolMinVersion=protocolMaxVersion and sdkMinVersion=sdkMaxVersion to the manifest.",
                ))
            else:
                expected_compatibility = {
                    "protocolMinVersion": manifest_compatibility.protocol_min_version,
                    "protocolMaxVersion": manifest_compatibility.protocol_max_version,
                    "sdkMinVersion": manifest_compatibility.sdk_min_version,
                    "sdkMaxVersion": manifest_compatibility.sdk_max_version,
                    "capabilities": sorted(manifest_compatibility.capabilities),
                }
                if manifest_compatibility.domain_contract_version is not None:
                    expected_compatibility["domainContractVersion"] = manifest_compatibility.domain_contract_version
                if declared_compatibility != expected_compatibility:
                    issues.append(_package_issue(
                        "PLUGIN_COMPATIBILITY_IDENTITY_MISMATCH",
                        "The release descriptor and manifest compatibility declarations differ.",
                        "Copy the manifest compatibility declaration into the release descriptor.",
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
        acceptance_spec = descriptor.get("releaseAcceptance")
        if acceptance_spec:
            acceptance_module = acceptance_spec.partition(":")[0]
            acceptance_path = runtime_root.joinpath(*acceptance_module.split("."))
            if (
                not acceptance_path.with_suffix(".py").is_file()
                and not (acceptance_path / "__init__.py").is_file()
            ):
                issues.append(_package_issue(
                    "PLUGIN_RELEASE_ACCEPTANCE_SOURCE_MISSING",
                    f"The release-acceptance module is absent from runtimeSource: {acceptance_module}",
                    "Package the module named by releaseAcceptance in the built wheel.",
                ))
        # Compiler-generated ordinary packages retain the author module for
        # runtime scanning.  Re-run the same no-import static gate here so a
        # changed staging tree or unpacked wheel cannot bypass compilation.
        package_dir = module_path if module_path.is_dir() else module_path.parent
        compiler_metadata = package_dir / "compiler-metadata.json"
        author_source = package_dir / "author.py"
        if compiler_metadata.is_file() and author_source.is_file():
            metadata_value = _read_json_resource(
                compiler_metadata, kind="Simple compiler metadata", issues=issues,
            )
            if (
                isinstance(metadata_value, dict)
                and metadata_value.get("compiler") == "assayer-simple-sdk"
            ):
                try:
                    from .simple_author_conformance import validate_simple_author_source

                    validate_simple_author_source(author_source)
                except PlatformContractError as error:
                    issues.append(_package_issue(
                        error.code,
                        f"The packaged Simple author source is invalid: {error.message}",
                        "Recompile the original domain-only source with assayer plugin verify.",
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

    fixture_validator = schema_validator("plugin-fixture.schema.json")
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
            entry_point_groups = project["entry-points"]
            entry_points = entry_point_groups["assayer.plugins"]
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
                and re.match(r"^\s*assayer-plugin-sdk(?:\s|$|[<>=!~;\[])", item, re.IGNORECASE)
                for item in dependencies
            ):
                issues.append(_package_issue(
                    "PLUGIN_SDK_DEPENDENCY_MISSING",
                    "Python package metadata does not declare an Assayer plugin SDK dependency.",
                    "Declare a compatible assayer-plugin-sdk version range in project.dependencies.",
                ))
            if descriptor["registration"] not in entry_points.values():
                issues.append(_package_issue(
                    "PLUGIN_ENTRY_POINT_MISSING",
                    "Python package metadata does not export the declared registration.",
                    "Add the declared registration to the assayer.plugins entry-point group.",
                ))
            acceptance_spec = descriptor.get("releaseAcceptance")
            acceptance_entries = entry_point_groups.get("assayer.release_acceptance", {})
            if acceptance_spec and (
                not isinstance(acceptance_entries, dict)
                or tuple(acceptance_entries.values()).count(acceptance_spec) != 1
            ):
                issues.append(_package_issue(
                    "PLUGIN_RELEASE_ACCEPTANCE_ENTRY_POINT_MISSING",
                    "Python package metadata does not export the declared release acceptance driver exactly once.",
                    "Add releaseAcceptance to [project.entry-points.\"assayer.release_acceptance\"].",
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
