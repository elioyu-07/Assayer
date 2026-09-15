"""Compile ordinary domain sources into strict Advanced-SPI artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys
from typing import Any

from assayer_plugin_sdk.contract import PLATFORM_API_VERSION, PlatformContractError
from assayer_plugin_sdk.browser import BrowserSnapshot
from assayer_plugin_sdk.plugin_compatibility import (
    HOST_PROTOCOL_VERSION,
    HOST_SDK_VERSION,
)
from assayer_plugin_sdk.simple_compiler import InvariantProgram, compile_invariants

from .simple_author_conformance import validate_simple_author_source
from .yaml_subset import load_yaml_subset
from .simple_acceptance_template import generated_acceptance_source


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
        raise PlatformContractError("INVALID_POLICY_PACK", f"{label} must be nonempty")
    return value.strip()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformContractError("INVALID_POLICY_PACK", f"{label} must be an object")
    return dict(value)


def _load_author(
    root: Path, plugin: Mapping[str, Any],
) -> tuple[type | None, InvariantProgram]:
    path = root / "plugin.py"
    if not path.is_file():
        return None, InvariantProgram(())
    # Author source is executable.  Enforce the ordinary dependency and
    # determinism boundary before import machinery evaluates any of it.
    validate_simple_author_source(path)
    module_name = "_assayer_author_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PlatformContractError("INVALID_SIMPLE_PLUGIN", "plugin.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as error:
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN", f"plugin.py failed to load: {error}",
        ) from error
    finally:
        sys.dont_write_bytecode = previous_bytecode
        sys.modules.pop(module_name, None)
    candidates = tuple(
        value for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module_name
        and isinstance(getattr(value, "_assayer_simple_declaration", None), Mapping)
    )
    if len(candidates) != 1:
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN", "plugin.py must declare exactly one policy_plugin class",
        )
    plugin_type = candidates[0]
    declaration = plugin_type._assayer_simple_declaration
    expected = {
        "id": plugin["id"], "version": plugin["version"], "input": plugin["input"],
        "checks": plugin["checks"], "instructions": plugin["instructions"],
    }
    if dict(declaration) != expected:
        raise PlatformContractError(
            "SIMPLE_DECLARATION_MISMATCH",
            "plugin.py declaration differs from plugin.yaml",
        )
    try:
        instance = plugin_type()
    except Exception as error:
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN", "Simple plugin class must have a zero-argument constructor",
        ) from error
    declared = [
        member for _, member in inspect.getmembers(instance, callable)
        if hasattr(member, "_assayer_invariant_declaration")
    ]
    return plugin_type, compile_invariants(declared)


@dataclass(frozen=True)
class CompiledSimplePlugin:
    source_root: Path
    output_root: Path
    plugin_id: str
    version: str
    package_name: str
    artifacts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pluginId": self.plugin_id,
            "pluginVersion": self.version,
            "package": self.package_name,
            "outputRoot": str(self.output_root),
            "artifacts": list(self.artifacts),
        }


def compile_simple_plugin(
    source: str | Path, destination: str | Path,
    *, distribution_name: str | None = None,
) -> CompiledSimplePlugin:
    """Compile one Python-backed Simple plugin into a buildable source tree."""
    root = Path(source).expanduser().resolve()
    output = Path(destination).expanduser().resolve()
    plugin = _mapping(load_yaml_subset(root / "plugin.yaml"), "plugin.yaml")
    required_plugin = {"id", "name", "description", "version", "input", "checks", "instructions"}
    if not required_plugin.issubset(plugin) or set(plugin) - (required_plugin | {"subject_kind"}):
        raise PlatformContractError(
            "INVALID_POLICY_PACK", "plugin.yaml fields must match the ordinary author contract",
        )
    for key in required_plugin:
        plugin[key] = _text(plugin[key], f"plugin {key}")
    if "subject_kind" in plugin:
        plugin["subject_kind"] = _text(plugin["subject_kind"], "plugin subject_kind")
        if _SUBJECT_KIND.fullmatch(plugin["subject_kind"]) is None:
            raise PlatformContractError(
                "INVALID_POLICY_PACK", "Plugin subject_kind must be a lowercase identifier",
            )
    if _PLUGIN_ID.fullmatch(plugin["id"]) is None or _SEMVER.fullmatch(plugin["version"]) is None:
        raise PlatformContractError(
            "INVALID_POLICY_PACK", "Plugin ID or business version is invalid",
        )
    if plugin["input"] not in {"document", "markdown", "browser_snapshot"}:
        raise PlatformContractError(
            "INVALID_POLICY_PACK",
            "Simple compiler supports document, markdown, or browser_snapshot input",
        )
    checks_path = (root / plugin["checks"]).resolve()
    instructions_path = (root / plugin["instructions"]).resolve()
    try:
        checks_path.relative_to(root)
        instructions_path.relative_to(root)
    except ValueError as error:
        raise PlatformContractError(
            "INVALID_POLICY_PACK", "Policy resources must remain inside the source root",
        ) from error
    checks_source = _mapping(load_yaml_subset(checks_path), "checks.yaml")
    if set(checks_source) != {"checks"} or not isinstance(checks_source["checks"], list) or not checks_source["checks"]:
        raise PlatformContractError("INVALID_POLICY_PACK", "checks.yaml requires a checks array")
    normalized_checks: list[dict[str, Any]] = []
    for raw in checks_source["checks"]:
        check = _mapping(raw, "Check")
        required = {
            "id", "title", "description", "applicability", "default_severity",
            "recommendation", "unknown_when",
        }
        if not required.issubset(check) or set(check) - (required | {"detect", "dimensions"}):
            raise PlatformContractError(
                "INVALID_POLICY_PACK", "Every Check must declare only domain-owned fields",
            )
        for key in required:
            check[key] = _text(check[key], f"Check {key}")
        if _CHECK_ID.fullmatch(check["id"]) is None or check["default_severity"] not in {"P0", "P1", "P2", "P3", "P4"}:
            raise PlatformContractError("INVALID_POLICY_PACK", "Check ID or severity is invalid")
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
                "INVALID_POLICY_PACK",
                f"Check {check['id']} dimensions must be unique nonempty IDs",
            )
        check["dimensions"] = [item.strip() for item in raw_dimensions]
        if "detect" in check:
            detect = _mapping(check["detect"], "Check detect")
            if not detect or set(detect) - {"contains_all", "contains_any", "absent_all"}:
                raise PlatformContractError(
                    "INVALID_POLICY_PACK",
                    "Check detect supports contains_all, contains_any, and absent_all",
                )
            for predicate, raw_terms in detect.items():
                if (
                    not isinstance(raw_terms, list) or not raw_terms
                    or any(not isinstance(term, str) or not term.strip() for term in raw_terms)
                ):
                    raise PlatformContractError(
                        "INVALID_POLICY_PACK", f"Check detect {predicate} must be a nonempty string array",
                    )
                detect[predicate] = [term.strip() for term in raw_terms]
            check["detect"] = detect
        normalized_checks.append(check)
    if len({item["id"] for item in normalized_checks}) != len(normalized_checks):
        raise PlatformContractError("INVALID_POLICY_PACK", "Check IDs must be unique")
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PlatformContractError("POLICY_SOURCE_UNAVAILABLE", "Semantic instructions cannot be read") from error
    if not instructions.strip() or instructions_path.suffix.lower() != ".md":
        raise PlatformContractError("INVALID_POLICY_PACK", "Semantic instructions must be nonempty Markdown")
    case_paths = tuple(sorted((root / "cases").glob("*.yaml"))) + tuple(
        sorted((root / "cases").glob("*.yml")),
    )
    if not case_paths:
        raise PlatformContractError(
            "POLICY_CASES_REQUIRED", "Policy Pack requires at least one business case",
        )
    check_ids = {item["id"] for item in normalized_checks}
    final_results = {
        "ready": "scanned_no_issue",
        "rework": "issue_found",
        "needs_review": "needs_review",
        "not_applicable": "not_applicable",
    }
    business_cases: list[dict[str, Any]] = []
    for case_path in case_paths:
        case = _mapping(load_yaml_subset(case_path), f"Business case {case_path.name}")
        if not {"input", "expect"}.issubset(case) or set(case) - {"input", "check", "expect"}:
            raise PlatformContractError(
                "INVALID_POLICY_CASE", "Business case requires input, optional check, and expect",
            )
        check_id = case.get("check")
        if check_id is None and len(check_ids) == 1:
            check_id = next(iter(check_ids))
        check_id = _text(check_id, "Business case Check")
        if check_id not in check_ids:
            raise PlatformContractError("INVALID_POLICY_CASE", "Business case Check is unknown")
        expect = _mapping(case["expect"], "Business case expectation")
        if set(expect) != {"candidate_rules", "final"}:
            raise PlatformContractError(
                "INVALID_POLICY_CASE", "Business case expect requires candidate_rules and final",
            )
        candidate_rules = expect["candidate_rules"]
        if (
            not isinstance(candidate_rules, list)
            or any(not isinstance(item, str) or item not in check_ids for item in candidate_rules)
            or len(candidate_rules) != len(set(candidate_rules))
        ):
            raise PlatformContractError(
                "INVALID_POLICY_CASE", "Expected candidate_rules must be unique declared rule IDs",
            )
        final = _text(expect["final"], "Business case final")
        if final not in final_results:
            raise PlatformContractError(
                "INVALID_POLICY_CASE",
                "Business case final must be ready, rework, needs_review, or not_applicable",
            )
        input_path = (case_path.parent / _text(case["input"], "Business case input")).resolve()
        input_data: Mapping[str, Any] | None = None
        try:
            input_path.relative_to(root)
            input_text = input_path.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeError) as error:
            raise PlatformContractError(
                "INVALID_POLICY_CASE", "Business case input must be readable inside the Policy Pack",
            ) from error
        if plugin["input"] == "browser_snapshot":
            if input_path.suffix.lower() != ".json":
                raise PlatformContractError(
                    "INVALID_POLICY_CASE",
                    "browser_snapshot business cases must use a JSON snapshot input",
                )
            try:
                parsed_input = json.loads(input_text)
            except (TypeError, ValueError) as error:
                raise PlatformContractError(
                    "INVALID_POLICY_CASE",
                    "browser_snapshot business case input must be valid JSON",
                ) from error
            if not isinstance(parsed_input, Mapping):
                raise PlatformContractError(
                    "INVALID_POLICY_CASE",
                    "browser_snapshot business case input must be a JSON object",
                )
            input_data = dict(parsed_input)
            try:
                snapshot = BrowserSnapshot(**input_data)
            except (TypeError, PlatformContractError) as error:
                raise PlatformContractError(
                    "INVALID_POLICY_CASE",
                    "browser_snapshot business case input does not match BrowserSnapshot",
                ) from error
            if snapshot.url is None:
                raise PlatformContractError(
                    "INVALID_POLICY_CASE",
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
            "expectedDecision": final_results[final],
        })
    uncovered = check_ids - {item["checkId"] for item in business_cases}
    if uncovered:
        raise PlatformContractError(
            "POLICY_CASE_COVERAGE_INCOMPLETE",
            "Every Check requires a business case: " + ", ".join(sorted(uncovered)),
        )
    plugin_type, invariants = _load_author(root, plugin)
    invariants.run_generated_cases()

    package_name = re.sub(r"[^A-Za-z0-9_]", "_", plugin["id"])
    if package_name[:1].isdigit():
        package_name = "plugin_" + package_name
    package_root = output / "src" / package_name
    package_root.mkdir(parents=True, exist_ok=True)
    compatibility = {
        "protocolMinVersion": HOST_PROTOCOL_VERSION,
        "protocolMaxVersion": HOST_PROTOCOL_VERSION,
        "sdkMinVersion": HOST_SDK_VERSION,
        "sdkMaxVersion": HOST_SDK_VERSION,
        "capabilities": ["supported_by", "task_local_evidence_handles"],
        "domainContractVersion": "1.0.0",
    }
    subject_kind = {
        "markdown": "markdown_document",
        "document": "document",
        "browser_snapshot": "browser_page",
    }[plugin["input"]]
    subject_kind = plugin.get("subject_kind", subject_kind)
    browser_input = plugin["input"] == "browser_snapshot"
    required_evidence_kinds = ["browser_snapshot"] if browser_input else ["document_snapshot"]
    required_capabilities = ["browser_snapshot"] if browser_input else []
    # The public plugin id is namespaced (for example
    # ``assayer.frontend-audit``), while registry selection uses the stable
    # domain slug.  Keep that mechanical projection compiler-owned so an
    # ordinary author does not maintain a second domain identifier.
    domain_id = plugin["id"].rsplit(".", 1)[-1]
    manifest = {
        "pluginId": plugin["id"],
        "version": plugin["version"],
        "platformApiVersion": PLATFORM_API_VERSION,
        "compatibility": compatibility,
        "domains": [domain_id],
        "subjectKinds": [subject_kind],
        "checks": [{
            "checkId": item["id"],
            "version": plugin["version"],
            "subjectKinds": [subject_kind],
            "dimensions": list(item["dimensions"]),
            "decisionStates": [
                "issue_found", "scanned_no_issue", "not_applicable", "needs_review", "noise",
            ],
            "requiredEvidenceKinds": required_evidence_kinds,
            "requiredCapabilities": required_capabilities,
            "capabilityMissingOutcome": "needs_review",
            "invalidationSignals": ["browser_state_digest"] if browser_input else ["source_digest"],
        } for item in normalized_checks],
        "executionProfile": {
            "discoverBatching": "allowed", "inspectBatching": "allowed",
            "decisionBatching": "allowed", "parallelism": "forbidden",
            "cacheReuse": "allowed", "maxBatchSize": 32,
            "ordering": "independent", "failureSplitting": "allowed",
        },
    }
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
    common_review_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://assayer.dev/plugins/{plugin['id']}/common-review.schema.json",
        "type": "object",
        "description": "Platform common-review model; batch shape is Host-owned.",
        **invariants.schema_annotation,
    }
    registration_spec = f"{package_name}:registration"
    acceptance_spec = f"{package_name}.acceptance:run"
    semantic_digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    fixture_paths = [f"fixtures/{item['caseId']}.json" for item in business_cases]
    release = {
        "schemaVersion": "1.0.0", "pluginId": plugin["id"],
        "name": plugin["name"], "description": plugin["description"],
        "pluginVersion": plugin["version"], "platformApiVersion": PLATFORM_API_VERSION,
        "compatibility": compatibility, "registration": registration_spec,
        "manifest": f"src/{package_name}/manifest.json",
        "scopeSchema": f"src/{package_name}/scope.schema.json",
        "runtimeSource": "src",
        "semanticReview": f"src/{package_name}/semantic-review.md",
        "releaseAcceptance": acceptance_spec,
        "fixtures": fixture_paths,
        "packageMetadata": "pyproject.toml",
        "conformance": {"contractVersion": "1.0.0"},
    }
    if plugin_type is None:
        runtime_import = """from assayer_plugin_sdk.policy_pack import scan_policy
