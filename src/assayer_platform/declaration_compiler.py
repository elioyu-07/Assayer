"""Compile declaration-only ordinary plugin sources into platform artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from assayer_plugin_sdk.contract import PlatformContractError
from assayer_plugin_sdk.browser import BrowserSnapshot
from .compiled_plugin_contract import COMPILED_PLUGIN_CONTRACT, contract_digest
from .yaml_subset import load_yaml_subset


_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_PLUGIN_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,255}$")
_CHECK_ID = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
_DIMENSION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SUBJECT_KIND = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError("INVALID_PLUGIN_DECLARATION", f"{label} must be nonempty")
    return value.strip()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformContractError("INVALID_PLUGIN_DECLARATION", f"{label} must be an object")
    return dict(value)


@dataclass(frozen=True)
class CompiledPluginArtifact:
    source_root: Path
    output_root: Path
    plugin_id: str
    version: str
    artifacts: tuple[str, ...]
    contract_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pluginId": self.plugin_id,
            "pluginVersion": self.version,
            "outputRoot": str(self.output_root),
            "artifacts": list(self.artifacts),
            "contractDigest": self.contract_digest,
        }


def compile_plugin_contract(
    source: str | Path, destination: str | Path,
) -> CompiledPluginArtifact:
    """Compile one declaration-only plugin into a buildable source tree."""
    root = Path(source).expanduser().resolve()
    output = Path(destination).expanduser().resolve()
    plugin = _mapping(load_yaml_subset(root / "plugin.yaml"), "plugin.yaml")
    required_plugin = {"id", "name", "description", "version", "input", "checks", "instructions"}
    if not required_plugin.issubset(plugin) or set(plugin) - (required_plugin | {"subject_kind"}):
        raise PlatformContractError(
            "INVALID_PLUGIN_DECLARATION", "plugin.yaml fields must match the ordinary author contract",
        )
    for key in required_plugin:
        plugin[key] = _text(plugin[key], f"plugin {key}")
    if "subject_kind" in plugin:
        plugin["subject_kind"] = _text(plugin["subject_kind"], "plugin subject_kind")
        if _SUBJECT_KIND.fullmatch(plugin["subject_kind"]) is None:
            raise PlatformContractError(
                "INVALID_PLUGIN_DECLARATION", "Plugin subject_kind must be a lowercase identifier",
            )
    if _PLUGIN_ID.fullmatch(plugin["id"]) is None or _SEMVER.fullmatch(plugin["version"]) is None:
        raise PlatformContractError(
            "INVALID_PLUGIN_DECLARATION", "Plugin ID or business version is invalid",
        )
    if plugin["input"] not in {"document", "markdown", "browser_snapshot"}:
        raise PlatformContractError(
            "INVALID_PLUGIN_DECLARATION",
            "Simple compiler supports document, markdown, or browser_snapshot input",
        )
    checks_path = (root / plugin["checks"]).resolve()
    instructions_path = (root / plugin["instructions"]).resolve()
    try:
        checks_path.relative_to(root)
        instructions_path.relative_to(root)
    except ValueError as error:
        raise PlatformContractError(
            "INVALID_PLUGIN_DECLARATION", "Plugin resources must remain inside the source root",
        ) from error
    checks_source = _mapping(load_yaml_subset(checks_path), "checks.yaml")
    if set(checks_source) != {"checks"} or not isinstance(checks_source["checks"], list) or not checks_source["checks"]:
        raise PlatformContractError("INVALID_PLUGIN_DECLARATION", "checks.yaml requires a checks array")
    normalized_checks: list[dict[str, Any]] = []
    for raw in checks_source["checks"]:
        check = _mapping(raw, "Check")
        required = {
            "id", "title", "description", "applicability", "default_severity",
            "recommendation", "unknown_when",
        }
        if not required.issubset(check) or set(check) - (required | {"detect", "dimensions"}):
            raise PlatformContractError(
                "INVALID_PLUGIN_DECLARATION", "Every Check must declare only domain-owned fields",
            )
        for key in required:
            check[key] = _text(check[key], f"Check {key}")
        if _CHECK_ID.fullmatch(check["id"]) is None or check["default_severity"] not in {"P0", "P1", "P2", "P3", "P4"}:
            raise PlatformContractError("INVALID_PLUGIN_DECLARATION", "Check ID or severity is invalid")
        raw_dimensions = check.get("dimensions", [check["id"]])
        if (
            not isinstance(raw_dimensions, list)
            or not raw_dimensions
            or any(
                not isinstance(item, str)
                or not item.strip()
                or _DIMENSION_ID.fullmatch(item.strip()) is None
                for item in raw_dimensions
            )
            or len(raw_dimensions) != len({item.strip() for item in raw_dimensions})
        ):
            raise PlatformContractError(
                "INVALID_PLUGIN_DECLARATION",
                f"Check {check['id']} dimensions must be unique nonempty IDs",
            )
        check["dimensions"] = [item.strip() for item in raw_dimensions]
        if "detect" in check:
            detect = _mapping(check["detect"], "Check detect")
            if not detect or set(detect) - {"contains_all", "contains_any", "absent_all"}:
                raise PlatformContractError(
                    "INVALID_PLUGIN_DECLARATION",
                    "Check detect supports contains_all, contains_any, and absent_all",
                )
            for predicate, raw_terms in detect.items():
                if (
                    not isinstance(raw_terms, list) or not raw_terms
                    or any(not isinstance(term, str) or not term.strip() for term in raw_terms)
                ):
                    raise PlatformContractError(
                        "INVALID_PLUGIN_DECLARATION", f"Check detect {predicate} must be a nonempty string array",
                    )
                detect[predicate] = [term.strip() for term in raw_terms]
            check["detect"] = detect
        normalized_checks.append(check)
    if len({item["id"] for item in normalized_checks}) != len(normalized_checks):
        raise PlatformContractError("INVALID_PLUGIN_DECLARATION", "Check IDs must be unique")
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PlatformContractError("POLICY_SOURCE_UNAVAILABLE", "Semantic instructions cannot be read") from error
    if not instructions.strip() or instructions_path.suffix.lower() != ".md":
        raise PlatformContractError("INVALID_PLUGIN_DECLARATION", "Semantic instructions must be nonempty Markdown")
    case_paths = tuple(sorted((root / "cases").glob("*.yaml"))) + tuple(
        sorted((root / "cases").glob("*.yml")),
    )
    if not case_paths:
        raise PlatformContractError(
            "PLUGIN_CASES_REQUIRED", "Plugin requires at least one business case",
        )
    check_ids = {item["id"] for item in normalized_checks}
    business_final_results = ("ready", "rework", "needs_review", "not_applicable")
    business_cases: list[dict[str, Any]] = []
    for case_path in case_paths:
        case = _mapping(load_yaml_subset(case_path), f"Business case {case_path.name}")
        if not {"input", "expect"}.issubset(case) or set(case) - {"input", "check", "expect"}:
            raise PlatformContractError(
                "INVALID_PLUGIN_CASE", "Business case requires input, optional check, and expect",
            )
        check_id = case.get("check")
        if check_id is None and len(check_ids) == 1:
            check_id = next(iter(check_ids))
        check_id = _text(check_id, "Business case Check")
        if check_id not in check_ids:
            raise PlatformContractError("INVALID_PLUGIN_CASE", "Business case Check is unknown")
        expect = _mapping(case["expect"], "Business case expectation")
        if set(expect) != {"candidate_rules", "final"}:
            raise PlatformContractError(
                "INVALID_PLUGIN_CASE", "Business case expect requires candidate_rules and final",
            )
        candidate_rules = expect["candidate_rules"]
        if (
            not isinstance(candidate_rules, list)
            or any(not isinstance(item, str) or item not in check_ids for item in candidate_rules)
            or len(candidate_rules) != len(set(candidate_rules))
        ):
            raise PlatformContractError(
                "INVALID_PLUGIN_CASE", "Expected candidate_rules must be unique declared rule IDs",
            )
        final = _text(expect["final"], "Business case final")
        if final not in business_final_results:
            raise PlatformContractError(
                "INVALID_PLUGIN_CASE",
                "Business case final must be ready, rework, needs_review, or not_applicable",
            )
        input_path = (case_path.parent / _text(case["input"], "Business case input")).resolve()
        input_data: Mapping[str, Any] | None = None
        try:
            input_path.relative_to(root)
            input_text = input_path.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeError) as error:
            raise PlatformContractError(
                "INVALID_PLUGIN_CASE", "Business case input must be readable inside the plugin source",
            ) from error
        if plugin["input"] == "browser_snapshot":
            if input_path.suffix.lower() != ".json":
                raise PlatformContractError(
                    "INVALID_PLUGIN_CASE",
                    "browser_snapshot business cases must use a JSON snapshot input",
                )
            try:
                parsed_input = json.loads(input_text)
            except (TypeError, ValueError) as error:
                raise PlatformContractError(
                    "INVALID_PLUGIN_CASE",
                    "browser_snapshot business case input must be valid JSON",
                ) from error
            if not isinstance(parsed_input, Mapping):
                raise PlatformContractError(
                    "INVALID_PLUGIN_CASE",
                    "browser_snapshot business case input must be a JSON object",
                )
            input_data = dict(parsed_input)
            try:
                snapshot = BrowserSnapshot(**input_data)
            except (TypeError, PlatformContractError) as error:
                raise PlatformContractError(
                    "INVALID_PLUGIN_CASE",
                    "browser_snapshot business case input does not match BrowserSnapshot",
                ) from error
            if snapshot.url is None:
                raise PlatformContractError(
                    "INVALID_PLUGIN_CASE",
                    "browser_snapshot business case input requires a URL",
                )
            input_data = _plain({
                key: getattr(snapshot, key)
                for key in (
                    "visible_text", "entrypoints", "candidates", "network_summary",
                    "route", "state_kind", "structure_summary", "active_tab",
                    "dom_digest", "visual_digest", "state_digest", "url", "origin",
                    "title",
                )
            })
        business_cases.append({
            "caseId": case_path.stem,
            "checkId": check_id,
            "inputText": input_text,
            "inputSuffix": input_path.suffix.lstrip(".") or (
                "md" if plugin["input"] == "markdown" else "txt"
            ),
            "inputKind": plugin["input"],
            **({"inputData": input_data} if input_data is not None else {}),
            "expectedCandidateRules": candidate_rules,
            "expectedFinal": final,
        })
    uncovered = check_ids - {item["checkId"] for item in business_cases}
    if uncovered:
        raise PlatformContractError(
            "POLICY_CASE_COVERAGE_INCOMPLETE",
            "Every Check requires a business case: " + ", ".join(sorted(uncovered)),
        )
    author_source = root / "plugin.py"
    if author_source.exists():
        raise PlatformContractError(
            "ORDINARY_PLUGIN_PYTHON_FORBIDDEN",
            "ordinary plugins are declaration-only; remove plugin.py and express the requirement in the Plugin Contract",
        )
    subject_kind = {
        "markdown": "markdown_document",
        "document": "document",
        "browser_snapshot": "browser_page",
    }[plugin["input"]]
    subject_kind = plugin.get("subject_kind", subject_kind)
    browser_input = plugin["input"] == "browser_snapshot"
    scope_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["url"] if browser_input else ["files"],
        "properties": ({"url": {"type": "string", "format": "uri", "minLength": 1}}
                       if browser_input else {"files": {
                           "type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"oneOf": [
                               {"type": "string", "minLength": 1},
                               {"type": "object", "additionalProperties": False,
                                "required": ["path"], "properties": {"path": {"type": "string", "minLength": 1}}},
                           ]},
                       }}),
    }
    semantic_digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    checks = []
    for item in normalized_checks:
        compiled_check = {
            "id": item["id"],
            "title": item["title"],
            "description": item["description"],
            "applicability": item["applicability"],
            "defaultSeverity": item["default_severity"],
            "recommendation": item["recommendation"],
            "unknownWhen": item["unknown_when"],
            "dimensions": list(item["dimensions"]),
        }
        if "detect" in item:
            compiled_check["detect"] = {
                {"contains_all": "containsAll", "contains_any": "containsAny", "absent_all": "absentAll"}[key]: value
                for key, value in item["detect"].items()
            }
        checks.append(compiled_check)
    compiled_cases = [{
        "id": item["caseId"],
        "checkId": item["checkId"],
        "inputKind": item["inputKind"],
        "input": item.get("inputData", item["inputText"]),
        "expected": {
            "candidateRules": list(item["expectedCandidateRules"]),
            "final": item["expectedFinal"],
        },
    } for item in business_cases]
    contract: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "contractVersion": "1.0.0",
        "plugin": {
            "id": plugin["id"],
            "name": plugin["name"],
            "description": plugin["description"],
            "version": plugin["version"],
        },
        "input": {
            "kind": plugin["input"],
            "subjectKind": subject_kind,
            "scopeSchema": scope_schema,
        },
        "checks": checks,
        "semanticReview": {
            "format": "markdown",
            "text": instructions,
            "sha256": semantic_digest,
        },
        "cases": compiled_cases,
    }
    digest = contract_digest(contract)
    contract["contractDigest"] = digest
    artifacts: dict[str, bytes] = {COMPILED_PLUGIN_CONTRACT: _json_bytes(contract)}
    for relative, content in artifacts.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return CompiledPluginArtifact(
        root, output, plugin["id"], plugin["version"], tuple(sorted(artifacts)), digest,
    )


__all__ = ["CompiledPluginArtifact", "compile_plugin_contract"]
