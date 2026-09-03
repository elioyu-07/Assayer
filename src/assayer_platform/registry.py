"""Load and semantically validate domain-neutral plugin manifests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, RefResolver

from .contract import (
    PLATFORM_API_VERSION, CheckContract, ExecutionProfile,
    PlatformContractError, PluginManifest,
)


def _version_core(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _schema_root() -> Path:
    configured = os.environ.get("ASSAYER_RESOURCE_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve() / "schemas"
        if (candidate / "plugin-manifest.schema.json").is_file():
            return candidate
        raise PlatformContractError(
            "RESOURCE_UNAVAILABLE", "ASSAYER_RESOURCE_ROOT does not contain plugin manifest schemas",
        )
    candidates = (
        Path(sys.prefix) / "share" / "assayer" / "schemas",
        Path(sys.prefix) / "local" / "share" / "assayer" / "schemas",
        Path(__file__).resolve().parents[2] / "schemas",
    )
    for candidate in candidates:
        if (candidate / "plugin-manifest.schema.json").is_file():
            return candidate
    raise PlatformContractError("RESOURCE_UNAVAILABLE", "Plugin manifest schemas are unavailable")


def validate_plugin_manifest(value: Mapping[str, Any]) -> None:
    root = _schema_root()
    schema = json.loads((root / "plugin-manifest.schema.json").read_text(encoding="utf-8"))
    common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store={common["$id"]: common, "common.schema.json": common}),
    )
    error = next(validator.iter_errors(dict(value)), None)
    if error is not None:
        raise PlatformContractError("INVALID_PLUGIN_MANIFEST", error.message)
    required_api = _version_core(value["platformApiVersion"])
    current_api = _version_core(PLATFORM_API_VERSION)
    if required_api[0] != current_api[0] or required_api[1] > current_api[1]:
        raise PlatformContractError(
            "PLUGIN_API_INCOMPATIBLE",
            f"Plugin requires platform API {value['platformApiVersion']}; this Host provides {PLATFORM_API_VERSION}",
        )
    subject_kinds = set(value["subjectKinds"])
    seen: set[tuple[str, str]] = set()
    for check in value["checks"]:
        ref = check["checkId"], check["version"]
        if ref in seen:
            raise PlatformContractError("INVALID_PLUGIN_MANIFEST", "Plugin check references must be unique")
        seen.add(ref)
        if not set(check["subjectKinds"]).issubset(subject_kinds):
            raise PlatformContractError(
                "INVALID_PLUGIN_MANIFEST", "Check subject kinds must be declared by the plugin",
            )
        if check["capabilityMissingOutcome"] == "needs_review" and "needs_review" not in check["decisionStates"]:
            raise PlatformContractError(
                "INVALID_PLUGIN_MANIFEST", "A needs_review capability outcome requires needs_review as an allowed decision state",
            )


def load_plugin_manifest(source: str | Path | Mapping[str, Any]) -> PluginManifest:
    if isinstance(source, Mapping):
        value = dict(source)
    else:
        value = json.loads(Path(source).read_text(encoding="utf-8"))
    validate_plugin_manifest(value)
    checks = tuple(CheckContract(
        check_id=item["checkId"], version=item["version"],
        subject_kinds=tuple(item["subjectKinds"]), dimensions=tuple(item["dimensions"]),
        decision_states=tuple(item["decisionStates"]),
        required_evidence_kinds=tuple(item["requiredEvidenceKinds"]),
        required_capabilities=tuple(item["requiredCapabilities"]),
        capability_missing_outcome=item["capabilityMissingOutcome"],
        invalidation_signals=tuple(item["invalidationSignals"]),
    ) for item in value["checks"])
    profile = value["executionProfile"]
    execution = ExecutionProfile(
        discover_batching=profile["discoverBatching"], inspect_batching=profile["inspectBatching"],
        decision_batching=profile["decisionBatching"], parallelism=profile["parallelism"],
        cache_reuse=profile["cacheReuse"], checkpoint=profile["checkpoint"],
        max_batch_size=profile.get("maxBatchSize", 1), ordering=profile.get("ordering", "independent"),
        failure_splitting=profile.get("failureSplitting", "forbidden"),
    )
    return PluginManifest(
        plugin_id=value["pluginId"], version=value["version"],
        platform_api_version=value["platformApiVersion"], domains=tuple(value["domains"]),
        subject_kinds=tuple(value["subjectKinds"]), checks=checks, execution_profile=execution,
    )
