import dataclasses
import tempfile
import unittest
from pathlib import Path

from assayer_platform import (
    PlatformContext,
    PlatformContractError,
    PlatformKernel,
    extract_result_delivery,
)
from assayer_platform.testing.config_quality import (
    ConfigQualityPlugin,
    ConfigurationDecisionProvider,
)


class ActionableResultContractTest(unittest.TestCase):
    def _reviewed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.json"
            source.write_text("not-json", encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(source), "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext("run-actionable-result", frozenset({"structured_read"})),
            )
        return result.ledger.decisions[0], result.ledger.investigations[0]

    def test_missing_delivery_remains_explicitly_not_declared(self):
        decision, packet = self._reviewed_result()
        self.assertEqual(extract_result_delivery(decision, packet), ("not_declared", []))

    def test_declared_delivery_is_checked_against_dimensions_and_evidence(self):
        decision, packet = self._reviewed_result()
        violated = [item.dimension for item in decision.findings if item.status == "violated"]
        evidence_id = packet.evidence[0].evidence_id
        raw = {
            "schemaVersion": "1.0.0", "status": "complete", "remediations": [{
                "remediationId": "config-invalid-json", "title": "Configuration is not valid JSON",
                "severity": "P1", "dimensions": violated,
                "affectedElements": ["configuration-document"], "evidenceRefs": [evidence_id],
                "problem": "The document cannot be parsed as JSON.",
                "impact": "The configuration cannot be loaded deterministically.",
                "recommendation": "Correct the document syntax and add validation.",
                "nextAction": "Fix the JSON and rerun CFG-001.",
                "closureEvidence": "A successful structured read with a passing validation result.",
                "owner": {"status": "unassigned", "reason": "The source does not name an owner."},
            }],
        }
        reviewed = dataclasses.replace(decision, details={"result_delivery": raw})
        status, remediations = extract_result_delivery(reviewed, packet)
        self.assertEqual(status, "complete")
        self.assertEqual(remediations[0]["remediationId"], "config-invalid-json")

    def test_delivery_cannot_reference_foreign_evidence(self):
        decision, packet = self._reviewed_result()
        violated = [item.dimension for item in decision.findings if item.status == "violated"]
        raw = {
            "schemaVersion": "1.0.0", "status": "complete", "remediations": [{
                "remediationId": "foreign-evidence", "title": "Invalid evidence binding",
                "severity": "P2", "dimensions": violated,
                "affectedElements": ["configuration-document"], "evidenceRefs": ["evidence:foreign"],
                "problem": "The evidence is not from this investigation.",
                "impact": "The result cannot be trusted.", "recommendation": "Collect bound evidence.",
                "nextAction": "Rerun the investigation.", "closureEvidence": "Bound evidence reference.",
                "owner": {"status": "unassigned", "reason": "No owner is named."},
            }],
        }
        with self.assertRaisesRegex(PlatformContractError, "outside this InvestigationPacket"):
            extract_result_delivery(dataclasses.replace(decision, details={"result_delivery": raw}), packet)

    def test_absence_claim_requires_bounded_search_and_binds_to_remediation(self):
        decision, packet = self._reviewed_result()
        violated = [item.dimension for item in decision.findings if item.status == "violated"]
        evidence_id = packet.evidence[0].evidence_id
        raw = {
            "schemaVersion": "1.0.0", "status": "complete",
            "evidenceClaims": [{
                "claimId": "claim-missing-type", "kind": "absence",
                "evidenceRefs": [evidence_id],
                "scope": {"sourceRef": evidence_id, "startLine": 1, "endLine": 12},
                "searchedFor": ["A declared value type"],
                "observed": ["No type declaration was found in the inspected document."],
                "conclusion": "No declared value type was found in the inspected scope.",
            }],
            "remediations": [{
                "remediationId": "missing-type", "title": "Value type is absent",
                "severity": "P2", "dimensions": violated,
                "affectedElements": ["configuration-document"], "evidenceRefs": [evidence_id],
                "claimRefs": ["claim-missing-type"],
                "problem": "The document does not declare a value type.",
                "impact": "Consumers cannot validate the value deterministically.",
                "recommendation": "Declare the expected value type.",
                "nextAction": "Add the type and rerun CFG-001.",
                "closureEvidence": "A structured read confirms the declared type.",
                "owner": {"status": "unassigned", "reason": "The source does not name an owner."},
            }],
        }
        reviewed = dataclasses.replace(decision, details={"result_delivery": raw})
        status, remediations = extract_result_delivery(reviewed, packet)
        self.assertEqual(status, "complete")
        self.assertEqual(remediations[0]["claimRefs"], ["claim-missing-type"])

    def test_claim_scope_cannot_reverse_lines_or_escape_packet(self):
        decision, packet = self._reviewed_result()
        violated = [item.dimension for item in decision.findings if item.status == "violated"]
        evidence_id = packet.evidence[0].evidence_id
        claim = {
            "claimId": "claim-invalid-scope", "kind": "absence",
            "evidenceRefs": [evidence_id],
            "scope": {"sourceRef": evidence_id, "startLine": 4, "endLine": 2},
            "searchedFor": ["a value type"], "conclusion": "No type was found.",
        }
        raw = {
            "schemaVersion": "1.0.0", "status": "partial", "evidenceClaims": [claim],
            "remediations": [{
                "remediationId": "invalid-scope", "title": "Invalid claim scope", "severity": "P2",
                "dimensions": violated, "affectedElements": ["configuration-document"],
                "evidenceRefs": [evidence_id], "claimRefs": ["claim-invalid-scope"],
                "problem": "The claim is invalid.", "impact": "The result is not trustworthy.",
                "recommendation": "Collect a bounded claim.", "nextAction": "Rerun the investigation.",
                "closureEvidence": "A valid bounded claim.",
                "owner": {"status": "unassigned", "reason": "No owner is named."},
            }],
        }
        with self.assertRaisesRegex(PlatformContractError, "reversed line scope"):
            extract_result_delivery(dataclasses.replace(decision, details={"result_delivery": raw}), packet)


if __name__ == "__main__":
    unittest.main()
