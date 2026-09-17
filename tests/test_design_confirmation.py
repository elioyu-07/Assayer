from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_design_confirmation import validate_changed_paths, validate_confirmation


ROOT = Path(__file__).resolve().parents[1]


def _record(**overrides):
    value = {
        "schemaVersion": "1.0.0",
        "changeId": "example-platform-boundary",
        "changeType": "platform_change",
        "title": "Example platform boundary change",
        "summary": "Prove the platform owns the capability.",
        "changedPaths": ["docs/example.md", "scripts/example.py"],
        "authorityDocuments": ["docs/platform-constitution-v2.md"],
        "ownership": {
            "platform": ["capability contract"],
            "plugin": ["domain requirements"],
            "provider": ["source snapshot"],
            "agent": ["bounded semantic review"],
        },
        "capabilities": {"inputKinds": ["document"], "required": ["structured_read"]},
        "forbiddenResponsibilities": ["plugin lifecycle", "plugin persistence"],
        "publicContractImpact": "compatible",
        "acceptance": ["source-boundary", "installed-wheel"],
        "humanApproval": {
            "required": True,
            "status": "approved",
            "approver": "maintainer",
            "approvedAt": "2026-09-15T00:00:00Z",
        },
        "state": "approved",
    }
    value.update(overrides)
    return value


class DesignConfirmationTests(unittest.TestCase):
    def test_valid_approved_public_change_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "confirmation.json"
            path.write_text(json.dumps(_record()), encoding="utf-8")
            self.assertEqual(validate_confirmation(path, root=ROOT), ())

    def test_public_change_cannot_skip_human_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "confirmation.json"
            path.write_text(
                json.dumps(_record(humanApproval={"required": True, "status": "pending"})),
                encoding="utf-8",
            )
            issues = validate_confirmation(path, root=ROOT)
        self.assertIn("public-impact changes require approved humanApproval", issues)

    def test_unknown_authority_document_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "confirmation.json"
            path.write_text(
                json.dumps(_record(authorityDocuments=["docs/does-not-exist.md"])),
                encoding="utf-8",
            )
            issues = validate_confirmation(path, root=ROOT)
        self.assertIn("authority document does not exist: docs/does-not-exist.md", issues)

    def test_changed_path_must_be_covered_by_approved_record(self):
        record = _record()
        self.assertEqual(validate_changed_paths([record], ["docs/example.md"]), ())
        issues = validate_changed_paths([record], ["src/unplanned.py"])
        self.assertEqual(
            issues,
            ("changed path is not covered by an approved design: src/unplanned.py",),
        )


if __name__ == "__main__":
    unittest.main()
