import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform import PLATFORM_API_VERSION
from assayer_platform.contract import DECISION_STATES


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
CONTRACTS = (
    ROOT / "docs/platform-constitution-v1.md",
    ROOT / "docs/plugin-contract-v1.md",
    ROOT / "docs/capability-provider-contract-v1.md",
    ROOT / "docs/canonical-result-contract-v1.md",
    ROOT / "docs/platform-contract-traceability-v1.md",
)


def validator(filename: str) -> Draft202012Validator:
    schemas = {}
    for path in SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[schema["$id"]] = schema
        schemas[path.name] = schema
    schema = schemas[filename]
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


class PlatformV1ContractTests(unittest.TestCase):
    def test_frozen_contracts_share_v1_and_have_valid_local_links(self):
        for path in CONTRACTS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("| Document version | 1.0.0 |", text, path.name)
            for target in re.findall(r"\[[^]]+\]\(([^)#]+\.md)(?:#[^)]+)?\)", text):
                self.assertTrue((path.parent / target).is_file(), f"{path.name}: {target}")
        self.assertEqual(PLATFORM_API_VERSION, "1.0.0")

    def test_generic_schema_vocabulary_has_no_frontend_only_properties(self):
        forbidden = {"pagestate", "dom", "tab", "screenshot", "chromium"}
        files = (
            "plugin-manifest.schema.json",
            "platform-ledger.schema.json",
            "capability-provider.schema.json",
            "canonical-result.schema.json",
        )

        def property_names(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "properties":
                        yield from item.keys()
                    yield from property_names(item)
            elif isinstance(value, list):
                for item in value:
                    yield from property_names(item)

        for filename in files:
            schema = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
            leaked = forbidden.intersection(name.lower() for name in property_names(schema))
            self.assertFalse(leaked, f"{filename} leaks frontend properties: {sorted(leaked)}")

    def test_canonical_result_accepts_completed_and_rejects_failed_outcomes(self):
        check = validator("canonical-result.schema.json")
        result = {
            "schemaVersion": "1.0.0",
            "run": {
                "runId": "run-001", "pluginId": "example.plugin",
                "pluginVersion": "1.0.0", "checkId": "CFG-001",
                "checkVersion": "1.0.0",
            },
            "status": "completed", "conclusionValidity": "valid",
            "coverage": {
                "discovered": 1, "processed": 1, "skipped": 0,
                "unprocessed": 0, "complete": True,
            },
            "outcomes": [{
                "workItemId": "work-001", "checkId": "CFG-001",
                "checkVersion": "1.0.0", "result": "scanned_no_issue",
                "reason": "All required dimensions are satisfied.",
                "receiptId": "commit-001",
            }],
            "findings": [{
                "findingId": "finding-001", "workItemId": "work-001",
                "checkId": "CFG-001", "checkVersion": "1.0.0",
                "dimension": "valid", "status": "satisfied",
                "reason": "The source is valid.", "evidenceRefs": ["evidence-001"],
            }],
            "needsReview": [], "unverified": [], "failures": [],
            "performance": {
                "wallClock": {"status": "measured", "durationMs": 12},
                "agentWait": {"status": "unavailable", "reason": "The client did not expose model timing."},
                "transport": {"status": "measured", "durationMs": 1},
                "host": {"status": "measured", "durationMs": 8},
                "provider": {"status": "measured", "durationMs": 3},
            },
            "trace": {"ledgerRef": "platform-ledger.json", "ledgerDigest": "a" * 64},
        }
        check.validate(result)
        failed = {**result, "status": "failed", "conclusionValidity": "invalidated"}
        self.assertTrue(list(check.iter_errors(failed)))

    def test_provider_descriptor_freezes_authorization_limits_and_failures(self):
        check = validator("capability-provider.schema.json")
        descriptor = {
            "providerId": "example.file-provider", "version": "1.0.0",
            "platformApiVersion": "1.0.0",
            "capabilities": [{
                "name": "structured_read", "version": "1.0.0",
                "accessMode": "read_only", "evidenceKinds": ["structured"],
            }],
            "scopeSchema": {"type": "object"},
            "authorization": {"userScopeRequired": True, "secretHandling": "none"},
            "limits": {"timeoutMs": 30000, "maxBytes": 1000000, "maxItems": 100, "maxConcurrency": 1},
            "failurePolicy": [{"code": "timeout", "retry": "safe_with_same_request"}],
            "algorithmVersions": {"sourceDigest": "1.0.0"},
        }
        check.validate(descriptor)
        broken = {**descriptor, "authorization": {"userScopeRequired": True}}
        self.assertTrue(list(check.iter_errors(broken)))

    def test_decision_states_match_the_frozen_common_schema(self):
        common = json.loads((SCHEMAS / "common.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(common["$defs"]["decisionResult"]["enum"]), set(DECISION_STATES))

    def test_staged_result_delivery_contract_accepts_summary_first_metadata(self):
        check = validator("result-delivery.schema.json")
        check.validate({
            "schemaVersion": "1.0.0",
            "mode": "summary_first",
            "sourceDigest": "a" * 64,
            "detailSectionCount": 3,
            "detailsAvailable": True,
            "pageBudgetBytes": 65536,
            "nextAction": "Request one detail section when needed.",
        })


if __name__ == "__main__":
    unittest.main()
