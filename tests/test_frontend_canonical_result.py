import copy
import hashlib
import json
import unittest
from pathlib import Path

from assayer_host.frontend_canonical_result import build_frontend_canonical_result
from assayer_host.observability import render_performance_bill
from assayer_platform import PlatformContractError


ROOT = Path(__file__).resolve().parents[1]


class FrontendCanonicalResultTest(unittest.TestCase):
    def build(self, name: str, mutate=None):
        ledger = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(ledger)
        ledger_bytes = (
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        _json_bytes, _markdown_bytes, bill = render_performance_bill(
            ledger, [], ROOT,
        )
        return ledger_bytes, build_frontend_canonical_result(
            ledger, ledger_bytes=ledger_bytes, performance_bill=bill,
        )

    def test_completed_frontend_result_uses_the_common_machine_contract(self):
        ledger_bytes, result = self.build("minimal-ledger.json")
        self.assertEqual(result["run"], {
            "runId": "run-001",
            "pluginId": "assayer.frontend-audit",
            "pluginVersion": "1.0.0",
            "checkId": "FUA-10",
            "checkVersion": "1.1.0",
        })
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["complete"], True)
        self.assertEqual(result["outcomes"][0]["result"], "scanned_no_issue")
        self.assertEqual(len(result["findings"]), 4)
        self.assertEqual(result["trace"]["ledgerRef"], "audit-ledger.json")
        self.assertEqual(
            result["trace"]["ledgerDigest"], hashlib.sha256(ledger_bytes).hexdigest(),
        )
        extension = result["domainExtension"]
        self.assertEqual(
            extension["schemaId"],
            "assayer.frontend-audit/canonical-result-extension",
        )
        self.assertEqual(extension["data"]["coverage"]["pages"]["processed"], 1)

    def test_partial_frontend_result_discloses_unverified_entrypoint_scope(self):
        _ledger_bytes, result = self.build("partial-ledger.json")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["unprocessed"], 1)
        self.assertEqual(result["unverified"][0]["workItemId"], "entry-partial-001")
        self.assertEqual(result["outcomes"], [])

    def test_failed_frontend_result_suppresses_all_formal_history(self):
        def add_invalid_history(ledger):
            source = json.loads(
                (ROOT / "examples" / "minimal-ledger.json").read_text(encoding="utf-8")
            )
            ledger["pageStates"] = copy.deepcopy(source["pageStates"])
            ledger["objects"] = copy.deepcopy(source["objects"])
            ledger["dimensionFindings"] = copy.deepcopy(source["dimensionFindings"])
            ledger["assessments"] = copy.deepcopy(source["assessments"])
            for collection in (
                ledger["pageStates"], ledger["objects"], ledger["dimensionFindings"],
                ledger["assessments"],
            ):
                for item in collection:
                    item["scanId"] = ledger["scan"]["scanId"]
            for assessment in ledger["assessments"]:
                assessment["conclusionValidity"] = "invalidated"
                assessment["invalidatedBy"] = ["operation-invalidated-001"]

        _ledger_bytes, result = self.build("failed-ledger.json", add_invalid_history)
        self.assertEqual(result["conclusionValidity"], "invalidated")
        self.assertEqual(result["outcomes"], [])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["needsReview"], [])
        self.assertTrue(result["failures"])

    def test_issue_frontend_result_preserves_outcome_and_evidence_references(self):
        _ledger_bytes, result = self.build("issue-ledger.json")
        self.assertEqual(result["outcomes"][0]["result"], "issue_found")
        self.assertTrue(any(item["status"] == "violated" for item in result["findings"]))
        self.assertTrue(all(item["evidenceRefs"] for item in result["findings"]))
        self.assertEqual(result["domainExtension"]["data"]["issues"]["published"], 1)

    def test_needs_review_is_distinct_from_unprocessed_scope(self):
        def make_review(ledger):
            assessment = ledger["assessments"][0]
            assessment["result"] = "needs_review"
            assessment["reasonText"] = "Binding remains unresolved."
            assessment["coverage"]["resolvedDimensions"] = [
                "filter_present", "query_action", "reset_action",
            ]
            assessment["coverage"]["unresolvedDimensions"] = ["binding_to_list"]
            assessment["coverage"]["complete"] = False
            assessment["blocker"] = {
                "code": "BINDING_UNRESOLVED",
                "message": "Visual and DOM evidence do not identify one target list.",
            }
            finding = next(
                item for item in ledger["dimensionFindings"]
                if item["dimension"] == "binding_to_list"
            )
            finding["status"] = "unresolved"
            finding["reasonText"] = "The target list remains ambiguous."
            summary = ledger["scan"]["coverageProof"]["ruleSummaries"][0]
            summary["resultCounts"] = {"needs_review": 1}
            summary["coverageComplete"] = False
            ledger["scan"]["status"] = "partial"
            ledger["scan"]["terminalReason"] = {
                "code": "COVERAGE_PARTIAL", "message": "One fact needs review.",
            }

        _ledger_bytes, result = self.build("minimal-ledger.json", make_review)
        self.assertEqual(result["outcomes"][0]["result"], "needs_review")
        self.assertEqual(result["needsReview"][0]["unresolvedDimensions"], ["binding_to_list"])
        self.assertEqual(result["unverified"], [])

    def test_public_text_removes_secrets_and_local_paths(self):
        def add_private_text(ledger):
            ledger["assessments"][0]["reasonText"] = (
                "Checked /private/tmp/page.html with token=private-value"
            )
            ledger["dimensionFindings"][0]["reasonText"] = (
                "Evidence came from C:\\Users\\private\\page.html"
            )

        _ledger_bytes, result = self.build("minimal-ledger.json", add_private_text)
        encoded = json.dumps(result)
        self.assertNotIn("/private/tmp/page.html", encoded)
        self.assertNotIn("private-value", encoded)
        self.assertNotIn("C:\\Users\\private\\page.html", encoded)
        self.assertIn("[LOCAL_PATH]", encoded)
        self.assertIn("[REDACTED]", encoded)

    def test_detached_audit_ledger_bytes_are_rejected(self):
        ledger = json.loads(
            (ROOT / "examples" / "minimal-ledger.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(PlatformContractError, "do not match"):
            build_frontend_canonical_result(ledger, ledger_bytes=b"{}\n")


if __name__ == "__main__":
    unittest.main()
