from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_operator_acceptance.py"
SPEC = importlib.util.spec_from_file_location("prepare_operator_acceptance", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("operator acceptance preparation script cannot be loaded")
prepare_operator_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_operator_acceptance)


class PrepareOperatorAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _release(path: Path) -> None:
        manifest = {"name": "assayer", "version": "0.1.2+codex.test"}
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr(
                "assayer-plugin/.codex-plugin/plugin.json",
                json.dumps(manifest),
            )
            bundle.writestr("assayer-plugin/scripts/launch_assayer_mcp", "#!/bin/sh\n")

    def test_prepares_isolated_path_safe_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            release = root / "assayer.zip"
            self._release(release)
            audit_input = root / "spec.md"
            audit_input.write_text("# Controlled specification\n", encoding="utf-8")
            output = root / "acceptance"

            result = prepare_operator_acceptance.prepare(
                output,
                release_artifact=release,
                controlled_input=audit_input,
                source_root=source,
                now=datetime(2026, 9, 14, 3, 4, 5, tzinfo=timezone.utc),
                execution_id="baseline-test",
            )

            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["gateId"], "OPR-J04-B01")
            evidence = Path(result["evidenceRoot"])
            scenario = json.loads((evidence / "scenario.json").read_text(encoding="utf-8"))
            environment = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
            artifact_index = json.loads((evidence / "artifact-index.json").read_text(encoding="utf-8"))
            launch = json.loads(Path(result["launchEnvironment"]).read_text(encoding="utf-8"))

            self.assertEqual(scenario["phase"], "prepared")
            self.assertNotIn("status", scenario)
            self.assertEqual(environment["releaseCandidate"]["sha256"], result["releaseCandidateSha256"])
            self.assertFalse(environment["cleanEnvironment"]["developmentCheckoutImported"])
            self.assertEqual(artifact_index["artifacts"][0]["privacy"], "controlled_synthetic")
            self.assertTrue(Path(launch["CODEX_HOME"]).is_dir())
            self.assertTrue(Path(launch["ASSAYER_OUTPUT_ROOT"]).is_dir())
            marketplace = Path(result["marketplaceRoot"])
            marketplace_manifest = json.loads(
                (marketplace / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["pluginSelector"], "assayer@assayer-operator")
            self.assertEqual(
                marketplace_manifest["plugins"][0]["source"]["path"],
                "./plugins/assayer",
            )
            self.assertTrue((marketplace / "plugins/assayer/scripts/launch_assayer_mcp").stat().st_mode & 0o100)
            self.assertFalse((evidence / "gate-results.json").exists())
            self.assertNotIn(str(source), json.dumps(environment))

    def test_rejects_output_inside_source_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "assayer.zip"
            self._release(release)
            audit_input = root / "spec.md"
            audit_input.write_text("controlled", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside the source checkout"):
                prepare_operator_acceptance.prepare(
                    root / "source-output",
                    release_artifact=release,
                    controlled_input=audit_input,
                    source_root=root,
                    execution_id="inside-source",
                )

    def test_rejects_reused_execution_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            release = root / "assayer.zip"
            self._release(release)
            audit_input = root / "spec.md"
            audit_input.write_text("controlled", encoding="utf-8")
            output = root / "acceptance"
            options = {
                "release_artifact": release,
                "controlled_input": audit_input,
                "source_root": source,
                "now": datetime(2026, 9, 14, tzinfo=timezone.utc),
                "execution_id": "duplicate",
            }
            prepare_operator_acceptance.prepare(output, **options)
            with self.assertRaises(FileExistsError):
                prepare_operator_acceptance.prepare(output, **options)

    def test_accepts_release_candidate_beside_evidence_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            output = root / "acceptance"
            release = output / "release-candidate" / "assayer.zip"
            release.parent.mkdir(parents=True)
            self._release(release)
            audit_input = root / "spec.md"
            audit_input.write_text("controlled", encoding="utf-8")

            result = prepare_operator_acceptance.prepare(
                output,
                release_artifact=release,
                controlled_input=audit_input,
                source_root=source,
                execution_id="sibling-release",
            )

            self.assertEqual(result["status"], "prepared")


if __name__ == "__main__":
    unittest.main()
