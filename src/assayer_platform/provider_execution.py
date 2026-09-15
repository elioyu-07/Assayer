"""Controlled execution boundary for negotiated capability providers."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import fields, is_dataclass
from collections.abc import Callable
from typing import Any, Mapping

from jsonschema import Draft202012Validator, RefResolver

from .capability_negotiation import CapabilityNegotiation
from .contract import (
    CheckContract,
    EvidenceRecord,
    PlatformContractError,
    ProviderCollectionResult,
    ProviderEvidenceExpectation,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    ProviderSourceSnapshot,
    WorkItem,
    _freeze,
)
from .provider_registry import ProviderRegistration
from .registry import schema_store


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


class ProviderRuntimeLease:
    """Explicitly transfer one opaque provider runtime lifetime to the Host.

    A plain runtime passed to :class:`BoundCapabilityProvider` remains owned by
    the embedding product.  Wrapping it in this lease is the opt-in signal that
    the bound provider must release the runtime when its Run ends.  This keeps
    existing embeddings compatible while giving per-Run resources, such as a
    browser Context, one deterministic owner.
    """

    def __init__(
        self,
        runtime: Any,
        release: Callable[[], None] | None = None,
    ) -> None:
        if release is None:
            release = getattr(runtime, "close", None)
        if not callable(release):
            raise TypeError("A provider runtime lease requires a release callback")
        self.runtime = runtime
        self._release = release
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._release()


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
        self._runtime_lease = runtime if isinstance(runtime, ProviderRuntimeLease) else None
        provider_runtime = self._runtime_lease.runtime if self._runtime_lease is not None else runtime
        provider = None
        try:
            provider = registration.create_provider(provider_runtime)
            if getattr(provider, "descriptor", None) != descriptor:
                raise PlatformContractError(
                    "PROVIDER_IDENTITY_MISMATCH",
                    "Provider factory returned an implementation with different registered metadata",
                )
            if not callable(getattr(provider, "collect", None)):
                raise PlatformContractError(
                    "PROVIDER_RUNTIME_INCOMPLETE",
                    "Provider implementation does not expose collect",
                )
            schemas = schema_store()
            schema = schemas["provider-execution.schema.json"]
            common = schemas["common.schema.json"]
            self._validator = Draft202012Validator(
                schema,
                resolver=RefResolver(
                    schema["$id"], schema,
                    store={common["$id"]: common, "common.schema.json": common},
                ),
            )
        except Exception:
            close = getattr(provider, "close", None)
            try:
                if callable(close):
                    close()
            finally:
                if self._runtime_lease is not None:
                    self._runtime_lease.close()
            raise
        self.provider = provider
        self._capabilities = {item.name: item for item in descriptor.capabilities}
        self._failure_policy = {
            str(item["code"]): str(item["retry"]) for item in descriptor.failure_policy
        }
        self._results: dict[str, ProviderCollectionResult] = {}
        self._source_discoveries: dict[str, tuple[ProviderSourceSnapshot, ...]] = {}
        self._issued_evidence: dict[str, EvidenceRecord] = {}
        self._lock = threading.Lock()
        self._source_lock = threading.Lock()
        self._active = 0
        self._request_count = 0
        self._summed_request_duration_ms = 0
        self._closed = False
        self._close_lock = threading.Lock()

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
        scope: Mapping[str, Any] | None = None,
    ) -> ProviderCollectionResult:
        """Collect once and return only Host-validated Evidence or a safe failure.

        ``scope`` overrides the Run-level provider scope for this request after
        validating it against the provider's registered business-input schema;
        it lets a plugin read distinct per-work-item sources under one bound
        provider.
        """
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
        request = self._request(work_item, check, capability, scope)
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

    def discover_sources(
        self,
        capability: str,
        scope: Mapping[str, Any] | None = None,
    ) -> tuple[ProviderSourceSnapshot, ...]:
        """Discover and freeze provider sources once for this bound Run.

        Source discovery is an optional provider extension.  The Host caches
        its result by capability and validated provider scope, so inspection
        and resume code can reuse the same frozen source identities without
        asking the provider to reopen the source on every batch.
        """
        if capability not in self.context.capabilities:
            raise PlatformContractError(
                "PROVIDER_CAPABILITY_NOT_GRANTED",
                "The provider capability is outside the negotiated context",
            )
        request_scope = self.scope if scope is None else self._validated_scope(scope)
        try:
            cache_key = _digest({"capability": capability, "scope": request_scope})
        except (TypeError, ValueError) as error:
            raise PlatformContractError(
                "PROVIDER_SOURCE_DISCOVERY_INVALID",
                "Provider source scope is not deterministic JSON data",
            ) from error
        with self._source_lock:
            cached = self._source_discoveries.get(cache_key)
            if cached is not None:
                return cached
            discover = getattr(self.provider, "discover_sources", None)
            if not callable(discover):
                raise PlatformContractError(
                    "PROVIDER_SOURCE_DISCOVERY_UNAVAILABLE",
                    "The selected provider does not expose Host source discovery",
                )
            try:
                value = discover(request_scope, self.context)
            except PlatformContractError:
                raise
            except Exception as error:
                raise PlatformContractError(
                    "PROVIDER_SOURCE_DISCOVERY_FAILED",
                    "The provider failed to discover source snapshots",
                ) from error
            if isinstance(value, ProviderSourceSnapshot):
                snapshots = (value,)
            else:
                try:
                    snapshots = tuple(value)
                except TypeError as error:
                    raise PlatformContractError(
                        "PROVIDER_SOURCE_DISCOVERY_INVALID",
                        "Provider source discovery must return a sequence of snapshots",
                    ) from error
            if any(not isinstance(item, ProviderSourceSnapshot) for item in snapshots):
                raise PlatformContractError(
                    "PROVIDER_SOURCE_DISCOVERY_INVALID",
                    "Provider source discovery returned an invalid snapshot",
                )
            identities = tuple(item.source_identity for item in snapshots)
            if len(identities) != len(set(identities)):
                raise PlatformContractError(
                    "PROVIDER_SOURCE_DISCOVERY_INVALID",
                    "Provider source identities must be unique within one scope",
                )
            try:
                json.dumps(
                    _plain([item.metadata for item in snapshots]),
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError) as error:
                raise PlatformContractError(
                    "PROVIDER_SOURCE_DISCOVERY_INVALID",
                    "Provider source metadata must be finite JSON data",
                ) from error
            frozen = tuple(snapshots)
            self._source_discoveries[cache_key] = frozen
            return frozen

    def discover_work_items(
        self,
        check: CheckContract,
        capability: str,
        scope: Mapping[str, Any] | None = None,
    ) -> tuple[WorkItem, ...]:
        """Turn provider source snapshots into Host-owned WorkItems."""
        if len(check.subject_kinds) != 1:
            raise PlatformContractError(
                "PROVIDER_SOURCE_DISCOVERY_INVALID",
                "A provider-discovered Check must declare exactly one subject kind",
            )
        snapshots = self.discover_sources(capability, scope)
        descriptor = self.registration.descriptor
        work_items: list[WorkItem] = []
        for snapshot in snapshots:
            identity_material = "\x1f".join((
                descriptor.provider_id, descriptor.version, capability,
                snapshot.source_identity,
            ))
            item_id = "provider-source:" + hashlib.sha256(
                identity_material.encode("utf-8"),
            ).hexdigest()[:24]
            metadata = {
                "providerId": descriptor.provider_id,
                "providerVersion": descriptor.version,
                "capability": capability,
                "sourceMetadata": _plain(snapshot.metadata),
            }
            work_items.append(WorkItem(
                item_id,
                check.subject_kinds[0],
                snapshot.source_identity,
                snapshot.state_digest,
                metadata,
            ))
        return tuple(work_items)

    def issued_evidence(self) -> dict[str, EvidenceRecord]:
        """Return the exact Evidence the Host produced for this bound provider.

        The Kernel validates a packet's provider-bound Evidence against this
        ledger, so a plugin cannot fabricate or tamper with provider Evidence it
        never received from ``collect``.
        """
        with self._lock:
            return dict(self._issued_evidence)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._source_discoveries.clear()
        close = getattr(self.provider, "close", None)
        try:
            if callable(close):
                close()
        finally:
            if self._runtime_lease is not None:
                self._runtime_lease.close()

    def _request(
        self, work_item: WorkItem, check: CheckContract, capability: str,
        scope: Mapping[str, Any] | None = None,
    ) -> ProviderRequest:
        descriptor = self.registration.descriptor
        request_scope = self.scope if scope is None else self._validated_scope(scope)
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
            "scope": request_scope,
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
            request_scope,
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

    def _validated_scope(self, scope: Mapping[str, Any]) -> Mapping[str, Any]:
        """Validate a per-request scope against the provider business-input schema."""
        material = _plain(scope)
        if not isinstance(material, Mapping):
            raise PlatformContractError(
                "PROVIDER_SCOPE_INVALID",
                "Provider request scope must be an object",
            )
        descriptor = self.registration.descriptor
        error = next(
            Draft202012Validator(_plain(descriptor.scope_schema)).iter_errors(material),
            None,
        )
        if error is not None:
            raise PlatformContractError(
                "PROVIDER_SCOPE_INVALID",
                "Provider request scope does not satisfy the registered business-input schema",
            )
        return _freeze(dict(material))

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
            failure = response.failure
            if failure is None:
                raise PlatformContractError(
                    "PROVIDER_RESPONSE_INVALID",
                    "A failed provider response must include failure details",
                )
            if failure.code not in self._failure_policy:
                raise PlatformContractError(
                    "PROVIDER_FAILURE_UNCLASSIFIED",
                    "Provider returned a failure outside its declared taxonomy",
                )
            return ProviderCollectionResult(
                request,
                failure=ProviderFailure(
                    failure.code,
                    _SAFE_FAILURE_MESSAGES[failure.code],
                ),
                retry=self._failure_policy[failure.code],
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
            result_schema = self.registration.descriptor.result_schema
            if result_schema is not None:
                error = next(
                    Draft202012Validator(_plain(result_schema)).iter_errors(_plain(fact.payload)),
                    None,
                )
                if error is not None:
                    raise PlatformContractError(
                        "PROVIDER_RESULT_INVALID",
                        "Provider fact payload does not satisfy the published result contract",
                    )
            evidence_digest = _digest({
                "requestId": request.request_id,
                "index": index,
                "fact": fact,
            })
            record = EvidenceRecord(
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
            )
            with self._lock:
                self._issued_evidence[record.evidence_id] = record
            evidence.append(record)
        return ProviderCollectionResult(request, tuple(evidence))


__all__ = [
    "BoundCapabilityProvider",
    "ProviderRuntimeLease",
]
