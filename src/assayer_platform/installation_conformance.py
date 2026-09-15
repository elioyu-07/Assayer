"""Isolated installation and deterministic fixture gate for plugin releases."""

from __future__ import annotations

import argparse
import hashlib
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
    inspect_plugin_lifecycle,
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
from .interactive import InteractivePluginController
from .incremental_review import JsonCoverageLedgerStore
from .plugin_registry import PluginRegistration, PluginRegistry
from .provider_catalog import installed_provider_registry
from .provider_binding import bind_capability_provider, check_for, grant_check_capabilities
from .registry import load_plugin_manifest, schema_validator
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


def _entry_point_release_acceptance(
    target: Path, acceptance_spec: str, plugin_version: str,
) -> tuple[Any | None, list[PluginConformanceIssue]]:
    candidates = [
        entry
        for distribution in metadata.distributions(path=[str(target)])
        for entry in distribution.entry_points
        if entry.group == "assayer.release_acceptance"
    ]
    if len(candidates) != 1 or candidates[0].value != acceptance_spec:
        return None, [_package_issue(
            "PLUGIN_INSTALLED_RELEASE_ACCEPTANCE_INVALID",
            f"Expected one installed assayer.release_acceptance entry point for {acceptance_spec}; found {len(candidates)} total.",
            "Publish exactly one matching release-acceptance entry point in the wheel.",
        )]
    if candidates[0].dist is None or candidates[0].dist.version != plugin_version:
        return None, [_package_issue(
            "PLUGIN_INSTALLED_RELEASE_ACCEPTANCE_VERSION_MISMATCH",
            "The installed release-acceptance driver belongs to a different distribution version.",
            "Publish the acceptance driver in the same wheel as the plugin registration.",
        )]
    module_name = acceptance_spec.partition(":")[0]
    spec = importlib.util.find_spec(module_name)
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    try:
        if origin is None:
            raise ValueError("module origin is unavailable")
        origin.relative_to(target)
    except ValueError:
        return None, [_package_issue(
            "PLUGIN_INSTALLED_RELEASE_ACCEPTANCE_SOURCE_MISMATCH",
            "The release-acceptance module did not resolve from the isolated wheel target.",
            "Package the declared acceptance module in the same wheel as the plugin.",
        )]
    try:
        value = candidates[0].load()
    except Exception as error:
        return None, [_package_issue(
            "PLUGIN_INSTALLED_RELEASE_ACCEPTANCE_LOAD_FAILED",
            f"The installed release-acceptance driver could not be loaded: {error}",
            "Fix the packaged acceptance driver imports before publication.",
        )]
    if not callable(value):
        return None, [_package_issue(
            "PLUGIN_INSTALLED_RELEASE_ACCEPTANCE_INVALID",
            "The installed release-acceptance entry point is not callable.",
            "Export a callable release-acceptance driver.",
        )]
    return value, []


