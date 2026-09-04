from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import inspect_provider_installation, inspect_provider_package
from assayer_platform.provider_installation_conformance import main as install_main
from assayer_platform.provider_package_conformance import main as package_main


FAILURES = (
    "capability_unavailable",
    "authorization_denied",
    "timeout",
    "budget_exceeded",
    "source_changed",
    "stale_state",
    "source_error",
    "result_unknown",
)


class ProviderReleaseGateTests(unittest.TestCase):
    def write_package(self, root: Path, **release_overrides) -> Path:
        package = root / "provider-release"
        (package / "provider" / "fixtures").mkdir(parents=True)
        (package / "src" / "fixture_provider").mkdir(parents=True)
        descriptor = {
            "providerId": "fixture.release-provider",
            "version": "1.0.0",
            "platformApiVersion": "1.0.0",
            "capabilities": [{
                "name": "structured_read",
                "version": "1.0.0",
                "accessMode": "read_only",
                "evidenceKinds": ["structured"],
            }],
            "scopeSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["outcome"],
                "properties": {
                    "outcome": {"enum": ["success", "source_error"]},
                },
            },
            "authorization": {
                "userScopeRequired": True,
                "secretHandling": "none",
            },
            "limits": {
                "timeoutMs": 30000,
                "maxBytes": 100000,
                "maxItems": 10,
                "maxConcurrency": 1,
            },
            "failurePolicy": [{
                "code": code,
                "retry": "resolve_unknown_first" if code == "result_unknown" else "never",
            } for code in FAILURES],
            "algorithmVersions": {
                "sourceIdentity": "1.0.0",
                "stateDigest": "1.0.0",
            },
        }
        descriptor_text = json.dumps(descriptor)
        (package / "provider" / "descriptor.json").write_text(descriptor_text)
        success = {
            "schemaVersion": "1.0.0",
            "fixtureId": "fixture.provider-pass",
            "description": "The provider returns one structured fact.",
            "capability": "structured_read",
            "scope": {"outcome": "success"},
            "sourceIdentity": "fixture-source",
            "stateDigest": "fixture-state",
            "expected": {
                "status": "succeeded",
                "evidenceKinds": ["structured"],
                "evidenceCount": 1,
            },
        }
        failure = {
            "schemaVersion": "1.0.0",
            "fixtureId": "fixture.provider-failure",
            "description": "The provider returns one classified source failure.",
            "capability": "structured_read",
            "scope": {"outcome": "source_error"},
            "sourceIdentity": "fixture-source",
            "stateDigest": "fixture-state",
            "expected": {
                "status": "failed",
                "failureCode": "source_error",
            },
        }
        (package / "provider" / "fixtures" / "success.json").write_text(json.dumps(success))
        (package / "provider" / "fixtures" / "failure.json").write_text(json.dumps(failure))
        (package / "src" / "fixture_provider" / "__init__.py").write_text("")
        (package / "src" / "fixture_provider" / "provider.py").write_text(
            "raise RuntimeError('static validation must not import provider code')\n"
        )
        (package / "pyproject.toml").write_text(
            "[build-system]\n"
            "requires = [\"setuptools>=68\"]\n"
            "build-backend = \"setuptools.build_meta\"\n"
            "[project]\n"
            "name = \"fixture-assayer-provider\"\n"
            "version = \"1.0.0\"\n"
            "dependencies = [\"assayer>=0.1.0,<0.2.0\"]\n"
            "[project.entry-points.\"assayer.providers\"]\n"
            "fixture = \"fixture_provider.provider:registration\"\n"
            "[tool.setuptools.packages.find]\n"
            "where = [\"src\"]\n"
        )
        release = {
            "schemaVersion": "1.0.0",
            "providerId": "fixture.release-provider",
            "providerVersion": "1.0.0",
            "platformApiVersion": "1.0.0",
            "registration": "fixture_provider.provider:registration",
            "providerDescriptor": "provider/descriptor.json",
            "runtimeSource": "src",
            "fixtures": [
                "provider/fixtures/success.json",
                "provider/fixtures/failure.json",
            ],
            "packageMetadata": "pyproject.toml",
            "conformance": {"contractVersion": "1.0.0"},
        }
        release.update(release_overrides)
        (package / "assayer-provider-release.json").write_text(json.dumps(release))
        return package

    @staticmethod
    def make_installable(package: Path, *, descriptor_drift: bool = False) -> None:
        descriptor = json.loads(
            (package / "provider" / "descriptor.json").read_text()
        )
        if descriptor_drift:
            descriptor["algorithmVersions"]["stateDigest"] = "1.1.0"
        source = f'''import json
from assayer_platform import (
    ProviderFact, ProviderFailure, ProviderRegistration, ProviderResponse,
    load_provider_descriptor,
)

DESCRIPTOR = load_provider_descriptor(json.loads({json.dumps(descriptor)!r}))


class FixtureProvider:
    descriptor = DESCRIPTOR

    def collect(self, request, context):
        del context
        if request.scope["outcome"] == "source_error":
            return ProviderResponse(
                request.request_id, request.provider_id, request.provider_version,
                request.capability, "failed", failure=ProviderFailure(
                    "source_error", "The deterministic source failed.",
                ),
            )
        return ProviderResponse(
            request.request_id, request.provider_id, request.provider_version,
            request.capability, "succeeded", (ProviderFact(
                "structured", request.source_identity, request.state_digest,
                {{"observed": True}},
            ),),
        )


registration = ProviderRegistration(
    DESCRIPTOR,
    provider_factory=lambda runtime=None: FixtureProvider(),
)
'''
        (package / "src" / "fixture_provider" / "provider.py").write_text(source)

    def test_static_package_passes_without_importing_provider_code(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            report = inspect_provider_package(package)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = package_main([str(package)])
        self.assertTrue(report.passed, report.as_dict())
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "passed")

    def test_static_gate_rejects_unsafe_resource_path_before_access(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(
                Path(directory),
                runtimeSource="../outside",
            )
            report = inspect_provider_package(package)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PROVIDER_RELEASE_DESCRIPTOR_INVALID"},
        )

    def test_static_gate_rejects_identity_fixture_and_metadata_mismatches(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(
                Path(directory),
                providerVersion="1.1.0",
            )
            fixture = package / "provider" / "fixtures" / "success.json"
            value = json.loads(fixture.read_text())
            value["capability"] = "unknown_read"
            fixture.write_text(json.dumps(value))
            metadata = package / "pyproject.toml"
            metadata.write_text(metadata.read_text().replace(
                "fixture_provider.provider:registration",
                "another_provider.provider:registration",
            ))
            report = inspect_provider_package(package)
        codes = {issue.code for issue in report.issues}
        self.assertTrue({
            "PROVIDER_PACKAGE_IDENTITY_MISMATCH",
            "PROVIDER_PACKAGE_VERSION_MISMATCH",
            "PROVIDER_FIXTURE_CAPABILITY_UNKNOWN",
            "PROVIDER_ENTRY_POINT_MISSING",
        }.issubset(codes))

    def test_static_gate_requires_success_and_failure_fixture_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            release_path = package / "assayer-provider-release.json"
            release = json.loads(release_path.read_text())
            release["fixtures"] = ["provider/fixtures/success.json"]
            release_path.write_text(json.dumps(release))
            report = inspect_provider_package(package)
        self.assertIn(
            "PROVIDER_FIXTURE_COVERAGE_INCOMPLETE",
            {issue.code for issue in report.issues},
        )

    def test_static_gate_requires_success_coverage_for_every_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            descriptor_path = package / "provider" / "descriptor.json"
            descriptor = json.loads(descriptor_path.read_text())
            descriptor["capabilities"].append({
                "name": "visual_read",
                "version": "1.0.0",
                "accessMode": "read_only",
                "evidenceKinds": ["visual"],
            })
            descriptor_path.write_text(json.dumps(descriptor))
            report = inspect_provider_package(package)
        self.assertIn(
            "PROVIDER_FIXTURE_CAPABILITY_COVERAGE_INCOMPLETE",
            {issue.code for issue in report.issues},
        )

    def test_isolated_install_discovers_provider_and_runs_both_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            report = inspect_provider_installation(package)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = install_main([str(package)])
        self.assertTrue(report.passed, report.as_dict())
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "passed")

    def test_isolated_install_rejects_runtime_descriptor_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package, descriptor_drift=True)
            report = inspect_provider_installation(package)
        self.assertIn(
            "PROVIDER_INSTALLED_DESCRIPTOR_MISMATCH",
            {issue.code for issue in report.issues},
        )

    def test_isolated_install_rejects_fixture_expectation_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            fixture = package / "provider" / "fixtures" / "success.json"
            value = json.loads(fixture.read_text())
            value["expected"]["evidenceCount"] = 2
            fixture.write_text(json.dumps(value))
            report = inspect_provider_installation(package)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PROVIDER_FIXTURE_EXPECTATION_MISMATCH"},
        )

    def test_isolated_install_rejects_additional_provider_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.write_package(Path(directory))
            self.make_installable(package)
            metadata = package / "pyproject.toml"
            metadata.write_text(metadata.read_text().replace(
                'fixture = "fixture_provider.provider:registration"',
                'fixture = "fixture_provider.provider:registration"\n'
                'duplicate = "fixture_provider.provider:registration"',
            ))
            report = inspect_provider_installation(package)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"PROVIDER_INSTALLED_ENTRY_POINT_INVALID"},
        )


if __name__ == "__main__":
    unittest.main()
