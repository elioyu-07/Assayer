"""SDK-only browser snapshot capability provider.

The Host injects a ``BrowserSnapshotSource`` implementation backed by its
browser session.  This package never imports Playwright or Host modules; it
only serializes the already-frozen SDK snapshot into provider Evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from assayer_plugin_sdk import (
    BrowserSnapshot,
    BrowserSnapshotSource,
    ProviderFact,
    ProviderFailure,
    ProviderRegistration,
    ProviderResponse,
    ProviderSourceSnapshot,
    load_provider_descriptor,
)


_DESCRIPTOR = load_provider_descriptor(Path(__file__).with_name("descriptor.json"))
BROWSER_RESULT_SCHEMA: dict[str, Any] = dict(_DESCRIPTOR.result_schema or {})


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class _FixtureBrowserSnapshotSource:
    """Deterministic source used only by installed-wheel release fixtures."""

    def __init__(self, snapshot: BrowserSnapshot) -> None:
        self._snapshot = snapshot

    def observe_snapshot(self) -> BrowserSnapshot:
        return self._snapshot


def browser_fixture_runtime(fixture: Mapping[str, Any]) -> BrowserSnapshotSource:
    """Build a fake SDK source for the isolated provider release gate.

    The fixture worker passes a detached, schema-validated fixture.  This
    function performs no browser I/O and is never used by production binding.
    """
    source_identity = fixture["sourceIdentity"]
    requested_state = fixture["stateDigest"]
    expected = fixture["expected"]
    state_digest = (
        "0" * 64
        if expected.get("status") == "failed"
        and expected.get("failureCode") == "stale_state"
        else requested_state
    )
    snapshot = BrowserSnapshot(
        visible_text="Orders\nFilter\nQuery\nReset",
        entrypoints=({"kind": "safe_action", "intent": "query"},),
        candidates=({"kind": "filter_region", "label": "Order filters"},),
        network_summary={"status": "not_observed"},
        route="/orders",
        state_kind="page",
        structure_summary={"fields": 1, "tables": 1},
        active_tab=None,
        dom_digest="d" * 64,
        visual_digest=None,
        state_digest=state_digest,
        url=source_identity,
        origin="https://example.test",
        title="Orders",
    )
    return _FixtureBrowserSnapshotSource(snapshot)


class BrowserSnapshotProvider:
    """Serialize one Host-owned frozen browser snapshot as provider Evidence."""

    descriptor = _DESCRIPTOR

    def __init__(self, runtime: BrowserSnapshotSource | None = None) -> None:
        self._runtime = runtime
        self._snapshots: dict[tuple[str, str], BrowserSnapshot] = {}

    def discover_sources(
        self, scope: Mapping[str, Any], context: Any,
    ) -> tuple[ProviderSourceSnapshot, ...]:
        """Freeze the current page once and return its Host source identity."""
        del context
        if not isinstance(self._runtime, BrowserSnapshotSource):
            raise RuntimeError("The Host did not provide a browser snapshot source.")
        try:
            snapshot = self._runtime.observe_snapshot()
        except Exception as error:
            raise RuntimeError("The browser snapshot source failed during discovery.") from error
        if not isinstance(snapshot, BrowserSnapshot) or snapshot.url is None:
            raise RuntimeError("The browser snapshot source returned an invalid snapshot.")
        requested_url = scope.get("url") if isinstance(scope, Mapping) else None
        if requested_url is not None and requested_url != snapshot.url:
            raise RuntimeError("The browser page no longer matches the requested source.")
        self._snapshots[(snapshot.url, snapshot.state_digest)] = snapshot
        return (ProviderSourceSnapshot(
            snapshot.url,
            snapshot.state_digest,
            {"title": snapshot.title, "route": snapshot.route, "stateKind": snapshot.state_kind},
        ),)

    @staticmethod
    def _payload(snapshot: BrowserSnapshot) -> dict[str, Any] | None:
        if snapshot.url is None or snapshot.origin is None or snapshot.title is None:
            return None
        return {
            "format": "browser_snapshot",
            "url": snapshot.url,
            "origin": snapshot.origin,
            "title": snapshot.title,
            "route": snapshot.route or "/",
            "stateKind": snapshot.state_kind,
            "visibleText": snapshot.visible_text,
            "entrypoints": _plain(snapshot.entrypoints),
            "candidates": _plain(snapshot.candidates),
            "networkSummary": _plain(snapshot.network_summary or {}),
            "structureSummary": _plain(snapshot.structure_summary or {}),
            "activeTab": snapshot.active_tab,
            "domDigest": snapshot.dom_digest,
            "visualDigest": snapshot.visual_digest,
            "stateDigest": snapshot.state_digest,
        }

    def collect(self, request: Any, context: Any) -> ProviderResponse:
        del context
        if not isinstance(self._runtime, BrowserSnapshotSource):
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "capability_unavailable",
                    "The Host did not provide a browser snapshot source.",
                ),
            )
        try:
            snapshot = self._snapshots.get(
                (request.source_identity, request.state_digest),
            )
            if snapshot is None:
                snapshot = self._runtime.observe_snapshot()
        except Exception:
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "source_error",
                    "The browser snapshot source failed to produce a snapshot.",
                ),
            )
        if not isinstance(snapshot, BrowserSnapshot):
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "source_error",
                    "The browser snapshot source returned an invalid snapshot.",
                ),
            )
        if snapshot.url != request.source_identity:
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "source_changed",
                    "The browser page no longer matches the requested source.",
                ),
            )
        if snapshot.state_digest != request.state_digest:
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "stale_state",
                    "The browser snapshot no longer matches the requested source state.",
                ),
            )
        payload = self._payload(snapshot)
        if payload is None:
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "source_error",
                    "The browser snapshot is missing verified page identity.",
                ),
            )
        try:
            Draft202012Validator(self.descriptor.result_schema).validate(payload)
        except Exception:
            return ProviderResponse(
                request.request_id,
                request.provider_id,
                request.provider_version,
                request.capability,
                "failed",
                failure=ProviderFailure(
                    "source_error",
                    "The browser snapshot violated its published result contract.",
                ),
            )
        return ProviderResponse(
            request.request_id,
            request.provider_id,
            request.provider_version,
            request.capability,
            "succeeded",
            facts=(ProviderFact(
                "browser_snapshot",
                request.source_identity,
                request.state_digest,
                payload,
            ),),
        )

    def close(self) -> None:
        """Release provider-local snapshot references; Host owns browser lifetime."""
        self._snapshots.clear()


def browser_registration() -> ProviderRegistration:
    return ProviderRegistration(
        BrowserSnapshotProvider.descriptor,
        # Keep ``runtime`` required in the factory signature so the SDK
        # forwards the Host-injected BrowserSnapshotSource.  Factories with an
        # optional-only parameter are intentionally invoked without an
        # argument by the SDK for providers that do not need a runtime.
        provider_factory=lambda runtime: BrowserSnapshotProvider(runtime),
    )


__all__ = [
    "BROWSER_RESULT_SCHEMA",
    "BrowserSnapshotProvider",
    "browser_fixture_runtime",
    "browser_registration",
]