class _ReleaseAcceptanceTransport:
    """Small platform-only adapter used by installed release fixtures."""
    def __init__(
        self, root: Path, registry: PluginRegistry, tracker: dict[str, Any],
        *, provider_runtime: Any = None,
    ) -> None:
        # A release fixture may require capability providers; bind them from the
        # installed entry-point catalog so the same provider contract used in
        # production is exercised here.  Profiles grant exactly the capabilities
        # the selected Check declares.
        self._controller = InteractivePluginController(
            registry, root,
            provider_registry=installed_provider_registry(),
            provider_runtime=provider_runtime,
            platform_profile=grant_check_capabilities,
            user_profile=grant_check_capabilities,
        )
        self._active_run_id: str | None = None
        self._terminal_run_id = self._controller.terminal_run_id
        self._tracker = tracker

    @staticmethod
    def _response(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "structuredContent": {"status": "ok", "result": result},
            "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
            "isError": False,
        }

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == "start_plugin_run":
            if self._active_run_id is not None:
                raise PlatformContractError("RUN_CONFLICT", "A release acceptance Run is already active")
            result = self._controller.start(
                plugin_id=str(arguments["pluginId"]),
                check_id=str(arguments["checkId"]),
                check_version=arguments.get("checkVersion"),
                scope=arguments["scope"],
            )
            self._active_run_id = result["runId"]
            self._terminal_run_id = None
        elif name == "resume_plugin_run":
            result = self._controller.resume(str(arguments["runId"]))
            self._tracker["resumed"].add(result["runId"])
            if result.get("status") in {"completed", "partial", "failed"}:
                self._terminal_run_id = result["runId"]
            else:
                self._active_run_id = result["runId"]
                self._terminal_run_id = None
        elif name == "advance_plugin_run":
            if self._active_run_id is None:
                if self._terminal_run_id is None:
                    raise PlatformContractError("RUN_NOT_STARTED", "No release acceptance Run is active")
                if "domainResult" in arguments or "reviewSubmission" in arguments:
                    raise PlatformContractError("RUN_TERMINAL", "The release acceptance Run is terminal")
                result = self._controller.terminal_status(self._terminal_run_id)
                self._tracker["replayed"].add(self._terminal_run_id)
            else:
                submitted_domain_result = "domainResult" in arguments
                submitted_review_batch = "reviewSubmission" in arguments
                result = self._controller.advance(
                    self._active_run_id,
                    domain_result=arguments.get("domainResult"),
                    review_submission=arguments.get("reviewSubmission"),
                    page_size=arguments.get("pageSize"),
                )
                if submitted_domain_result:
                    self._tracker["domainResultSubmissions"] += 1
                if submitted_review_batch:
                    self._tracker["reviewBatchSubmissions"] += 1
                if result.get("status") in {"completed", "partial", "failed"}:
                    self._terminal_run_id = result["runId"]
                    self._active_run_id = None
        elif name == "expand_evidence_collection":
            if self._active_run_id is None:
                raise PlatformContractError("RUN_NOT_STARTED", "No release acceptance Run is active")
            result = self._controller.expand_evidence_collection(
                self._active_run_id,
                str(arguments["workItemId"]),
                str(arguments["collectionId"]),
                cursor=arguments.get("cursor"),
                page_size=arguments.get("pageSize"),
                group_key=arguments.get("groupKey"),
            )
        elif name == "get_plugin_result":
            if self._terminal_run_id is None:
                raise PlatformContractError("RESULT_NOT_AVAILABLE", "No terminal release result is available")
            result = self._controller.get_result(
                self._terminal_run_id,
                str(arguments["sectionId"]),
                cursor=arguments.get("cursor"),
                page_size=arguments.get("pageSize"),
            )
            self._tracker["resultPaged"].add(self._terminal_run_id)
        else:
            raise PlatformContractError(
                "UNKNOWN_RELEASE_ACCEPTANCE_OPERATION",
                f"Release acceptance cannot invoke {name}",
            )
        return self._response(result)

    def close(self) -> None:
        self._controller.close()
        self._active_run_id = None