from assayer_plugin_sdk.simple import Document, policy_plugin

policy = json.loads((_ROOT / "policy.json").read_text(encoding="utf-8"))

@policy_plugin(
    id=policy["id"], version=policy["version"], input=policy["input"],
    checks="checks.yaml", instructions="semantic-review.md",
)
class GeneratedPolicy:
    def scan(self, document: Document):
        return scan_policy(document, policy["checks"])
"""
        plugin_class_name = "GeneratedPolicy"
    else:
        runtime_import = f"from .author import {plugin_type.__name__}\n"
        plugin_class_name = plugin_type.__name__
    browser_registration = """\ndef _provider_scope(scope, check):
    del check
    return {"url": scope["url"]}
""" if browser_input else ""
    registration_source = f'''"""Generated by the Assayer Simple SDK compiler; do not edit."""
from pathlib import Path
import json
from assayer_plugin_sdk.manifest import load_plugin_manifest
from assayer_plugin_sdk.registration import PluginRegistration

_ROOT = Path(__file__).parent
{runtime_import}
manifest = load_plugin_manifest(_ROOT / "manifest.json")
scope_schema = json.loads((_ROOT / "scope.schema.json").read_text(encoding="utf-8"))
{browser_registration}

def _create_plugin(_runtime=None):
    value = {plugin_class_name}()
    value.manifest = manifest
    return value

registration = PluginRegistration(
    manifest=manifest, plugin_factory=_create_plugin,
    capabilities=frozenset({required_capabilities!r}),
    provider_capabilities=frozenset({required_capabilities!r}),
    provider_source_capabilities=frozenset({required_capabilities!r}),
    execution_modes=frozenset({{"interactive"}}),
    result_features=frozenset({{"common_review"}}),
    scope_schema=scope_schema,
    provider_scope_resolver={"_provider_scope" if browser_input else "None"},
    semantic_instructions_path={f"{package_name}/semantic-review.md"!r},
    semantic_instructions_sha256={semantic_digest!r},
)
'''
    acceptance_source = generated_acceptance_source()
    project_name = distribution_name or plugin["id"]
    project_name = _text(project_name, "distribution name")
    pyproject = f'''[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = {json.dumps(project_name)}\nversion = "{plugin['version']}"\ndescription = {json.dumps(plugin['description'])}\nrequires-python = ">=3.11"\ndependencies = ["assayer-plugin-sdk=={HOST_SDK_VERSION}"]\n\n[project.entry-points."assayer.plugins"]\n{json.dumps(plugin['id'])} = "{registration_spec}"\n\n[project.entry-points."assayer.release_acceptance"]\n{json.dumps(plugin['id'])} = "{acceptance_spec}"\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\n\n[tool.setuptools.package-data]\n{package_name} = ["*.json", "*.md"]\n'''
    fixture_artifacts: dict[str, bytes] = {}
    for case in business_cases:
        case_scope = (
            {"url": case["inputData"].get("url")}
            if browser_input and isinstance(case.get("inputData"), Mapping)
            and isinstance(case["inputData"].get("url"), str)
            else {"files": [f"fixtures/{case['caseId']}.{case['inputSuffix']}"]}
        )
        fixture_artifacts[f"fixtures/{case['caseId']}.json"] = _json_bytes({
            "schemaVersion": "1.0.0",
            "fixtureId": f"{plugin['id']}.{case['caseId']}",
            "checkId": case["checkId"],
            "description": f"Generated from business case {case['caseId']}.",
            "scope": case_scope,
            "expected": {
                "terminalStatus": "completed",
                "decisionResults": [case["expectedDecision"]],
            },
        })
        if not browser_input:
            fixture_artifacts[
                f"fixtures/{case['caseId']}.{case['inputSuffix']}"
            ] = case["inputText"].encode("utf-8")
    artifacts: dict[str, bytes] = {
        "assayer-plugin-release.json": _json_bytes(release),
        "pyproject.toml": pyproject.encode("utf-8"),
        f"src/{package_name}/__init__.py": b"from .registration import registration\n",
        f"src/{package_name}/registration.py": registration_source.encode("utf-8"),
        f"src/{package_name}/acceptance.py": acceptance_source.encode("utf-8"),
        f"src/{package_name}/manifest.json": _json_bytes(manifest),
        f"src/{package_name}/scope.schema.json": _json_bytes(scope_schema),
        f"src/{package_name}/common-review.schema.json": _json_bytes(common_review_schema),
        f"src/{package_name}/compatibility.json": _json_bytes(compatibility),
        f"src/{package_name}/invariants.json": _json_bytes({
            "invariants": [item.as_dict() for item in invariants.descriptors],
            "generatedCases": list(invariants.generated_cases),
        }),
        f"src/{package_name}/agent-rules.json": _json_bytes({"rules": list(invariants.agent_rules)}),
        f"src/{package_name}/policy.json": _json_bytes({
            "id": plugin["id"], "version": plugin["version"],
            "input": plugin["input"], "checks": normalized_checks,
        }),
        f"src/{package_name}/business-cases.json": _json_bytes({
            "schemaVersion": "1.0.0", "cases": business_cases,
        }),
        f"src/{package_name}/compiler-metadata.json": _json_bytes({
            "schemaVersion": "1.0.0",
            "compiler": "assayer-simple-sdk",
            "sdkVersion": HOST_SDK_VERSION,
            "semanticInstructionsSha256": semantic_digest,
        }),
        f"src/{package_name}/semantic-review.md": instructions.encode("utf-8"),
    }
    artifacts.update(fixture_artifacts)
    if plugin_type is not None:
        artifacts[f"src/{package_name}/author.py"] = (root / "plugin.py").read_bytes()
    for relative, content in artifacts.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return CompiledSimplePlugin(
        root, output, plugin["id"], plugin["version"], package_name,
        tuple(sorted(artifacts)),
    )


__all__ = ["CompiledSimplePlugin", "compile_simple_plugin"]
