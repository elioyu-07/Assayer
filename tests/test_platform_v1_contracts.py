"""Repository-wide governance checks for the frozen v1 contracts.

Producer and consumer behavior belongs to focused runtime suites. This file
only protects relationships that cannot be proved by one producer: document
linkage, domain-neutral vocabulary, and the decision-state source of truth.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from assayer_platform import PLATFORM_API_VERSION
from assayer_platform.contract import DECISION_STATES
from assayer_platform.registry import schema_path
from scripts.check_document_boundaries import find_violations


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
CONTRACTS = (
    ROOT / "docs/platform-constitution-v1.md",
    ROOT / "docs/plugin-contract-v1.md",
    ROOT / "docs/plugin-development-standard-v1.md",
    ROOT / "docs/audit-report-format-v1.md",
    ROOT / "docs/capability-provider-contract-v1.md",
    ROOT / "docs/canonical-result-contract-v1.md",
    ROOT / "docs/platform-contract-traceability-v1.md",
)
V2_AUTHORITY = ROOT / "docs/platform-constitution-v2.md"


class PlatformV1ContractTests(unittest.TestCase):
    def test_v2_constitution_is_the_active_authority(self):
        text = V2_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("| Document version | 2.0.0 |", text)
        self.assertIn("This Constitution is the highest technical authority", text)
        self.assertIn("Ordinary plugins contain no Python", text)
        self.assertIn("element × Check × Dimension", text)

    def test_normative_documents_remain_domain_neutral(self):
        violations = find_violations(ROOT)
        self.assertEqual(violations, ())

    def test_contract_documents_use_v1_and_have_valid_local_links(self):
        for path in CONTRACTS:
            text = path.read_text(encoding="utf-8")
            version = re.search(r"\| Document version \| (\d+)\.(\d+)\.(\d+) \|", text)
            self.assertIsNotNone(version, path.name)
            self.assertEqual(version.group(1), "1", path.name)
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
            "platform-performance-bill.schema.json",
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
            schema = json.loads(schema_path(filename, SCHEMAS).read_text(encoding="utf-8"))
            leaked = forbidden.intersection(name.lower() for name in property_names(schema))
            self.assertFalse(leaked, f"{filename} leaks frontend properties: {sorted(leaked)}")

    def test_decision_states_match_the_frozen_common_schema(self):
        common = json.loads(schema_path("common.schema.json", SCHEMAS).read_text(encoding="utf-8"))
        self.assertEqual(set(common["$defs"]["decisionResult"]["enum"]), set(DECISION_STATES))


if __name__ == "__main__":
    unittest.main()
