"""Package-time plugin conformance and release-gate tests."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    PlatformContractError,
    PluginRegistration,
    PluginRegistry,
    inspect_plugin_package,
    inspect_plugin_registration,
    load_plugin_manifest,
)
from assayer_platform import installed_plugin_registry
from tests.helpers.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)
from assayer_platform.conformance import main
from assayer_platform.installation_conformance import inspect_plugin_installation
from assayer_platform.package_conformance import main as package_main
from assayer_platform.release_conformance import (
    inspect_plugin_release,
    main as release_main,
)


class PluginReleaseGateTest(unittest.TestCase):
    def registration(self, **overrides):
        values = {
            "manifest": ConfigQualityPlugin.manifest,
            "plugin_factory": lambda: ConfigQualityPlugin(),
            "decision_provider_factory": lambda: ConfigurationDecisionProvider(),
            "capabilities": frozenset({"structured_read"}),
            "scope_schema": {"type": "object"},
        }
        values.update(overrides)
        return PluginRegistration(**values)

    def write_package(self, root: Path, **descriptor_overrides) -> Path:
        package = root / "plugin-release"
        (package / "plugin" / "fixtures").mkdir(parents=True)
        (package / "src" / "fixture_plugin").mkdir(parents=True)
        manifest = {
            "pluginId": "fixture.release-quality", "version": "1.0.0",
            "platformApiVersion": "1.0.0", "domains": ["fixture"],
            "subjectKinds": ["fixture_item"],
            "checks": [{
                "checkId": "FIX-001", "version": "1.0.0",
                "subjectKinds": ["fixture_item"], "dimensions": ["present"],
                "decisionStates": ["scanned_no_issue", "needs_review"],
                "requiredEvidenceKinds": ["structured"],
                "requiredCapabilities": ["structured_read"],
                "capabilityMissingOutcome": "needs_review",
                "invalidationSignals": ["source_digest"],
            }],
            "executionProfile": {
                "discoverBatching": "allowed", "inspectBatching": "allowed",
                "decisionBatching": "allowed", "parallelism": "forbidden",
                "cacheReuse": "allowed", "checkpoint": "required",
                "ordering": "independent", "failureSplitting": "allowed",
            },
        }
        (package / "plugin" / "manifest.json").write_text(json.dumps(manifest))
        (package / "plugin" / "scope.schema.json").write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }))
        (package / "plugin" / "review.md").write_text(
            "# Review contract\n\nReview the declared dimension from immutable evidence.\n",
        )
        (package / "plugin" / "fixtures" / "pass.json").write_text(json.dumps({
            "schemaVersion": "1.0.0", "fixtureId": "fixture.pass",
            "checkId": "FIX-001", "description": "A valid fixture.",
            "scope": {"path": "fixture.json"},
            "expected": {
                "terminalStatus": "completed",
                "decisionResults": ["scanned_no_issue"],
            },
        }))
        (package / "src" / "fixture_plugin" / "plugin.py").write_text(
            "raise RuntimeError('static package validation must not import plugin code')\n",
        )
        (package / "pyproject.toml").write_text(
            "[build-system]\n"
            "requires = [\"setuptools>=68\"]\n"
            "build-backend = \"setuptools.build_meta\"\n"
            "[project]\n"
            "name = \"fixture-assayer-plugin\"\n"
            "version = \"1.0.0\"\n"
            "dependencies = [\"assayer>=0.1.0,<0.2.0\"]\n"
            "[project.entry-points.\"assayer.plugins\"]\n"
            "fixture = \"fixture_plugin.plugin:registration\"\n"
            "[tool.setuptools.packages.find]\n"
            "where = [\"src\"]\n",
        )
        descriptor = {
            "schemaVersion": "1.0.0",
            "pluginId": "fixture.release-quality",
            "pluginVersion": "1.0.0",
            "platformApiVersion": "1.0.0",
            "registration": "fixture_plugin.plugin:registration",
            "manifest": "plugin/manifest.json",
            "scopeSchema": "plugin/scope.schema.json",
            "runtimeSource": "src",
            "semanticReview": "plugin/review.md",
            "fixtures": ["plugin/fixtures/pass.json"],
            "packageMetadata": "pyproject.toml",
            "conformance": {"contractVersion": "1.0.0"},
        }
        descriptor.update(descriptor_overrides)
        (package / "assayer-plugin-release.json").write_text(json.dumps(descriptor))
        return package

    def make_installable(self, package: Path) -> None:
        manifest = (package / "plugin" / "manifest.json").read_text()
        scope_schema = (package / "plugin" / "scope.schema.json").read_text()
        source = f'''import json
from assayer_platform import PluginRegistration
from assayer_platform.contract import (
    DecisionProposal, DimensionObservation, EvidenceRecord, Finding,
    InvestigationPacket, WorkItem,
)
from assayer_platform.registry import load_plugin_manifest

MANIFEST = load_plugin_manifest(json.loads({manifest!r}))
SCOPE_SCHEMA = json.loads({scope_schema!r})


class FixturePlugin:
    manifest = MANIFEST

    def discover(self, scope, context):
        del context
        present = bool(scope.get("path"))
        return (WorkItem("fixture:item", "fixture_item", "fixture:source", str(present), {{"present": present}}),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        status = "satisfied" if item.metadata["present"] else "violated"
        evidence = EvidenceRecord(
            "fixture:evidence", item.work_item_id, check.check_id, check.version,
            "structured", item.identity, {{"present": item.metadata["present"]}},
        )
        dimension = DimensionObservation(
            "present", ("Fixture presence was observed.",),
            (evidence.evidence_id,), status,
        )
        return (InvestigationPacket(
            item, check.check_id, check.version, (dimension,), (evidence,), "not_required",
        ),)


class FixtureDecisionProvider:
    def decide(self, packets, check, context):
        del context
        packet = packets[0]
        status = packet.dimensions[0].candidate_status
        result = "scanned_no_issue" if status == "satisfied" else "issue_found"
        return (DecisionProposal(
            packet.work_item.work_item_id, check.check_id, check.version,
            result, (Finding("present", status, "Fixture presence was observed."),),
            "The deterministic fixture was evaluated.",
        ),)


registration = PluginRegistration(
    MANIFEST,
    plugin_factory=lambda runtime=None: FixturePlugin(),
    decision_provider_factory=lambda runtime=None: FixtureDecisionProvider(),
    capabilities=frozenset({{"structured_read"}}),
    scope_schema=SCOPE_SCHEMA,
)
'''
        (package / "src" / "fixture_plugin" / "plugin.py").write_text(source)

    def make_strict_installable(
        self, package: Path, *, acceptance: bool = True,
        semantic_resource: bool = True, acceptance_replay: bool = True,
        checkpoint_collection: str = "signals",
        checkpoint_schema_valid: bool = True,
        finalization_schema: bool = True,
    ) -> None:
        semantic = "# Fixture semantic review\n"
        checkpoint_schemas = {checkpoint_collection: {
            "type": "object" if checkpoint_schema_valid else "invalid-type",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}},
        }}
        finalization = ({
            "type": "object", "additionalProperties": False,
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}},
        } if finalization_schema else None)
        (package / "plugin" / "review.md").write_text(semantic)
        if semantic_resource:
            (package / "src" / "fixture_plugin" / "review.md").write_text(semantic)
        manifest = (package / "plugin" / "manifest.json").read_text()
        scope_schema = (package / "plugin" / "scope.schema.json").read_text()
        source = f'''import hashlib
import json
from assayer_platform import AgentContractBundle, PluginRegistration
from assayer_platform.contract import (
    DimensionObservation, EvidenceRecord, InvestigationPacket, WorkItem,
)
from assayer_platform.registry import load_plugin_manifest

MANIFEST = load_plugin_manifest(json.loads({manifest!r}))
SCOPE_SCHEMA = json.loads({scope_schema!r})


class FixturePlugin:
    manifest = MANIFEST

    def discover(self, scope, context):
        del scope, context
        return (WorkItem("fixture:item", "fixture_item", "fixture:source", "state"),)

    def inspect(self, work_items, check, context):
        del context
        item = work_items[0]
        evidence = EvidenceRecord(
            "fixture:evidence", item.work_item_id, check.check_id, check.version,
            "structured", item.identity,
            {{"signals": [{{"signal_id": "signal:1", "present": True}}]}},
        )
        return (InvestigationPacket(
            item, check.check_id, check.version,
            (DimensionObservation("present", ("Present.",), (evidence.evidence_id,), "satisfied"),),
            (evidence,), "not_required", metadata={{"evidenceCollections": [{{
                "collectionId": "signals", "evidenceId": evidence.evidence_id,
                "jsonPointer": "/signals", "itemIdField": "signal_id",
            }}]}},
        ),)

    def validate_review_checkpoint(self, checkpoint, collection_items, prior, packet, check, context):
        del checkpoint, prior, packet, check, context
        if not collection_items:
            raise ValueError("empty fixture checkpoint")

    def assemble_review_checkpoints(self, checkpoints, finalization, packet, check, context):
        del checkpoints, packet, check, context
        return {{"finalization": dict(finalization)}}


CONTRACT = AgentContractBundle(
    "dev.assayer.fixture.release", "1.0.0", "FIX-001", "1.0.0",
    {checkpoint_schemas!r},
    {finalization!r},
    "fixture_plugin/review.md", hashlib.sha256({semantic!r}.encode()).hexdigest(),
)

registration = PluginRegistration(
    MANIFEST,
    plugin_factory=lambda runtime=None: FixturePlugin(),
    capabilities=frozenset({{"structured_read"}}),
    execution_modes=frozenset({{"interactive"}}),
    scope_schema=SCOPE_SCHEMA,
    agent_contracts=(CONTRACT,),
)
'''
        (package / "src" / "fixture_plugin" / "plugin.py").write_text(source)
        metadata = package / "pyproject.toml"
        metadata.write_text(metadata.read_text() + (
            '\n[tool.setuptools.package-data]\nfixture_plugin = ["review.md"]\n'
        ))
        if not acceptance:
            return
        descriptor_path = package / "assayer-plugin-release.json"
        descriptor = json.loads(descriptor_path.read_text())
        descriptor["releaseAcceptance"] = "fixture_plugin.acceptance:run"
        descriptor_path.write_text(json.dumps(descriptor))
        metadata.write_text(metadata.read_text() + (
            '\n[project.entry-points."assayer.release_acceptance"]\n'
            'fixture = "fixture_plugin.acceptance:run"\n'
        ))
        acceptance_source = f'''from pathlib import Path


def value(response):
    return response["structuredContent"]["result"]


def run(*, registration, output_root, transport_factory):
    output_root = Path(output_root)
    transport = transport_factory(output_root)
    started = value(transport.call_tool("start_plugin_run", {{
        "pluginId": registration.manifest.plugin_id,
        "checkId": "FIX-001", "scope": {{"path": "fixture"}},
    }}))
    run_id = started["runId"]
    digest = started["result"]["agentContract"]["contractDigest"]
    boundary = value(transport.call_tool("advance_plugin_run", {{}}))
    task = boundary["result"]["semanticTask"]
    transport.call_tool("advance_plugin_run", {{"reviewCheckpoint": {{
        "workItemId": task["workItemId"], "collectionId": "signals",
        "itemIds": task["itemIds"], "payload": {{"summary": "Reviewed."}},
        "contractDigest": digest,
    }}}})
    transport.close()
    transport = transport_factory(output_root)
    resumed = value(transport.call_tool("resume_plugin_run", {{"runId": run_id}}))
    task = resumed["result"]["semanticTask"]
    terminal = value(transport.call_tool("advance_plugin_run", {{"decision": {{
        "workItemId": task["workItemId"], "result": "scanned_no_issue",
        "findings": [{{"dimension": "present", "status": "satisfied", "reason": "Reviewed."}}],
        "reason": "The fixture was reviewed.", "finalization": {{"summary": "Complete."}},
        "contractDigest": digest,
    }}}}))
    replay = value(transport.call_tool("advance_plugin_run", {{}}))
    transport.call_tool("get_plugin_result", {{
        "sectionId": replay["result"]["decisions"]["sectionId"],
    }})
    ledger = output_root / run_id / f"{{run_id}}.platform-ledger.json"
    return {{
        "schemaVersion": "1.0.0", "status": "passed", "checks": [{{
            "checkId": "FIX-001", "checkVersion": "1.0.0",
            "completedRuns": 1, "checkpointPages": 1,
            "reviewedCollections": ["signals"], "agentRetries": 0,
            "ledgerPaths": [ledger.relative_to(output_root).as_posix()],
            "resumeVerified": resumed.get("resumed") is True,
            "replayVerified": {acceptance_replay!r} and replay.get("replayed") is True,
            "terminalPublicationVerified": terminal["status"] == "completed"
                and (output_root / run_id / "result-summary.json").is_file(),
        }}]
    }}
'''
        (package / "src" / "fixture_plugin" / "acceptance.py").write_text(
            acceptance_source,
        )

    def test_installed_plugins_pass_the_same_registration_gate(self):
        reports = tuple(
            inspect_plugin_registration(registration)
            for registration in installed_plugin_registry().list()
        )
        self.assertTrue(reports)
        self.assertTrue(all(report.passed for report in reports))
        self.assertEqual(
            {report.plugin_id for report in reports},
            {"assayer.frontend-audit"},
        )

    def test_missing_batch_decision_provider_is_rejected_before_registration(self):
        with self.assertRaises(PlatformContractError) as rejected:
            PluginRegistry((self.registration(decision_provider_factory=None),))
        self.assertEqual(rejected.exception.code, "PLUGIN_DECISION_UNAVAILABLE")
        self.assertIn("PCV1-BATCH-DECISION", rejected.exception.message)
        self.assertIn("Next action:", rejected.exception.message)

    def test_missing_capability_and_scope_schema_are_both_reported(self):
        report = inspect_plugin_registration(self.registration(
            capabilities=frozenset(), scope_schema={},
        ))
        self.assertFalse(report.passed)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PLUGIN_CAPABILITY_UNDECLARED", "PLUGIN_SCOPE_SCHEMA_MISSING"},
        )
        self.assertTrue(all(issue.invariant and issue.next_action for issue in report.issues))

    def test_invalid_scope_schema_is_rejected(self):
        report = inspect_plugin_registration(self.registration(
            scope_schema={"type": "not-a-json-schema-type"},
        ))
        self.assertFalse(report.passed)
        self.assertIn("PLUGIN_SCOPE_SCHEMA_INVALID", {issue.code for issue in report.issues})

    def test_release_validation_constructs_and_checks_the_runtime(self):
        broken = self.registration(plugin_factory=lambda: object())
        report = inspect_plugin_registration(
            broken, construct_implementations=True,
        )
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PLUGIN_IDENTITY_MISMATCH", "PLUGIN_RUNTIME_INCOMPLETE"},
        )

    def test_evidence_graph_feature_requires_runtime_marker(self):
        declared = self.registration(result_features=frozenset({"evidence_graph"}))
        report = inspect_plugin_registration(declared, construct_implementations=True)
        self.assertTrue(report.passed)

        class Unmarked(ConfigQualityPlugin):
            evidence_graph_enabled = False

        unmarked = self.registration(
            plugin_factory=lambda: Unmarked(),
            result_features=frozenset({"evidence_graph"}),
        )
        report = inspect_plugin_registration(unmarked, construct_implementations=True)
        self.assertIn("PLUGIN_RESULT_FEATURE_UNIMPLEMENTED", {issue.code for issue in report.issues})

    def test_registration_validation_does_not_construct_the_runtime(self):
        def unavailable_until_execution():
            raise RuntimeError("live runtime is not configured")

        registry = PluginRegistry((self.registration(
            plugin_factory=unavailable_until_execution,
        ),))
        self.assertEqual(len(registry.list()), 1)
        report = inspect_plugin_registration(
            registry.list()[0], construct_implementations=True,
        )
        self.assertEqual(report.issues[0].code, "PLUGIN_INITIALIZATION_FAILED")

    def test_unsafe_failure_splitting_declaration_is_rejected(self):
        payload = {
            "pluginId": "fixture.unsafe-split", "version": "1.0.0",
            "platformApiVersion": "1.0.0", "domains": ["fixture"],
            "subjectKinds": ["fixture_item"],
            "checks": [{
                "checkId": "FIX-001", "version": "1.0.0",
                "subjectKinds": ["fixture_item"], "dimensions": ["present"],
                "decisionStates": ["scanned_no_issue", "needs_review"],
                "requiredEvidenceKinds": ["structured"],
                "requiredCapabilities": [], "capabilityMissingOutcome": "needs_review",
                "invalidationSignals": ["source_digest"],
            }],
            "executionProfile": {
                "discoverBatching": "allowed", "inspectBatching": "forbidden",
                "decisionBatching": "allowed", "parallelism": "forbidden",
                "cacheReuse": "forbidden", "checkpoint": "required",
                "failureSplitting": "allowed", "ordering": "strict",
            },
        }
        registration = self.registration(
            manifest=load_plugin_manifest(payload), capabilities=frozenset(),
        )
        report = inspect_plugin_registration(registration)
        self.assertEqual(report.issues[0].code, "PLUGIN_EXECUTION_PROFILE_INVALID")
        with self.assertRaisesRegex(PlatformContractError, "PCV1-FAILURE-SPLITTING"):
            PluginRegistry((registration,))

    def test_release_cli_emits_machine_readable_failure_with_next_action(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["missing.module:registration"])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(payload["status"], "failed")
        issue = payload["plugins"][0]["issues"][0]
        self.assertEqual(issue["invariant"], "PCV1-REGISTRATION")
        self.assertTrue(issue["nextAction"])

    def test_release_cli_accepts_a_registry_factory(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "assayer_platform.plugin_discovery:installed_plugin_registry",
            ])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(len(payload["plugins"]), 1)

    def test_independent_package_passes_without_importing_plugin_code(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = package_main([str(package)])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["plugins"][0]["pluginId"], "fixture.release-quality")

    def test_package_rejects_unsafe_resource_path_before_reading_it(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(
                Path(directory), semanticReview="../outside.md",
            )
            report = inspect_plugin_package(package)
        self.assertFalse(report.passed)
        self.assertIn(
            "PLUGIN_RELEASE_DESCRIPTOR_INVALID",
            {issue.code for issue in report.issues},
        )

    def test_package_rejects_identity_fixture_and_entry_point_mismatches(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory), pluginVersion="1.1.0")
            fixture = package / "plugin" / "fixtures" / "pass.json"
            value = json.loads(fixture.read_text())
            value["checkId"] = "UNKNOWN"
            fixture.write_text(json.dumps(value))
            metadata = package / "pyproject.toml"
            metadata.write_text(metadata.read_text().replace(
                "fixture_plugin.plugin:registration", "other.module:registration",
            ))
            report = inspect_plugin_package(package)
        codes = {issue.code for issue in report.issues}
        self.assertTrue({
            "PLUGIN_PACKAGE_IDENTITY_MISMATCH",
            "PLUGIN_PACKAGE_VERSION_MISMATCH",
            "PLUGIN_FIXTURE_CHECK_UNKNOWN",
            "PLUGIN_ENTRY_POINT_MISSING",
        }.issubset(codes))

    def test_package_requires_an_explicit_platform_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            metadata = package / "pyproject.toml"
            metadata.write_text(metadata.read_text().replace(
                'dependencies = ["assayer>=0.1.0,<0.2.0"]\n', "",
            ))
            report = inspect_plugin_package(package)
        self.assertIn(
            "PLUGIN_PLATFORM_DEPENDENCY_MISSING",
            {issue.code for issue in report.issues},
        )

    def test_isolated_install_discovers_registration_and_runs_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            report = inspect_plugin_installation(package)
        self.assertTrue(report.passed, report.as_dict())

    def test_isolated_install_rejects_fixture_expectation_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            fixture = package / "plugin" / "fixtures" / "pass.json"
            value = json.loads(fixture.read_text())
            value["expected"]["decisionResults"] = ["issue_found"]
            fixture.write_text(json.dumps(value))
            report = inspect_plugin_installation(package)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PLUGIN_FIXTURE_EXPECTATION_MISMATCH"},
        )

    def test_isolated_install_rejects_runtime_manifest_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            source = package / "src" / "fixture_plugin" / "plugin.py"
            source.write_text(source.read_text().replace(
                '"version": "1.0.0"', '"version": "1.0.1"', 1,
            ))
            report = inspect_plugin_installation(package)
        self.assertIn(
            "PLUGIN_INSTALLED_MANIFEST_MISMATCH",
            {issue.code for issue in report.issues},
        )

    def test_isolated_install_rejects_an_extra_plugin_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            metadata = package / "pyproject.toml"
            metadata.write_text(metadata.read_text().replace(
                'fixture = "fixture_plugin.plugin:registration"',
                'fixture = "fixture_plugin.plugin:registration"\n'
                'duplicate = "fixture_plugin.plugin:registration"',
            ))
            report = inspect_plugin_installation(package)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PLUGIN_INSTALLED_ENTRY_POINT_INVALID"},
        )

    def test_complete_release_gate_builds_and_exercises_the_exact_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            result = inspect_plugin_release(package)
            source_artifacts = [
                path.relative_to(package).as_posix()
                for pattern in ("build", "*.egg-info", "src/*.egg-info")
                for path in package.glob(pattern)
            ]
        self.assertEqual(result["status"], "passed", result)
        self.assertRegex(result["artifact"]["filename"], r"\.whl$")
        self.assertRegex(result["artifact"]["sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(result["stages"], [
            {"name": "source_static", "status": "passed"},
            {"name": "public_surface", "status": "passed"},
            {"name": "wheel_build", "status": "passed"},
            {"name": "installed_lifecycle", "status": "passed"},
        ])
        self.assertEqual(source_artifacts, [])

    def test_complete_release_gate_stops_before_build_on_static_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory), semanticReview="missing.md")
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            [stage["status"] for stage in result["stages"]],
            ["failed", "not_run", "not_run", "not_run"],
        )
        self.assertNotIn("artifact", result)

    def test_complete_release_gate_requires_source_for_a_prebuilt_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "fixture-1.0.0-py3-none-any.whl"
            wheel.write_bytes(b"not inspected because source is absent")
            result = inspect_plugin_release(wheel)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["code"], "PLUGIN_WHEEL_SOURCE_REQUIRED")
        self.assertEqual(
            [stage["status"] for stage in result["stages"]],
            ["failed", "not_run", "not_run", "not_run"],
        )

    def test_complete_release_cli_emits_stage_specific_machine_result(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = release_main([str(package)])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(
            [stage["name"] for stage in payload["plugins"][0]["stages"]],
            ["source_static", "public_surface", "wheel_build", "installed_lifecycle"],
        )

    def test_strict_interactive_wheel_runs_declared_acceptance_and_validates_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "passed", result)

    def test_strict_interactive_wheel_without_acceptance_fails_before_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package, acceptance=False)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_INTERACTIVE_RELEASE_ACCEPTANCE_MISSING",
        )

    def test_strict_wheel_missing_semantic_resource_fails_before_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package, semantic_resource=False)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_INSTALLED_SEMANTIC_INSTRUCTIONS_MISSING",
        )

    def test_strict_acceptance_cannot_claim_success_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package, acceptance_replay=False)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_RELEASE_ACCEPTANCE_RESULT_INVALID",
        )

    def test_strict_wheel_rejects_malformed_checkpoint_schema_before_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package, checkpoint_schema_valid=False)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_AGENT_CONTRACT_SCHEMA_INVALID",
        )

    def test_strict_wheel_rejects_missing_finalization_before_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(package, finalization_schema=False)
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_AGENT_CONTRACT_FINALIZATION_MISSING",
        )

    def test_runtime_collection_without_schema_fails_the_installed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_strict_installable(
                package, checkpoint_collection="other-signals",
            )
            result = inspect_plugin_release(package)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["issues"][0]["code"],
            "PLUGIN_RELEASE_ACCEPTANCE_EXECUTION_FAILED",
        )
        self.assertEqual(result["stages"][-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
