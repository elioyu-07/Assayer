"""Tests for typed ReviewAtom projection."""

from __future__ import annotations

import json
import unittest

from assayer_platform.compiled_review_plan import compile_review_items
from assayer_platform.contract import (
    DimensionObservation,
    EvidenceRecord,
    InvestigationPacket,
    PlatformContractError,
    WorkItem,
)
from assayer_platform.incremental_review import CoverageLedger, ReviewAtom
from assayer_platform.review_binding import ReviewTaskBinding
from assayer_platform.review_projection import build_common_review_task


class TypedReviewProjectionTests(unittest.TestCase):
    @staticmethod
    def ledger() -> CoverageLedger:
        return CoverageLedger.plan(
            "run:projection",
            (
                ReviewAtom("review-atom:candidate", "work:document", "candidate", {
                    "rule": "PERM-001",
                    "subject": "permission behavior",
                    "message": "Denial behavior is missing.",
                    "severity": "P2",
                    "recommendation": "Define denial responses.",
                    "supportIds": ["evidence:permission"],
                }),
                ReviewAtom("review-atom:dimension", "work:document", "dimension", {
                    "dimension": "permissions",
                    "instruction": "Assess role and data-scope completeness.",
                    "supportIds": ["evidence:permission"],
                }),
                ReviewAtom("review-atom:relation", "work:document", "relationship", {
                    "relationship": "role-to-data-scope",
                    "left": "role",
                    "right": "data scope",
                    "instruction": "Decide whether every role has an explicit scope.",
                    "supportIds": ["evidence:permission"],
                }),
            ),
        ).offer()

    def test_typed_projection_exposes_only_allowlisted_domain_fields(self):
        ledger = self.ledger()
        binding = ReviewTaskBinding.from_ledger(
            ledger,
            ledger.batches[0].batch_id,
            evidence_bindings={"R1": "evidence:permission"},
        )

        task = build_common_review_task(ledger, binding)
        encoded = json.dumps(task)

        self.assertEqual([item["itemRef"] for item in task["items"]], ["I1", "I2", "I3"])
        self.assertTrue(all(item["support"] == ["R1"] for item in task["items"]))
        for forbidden in (
            "run:projection", "work:document", "review-atom:",
            "review-batch:", "evidence:permission", "supportIds",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_unknown_payload_fields_are_rejected_instead_of_name_filtered(self):
        atom = ReviewAtom("review-atom:leak", "work:document", "candidate", {
            "rule": "PERM-001",
            "subject": "permission behavior",
            "message": "Missing.",
            "severity": "P2",
            "recommendation": "Define it.",
            "supportIds": ["evidence:permission"],
            "path": "/private/source.md",
        })
        ledger = CoverageLedger.plan("run:leak", (atom,)).offer()
        binding = ReviewTaskBinding.from_ledger(
            ledger,
            ledger.batches[0].batch_id,
            evidence_bindings={"R1": "evidence:permission"},
        )

        with self.assertRaises(PlatformContractError) as rejected:
            build_common_review_task(ledger, binding)

        self.assertEqual(rejected.exception.code, "INVALID_REVIEW_ATOM")

    def test_unbound_internal_evidence_is_rejected(self):
        ledger = self.ledger()
        binding = ReviewTaskBinding.from_ledger(
            ledger,
            ledger.batches[0].batch_id,
            evidence_bindings={"R1": "evidence:different"},
        )

        with self.assertRaises(PlatformContractError) as rejected:
            build_common_review_task(ledger, binding)

        self.assertEqual(rejected.exception.code, "INVALID_REVIEW_BINDING")

    def test_identical_typed_context_is_projected_once_and_referenced_by_items(self):
        context = {
            "facts": [{
                "name": "permissionMentioned",
                "value": True,
                "supportIds": ["evidence:fact"],
            }],
            "unknowns": [{
                "reason": "Denial behavior is not explicit.",
                "missingInformation": ["denial response"],
            }],
        }
        atoms = (
            ReviewAtom("review-atom:context-one", "work:document", "dimension", {
                "dimension": "permissions",
                "instruction": "Review permissions.",
                "supportIds": ["evidence:permission"],
                "context": context,
            }),
            ReviewAtom("review-atom:context-two", "work:document", "dimension", {
                "dimension": "denials",
                "instruction": "Review denials.",
                "supportIds": ["evidence:permission"],
                "context": context,
            }),
        )
        ledger = CoverageLedger.plan("run:context", atoms).offer()
        binding = ReviewTaskBinding.from_ledger(
            ledger,
            ledger.batches[0].batch_id,
            evidence_bindings={
                "R1": "evidence:permission", "R2": "evidence:fact",
            },
        )

        task = build_common_review_task(ledger, binding)

        self.assertEqual(len(task["contexts"]), 1)
        self.assertEqual(task["contexts"][0]["itemRefs"], ["I1", "I2"])
        self.assertEqual(
            [item["contextRef"] for item in task["items"]], ["C1", "C1"],
        )
        self.assertNotIn("context", task["items"][0])
        self.assertEqual(json.dumps(task).count("permissionMentioned"), 1)

    def test_compiled_plan_can_bind_precise_support_to_declared_dimensions(self):
        work_item = WorkItem(
            "work:document", "document", "sha256:source", "sha256:state",
        )
        evidence = EvidenceRecord(
            "evidence:document", work_item.work_item_id, "SPEC-001", "1.0.0",
            "document", work_item.identity,
            {"sourceChunks": [
                {"source_chunk_id": "source:document:1:5"},
                {"source_chunk_id": "source:document:20:25"},
            ]},
        )
        packet = InvestigationPacket(
            work_item, "SPEC-001", "1.0.0",
            (DimensionObservation(
                "consistency", ("Review consistency.",),
                (evidence.evidence_id,), "unresolved",
            ),),
            (evidence,), "not_required",
        )

        plan = compile_review_items({
            "items": [],
            "dimensionSupports": [{
                "dimension": "consistency",
                "supportRefs": ["source:document:1:5", "source:document:20:25"],
            }],
        }, run_id="run:dimension-support", packet=packet)

        self.assertEqual(plan.atoms, ())
        self.assertEqual(
            plan.dimension_supports["consistency"],
            ("source:document:1:5", "source:document:20:25"),
        )


if __name__ == "__main__":
    unittest.main()
