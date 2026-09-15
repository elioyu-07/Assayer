from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_operator_acceptance.py"
SPEC = importlib.util.spec_from_file_location("record_operator_acceptance", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("operator acceptance recorder cannot be loaded")
record_operator_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record_operator_acceptance)


class RecordOperatorAcceptanceTests(unittest.TestCase):
    def _prepared(self, root: Path) -> Path:
        evidence = root / "execution"
        artifacts = evidence / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "transport-events.jsonl").write_text(
            '{"type":"error","message":"request timed out"}\n', encoding="utf-8",
        )
        (evidence / "environment.json").write_text(json.dumps({
            "releaseCandidate": {"pluginVersion": "0.1.2+codex.test"},
        }), encoding="utf-8")
        (evidence / "scenario.json").write_text(json.dumps({
            "executionId": "execution-test",
            "gateId": "OPR-J04-B01",
            "phase": "prepared",
            "requiredObservations": [
                "correct_skill_and_tool_routing",
                "no_internal_protocol_input",
            ],
        }), encoding="utf-8")
        (evidence / "artifact-index.json").write_text(json.dumps({
            "artifacts": [],
        }), encoding="utf-8")
        return evidence

    def test_records_blocked_model_transport_without_claiming_product_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = self._prepared(Path(directory))
            result = record_operator_acceptance.record_blocked(
                evidence,
                code="MODEL_TRANSPORT_TIMEOUT",
                summary="The model transport did not complete after bounded retries.",
                thread_id="thread-test",
                evidence_file=evidence / "artifacts/transport-events.jsonl",
                codex_version="codex-cli 0.153.0",
                observed_at=datetime(2026, 9, 14, 4, 5, 6, tzinfo=timezone.utc),
            )

            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["blocker"]["productJourneyStarted"])
            self.assertFalse(result["releaseAdmission"])
            observations = {item["id"]: item["status"] for item in result["observations"]}
            self.assertEqual(observations["correct_skill_and_tool_routing"], "blocked")
            self.assertEqual(observations["no_internal_protocol_input"], "passed")
            scenario = json.loads((evidence / "scenario.json").read_text(encoding="utf-8"))
            environment = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
            artifact_index = json.loads((evidence / "artifact-index.json").read_text(encoding="utf-8"))
            self.assertEqual(scenario["status"], "blocked")
            self.assertEqual(environment["executionIdentity"]["codexVersion"], "codex-cli 0.153.0")
            self.assertEqual(artifact_index["artifacts"][0]["privacy"], "sanitized_diagnostic")

    def test_refuses_to_overwrite_recorded_result(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = self._prepared(Path(directory))
            arguments = {
                "code": "MODEL_TRANSPORT_TIMEOUT",
                "summary": "Model transport unavailable.",
                "thread_id": "thread-test",
                "evidence_file": evidence / "artifacts/transport-events.jsonl",
                "codex_version": "codex-cli 0.153.0",
            }
            record_operator_acceptance.record_blocked(evidence, **arguments)
            with self.assertRaises(FileExistsError):
                record_operator_acceptance.record_blocked(evidence, **arguments)


if __name__ == "__main__":
    unittest.main()
