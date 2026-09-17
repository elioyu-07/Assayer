import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from assayer_platform.registry import schema_store


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"


def load_validator(filename):
    schemas = schema_store(SCHEMA_ROOT)
    schema = schemas[filename]
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=schemas))


class SchemaContractTest(unittest.TestCase):
    def test_platform_plugin_manifest_accepts_domain_neutral_contract(self):
        validator = load_validator("plugin-manifest.schema.json")
        manifest = {
            "pluginId": "example.config-quality", "version": "1.0.0", "platformApiVersion": "1.0.0",
            "compatibility": {"protocolMinVersion": "1.2.0", "protocolMaxVersion": "1.2.0", "sdkMinVersion": "0.1.2", "sdkMaxVersion": "0.1.2"},
            "domains": ["configuration-quality"],
            "subjectKinds": ["configuration_file"],
            "checks": [{
                "checkId": "CFG-001", "version": "1.0.0",
                "subjectKinds": ["configuration_file"],
                "dimensions": ["required_keys", "value_types"],
                "decisionStates": ["issue_found", "scanned_no_issue", "needs_review"],
                "requiredEvidenceKinds": ["structured"],
                "requiredCapabilities": ["structured_read"],
                "capabilityMissingOutcome": "needs_review",
                "invalidationSignals": ["source_digest"],
            }],
            "executionProfile": {
                "discoverBatching": "allowed", "inspectBatching": "allowed",
                "decisionBatching": "allowed", "parallelism": "forbidden",
                "cacheReuse": "allowed",
            },
        }
        validator.validate(manifest)
        with self.assertRaises(Exception):
            validator.validate({**manifest, "executionProfile": {
                **manifest["executionProfile"], "parallelism": "unsafe"
            }})

    def test_platform_ledger_schema_accepts_generic_runtime_ledger(self):
        validator = load_validator("platform-ledger.schema.json")
        ledger = {
            "run": {
                "run_id": "run-001", "plugin_id": "example.plugin", "plugin_version": "1.0.0",
                "check_id": "CFG-001", "check_version": "1.0.0", "scope_digest": "a" * 64,
                "started_at": "2026-09-02T00:00:00Z",
            },
            "status": "completed", "operations": [], "events": [], "receipts": [], "artifacts": [],
            "work_items": [], "investigations": [], "decisions": [], "failures": [], "decision_authority": "platform",
        }
        validator.validate(ledger)


if __name__ == "__main__":
    unittest.main()
