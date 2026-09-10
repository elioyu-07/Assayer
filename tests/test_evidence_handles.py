from __future__ import annotations

import unittest

from assayer_platform.contract import EvidenceRecord, DimensionObservation, InvestigationPacket, WorkItem, PlatformContractError
from assayer_platform.evidence_handles import EvidenceHandleRegistry
from assayer_platform.evidence_reference import resolve_evidence_references
from assayer_platform.error_policy import boundary_error_policy


class EvidenceHandleRegistryTests(unittest.TestCase):
    def _packet(self):
        work_item = WorkItem("spec:one", "spec_document", "sha256:abc", "sha256:state", {})
        evidence = EvidenceRecord(
            evidence_id="evidence:one",
            work_item_id=work_item.work_item_id,
            check_id="SPEC-001",
            check_version="1.0.0",
            kind="spec",
            source_identity="sha256:abc",
            payload={"sourceChunks": [
                {"source_chunk_id": "source:one:1:1"},
                {"source_chunk_id": "source:one:2:2"},
            ]},
        )
        return InvestigationPacket(
            work_item=work_item,
            check_id="SPEC-001",
            check_version="1.0.0",
            dimensions=(DimensionObservation("CHK-01", (), (), "unresolved"),),
            evidence=(evidence,),
            recovery_status="not_required",
            case_ref=None,
            metadata={},
        )

    def test_handles_are_opaque_and_resolve_to_private_lineage(self):
        registry = EvidenceHandleRegistry.from_packet("task-1", self._packet())
        self.assertEqual(registry.handles, ("R1", "R2"))
        resolved = registry.resolve("R1")
        self.assertEqual(resolved.evidence_id, "evidence:one")
        self.assertEqual(resolved.source_chunk_id, "source:one:1:1")

    def test_unknown_handle_is_rejected(self):
        registry = EvidenceHandleRegistry.from_packet("task-1", self._packet())
        with self.assertRaises(PlatformContractError) as rejected:
            registry.resolve("evidence:one")
        self.assertEqual(rejected.exception.code, "UNKNOWN_EVIDENCE_HANDLE")

    def test_cross_task_resolution_is_rejected(self):
        registry = EvidenceHandleRegistry.from_packet("task-1", self._packet())
        with self.assertRaises(PlatformContractError) as rejected:
            registry.resolve("R1", task_key="task-2")
        self.assertEqual(rejected.exception.code, "CROSS_TASK_EVIDENCE_HANDLE")

    def test_run_local_ordinal_prevents_handle_reuse_between_tasks(self):
        first = EvidenceHandleRegistry.from_packet("task-1", self._packet())
        second = EvidenceHandleRegistry.from_packet(
            "task-2", self._packet(), start_ordinal=len(first.handles),
        )
        self.assertEqual(second.handles, ("R3", "R4"))
        with self.assertRaises(PlatformContractError) as rejected:
            second.resolve("R1")
        self.assertEqual(rejected.exception.code, "STALE_EVIDENCE_HANDLE")
        policy = boundary_error_policy(rejected.exception.code)
        self.assertEqual(policy.owner, "contract_state")
        self.assertEqual(policy.retry_disposition, "refresh_boundary")

    def test_public_sdk_resolves_source_chunks_without_plugin_indexing(self):
        records = resolve_evidence_references(self._packet(), ["source:one:1:1"])
        self.assertEqual(records[0]["source_chunk_id"], "source:one:1:1")

    def test_public_sdk_rejects_unknown_source_chunk(self):
        with self.assertRaises(PlatformContractError) as rejected:
            resolve_evidence_references(self._packet(), ["source:missing:1:1"])
        self.assertEqual(rejected.exception.code, "DOMAIN_EVIDENCE_REFERENCE_INVALID")


if __name__ == "__main__":
    unittest.main()
