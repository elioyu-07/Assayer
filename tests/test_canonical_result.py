import hashlib
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import (
    PlatformContext,
    PlatformContractError,
    PlatformKernel,
    PlatformRunner,
    PluginRegistry,
    InteractivePlatformSession,
    JsonPlatformLedgerStore,
    DecisionProposal,
    Finding,
    build_canonical_result,
    validate_canonical_result,
)
from assayer_platform.builtin_plugins import builtin_plugin_registry
from assayer_platform.builtin_plugins.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)


ROOT = Path(__file__).resolve().parents[1]


class CanonicalResultTest(unittest.TestCase):
    def validator(self):
        schemas = {}
        for path in (ROOT / "schemas").glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            schemas[schema["$id"]] = schema
        schema = schemas["canonical-result.schema.json"]
        return Draft202012Validator(
            schema,
            resolver=RefResolver(schema["$id"], schema, store=schemas),
        )

    def test_runner_writes_valid_portable_result_with_exact_ledger_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformRunner(
                PluginRegistry(tuple(
                    item for item in builtin_plugin_registry().list()
                    if item.manifest.plugin_id == "assayer.config-quality"
                )),
                Path(directory) / "output",
            ).run(
                plugin_id="assayer.config-quality",
                check_id="CFG-001",
                scope={"files": [{"path": str(source)}]},
                run_id="run-canonical-result",
            )
            run_root = Path(directory) / "output" / result.run_id
            ledger_path = run_root / f"{result.run_id}.platform-ledger.json"
            canonical_path = run_root / f"{result.run_id}.canonical-result.json"
            value = json.loads(canonical_path.read_text(encoding="utf-8"))
            ledger_digest = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        self.validator().validate(value)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["conclusionValidity"], "valid")
        self.assertEqual(value["coverage"], {
            "discovered": 1,
            "processed": 1,
            "skipped": 0,
            "unprocessed": 0,
            "complete": True,
            "note": "Non-applicable WorkItem kinds are counted as skipped; failures remain processed but incomplete.",
        })
        self.assertEqual(value["outcomes"][0]["result"], "scanned_no_issue")
        self.assertEqual(len(value["evidenceGraph"]), 1)
        self.assertEqual(value["evidenceGraph"][0]["candidateCount"], 3)
        self.assertTrue(value["evidenceGraph"][0]["coverageComplete"])
        self.assertEqual(
            value["trace"]["ledgerDigest"],
            ledger_digest,
        )

    def test_finding_evidence_and_needs_review_are_derived_from_the_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.json"
            source.write_text("not-json", encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-canonical-review", frozenset({"structured_read"})),
            )
        value = build_canonical_result(result.ledger)
        self.validator().validate(value)
        self.assertEqual(value["outcomes"][0]["result"], "issue_found")
        self.assertTrue(all(item["evidenceRefs"] for item in value["findings"]))
        self.assertEqual(value["needsReview"], [])

    def test_actionable_remediations_are_validated_and_published_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.json"
            source.write_text("not-json", encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-canonical-actionable", frozenset({"structured_read"})),
            )
        decision = result.ledger.decisions[0]
        packet = result.ledger.investigations[0]
        violated = [item.dimension for item in decision.findings if item.status == "violated"]
        delivery = {
            "schemaVersion": "1.0.0", "status": "complete",
            "evidenceClaims": [{
                "claimId": "claim-config-invalid", "kind": "direct",
                "evidenceRefs": [packet.evidence[0].evidence_id],
                "observed": ["The source is not valid JSON."],
                "conclusion": "The source cannot be parsed as JSON.",
            }],
            "remediations": [{
                "remediationId": "config-invalid-json", "title": "Configuration is not valid JSON",
                "severity": "P1", "dimensions": violated,
                "affectedElements": ["configuration-document"],
                "evidenceRefs": [packet.evidence[0].evidence_id],
                "claimRefs": ["claim-config-invalid"],
                "problem": "The document cannot be parsed as JSON.",
                "impact": "The configuration cannot be loaded.",
                "recommendation": "Correct /private/tmp/settings.json with token=private-value.",
                "nextAction": "Fix the source and rerun CFG-001.",
                "closureEvidence": "A successful structured-read result.",
                "owner": {"status": "unassigned", "reason": "The source does not name an owner."},
            }],
        }
        reviewed = dataclasses.replace(decision, details={"result_delivery": delivery})
        ledger = dataclasses.replace(result.ledger, decisions=(reviewed,))

        value = build_canonical_result(ledger)

        self.validator().validate(value)
        self.assertEqual(value["actionability"], "complete")
        self.assertEqual(value["remediations"][0]["dimensions"], violated)
        self.assertEqual(value["remediations"][0]["workItemId"], decision.work_item_id)
        self.assertEqual(value["remediations"][0]["sourceRemediationId"], "config-invalid-json")
        self.assertEqual(value["remediations"][0]["claimRefs"], [value["evidenceClaims"][0]["claimId"]])
        self.assertEqual(value["evidenceClaims"][0]["sourceClaimId"], "claim-config-invalid")
        self.assertNotIn("private-value", json.dumps(value["remediations"]))
        self.assertNotIn("/private/tmp/settings.json", json.dumps(value["remediations"]))
        self.assertEqual(value["findings"][0]["dimension"], decision.findings[0].dimension)

    def test_needs_review_and_unverified_scope_have_distinct_public_meanings(self):
        class ReviewProvider:
            def decide(self, packets, check, context):
                del context
                packet = packets[0]
                findings = tuple(
                    Finding(
                        item.name,
                        "unresolved" if item.name == "value_types" else "satisfied",
                        "A semantic reviewer must confirm the expected type boundary.",
                    )
                    for item in packet.dimensions
                )
                return (DecisionProposal(
                    packet.work_item.work_item_id, check.check_id, check.version,
                    "needs_review", findings,
                    "One required dimension remains unresolved.",
                ),)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            context = PlatformContext(
                "run-canonical-needs-review", frozenset({"structured_read"}),
            )
            reviewed = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001", ReviewProvider(), context,
            )
            reviewed_value = build_canonical_result(reviewed.ledger)

            plugin = ConfigQualityPlugin()
            partial_context = PlatformContext(
                "run-canonical-unverified", frozenset({"structured_read"}),
            )
            run = InteractivePlatformSession(plugin.manifest).begin(
                partial_context, str(source), "CFG-001", "1.0.0",
                JsonPlatformLedgerStore(Path(directory) / "partial"),
            )
            run.record_discovery(plugin.discover(str(source), partial_context))
            partial = run.finish("partial")
            partial_value = build_canonical_result(partial.ledger)

        self.assertEqual(
            reviewed_value["needsReview"][0]["unresolvedDimensions"],
            ["value_types"],
        )
        self.assertEqual(reviewed_value["unverified"], [])
        self.assertEqual(partial_value["needsReview"], [])
        self.assertEqual(len(partial_value["unverified"]), 1)
        self.assertEqual(partial_value["coverage"]["unprocessed"], 1)

    def test_failed_result_invalidates_and_suppresses_formal_outcomes(self):
        result = PlatformKernel().run(
            ConfigQualityPlugin(), "/path/that/does/not/exist.json", "CFG-001",
            ConfigurationDecisionProvider(),
            PlatformContext("run-canonical-failed", frozenset({"structured_read"})),
        )
        value = build_canonical_result(result.ledger)
        self.validator().validate(value)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["conclusionValidity"], "invalidated")
        self.assertEqual(value["outcomes"], [])
        self.assertEqual(value["findings"], [])
        self.assertTrue(value["failures"])
        encoded = json.dumps(value)
        self.assertNotIn("/path/that/does/not/exist.json", encoded)
        self.assertIn("[LOCAL_PATH]", encoded)

    def test_domain_extension_is_namespaced_and_cannot_override_common_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-canonical-extension", frozenset({"structured_read"})),
            )
        value = build_canonical_result(result.ledger, domain_extension={
            "schemaId": "assayer.config-quality/result-summary",
            "schemaVersion": "1.0.0",
            "data": {
                "status": "plugin-owned-label", "items": 1,
                "diagnostic": "token=private-value at /private/tmp/source.json",
                "authorization": "plain-secret-value",
                "stdout": "raw command output",
                "/private/tmp/source-key": "private key name",
            },
        })
        self.validator().validate(value)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(
            value["domainExtension"]["data"]["status"], "plugin-owned-label",
        )
        encoded = json.dumps(value["domainExtension"])
        self.assertNotIn("private-value", encoded)
        self.assertNotIn("plain-secret-value", encoded)
        self.assertNotIn("raw command output", encoded)
        self.assertNotIn("/private/tmp/source.json", encoded)
        self.assertNotIn("/private/tmp/source-key", encoded)

    def test_replay_validation_rejects_a_result_detached_from_its_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-canonical-trace", frozenset({"structured_read"})),
            )
        value = build_canonical_result(result.ledger)
        with self.assertRaisesRegex(PlatformContractError, "ledger digest"):
            validate_canonical_result(
                value, ledger_bytes=b"different-ledger\n",
                expected_run_id=result.run_id, expected_status=result.status,
            )


if __name__ == "__main__":
    unittest.main()
