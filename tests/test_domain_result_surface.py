"""Tests for the hard-cut SDK v2 Agent surface."""

from __future__ import annotations

import unittest

from assayer_host.errors import HostError
from assayer_host.transport import InteractivePlatformMcpToolTransport
from assayer_platform.interactive import INTERACTIVE_OPERATIONS


class DomainResultSurfaceTests(unittest.TestCase):
    def test_legacy_platform_operations_are_not_in_the_agent_catalog(self):
        transport = InteractivePlatformMcpToolTransport()
        names = {item["name"] for item in transport.list_tools()}
        self.assertNotIn("checkpoint_review", names)
        self.assertNotIn("validate_checkpoint_draft", names)
        self.assertNotIn("submit_decisions", names)
        self.assertNotIn("finish_plugin_run", names)
        self.assertTrue(
            {"checkpoint_review", "validate_checkpoint_draft", "submit_decisions", "finish_plugin_run"}
            .isdisjoint(INTERACTIVE_OPERATIONS)
        )

    def test_legacy_operations_fail_as_unsupported_protocol(self):
        transport = InteractivePlatformMcpToolTransport()
        for name in (
            "checkpoint_review", "validate_checkpoint_draft",
            "submit_decisions", "finish_plugin_run",
        ):
            with self.assertRaises(HostError) as rejected:
                transport.call_tool(name, {})
            self.assertEqual(rejected.exception.code, "UNSUPPORTED_PROTOCOL")

    def test_legacy_advance_envelope_is_rejected_before_schema_translation(self):
        transport = InteractivePlatformMcpToolTransport()
        with self.assertRaises(HostError) as rejected:
            transport.call_tool("advance_plugin_run", {"closeout": {"status": "partial"}})
        self.assertEqual(rejected.exception.code, "UNSUPPORTED_PROTOCOL")


if __name__ == "__main__":
    unittest.main()
