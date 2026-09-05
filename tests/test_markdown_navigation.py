from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assayer_document_navigation import MarkdownNavigationProvider, markdown_registration, parse_markdown
from assayer_platform import (
    BoundCapabilityProvider,
    CapabilityNegotiator,
    CapabilityProfile,
    CheckContract,
    ProviderRegistry,
    builtin_provider_registry,
    installed_provider_registry,
    WorkItem,
    inspect_provider_registration,
)


class MarkdownNavigationTest(unittest.TestCase):
    def test_parser_returns_ordered_units_with_exact_source_ranges(self):
        text = """---\ntitle: Demo\n---\n# Overview\n\nA paragraph.\n\n## Fields\n\n| Field | Required |\n| --- | --- |\n| code | NOT NULL |\n\n```json\n{\"ok\": true}\n```\n"""
        document = parse_markdown(text, path="spec.md")
        self.assertEqual(document["format"], "markdown")
        self.assertEqual(document["document"]["lineCount"], 16)
        self.assertEqual([item["kind"] for item in document["units"]], [
            "front_matter", "heading", "paragraph", "heading", "table", "code_block",
        ])
        table = next(item for item in document["units"] if item["kind"] == "table")
        self.assertEqual((table["startLine"], table["endLine"]), (10, 12))
        self.assertEqual(table["metadata"]["headers"], ["Field", "Required"])
        self.assertEqual(table["metadata"]["rows"], [["code", "NOT NULL"]])
        self.assertEqual(document["units"][1]["headingPath"], ["Overview"])
        self.assertEqual(table["headingPath"], ["Overview", "Fields"])
        self.assertTrue(all(item["excerpt"] for item in document["units"]))

    def test_duplicate_blocks_have_distinct_deterministic_ids(self):
        text = "# A\n\nSame text.\n\n# B\n\nSame text.\n"
        first = parse_markdown(text)
        second = parse_markdown(text)
        self.assertEqual([item["unitId"] for item in first["units"]], [item["unitId"] for item in second["units"]])
        paragraphs = [item for item in first["units"] if item["kind"] == "paragraph"]
        self.assertEqual(len({item["unitId"] for item in paragraphs}), 2)

    def test_provider_paginates_and_preserves_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n\n| Field | Value |\n| --- | --- |\n| code | yes |\n", encoding="utf-8")
            raw = path.read_bytes()
            digest = "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
            provider = MarkdownNavigationProvider()
            request = type("Request", (), {
                "request_id": "provider-request:test",
                "provider_id": provider.descriptor.provider_id,
                "provider_version": provider.descriptor.version,
                "capability": "document_navigation",
                "source_identity": str(path),
                "state_digest": digest,
                "scope": {"path": str(path), "format": "markdown", "page": {"size": 2}},
                "limits": {"maxItems": 2},
            })()
            response = provider.collect(request, None)
            self.assertEqual(response.status, "succeeded")
            self.assertEqual(len(response.facts[0].payload["units"]), 2)
            self.assertIsNotNone(response.facts[0].payload["nextCursor"])

            # Platform WorkItems may use the historical unqualified digest;
            # provider execution must treat it as equivalent to sha256:<hex>.
            request.state_digest = digest.removeprefix("sha256:")
            self.assertEqual(provider.collect(request, None).status, "succeeded")

            request.scope = {
                "path": str(path), "format": "markdown",
                "include": ["tables"], "page": {"size": 10},
            }
            filtered = provider.collect(request, None)
            self.assertEqual(filtered.status, "succeeded")
            self.assertEqual(
                [item["kind"] for item in filtered.facts[0].payload["units"]],
                ["table"],
            )
            self.assertEqual(filtered.facts[0].payload["coverage"]["discovered"], 5)
            self.assertEqual(filtered.facts[0].payload["coverage"]["selected"], 1)

    def test_registration_passes_capability_negotiation(self):
        registration = markdown_registration()
        negotiation = CapabilityNegotiator().negotiate(
            registration, ("document_navigation",),
            CapabilityProfile(frozenset({"document_navigation"})),
            user_profile=CapabilityProfile(frozenset({"document_navigation"})),
            scope={"path": "/tmp/spec.md", "format": "markdown"},
        )
        self.assertEqual(negotiation.status, "ready")
        self.assertEqual(registration.descriptor.capabilities[0].name, "document_navigation")

    def test_registration_passes_provider_release_conformance(self):
        report = inspect_provider_registration(markdown_registration(), construct_implementation=True)
        self.assertTrue(report.passed, report.as_dict())

    def test_platform_catalog_exposes_markdown_provider_without_concrete_imports(self):
        registry = builtin_provider_registry()
        registration = registry.select(provider_id="assayer.document-navigation")
        self.assertEqual(registration.descriptor.capabilities[0].name, "document_navigation")
        self.assertIsNot(registry, builtin_provider_registry())

    def test_installed_catalog_deduplicates_assayers_built_in_entry_point(self):
        registry = installed_provider_registry()
        self.assertEqual(
            [item.descriptor.provider_id for item in registry.list()],
            ["assayer.document-navigation"],
        )
