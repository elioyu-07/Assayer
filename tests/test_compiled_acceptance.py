from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_acceptance_module():
    name = "assayer_compiled_acceptance"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "compiled_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compiled_acceptance = _load_acceptance_module()

FIXTURE = Path(__file__).parent / "fixtures" / "plugins" / "policy-pack"


class CompiledAcceptanceDriverTests(unittest.TestCase):
    def _provider_registry(self):
        from assayer_document_navigation import markdown_registration
        from assayer_platform.provider_registry import ProviderRegistry

        return ProviderRegistry([markdown_registration()])

    def test_driver_produces_a_verified_artifact_from_the_generic_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = compiled_acceptance.run_driver(
                FIXTURE, Path(directory), provider_registry=self._provider_registry(),
            )
            artifact = Path(evidence["artifactPath"])
            artifact_digest = compiled_acceptance._digest_bytes(artifact.read_bytes())

        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["runStatus"], "completed")
        self.assertEqual(evidence["finalizedStatus"], "completed")
        self.assertEqual(set(evidence["distributions"]), set(compiled_acceptance.DISTRIBUTIONS))
        self.assertTrue(evidence["verdicts"])
        self.assertEqual(
            {item["state"] for item in evidence["verdicts"]},
            {"satisfied"},
        )
        self.assertEqual(artifact_digest, evidence["artifactDigest"])
        self.assertNotEqual(evidence["artifactDigest"], evidence["coverageDigest"])

    def test_driver_writes_a_result_artifact_with_the_ledger_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = compiled_acceptance.run_driver(
                FIXTURE, Path(directory), provider_registry=self._provider_registry(),
            )
            payload = json.loads(Path(evidence["artifactPath"]).read_text(encoding="utf-8"))

        self.assertEqual(payload["kind"], "compiled-run-result")
        self.assertEqual(payload["runId"], evidence["runId"])
        self.assertEqual(payload["status"], evidence["runStatus"])
        self.assertEqual(payload["coverageDigest"], evidence["coverageDigest"])
        self.assertEqual(payload["verdicts"], evidence["verdicts"])
        self.assertEqual(
            {item["state"] for item in payload["verdicts"]},
            {"satisfied"},
        )


class CompiledAcceptanceEvidenceTests(unittest.TestCase):
    def _venv(self, root: Path) -> Path:
        purelib = root / "venv" / "lib" / "python3.13" / "site-packages"
        for module in compiled_acceptance.MODULES:
            package = purelib / module
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
        return root / "venv"

    def _evidence(self, root: Path, *, origin: str | None = None, path: list[str] | None = None) -> dict:
        artifact = root / "verified-artifact.json"
        artifact.write_text("{}\n", encoding="utf-8")
        return {
            "status": "passed",
            "runStatus": "completed",
            "distributions": {name: "0.1.2" for name in compiled_acceptance.DISTRIBUTIONS},
            "moduleOrigins": {
                module: origin or str(root / "venv" / "lib" / "python3.13" / "site-packages" / module / "__init__.py")
                for module in compiled_acceptance.MODULES
            },
            "sysPath": path if path is not None else [],
            "verdicts": [{"atomId": "review-atom:1", "state": "satisfied"}],
            "artifactPath": str(artifact),
            "artifactDigest": compiled_acceptance._digest_bytes(artifact.read_bytes()),
        }

    def test_verify_evidence_accepts_a_clean_venv_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            compiled_acceptance.verify_evidence(
                self._evidence(root), venv_dir=venv_dir, repo_root=Path("/repo"),
            )

    def test_verify_evidence_rejects_a_module_outside_the_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            evidence = self._evidence(root, origin="/repo/src/assayer_platform/__init__.py")

            with self.assertRaises(SystemExit):
                compiled_acceptance.verify_evidence(evidence, venv_dir=venv_dir, repo_root=Path("/repo"))

    def test_verify_evidence_rejects_the_checkout_on_the_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            evidence = self._evidence(root, path=["/repo/src"])

            with self.assertRaises(SystemExit):
                compiled_acceptance.verify_evidence(evidence, venv_dir=venv_dir, repo_root=Path("/repo"))

    def test_verify_evidence_rejects_a_tampered_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            evidence = self._evidence(root)
            Path(evidence["artifactPath"]).write_text('{"tampered": true}\n', encoding="utf-8")

            with self.assertRaises(SystemExit):
                compiled_acceptance.verify_evidence(evidence, venv_dir=venv_dir, repo_root=Path("/repo"))

    def test_verify_evidence_rejects_an_empty_verdict_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            evidence = self._evidence(root)
            evidence["verdicts"] = []

            with self.assertRaises(SystemExit):
                compiled_acceptance.verify_evidence(evidence, venv_dir=venv_dir, repo_root=Path("/repo"))

    def test_verify_evidence_rejects_a_distribution_that_was_not_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv_dir = self._venv(root)
            evidence = self._evidence(root)
            evidence["distributions"]["assayer-platform"] = None

            with self.assertRaises(SystemExit):
                compiled_acceptance.verify_evidence(evidence, venv_dir=venv_dir, repo_root=Path("/repo"))


class CompiledAcceptanceVocabularyTests(unittest.TestCase):
    def test_acceptance_installs_the_split_distributions_and_one_provider(self):
        self.assertEqual(
            compiled_acceptance.DISTRIBUTIONS,
            ("assayer-platform", "assayer-plugin-sdk", "assayer-agent", "assayer-provider-markdown"),
        )
        self.assertNotIn("assayer", compiled_acceptance.DISTRIBUTIONS)

    def test_acceptance_five_state_vocabulary_matches_the_frozen_platform_states(self):
        from assayer_platform.compiled_interactive import TERMINAL_DECISION_STATES

        self.assertEqual(
            tuple(compiled_acceptance.FIVE_STATE_DECISIONS),
            tuple(TERMINAL_DECISION_STATES),
        )


if __name__ == "__main__":
    unittest.main()
