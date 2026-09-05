from __future__ import annotations

import unittest

from assayer_platform import PlatformContractError, RunOwnership


class OwnershipTest(unittest.TestCase):
    def test_active_owner_round_trips_without_secrets(self):
        owner = RunOwnership("run-123", "epoch-a", 42)
        value = owner.as_dict()
        self.assertNotIn("scope", value)
        self.assertNotIn("token", value)
        restored = RunOwnership.from_mapping(value)
        restored.assert_active("run-123", "epoch-a")

    def test_released_owner_is_fenced(self):
        owner = RunOwnership("run-123", "epoch-a", 42).released_record("shutdown")
        with self.assertRaises(PlatformContractError) as error:
            owner.assert_active("run-123", "epoch-a")
        self.assertEqual(error.exception.code, "STALE_RUN_OWNER")

    def test_wrong_epoch_is_fenced(self):
        owner = RunOwnership("run-123", "epoch-a", 42)
        with self.assertRaises(PlatformContractError) as error:
            owner.assert_active("run-123", "epoch-b")
        self.assertEqual(error.exception.code, "STALE_RUN_OWNER")

    def test_released_record_requires_reason(self):
        with self.assertRaises(PlatformContractError):
            RunOwnership("run-123", "epoch-a", released=True)


if __name__ == "__main__":
    unittest.main()
