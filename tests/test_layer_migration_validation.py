"""Deterministic equivalence proofs for the four-layer boundary migration.

These tests assert the platform layer interfaces are not decorative: Spec
consumes the platform Markdown navigation seam, its candidate checkpoints pass
through the generic review coverage validator, and Spec and Config publish the
same portable evidence-graph projection without losing candidate identity.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assayer_document_navigation import parse_markdown
from assayer_platform import (
    MarkdownNavigationAdapter,
    PlatformContext,
    PlatformContractError,
    validate_candidate_evidence_graph_projection,
    validate_review_submission,
)
from assayer_platform.builtin_plugins.config_quality.runtime import ConfigQualityPlugin
from assayer_spec_quality import SpecQualityPlugin


MIGRATION_SPEC = """# Product Spec: Migration Proof

## 1. Module Definition
The module provides a migration example capability.

## 2. State Model
The example has draft and active states.

## 3. Functional Requirements
FR-001 defines the observable migration behavior.
AC-FR001-01 defines a successful observable result.
CASE-01: Given a valid example, when it runs, then the result is returned.

## 4. Key Entities
The Example entity owns the example identity.

## 5. Data Fields
The identifier is a required string.

## 6. Non-functional Requirements
NFR-GEN-001 is adopted and verified by contract tests.

## 7. Success Criteria
SC-01 requires every accepted example to return one result.

## 8. References and Compliance
The project governance document is the selected authority.

## 9. Key Decisions
D-01 selects one result per accepted example.

## 10. Dependencies and Assumptions
The input source must be available.

## 11. Stage Differences
The first stage includes FR-001.

## 12. Revision History
Version 1.0 was created for the initial review.
"""


def _unit_signature(units):
    return [
        (unit["unitId"], unit["kind"], unit["startLine"], unit["endLine"])
        for unit in units
    ]


class LayerMigrationValidationTest(unittest.TestCase):
    def _spec_payload(self, root: Path):
        path = root / "spec.md"
        path.write_text(MIGRATION_SPEC, encoding="utf-8")
        plugin = SpecQualityPlugin()
        context = PlatformContext("run-migration-spec", frozenset({"structured_read"}))
        item = plugin.discover({"files": [{"path": str(path)}]}, context)[0]
        packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
        return path, packet.evidence[0].payload

    def test_spec_navigation_document_matches_standalone_markdown_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            path, payload = self._spec_payload(Path(directory))
            raw = path.read_bytes()
            adapter = MarkdownNavigationAdapter()
            context = PlatformContext("run-migration-nav", frozenset())

            direct = parse_markdown(raw, path=str(path))
            adapter_document = adapter.read_document(
                {"raw": raw, "path": str(path)}, context,
            )

            self.assertEqual(
                _unit_signature(payload["navigation"]["units"]),
                _unit_signature(direct["units"]),
            )
            self.assertEqual(
                _unit_signature(payload["navigation"]["units"]),
                _unit_signature(adapter_document["units"]),
            )

    def test_spec_and_config_publish_the_same_evidence_graph_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, spec_payload = self._spec_payload(root)

            spec_graph = spec_payload["candidateGraph"]
            validate_candidate_evidence_graph_projection(spec_graph)
            self.assertEqual(
                spec_graph["candidateCount"],
                len(spec_payload["candidateFindings"]),
            )

            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"name": "assayer", "port": 8080, "enabled": True}),
                encoding="utf-8",
            )
            config_plugin = ConfigQualityPlugin()
            config_context = PlatformContext(
                "run-migration-config", frozenset({"structured_read"}),
            )
            config_item = config_plugin.discover(
                {"files": [{"path": str(config_path), "requiredKeys": ["name", "port"]}]},
                config_context,
            )[0]
            config_packet = config_plugin.inspect(
                (config_item,), config_plugin.manifest.checks[0], config_context,
            )[0]
            config_graph = config_packet.evidence[0].payload["candidateGraph"]

            validate_candidate_evidence_graph_projection(config_graph)
            self.assertEqual(config_graph["candidateCount"], 3)
            self.assertEqual(len(config_packet.dimensions), 3)
            self.assertTrue(config_graph["coverageComplete"])

    def test_spec_candidate_ids_are_stable_across_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, first = self._spec_payload(root)
            _, second = self._spec_payload(root)

            self.assertEqual(
                [candidate["candidate_id"] for candidate in first["candidateFindings"]],
                [candidate["candidate_id"] for candidate in second["candidateFindings"]],
            )
            self.assertEqual(
                first["candidateGraph"]["candidateCount"],
                second["candidateGraph"]["candidateCount"],
            )

    def test_generic_review_validator_gates_spec_candidate_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            _, payload = self._spec_payload(Path(directory))
            candidate_ids = [
                candidate["candidate_id"] for candidate in payload["candidateFindings"]
            ]
            self.assertTrue(candidate_ids)

            complete = {"decisions": [
                {"candidateIds": [candidate_id], "disposition": "suppressed"}
                for candidate_id in candidate_ids
            ]}
            self.assertEqual(
                len(validate_review_submission(
                    complete, expected_item_ids=candidate_ids,
                )),
                len(candidate_ids),
            )

            incomplete = {"decisions": complete["decisions"][:-1]}
            with self.assertRaises(PlatformContractError) as error:
                validate_review_submission(
                    incomplete, expected_item_ids=candidate_ids,
                )
            self.assertEqual(error.exception.code, "INCOMPLETE_REVIEW_SUBMISSION")


if __name__ == "__main__":
    unittest.main()
