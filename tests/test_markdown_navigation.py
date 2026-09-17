from __future__ import annotations

import hashlib
from importlib.metadata import EntryPoint, EntryPoints
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_descriptor_publishes_the_provider_owned_result_contract(self):
        schema = MarkdownNavigationProvider.descriptor.result_schema
        self.assertIsNotNone(schema)
        self.assertEqual(schema["properties"]["format"], {"const": "markdown"})
        self.assertEqual(
            set(schema["required"]),
            {"format", "document", "sourceDigest", "units", "unitCount", "nextCursor", "coverage"},
        )

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

    def test_builtin_provider_registry_ships_no_bundled_providers(self):
        self.assertEqual(builtin_provider_registry().list(), ())
        self.assertIsNot(builtin_provider_registry(), builtin_provider_registry())

    def test_installed_catalog_discovers_markdown_via_entry_point(self):
        # Isolate installed-distribution discovery only. Load the real target
        # declared by this package rather than requiring unrelated providers
        # to be installed in the developer's interpreter.
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads(
            (root / "packages/assayer-provider-markdown/pyproject.toml").read_text(),
        )
        declarations = metadata["project"]["entry-points"]["assayer.providers"]
        entries = EntryPoints([
            EntryPoint(name=name, value=target, group="assayer.providers")
            for name, target in declarations.items()
        ])
        with patch("assayer_platform.provider_registry.metadata.entry_points", return_value=entries):
            registry = installed_provider_registry()
        registration = registry.select(provider_id="assayer.document-navigation")
        self.assertEqual(registration.descriptor.capabilities[0].name, "document_navigation")
        self.assertEqual(
            sorted(item.descriptor.provider_id for item in registry.list()),
            ["assayer.document-navigation"],
        )

    def test_platform_collection_binds_markdown_evidence_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Overview\n\nA bounded document.\n", encoding="utf-8")
            raw = path.read_bytes()
            bound, check, item = self._bound_source(path)
            try:
                result = bound.collect(item, check, "document_navigation")
                self.assertIsNone(result.failure)
                self.assertEqual(len(result.evidence), 1)
                evidence = result.evidence[0]
                self.assertEqual(evidence.work_item_id, item.work_item_id)
                self.assertEqual(evidence.source_state_digest, item.state_digest)
                self.assertEqual(evidence.payload["unitCount"], 2)
                self.assertEqual(evidence.payload["units"][1]["excerpt"], "A bounded document.")
                self.assertEqual(bound.issued_evidence()[evidence.evidence_id], evidence)
                self.assertEqual(bound.collect(item, check, "document_navigation"), result)
            finally:
                bound.close()

    def test_platform_collection_classifies_changed_and_missing_source(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "spec.md"
                path.write_text("# Original\n", encoding="utf-8")
                bound, check, item = self._bound_source(path)
                try:
                    if missing:
                        path.unlink()
                    else:
                        path.write_text("# Changed\n", encoding="utf-8")
                    result = bound.collect(item, check, "document_navigation")
                    self.assertEqual(result.failure.code, "source_error" if missing else "source_changed")
                    self.assertEqual(result.evidence, ())
                    self.assertEqual(bound.issued_evidence(), {})
                finally:
                    bound.close()

    def test_scope_supplied_digest_cannot_defeat_source_change_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Original\n", encoding="utf-8")
            bound, check, item = self._bound_source(path)
            try:
                path.write_text("# Changed\n", encoding="utf-8")
                current = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertNotEqual(current, item.state_digest)
                result = bound.collect(
                    item, check, "document_navigation",
                    scope={
                        "path": str(path),
                        "format": "markdown",
                        "sourceDigest": current,
                    },
                )
                self.assertEqual(result.failure.code, "source_changed")
                self.assertEqual(result.evidence, ())
                self.assertEqual(bound.issued_evidence(), {})
            finally:
                bound.close()

    def test_scope_supplied_digest_matching_the_pin_still_collects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Overview\n\nA bounded document.\n", encoding="utf-8")
            bound, check, item = self._bound_source(path)
            try:
                result = bound.collect(
                    item, check, "document_navigation",
                    scope={
                        "path": str(path),
                        "format": "markdown",
                        "sourceDigest": item.state_digest,
                    },
                )
                self.assertIsNone(result.failure)
                self.assertEqual(len(result.evidence), 1)
                self.assertEqual(result.evidence[0].source_state_digest, item.state_digest)
            finally:
                bound.close()

    def test_provider_discovers_frozen_markdown_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# Overview\n\nA bounded document.\n", encoding="utf-8")
            raw = path.read_bytes()
            registration = markdown_registration()
            profile = CapabilityProfile(frozenset({"document_navigation"}))
            scope = {"path": str(path), "format": "markdown"}
            negotiation = CapabilityNegotiator().negotiate(
                registration, ("document_navigation",), profile,
                user_profile=profile, scope=scope,
            )
            provider = registration.create_provider()
            snapshots = provider.discover_sources(scope, negotiation.context("run-discovery"))

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].source_identity, str(path.resolve()))
        self.assertEqual(snapshots[0].state_digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(snapshots[0].metadata["format"], "markdown")
        self.assertEqual(snapshots[0].metadata["byteCount"], len(raw))

    @staticmethod
    def _bound_source(path):
        registration = markdown_registration()
        scope = {"path": str(path), "format": "markdown"}
        profile = CapabilityProfile(frozenset({"document_navigation"}))
        negotiation = CapabilityNegotiator().negotiate(
            registration, ("document_navigation",), profile,
            user_profile=profile, scope=scope,
        )
        bound = BoundCapabilityProvider(registration, negotiation, run_id="markdown-baseline", scope=scope)
        check = CheckContract(
            check_id="MD-001", version="1.0.0", subject_kinds=("markdown_document",),
            dimensions=("content",), required_capabilities=("document_navigation",),
            required_evidence_kinds=("structured",),
            decision_states=("satisfied", "violated", "unknown", "blocked", "not_applicable"),
            capability_missing_outcome="needs_review", invalidation_signals=("source_digest",),
        )
        item = WorkItem("markdown-source", "markdown_document", str(path), hashlib.sha256(path.read_bytes()).hexdigest())
        return bound, check, item
