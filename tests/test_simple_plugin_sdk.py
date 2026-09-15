"""Tests for the ordinary Simple SDK authoring surface."""

from __future__ import annotations

import unittest

import assayer_plugin_sdk.simple as simple
from assayer_plugin_sdk import BrowserSnapshot
from assayer_plugin_sdk.contract import PlatformContractError
from assayer_plugin_sdk.simple import (
    Candidate,
    Document,
    Fact,
    Relation,
    Support,
    Unknown,
    invariant,
    policy_plugin,
)
from assayer_plugin_sdk.simple_compiler import compile_invariants


class SimplePluginSdkTests(unittest.TestCase):
    def test_public_surface_contains_exactly_eight_author_concepts(self):
        self.assertEqual(
            simple.__all__,
            [
                "policy_plugin", "Document", "Candidate", "Fact",
                "Relation", "Support", "Unknown", "invariant",
            ],
        )
        for forbidden in (
            "ReviewAtom", "WorkItem", "EvidenceRecord", "PluginRegistration",
            "DomainResultContract", "CommitReceipt",
        ):
            self.assertNotIn(forbidden, simple.__all__)

    def test_example_plugin_compiles_domain_values_without_platform_identity(self):
        @policy_plugin(
            id="ass-spec", version="3.1.0", input="markdown",
            checks="checks.yaml", instructions="semantic-review.md",
        )
        class SpecAudit:
            def scan(self, document: Document):
                support = document.absence(
                    ["denied", "unauthorized", "data scope"],
                    scope=document.full_scope,
                )
                yield Fact("permissionMentioned", document.contains("permission"), document.lines(1, 1))
                if document.contains("permission") and isinstance(support, Support):
                    yield Candidate(
                        rule="PERM-001",
                        subject="permission behavior",
                        message="Permission behavior lacks denial rules.",
                        support=support,
                        severity="P2",
                        recommendation="Define denial behavior.",
                    )
                    yield Relation(
                        "role-to-scope", "role", "data scope",
                        "Determine whether every role has an explicit scope.",
                        support,
                    )

        document = Document._from_snapshot(
            "permission is required", evidence_refs=("evidence:frozen",),
        )
        compiled = SpecAudit()._assayer_compile_scan(document)

        self.assertEqual([item["kind"] for item in compiled["items"]], [
            "candidate", "relationship",
        ])
        self.assertEqual(compiled["items"][0]["supportRefs"], ["evidence:frozen"])
        self.assertEqual(compiled["facts"][0]["value"], True)
        self.assertEqual(compiled["unknowns"], [])
        self.assertNotIn("runId", str(compiled))
        self.assertNotIn("workItemId", str(compiled))

    def test_document_and_support_cannot_be_created_from_paths_by_authors(self):
        with self.assertRaises(PlatformContractError) as document_error:
            Document("/tmp/source.md")
        self.assertEqual(document_error.exception.code, "SIMPLE_DOCUMENT_HOST_OWNED")
        with self.assertRaises(PlatformContractError) as support_error:
            Support("evidence:anything")
        self.assertEqual(support_error.exception.code, "SIMPLE_SUPPORT_HOST_OWNED")

    def test_browser_snapshot_is_exposed_as_the_same_read_only_document_surface(self):
        snapshot = BrowserSnapshot(
            visible_text="Orders\nFilter",
            entrypoints=({"kind": "safe_action", "intent": "query"},),
            url="https://example.test/orders",
            origin="https://example.test",
            title="Orders",
            route="/orders",
            dom_digest="a" * 64,
        )
        document = Document._from_browser_snapshot(
            snapshot, evidence_refs=("provider-evidence:1",),
        )

        self.assertEqual(document.visible_text, "Orders\nFilter")
        self.assertEqual(document.url, snapshot.url)
        self.assertEqual(document.entrypoints[0]["intent"], "query")
        self.assertEqual(document.lines(1, 1)._evidence_refs, ("provider-evidence:1",))
        self.assertFalse(document.full_scope.closed)
        self.assertIs(document.browser_snapshot, snapshot)

    def test_absence_on_open_scope_returns_unknown_instead_of_false_evidence(self):
        document = Document._from_snapshot(
            "partial source", evidence_refs=("evidence:partial",), closed=False,
        )

        result = document.absence("permission", scope=document.full_scope)

        self.assertIsInstance(result, Unknown)
        self.assertIn("not closed", result.reason)

    def test_absence_rejects_a_query_that_is_present(self):
        document = Document._from_snapshot(
            "permission is explicit", evidence_refs=("evidence:frozen",),
        )
        with self.assertRaises(PlatformContractError) as rejected:
            document.absence("permission", scope=document.full_scope)
        self.assertEqual(rejected.exception.code, "SIMPLE_ABSENCE_NOT_PROVEN")

    def test_fact_value_is_detached_and_non_json_values_are_rejected(self):
        document = Document._from_snapshot(
            "value", evidence_refs=("evidence:frozen",),
        )
        source = {"nested": [1]}
        fact = Fact("shape", source, document.lines(1, 1))
        source["nested"].append(2)
        self.assertEqual(tuple(fact.value["nested"]), (1,))
        with self.assertRaises(TypeError):
            fact.value["nested"] = (2,)
        with self.assertRaises(PlatformContractError):
            Fact("bad", {1, 2}, document.lines(1, 1))

    def test_search_sections_and_absence_bind_narrow_host_chunk_support(self):
        @policy_plugin(
            id="ass-spec", version="3.1.0", input="markdown",
            checks="checks.yaml", instructions="semantic-review.md",
        )
        class ChunkAudit:
            def scan(self, document: Document):
                match = document.search("denial")[0]
                section = document.sections("Permissions")[0]
                absence = document.absence("data scope", scope=document.full_scope)
                assert isinstance(absence, Support)
                yield Fact("permissionSection", True, section)
                yield Candidate(
                    "PERM-001", "denial", "Denial is mentioned.",
                    match, "P2", "Define the denial response.",
                )
                yield Candidate(
                    "PERM-002", "data scope", "Data scope is absent.",
                    absence, "P2", "Define the data scope.",
                )

        document = Document._from_snapshot(
            "# Intro\nOverview\n# Permissions\nA denial is required.",
            evidence_refs=("evidence:index",),
            coverage_refs=("evidence:coverage",),
            chunks=(
                {
                    "anchor": "chunk:intro", "startLine": 1, "endLine": 2,
                    "text": "# Intro\nOverview", "headingPath": ["Intro"],
                },
                {
                    "anchor": "chunk:permissions", "startLine": 3, "endLine": 4,
                    "text": "# Permissions\nA denial is required.",
                    "headingPath": ["Permissions"],
                },
            ),
        )

        compiled = ChunkAudit()._assayer_compile_scan(document)

        self.assertEqual(compiled["facts"][0]["supportRefs"], ["chunk:permissions"])
        self.assertEqual(compiled["items"][0]["supportRefs"], ["chunk:permissions"])
        self.assertEqual(compiled["items"][1]["supportRefs"], ["evidence:coverage"])

    def test_invariant_compiles_all_contract_views_from_one_declaration(self):
        @invariant(
            "candidate-dimension-consistency",
            guidance="Confirmed candidates require a failed dimension.",
            location="/dimensions",
            positive=({"candidate": True, "failed": True},),
            negative=({"candidate": True, "failed": False},),
        )
        def validate(value):
            return not value["candidate"] or value["failed"]

        program = compile_invariants((validate,))

        self.assertEqual(
            program.schema_annotation["x-assayer-invariants"][0]["ruleId"],
            "candidate-dimension-consistency",
        )
        self.assertEqual(program.agent_rules[0]["location"], "/dimensions")
        self.assertEqual(len(program.run_generated_cases()), 2)
        with self.assertRaises(PlatformContractError) as rejected:
            program.validate({"candidate": True, "failed": False})
        self.assertEqual(rejected.exception.code, "DOMAIN_INVARIANT_VIOLATION")
        self.assertEqual(rejected.exception.errors[0]["location"], "/dimensions")


if __name__ == "__main__":
    unittest.main()