def _run_release_acceptance(
    registration: PluginRegistration, descriptor: Mapping[str, Any], target: Path,
    output_root: Path,
) -> list[PluginConformanceIssue]:
    acceptance_spec = descriptor.get("releaseAcceptance")
    if not acceptance_spec:
        return [_fixture_issue(
            "PLUGIN_INTERACTIVE_RELEASE_ACCEPTANCE_MISSING",
            "An interactive plugin does not declare releaseAcceptance.",
            "Publish an installed acceptance driver that completes semantic submission, resume, replay, and terminal publication.",
        )]
    driver, issues = _entry_point_release_acceptance(
        target, acceptance_spec, descriptor["pluginVersion"],
    )
    if issues or driver is None:
        return issues
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        registry = PluginRegistry((registration,))
        tracker: dict[str, Any] = {
            "resumed": set(), "replayed": set(), "resultPaged": set(),
            "domainResultSubmissions": 0, "reviewBatchSubmissions": 0,
        }
        result = driver(
            registration=registration,
            output_root=output_root,
            transport_factory=lambda root, **kwargs: _ReleaseAcceptanceTransport(
                root, registry, tracker, **kwargs,
            ),
        )
    except Exception as error:
        return [_fixture_issue(
            "PLUGIN_RELEASE_ACCEPTANCE_EXECUTION_FAILED",
            f"The installed release-acceptance driver failed: {error}",
            "Make the installed acceptance journey deterministic and satisfy every strict lifecycle assertion.",
        )]
    errors = sorted(
        schema_validator("plugin-release-acceptance.schema.json").iter_errors(result),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "root"
        return [_fixture_issue(
            "PLUGIN_RELEASE_ACCEPTANCE_RESULT_INVALID",
            f"The release-acceptance result is invalid at {location}: {errors[0].message}",
            "Return the frozen plugin-release-acceptance result contract.",
        )]

    expected = {
        contract.check_ref: contract
        for contract in registration.domain_result_contracts
    }
    if "common_review" in registration.result_features:
        expected = {check.ref: check for check in registration.manifest.checks}
    actual = {
        (item["checkId"], item["checkVersion"]): item
        for item in result["checks"]
    }
    if set(actual) != set(expected) or len(actual) != len(result["checks"]):
        return [_fixture_issue(
            "PLUGIN_RELEASE_ACCEPTANCE_COVERAGE_INCOMPLETE",
            "The release-acceptance result does not cover every interactive Check exactly once.",
            "Return one acceptance record for every interactive Check and contract version.",
        )]
    ledger_validator = schema_validator("platform-ledger.schema.json")
    common_review = "common_review" in registration.result_features
    submission_key = (
        "reviewBatchSubmissions" if common_review else "domainResultSubmissions"
    )
    forbidden_submission_key = (
        "domainResultSubmissions" if common_review else "reviewBatchSubmissions"
    )
    declared_submissions = 0
    for check_ref, contract in expected.items():
        del contract
        item = actual[check_ref]
        if submission_key not in item or forbidden_submission_key in item:
            return [_fixture_issue(
                "PLUGIN_RELEASE_ACCEPTANCE_RESULT_MODE_MISMATCH",
                f"The acceptance record does not use {submission_key} for this plugin mode.",
                f"Declare exactly {submission_key} for every covered Check.",
            )]
        declared_submissions += item[submission_key]
        ledger_run_ids: set[str] = set()
        observed_submissions = 0
        for relative in item["ledgerPaths"]:
            ledger_path = (output_root / relative).resolve()
            try:
                ledger_path.relative_to(output_root)
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
                return [_fixture_issue(
                    "PLUGIN_RELEASE_ACCEPTANCE_LEDGER_INVALID",
                    f"An acceptance ledger is unavailable or unsafe: {error}",
                    "Return only relative ledger paths created inside the assigned output root.",
                )]
            ledger_error = next(ledger_validator.iter_errors(ledger), None)
            if ledger_error is not None or ledger.get("status") != "completed":
                return [_fixture_issue(
                    "PLUGIN_RELEASE_ACCEPTANCE_LEDGER_INVALID",
                    "An acceptance ledger is malformed or is not terminal completed.",
                    "Complete and validate every acceptance Run ledger before returning.",
                )]
            if (
                ledger["run"]["plugin_id"] != registration.manifest.plugin_id
                or (ledger["run"]["check_id"], ledger["run"]["check_version"]) != check_ref
            ):
                return [_fixture_issue(
                    "PLUGIN_RELEASE_ACCEPTANCE_LEDGER_IDENTITY_MISMATCH",
                    "An acceptance ledger belongs to a different plugin or Check.",
                    "Return ledgers produced by the installed plugin and declared Check.",
                )]
            ledger_run_ids.add(ledger["run"]["run_id"])
            if common_review:
                coverage = JsonCoverageLedgerStore(ledger_path.parent).load(
                    ledger["run"]["run_id"],
                )
                if coverage is None or coverage.terminal_status != "completed":
                    return [_fixture_issue(
                        "PLUGIN_RELEASE_ACCEPTANCE_COVERAGE_INVALID",
                        "A common-review acceptance Run lacks terminal coverage.",
                        "Complete every generated review batch before publication.",
                    )]
                observed_submissions += sum(
                    verdict.status == "accepted" for verdict in coverage.verdicts
                )
            else:
                observed_submissions += len(ledger.get("decisions", ()))
        if (
            len(ledger_run_ids) != item["completedRuns"]
            or observed_submissions != item[submission_key]
        ):
            return [_fixture_issue(
                "PLUGIN_RELEASE_ACCEPTANCE_METRICS_MISMATCH",
                "Declared completed Run or semantic submission counts differ from the durable ledgers.",
                "Derive acceptance metrics from the exact terminal ledgers returned to the platform.",
            )]
        if not ledger_run_ids.intersection(tracker["resumed"]):
            return [_fixture_issue(
                "PLUGIN_RELEASE_ACCEPTANCE_RESUME_UNPROVEN",
                "The platform-owned acceptance transport did not observe a resumed Run for this Check.",
                "Close and explicitly resume at least one running acceptance Run.",
            )]
        if not ledger_run_ids.issubset(tracker["replayed"]):
            return [_fixture_issue(
                "PLUGIN_RELEASE_ACCEPTANCE_REPLAY_UNPROVEN",
                "The platform-owned acceptance transport did not observe terminal replay for every Run.",
                "Replay each terminal result through the platform-owned transport.",
            )]
        if not ledger_run_ids.issubset(tracker["resultPaged"]):
            return [_fixture_issue(
                "PLUGIN_RELEASE_ACCEPTANCE_PUBLICATION_UNPROVEN",
                "The platform-owned acceptance transport did not page every terminal result.",
                "Read a bounded result page from every published terminal Run.",
            )]
    if declared_submissions != tracker[submission_key]:
        return [_fixture_issue(
            "PLUGIN_RELEASE_ACCEPTANCE_METRICS_MISMATCH",
            "The acceptance driver semantic submission count differs from Host-observed submissions.",
            "Report the exact number of submissions accepted by the platform-owned transport.",
        )]
    return []


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
        check = check_for(registration, check_id, fixture.get("checkVersion"))
        bound = (
            bind_capability_provider(registration, check, scope, f"conformance:{fixture_id}")
            if check is not None else None
        )
        try:
            plugin = registration.create_plugin(bound)
            provider = (
                registration.create_decision_provider(bound)
                if "batch" in registration.execution_modes
                else _PacketDecisionProvider()
            )
            context = (
                bound.context if bound is not None
                else PlatformContext(f"conformance:{fixture_id}", registration.capabilities)
            )
            result = PlatformKernel().run(
                plugin,
                scope,
                check_id,
                provider,
                context,
                check_version=fixture.get("checkVersion"),
                provider_evidence_expectation=(
                    bound.evidence_expectation if bound is not None else None
                ),
            )
        finally:
            if bound is not None:
                bound.close()
    except Exception as error:
        return [_fixture_issue(
            "PLUGIN_FIXTURE_EXECUTION_FAILED",
            f"Fixture {fixture_id} raised outside the platform result contract: {error}",
            "Make fixture execution return a deterministic platform result.",
        )]

    lifecycle = inspect_plugin_lifecycle(
        registration, scope, check_id,
        PlatformContext(f"conformance:{fixture_id}", registration.capabilities),
        check_version=fixture.get("checkVersion"),
        decision_provider=provider,
    )
    if not lifecycle.passed:
        first = lifecycle.issues[0]
        return [_fixture_issue(
            "PLUGIN_FIXTURE_LIFECYCLE_FAILED",
            f"Fixture {fixture_id} violated {first.invariant}: {first.message}",
            first.next_action,
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
        semantic_review_path = package_root / descriptor["semanticReview"]
        try:
            expected_semantic_digest = hashlib.sha256(
                semantic_review_path.read_bytes()
            ).hexdigest()
        except OSError as error:
            issues.append(_package_issue(
                "PLUGIN_SEMANTIC_INSTRUCTIONS_UNAVAILABLE",
                f"The validated semantic instructions could not be read: {error}",
                "Keep the validated semantic instructions available to the isolated release worker.",
            ))
        else:
            semantic_resources = [
                (
                    contract.semantic_instructions_path,
                    contract.semantic_instructions_sha256,
                )
                for contract in registration.domain_result_contracts
            ]
            if "common_review" in registration.result_features:
                if (
                    registration.semantic_instructions_path is None
                    or registration.semantic_instructions_sha256 is None
                ):
                    issues.append(_package_issue(
                        "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_MISSING",
                        "The common-review registration does not freeze its semantic instructions.",
                        "Compile the plugin so its semantic instruction path and digest are registered.",
                    ))
                else:
                    semantic_resources.append((
                        registration.semantic_instructions_path,
                        registration.semantic_instructions_sha256,
                    ))
            for semantic_path, declared_digest in semantic_resources:
                installed_semantic_path = (target / semantic_path).resolve()
                try:
                    installed_semantic_path.relative_to(target)
                except ValueError:
                    issues.append(_package_issue(
                        "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_PATH_UNSAFE",
                        "The installed Agent contract semantic-instructions path escapes the isolated target.",
                        "Publish a package-relative semanticInstructions.path inside the wheel.",
                    ))
                    continue
                try:
                    installed_semantic_digest = hashlib.sha256(
                        installed_semantic_path.read_bytes()
                    ).hexdigest()
                except OSError:
                    issues.append(_package_issue(
                        "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_MISSING",
                        "The wheel does not contain the DomainResult contract semantic-instructions file.",
                        "Include semanticInstructions.path in the built wheel package data.",
                    ))
                    continue
                if declared_digest != installed_semantic_digest:
                    issues.append(_package_issue(
                        "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_DIGEST_MISMATCH",
                        "The installed semantic-instructions bytes do not match the registered digest.",
                        "Generate the digest from the exact semantic-instructions file included in the wheel.",
                    ))
                if expected_semantic_digest != installed_semantic_digest:
                    issues.append(_package_issue(
                        "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_MISMATCH",
                        "The installed semantic-instructions bytes differ from the statically validated release resource.",
                        "Build the wheel from the same reviewed semantic-instructions resource.",
                    ))
        if not issues and (
            registration.domain_result_contracts
            or "common_review" in registration.result_features
        ):
            issues.extend(_run_release_acceptance(
                registration, descriptor, target, result_path.parent / "acceptance",
            ))
        if not issues and "common_review" not in registration.result_features:
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
    package_source: str | Path | None = None,
    install_timeout_seconds: int = 120,
    fixture_timeout_seconds: int = 300,
) -> PluginConformanceReport:
    """Install a statically valid package in a temporary target and run fixtures."""
    root = Path(package_root).expanduser().resolve()
    wheel = root if root.suffix == ".whl" else None
    source = (
        Path(package_source).expanduser().resolve()
        if package_source is not None
        else (root if wheel is None else None)
    )
    if source is None:
        return PluginConformanceReport(root.name, (_package_issue(
            "PLUGIN_WHEEL_SOURCE_REQUIRED",
            "A wheel installation check requires its statically validated release source.",
            "Use assayer-plugin-release-check on the source root, or pass package_source with the wheel.",
        ),))
    _cleanup_build_artifacts(source)
    static_report = inspect_plugin_package(source)
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
        platform_source = Path(__file__).resolve().parents[1]
        # Provider distributions are platform inputs, not plugin source.  The
        # repository checkout keeps the browser provider beside the platform;
        # expose that installed provider source to the isolated worker so a
        # browser-backed ordinary plugin can exercise its real provider gate.
        provider_source = platform_source.parent / "packages" / "assayer-provider-browser" / "src"
        python_paths = [str(platform_source)]
        if provider_source.is_dir():
            python_paths.append(str(provider_source))
        isolated_env["PYTHONPATH"] = os.pathsep.join(python_paths)
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
                "--no-build-isolation", "--target", str(target), str(wheel or source),
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
                    "--no-build-isolation", "--target", str(target), str(wheel or source),
                ]
            else:
                uv = shutil.which("uv")
                if uv is not None:
                    command = [
                        uv, "pip", "install", "--python", python,
                        "--no-python-downloads", "--no-deps", "--no-index",
                        "--no-build-isolation", "--target", str(target), str(wheel or source),
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
            _cleanup_build_artifacts(source)
        try:
            worker = subprocess.run(
                [
                    python, "-m", "assayer_platform.installation_conformance",
                    "--worker-target", str(target),
                    "--worker-package", str(source),
                    "--worker-result", str(result_path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=source,
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
            schema_validator("plugin-conformance.schema.json").validate({
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
