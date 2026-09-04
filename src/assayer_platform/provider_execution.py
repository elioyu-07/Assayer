"""Controlled execution boundary for negotiated capability providers."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import fields, is_dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator, RefResolver

from .capability_negotiation import CapabilityNegotiation
from .contract import (
    CheckContract,
    EvidenceRecord,
    PlatformContractError,
    ProviderCollectionResult,
    ProviderEvidenceExpectation,
    ProviderFact,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    WorkItem,
    _freeze,
)
from .provider_registry import ProviderRegistration
from .registry import _schema_root


_SAFE_FAILURE_MESSAGES = {
    "capability_unavailable": "The provider capability is unavailable for this request.",
    "authorization_denied": "The provider did not authorize the requested scope.",
    "timeout": "The provider request exceeded its negotiated operation timeout.",
    "budget_exceeded": "The provider request exceeded a negotiated resource budget.",
    "source_changed": "The source changed while the provider was collecting facts.",
    "stale_state": "The provider facts no longer describe the requested source state.",
    "source_error": "The source could not provide the requested facts.",
    "result_unknown": "The provider cannot prove whether the request completed.",
}


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_payload(request: ProviderRequest) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "requestId": request.request_id,
        "idempotencyKey": request.idempotency_key,
        "runId": request.run_id,
        "workItemId": request.work_item_id,
        "checkId": request.check_id,
        "checkVersion": request.check_version,
        "providerId": request.provider_id,
        "providerVersion": request.provider_version,
        "capability": request.capability,
        "sourceIdentity": request.source_identity,
        "stateDigest": request.state_digest,
        "scope": _plain(request.scope),
        "limits": _plain(request.limits),
    }


def _response_payload(response: ProviderResponse) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "requestId": response.request_id,
        "providerId": response.provider_id,
        "providerVersion": response.provider_version,
        "capability": response.capability,
        "status": response.status,
        "facts": [{
            "kind": fact.kind,
            "sourceIdentity": fact.source_identity,
            "stateDigest": fact.state_digest,
            "payload": _plain(fact.payload),
        } for fact in response.facts],
        "failure": None if response.failure is None else {
            "code": response.failure.code,
            "message": response.failure.message,
        },
    }


class BoundCapabilityProvider:
    """Execute one provider only inside a ready negotiated capability profile."""

    def __init__(
        self,
        registration: ProviderRegistration,
        negotiation: CapabilityNegotiation,
        *,
        run_id: str,
        scope: Mapping[str, Any],
        runtime: Any = None,
    ) -> None:
        descriptor = registration.descriptor
        if negotiation.status != "ready":
            raise PlatformContractError(
                "CAPABILITY_NEGOTIATION_BLOCKED",
                "A provider cannot be constructed from a blocked capability negotiation",
            )
        if (
            negotiation.provider_id != descriptor.provider_id
            or negotiation.provider_version != descriptor.version
        ):
            raise PlatformContractError(
                "PROVIDER_IDENTITY_MISMATCH",
                "Capability negotiation identity does not match the selected provider",
            )
        self.registration = registration
        self.negotiation = negotiation
        self.context = negotiation.context(run_id)
        self.scope = _freeze(dict(scope))
        self.provider = registration.create_provider(runtime)
        if getattr(self.provider, "descriptor", None) != descriptor:
            raise PlatformContractError(
                "PROVIDER_IDENTITY_MISMATCH",
                "Provider factory returned an implementation with different registered metadata",
            )
        if not callable(getattr(self.provider, "collect", None)):
            raise PlatformContractError(
                "PROVIDER_RUNTIME_INCOMPLETE",
                "Provider implementation does not expose collect",
            )
        root = _schema_root()
        schema = json.loads((root / "provider-execution.schema.json").read_text(encoding="utf-8"))
        common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            schema,
            resolver=RefResolver(
                schema["$id"], schema,
                store={common["$id"]: common, "common.schema.json": common},
            ),
        )
        self._capabilities = {item.name: item for item in descriptor.capabilities}
        self._failure_policy = {
            str(item["code"]): str(item["retry"]) for item in descriptor.failure_policy
        }
        self._results: dict[str, ProviderCollectionResult] = {}
        self._lock = threading.Lock()
        self._active = 0
        self._request_count = 0
        self._summed_request_duration_ms = 0

    @property
    def evidence_expectation(self) -> ProviderEvidenceExpectation:
        descriptor = self.registration.descriptor
        return ProviderEvidenceExpectation(
            self.context.run_id,
            descriptor.provider_id,
            descriptor.version,
            {
                capability: self._capabilities[capability].evidence_kinds
                for capability in self.negotiation.granted
            },
            descriptor.algorithm_versions,
        )

    def collect(
        self,
        work_item: WorkItem,
        check: CheckContract,
        capability: str,
    ) -> ProviderCollectionResult:
        """Collect once and return only Host-validated Evidence or a safe failure."""
        if capability not in check.required_capabilities:
            raise PlatformContractError(
                "PROVIDER_CAPABILITY_UNREQUESTED",
                "The Check did not request this provider capability",
            )
        if capability not in self.context.capabilities:
            raise PlatformContractError(
                "PROVIDER_CAPABILITY_NOT_GRANTED",
                "The provider capability is outside the negotiated context",
            )
        request = self._request(work_item, check, capability)
        replay = self._results.get(request.idempotency_key)
        if replay is not None:
            return replay

        with self._lock:
            replay = self._results.get(request.idempotency_key)
            if replay is not None:
                return replay
            if self._active >= int(self.context.limits["maxConcurrency"]):
                raise PlatformContractError(
                    "PROVIDER_CONCURRENCY_EXCEEDED",
                    "The provider has reached its negotiated concurrency limit",
                )
            self._active += 1
        started = time.monotonic()
        try:
            try:
                response = self.provider.collect(request, self.context)
            except Exception as error:
                raise PlatformContractError(
                    "PROVIDER_EXECUTION_FAILED",
                    "The provider failed without a classified response",
                ) from error
        finally:
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            with self._lock:
                self._active -= 1
                self._request_count += 1
                self._summed_request_duration_ms += elapsed_ms

        if elapsed_ms > int(self.context.limits["timeoutMs"]):
            result = ProviderCollectionResult(
                request,
                failure=ProviderFailure("timeout", _SAFE_FAILURE_MESSAGES["timeout"]),
                retry=self._failure_policy["timeout"],
            )
        else:
            result = self._validate_response(request, work_item, response)
        self._results[request.idempotency_key] = result
        return result

    def performance_metrics(self) -> dict[str, int]:
        """Return measured provider work without exposing request content."""
        with self._lock:
            return {
                "providerTimingAvailable": 1,
                "providerRequestCount": self._request_count,
                "providerRequestDurationMs": self._summed_request_duration_ms,
            }

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()

    def _request(
        self, work_item: WorkItem, check: CheckContract, capability: str,
    ) -> ProviderRequest:
        descriptor = self.registration.descriptor
        identity_material = {
            "runId": self.context.run_id,
            "workItemId": work_item.work_item_id,
            "workItemIdentity": work_item.identity,
            "workItemStateDigest": work_item.state_digest,
            "checkId": check.check_id,
            "checkVersion": check.version,
            "providerId": descriptor.provider_id,
            "providerVersion": descriptor.version,
            "capability": capability,
            "scope": self.scope,
            "limits": dict(self.context.limits),
        }
        try:
            idempotency_key = _digest(identity_material)
        except (TypeError, ValueError) as error:
            raise PlatformContractError(
                "PROVIDER_REQUEST_INVALID",
                "Provider request scope is not deterministic JSON data",
            ) from error
        request = ProviderRequest(
            f"provider-request:{idempotency_key[:32]}",
            idempotency_key,
            self.context.run_id,
            work_item.work_item_id,
            check.check_id,
            check.version,
            descriptor.provider_id,
            descriptor.version,
            capability,
            work_item.identity,
            work_item.state_digest,
            self.scope,
            self.context.limits,
        )
        try:
            self._validator.validate({"request": _request_payload(request)})
        except Exception as error:
            raise PlatformContractError(
                "PROVIDER_REQUEST_INVALID",
                "Provider request does not satisfy the execution contract",
            ) from error
        return request

    def _validate_response(
        self,
        request: ProviderRequest,
        work_item: WorkItem,
        response: Any,
    ) -> ProviderCollectionResult:
        if not isinstance(response, ProviderResponse):
            raise PlatformContractError(
                "PROVIDER_RESPONSE_INVALID",
                "Provider collect must return a ProviderResponse",
            )
        try:
            self._validator.validate({"response": _response_payload(response)})
        except Exception as error:
            raise PlatformContractError(
                "PROVIDER_RESPONSE_INVALID",
                "Provider response does not satisfy the execution contract",
            ) from error
        if (
            response.request_id != request.request_id
            or response.provider_id != request.provider_id
            or response.provider_version != request.provider_version
            or response.capability != request.capability
        ):
            raise PlatformContractError(
                "PROVIDER_RESPONSE_IDENTITY_MISMATCH",
                "Provider response identity does not match the Host request",
            )
        if response.status == "failed":
            assert response.failure is not None
            if response.failure.code not in self._failure_policy:
                raise PlatformContractError(
                    "PROVIDER_FAILURE_UNCLASSIFIED",
                    "Provider returned a failure outside its declared taxonomy",
                )
            return ProviderCollectionResult(
                request,
                failure=ProviderFailure(
                    response.failure.code,
                    _SAFE_FAILURE_MESSAGES[response.failure.code],
                ),
                retry=self._failure_policy[response.failure.code],
            )

        capability = self._capabilities[request.capability]
        if len(response.facts) > int(request.limits["maxItems"]):
            return ProviderCollectionResult(
                request,
                failure=ProviderFailure(
                    "budget_exceeded", _SAFE_FAILURE_MESSAGES["budget_exceeded"],
                ),
                retry=self._failure_policy["budget_exceeded"],
            )
        try:
            response_bytes = len(json.dumps(
                _response_payload(response), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8"))
        except (TypeError, ValueError) as error:
            raise PlatformContractError(
                "PROVIDER_RESPONSE_INVALID",
                "Provider response payload is not deterministic JSON data",
            ) from error
        if response_bytes > int(request.limits["maxBytes"]):
            return ProviderCollectionResult(
                request,
                failure=ProviderFailure(
                    "budget_exceeded", _SAFE_FAILURE_MESSAGES["budget_exceeded"],
                ),
                retry=self._failure_policy["budget_exceeded"],
            )

        evidence = []
        for index, fact in enumerate(response.facts):
            if fact.kind not in capability.evidence_kinds:
                raise PlatformContractError(
                    "PROVIDER_EVIDENCE_KIND_UNDECLARED",
                    "Provider returned an Evidence kind not declared for the capability",
                )
            if fact.source_identity != work_item.identity:
                raise PlatformContractError(
                    "PROVIDER_SOURCE_MISMATCH",
                    "Provider fact source identity does not match the WorkItem",
                )
            if fact.state_digest != work_item.state_digest:
                raise PlatformContractError(
                    "PROVIDER_STATE_MISMATCH",
                    "Provider fact state digest does not match the WorkItem",
                )
            evidence_digest = _digest({
                "requestId": request.request_id,
                "index": index,
                "fact": fact,
            })
            evidence.append(EvidenceRecord(
                f"provider-evidence:{evidence_digest[:32]}",
                work_item.work_item_id,
                check_id=request.check_id,
                check_version=request.check_version,
                kind=fact.kind,
                source_identity=fact.source_identity,
                payload=fact.payload,
                run_id=request.run_id,
                provider_request_id=request.request_id,
                provider_id=request.provider_id,
                provider_version=request.provider_version,
                capability=request.capability,
                source_state_digest=fact.state_digest,
                algorithm_versions=self.registration.descriptor.algorithm_versions,
            ))
        return ProviderCollectionResult(request, tuple(evidence))


__all__ = [
    "BoundCapabilityProvider",
]
