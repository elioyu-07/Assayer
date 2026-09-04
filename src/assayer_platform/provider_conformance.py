"""Reusable registration and release checks for capability providers."""

from __future__ import annotations

import argparse
import importlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .contract import CapabilityProviderDescriptor, PlatformContractError
from .provider_registry import (
    ProviderRegistration,
    ProviderRegistry,
    _descriptor_payload,
    validate_provider_descriptor,
)


_REQUIRED_FAILURES = frozenset({
    "capability_unavailable",
    "authorization_denied",
    "timeout",
    "budget_exceeded",
    "source_changed",
    "stale_state",
    "source_error",
    "result_unknown",
})


@dataclass(frozen=True)
class ProviderConformanceIssue:
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
class ProviderConformanceReport:
    provider_id: str
    issues: tuple[ProviderConformanceIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "providerId": self.provider_id,
            "status": "passed" if self.passed else "failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _issue(
    code: str, invariant: str, message: str, next_action: str,
) -> ProviderConformanceIssue:
    return ProviderConformanceIssue(code, invariant, message, next_action)


def inspect_provider_registration(
    registration: Any, *, construct_implementation: bool = False,
) -> ProviderConformanceReport:
    if not isinstance(registration, ProviderRegistration):
        return ProviderConformanceReport("unknown", (_issue(
            "INVALID_PROVIDER_REGISTRATION",
            "CPV1-REGISTRATION",
            "The release entry point did not provide a ProviderRegistration.",
            "Export one ProviderRegistration or a zero-argument factory that returns one.",
        ),))

    descriptor = registration.descriptor
    provider_id = (
        descriptor.provider_id
        if isinstance(descriptor, CapabilityProviderDescriptor) else "unknown"
    )
    issues: list[ProviderConformanceIssue] = []
    if not isinstance(descriptor, CapabilityProviderDescriptor):
        issues.append(_issue(
            "INVALID_PROVIDER_DESCRIPTOR",
            "CPV1-DESCRIPTOR",
            "The registration descriptor is not a validated CapabilityProviderDescriptor.",
            "Load the descriptor with load_provider_descriptor before registration.",
        ))
        return ProviderConformanceReport(provider_id, tuple(issues))
    try:
        validate_provider_descriptor(_descriptor_payload(descriptor))
    except PlatformContractError as error:
        issues.append(_issue(
            error.code,
            "CPV1-DESCRIPTOR",
            error.message,
            "Correct and reload the provider descriptor before packaging.",
        ))

    capabilities = [item.name for item in descriptor.capabilities]
    if len(capabilities) != len(set(capabilities)):
        issues.append(_issue(
            "PROVIDER_CAPABILITY_DUPLICATE",
            "CPV1-CAPABILITY-IDENTITY",
            "Provider capability names must be unique within one descriptor.",
            "Merge duplicate capability declarations or assign distinct capability names.",
        ))
    controlled = any(item.access_mode == "controlled_action" for item in descriptor.capabilities)
    if controlled and descriptor.authorization.get("userScopeRequired") is not True:
        issues.append(_issue(
            "PROVIDER_AUTHORIZATION_INCOMPLETE",
            "CPV1-AUTHORIZATION",
            "A controlled-action capability requires explicit user scope.",
            "Set authorization.userScopeRequired=true and enforce the granted scope at runtime.",
        ))

    failures = [item.get("code") for item in descriptor.failure_policy]
    if len(failures) != len(set(failures)):
        issues.append(_issue(
            "PROVIDER_FAILURE_DUPLICATE",
            "CPV1-FAILURE-SEMANTICS",
            "Provider failure codes must be declared exactly once.",
            "Keep one retry policy for each declared provider failure code.",
        ))
    missing_failures = sorted(_REQUIRED_FAILURES - set(failures))
    if missing_failures:
        issues.append(_issue(
            "PROVIDER_FAILURE_POLICY_INCOMPLETE",
            "CPV1-FAILURE-SEMANTICS",
            "Provider failure policy omits required outcomes: " + ", ".join(missing_failures),
            "Declare every capability-provider v1 failure outcome with an explicit retry policy.",
        ))
    result_unknown = next(
        (item for item in descriptor.failure_policy if item.get("code") == "result_unknown"),
        None,
    )
    if result_unknown is not None and result_unknown.get("retry") != "resolve_unknown_first":
        issues.append(_issue(
            "PROVIDER_UNKNOWN_RETRY_UNSAFE",
            "CPV1-FAILURE-SEMANTICS",
            "A result_unknown outcome must be reconciled before any retry.",
            "Set result_unknown retry to resolve_unknown_first.",
        ))
    if not descriptor.algorithm_versions:
        issues.append(_issue(
            "PROVIDER_ALGORITHM_IDENTITY_MISSING",
            "CPV1-EVIDENCE-IDENTITY",
            "Provider Evidence cannot freeze identity semantics without an algorithm version.",
            "Declare the source identity, state digest, normalization, or equivalent algorithm version used by Evidence.",
        ))

    if not callable(registration.provider_factory):
        issues.append(_issue(
            "PROVIDER_RUNTIME_UNAVAILABLE",
            "CPV1-RUNTIME-FACTORY",
            "The registration does not expose a callable provider factory.",
            "Provide provider_factory that constructs the registered provider adapter.",
        ))
    elif construct_implementation:
        try:
            provider = registration.create_provider(None)
        except Exception:
            issues.append(_issue(
                "PROVIDER_INITIALIZATION_FAILED",
                "CPV1-RUNTIME-IMPLEMENTATION",
                "The provider factory failed during release validation.",
                "Make provider construction deterministic and defer live source access until execution.",
            ))
        else:
            if getattr(provider, "descriptor", None) != descriptor:
                issues.append(_issue(
                    "PROVIDER_IDENTITY_MISMATCH",
                    "CPV1-RUNTIME-IMPLEMENTATION",
                    "The provider factory returned an implementation with different descriptor identity.",
                    "Return the implementation declared by this provider registration.",
                ))
            if not callable(getattr(provider, "collect", None)):
                issues.append(_issue(
                    "PROVIDER_RUNTIME_INCOMPLETE",
                    "CPV1-RUNTIME-IMPLEMENTATION",
                    "The provider implementation does not expose a callable collect operation.",
                    "Implement collect for bounded Host-created provider requests.",
                ))
            close = getattr(provider, "close", None)
            if close is not None and not callable(close):
                issues.append(_issue(
                    "PROVIDER_RUNTIME_INCOMPLETE",
                    "CPV1-RUNTIME-IMPLEMENTATION",
                    "The optional provider close attribute is not callable.",
                    "Implement close as a callable or remove the attribute.",
                ))
    return ProviderConformanceReport(provider_id, tuple(issues))


def inspect_provider_registrations(
    registrations: Iterable[Any], *, construct_implementations: bool = False,
) -> tuple[ProviderConformanceReport, ...]:
    return tuple(
        inspect_provider_registration(
            registration, construct_implementation=construct_implementations,
        )
        for registration in registrations
    )


def require_provider_registration_conformance(
    registration: Any, *, construct_implementation: bool = False,
) -> ProviderConformanceReport:
    report = inspect_provider_registration(
        registration, construct_implementation=construct_implementation,
    )
    if not report.passed:
        first = report.issues[0]
        raise PlatformContractError(
            first.code,
            f"{first.message} Contract: {first.invariant}. Next action: {first.next_action}",
        )
    return report


def _resolve_registrations(spec: str) -> tuple[Any, ...]:
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("Registration must use module:attribute format")
    value = getattr(importlib.import_module(module_name), attribute_name)
    if callable(value) and not isinstance(value, ProviderRegistration):
        value = value()
    if not isinstance(value, ProviderRegistration):
        value = getattr(value, "registration", value)
    if isinstance(value, ProviderRegistry):
        return value.list()
    return (value,)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Assayer provider registrations before packaging or publication",
    )
    parser.add_argument(
        "registration", nargs="+", metavar="MODULE:ATTRIBUTE",
        help="Import path for a ProviderRegistration or zero-argument registration factory",
    )
    args = parser.parse_args(argv)
    reports: list[ProviderConformanceReport] = []
    for spec in args.registration:
        try:
            reports.extend(
                inspect_provider_registration(
                    registration, construct_implementation=True,
                )
                for registration in _resolve_registrations(spec)
            )
        except Exception:
            reports.append(ProviderConformanceReport(spec, (_issue(
                "PROVIDER_RELEASE_LOAD_FAILED",
                "CPV1-REGISTRATION",
                "The provider registration could not be loaded.",
                "Fix the import path and registration initialization before packaging.",
            ),)))
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "providers": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
