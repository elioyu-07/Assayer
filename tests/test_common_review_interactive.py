"""End-to-end tests for common review in the interactive Host lifecycle."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from assayer_platform.contract import DimensionObservation
from assayer_platform.incremental_review import JsonCoverageLedgerStore
from assayer_platform.interactive import InteractivePluginController
from assayer_platform.ledger import JsonPlatformLedgerStore
from assayer_platform.error_policy import boundary_error_policy
from assayer_platform.plugin_registry import PluginRegistry
from assayer_platform.contract import PlatformContractError
from assayer_host.errors import HostError
from assayer_host.transport import InteractivePlatformMcpToolTransport
from assayer_plugin_sdk.simple import (
    Candidate, Document, Fact, Relation, Support, Unknown, invariant, policy_plugin,
)
from tests.helpers import ConfigQualityPlugin, config_quality_registration


def _submission(task: dict[str, object]) -> dict[str, object]:
    decisions = []
    for item in task["items"]:
        if item["kind"] == "dimension":
            value = {
                "dimension": item["dimension"],
                "verdict": "satisfied",
                "reason": "The frozen observations are sufficient.",
                "applicability": {"state": "applicable"},
                "confidence": {"level": "high"},
                "support": {"refs": item["support"]},
            }
        elif item["kind"] == "candidate":
            value = {
                "disposition": "suppressed",
                "reason": "The candidate is not a semantic issue.",
                "support": {"refs": item["support"]},
            }
        else:
            value = {
                "relationship": item["relationship"],
                "verdict": "confirmed",
                "reason": "The relationship is explicit.",
                "applicability": {"state": "applicable"},
                "confidence": {"level": "high"},
                "support": {"refs": item["support"]},
            }
        decisions.append({
            "itemRef": item["itemRef"],
            "kind": item["kind"],
            "value": value,
        })
    return {
        "decisions": decisions,
    }


def _delivered_text(
    controller: InteractivePluginController, run_id: str, value: object,
) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict) or value.get("delivery") != "chunked_text":
        raise AssertionError("terminal text is neither inline nor staged")
    chunks: list[str] = []
    cursor = None
    while True:
        result = controller.get_result(
            run_id, value["sectionId"], cursor=cursor, page_size=50,
        )["result"]
        chunks.extend(result["items"])
        cursor = result["nextCursor"]
        if cursor is None:
            return "".join(chunks)


class _NoDomainHooksPlugin(ConfigQualityPlugin):
    def map_domain_result(self, result, packet, check, context):
        del result, packet, check, context
        raise AssertionError("common review must not invoke plugin mapping")

    def summarize(self, work_items, investigations, decisions, status):
        del work_items, investigations, decisions, status
        raise AssertionError("common review must not invoke plugin summary")

    def finalize(self, work_items, investigations, decisions, output_root, status):
        del work_items, investigations, decisions, output_root, status
        raise AssertionError("common review must not invoke plugin finalization")


class _ManyDimensionsPlugin(_NoDomainHooksPlugin):
    def inspect(self, work_items, check, context):
        packet = super().inspect(work_items, check, context)[0]
        evidence_id = packet.evidence[0].evidence_id
        dimensions = tuple(
            DimensionObservation(
                f"dimension-{index:03d}",
                (f"Observation {index}",),
                (evidence_id,),
                "satisfied",
            )
            for index in range(70)
        )
        return (replace(packet, dimensions=dimensions),)


class _CompiledItemsPlugin(_NoDomainHooksPlugin):
    def _assayer_compiled_review_items(self, packet, check, context):
        del check, context
        evidence_id = packet.evidence[0].evidence_id
        return {"items": [
            {
                "kind": "candidate",
                "rule": "CFG-DENY-001",
                "subject": "denial behavior",
                "message": "Denial behavior may be incomplete.",
                "severity": "P2",
                "recommendation": "Define the denial behavior.",
                "supportRefs": [evidence_id],
            },
            {
                "kind": "relationship",
                "relationship": "key-to-type",
                "left": "configuration key",
                "right": "declared value type",
                "instruction": "Determine whether every declared key has the expected type.",
                "supportRefs": [evidence_id],
            },
        ]}


class _OutOfScopeCompiledItemsPlugin(_NoDomainHooksPlugin):
    def _assayer_compiled_review_items(self, packet, check, context):
        del packet, check, context
        return {"items": [{
            "kind": "candidate",
            "rule": "CFG-INVALID",
            "subject": "configuration",
            "message": "Invalid generated support.",
            "severity": "P2",
            "recommendation": "Regenerate the adapter.",
            "supportRefs": ["evidence:outside-packet"],
        }]}


@policy_plugin(
    id="test.config-quality", version="1.0.0", input="document",
    checks="checks.yaml", instructions="semantic-review.md",
)
class _SimpleAuthorPlugin:
    @invariant(
        "decision-reason-required",
        guidance="Every batch decision requires a reason.",
        location="/decisions",
    )
    def decision_reason_required(self, value):
        return all(
            bool(item.get("value", {}).get("reason"))
            for item in value.get("decisions", ())
        )

    def scan(self, document: Document):
        support = document.lines(1, 1)
        absence = document.absence("denied", scope=document.full_scope)
        yield Fact("containsEnabled", document.contains("enabled"), support)
        yield Unknown("Denial behavior is not explicit.", ("denial response",))
        if isinstance(absence, Support):
            yield Candidate(
                "CFG-DENY-001", "denial behavior", "Denial behavior may be incomplete.",
                absence, "P2", "Define denial behavior.",
            )
            yield Relation(
                "key-to-type", "configuration key", "declared value type",
                "Determine whether every declared key has the expected type.", support,
            )


@policy_plugin(
    id="test.config-quality", version="1.0.0", input="document",
    checks="checks.yaml", instructions="semantic-review.md",
)
class _SimpleOversizedContextPlugin:
    def scan(self, document: Document):
        yield Fact("oversized", "x" * (25 * 1024), document.lines(1, 1))


@policy_plugin(
    id="test.config-quality", version="1.0.0", input="document",
    checks="checks.yaml", instructions="semantic-review.md",
)
class _BrokenInvariantPlugin:
    @invariant(
        "broken-invariant",
        guidance="This plugin invariant is defective.",
        location="/decisions",
    )
    def broken_invariant(self, value):
        del value
        raise RuntimeError("plugin invariant failure")

    def scan(self, document: Document):
        del document
        return ()


def _registration(plugin_type=_NoDomainHooksPlugin):
    base = config_quality_registration()
    manifest = base.manifest
    if plugin_type is _ManyDimensionsPlugin:
        check = replace(
            manifest.checks[0],
            dimensions=tuple(f"dimension-{index:03d}" for index in range(70)),
        )
        manifest = replace(manifest, checks=(check,))

    def create_plugin(_runtime=None):
        plugin = plugin_type()
        plugin.manifest = manifest
        return plugin

    return replace(
        base,
        manifest=manifest,
        plugin_factory=create_plugin,
        execution_modes=frozenset({"interactive"}),
        result_features=frozenset({"common_review"}),
        domain_result_contracts=(),
    )


class CommonReviewInteractiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "config.json"
        self.source.write_text(json.dumps({"enabled": True}), encoding="utf-8")

    def controller(self, plugin_type=_NoDomainHooksPlugin) -> InteractivePluginController:
        return InteractivePluginController(
            PluginRegistry((_registration(plugin_type),)), self.root / "output",
        )

    def start(self, controller: InteractivePluginController, run_id: str) -> None:
        controller.start(
            plugin_id="test.config-quality",
            check_id="CFG-001",
            scope={"files": [{"path": str(self.source)}]},
            run_id=run_id,
        )

    def transport(
        self, name: str, plugin_type=_SimpleAuthorPlugin,
    ) -> InteractivePlatformMcpToolTransport:
        transport = InteractivePlatformMcpToolTransport(
            self.root / name,
            plugin_registry=PluginRegistry((_registration(plugin_type),)),
        )
        self.addCleanup(transport.close)
        transport.call_tool("start_plugin_run", {
            "pluginId": "test.config-quality",
            "checkId": "CFG-001",
            "scope": {"files": [{"path": str(self.source)}]},
        })
        return transport

    def test_common_review_agent_errors_have_one_correction_policy(self):
        for code in (
            "INVALID_COMMON_REVIEW",
            "DOMAIN_INVARIANT_VIOLATION",
            "INCOMPLETE_REVIEW_SUBMISSION",
            "UNKNOWN_REVIEW_ITEM",
            "UNKNOWN_REVIEW_EVIDENCE",
            "REVIEW_KIND_MISMATCH",
        ):
            with self.subTest(code=code):
                policy = boundary_error_policy(code)
                self.assertEqual(policy.owner, "agent_input")
                self.assertEqual(policy.retry_disposition, "agent_correction")
                self.assertEqual(policy.required_next_step, "correct_common_review")
        for code, owner in (
            ("INVARIANT_EXECUTION_FAILED", "plugin"),
            ("INVALID_INVARIANT_RESULT", "plugin"),
            ("INVALID_REVIEW_BINDING", "platform"),
            ("INVALID_COMMON_REVIEW_TASK", "platform"),
        ):
            with self.subTest(code=code):
                policy = boundary_error_policy(code)
                self.assertEqual(policy.owner, owner)
                self.assertEqual(policy.retry_disposition, "none")
                self.assertTrue(policy.terminal_on_rejection)

    def test_common_review_transport_accepts_one_corrected_resubmission(self):
        transport = self.transport("corrected-output")
        pending = transport.call_tool("advance_plugin_run", {})
        task = pending["structuredContent"]["result"]["result"]["semanticTask"]
        invalid = _submission(task)
        invalid["decisions"][0]["value"]["reason"] = ""

        with self.assertRaises(HostError) as rejected:
            transport.call_tool("advance_plugin_run", {"reviewSubmission": invalid})

        error = rejected.exception
        self.assertEqual(error.code, "DOMAIN_INVARIANT_VIOLATION")
        self.assertEqual(error.owner, "agent_input")
        self.assertEqual(error.retry_disposition, "agent_correction")
        self.assertEqual(error.next_step, "correct_common_review")
        self.assertEqual(error.correction_budget["correctionsRemaining"], 1)
        self.assertEqual(error.errors[0]["pointer"], "/decisions")

        terminal = transport.call_tool("advance_plugin_run", {
            "reviewSubmission": _submission(task),
        })
        self.assertEqual(
            terminal["structuredContent"]["result"]["status"], "completed",
        )

    def test_common_review_transport_exhausts_a_repeated_invalid_submission(self):
        transport = self.transport("exhausted-output")
        pending = transport.call_tool("advance_plugin_run", {})
        result = pending["structuredContent"]["result"]
        run_id = result["runId"]
        task = result["result"]["semanticTask"]
        invalid = _submission(task)
        invalid["decisions"][0]["value"]["reason"] = ""

        with self.assertRaises(HostError):
            transport.call_tool("advance_plugin_run", {"reviewSubmission": invalid})
        with self.assertRaises(HostError) as exhausted:
            transport.call_tool("advance_plugin_run", {"reviewSubmission": invalid})

        error = exhausted.exception
        self.assertEqual(error.code, "AGENT_CORRECTION_BUDGET_EXHAUSTED")
        self.assertEqual(error.terminal_status, "partial")
        self.assertEqual(error.correction_budget["correctionsRemaining"], 0)
        self.assertIn("/decisions", error.message)
        coverage = JsonCoverageLedgerStore(
            self.root / "exhausted-output" / run_id,
        ).load(run_id)
        self.assertEqual(coverage.terminal_status, "partial")
        self.assertTrue(all(batch.status == "terminal" for batch in coverage.batches))

    def test_plugin_invariant_failure_never_consumes_agent_correction(self):
        transport = self.transport("plugin-error-output", _BrokenInvariantPlugin)
        pending = transport.call_tool("advance_plugin_run", {})
        task = pending["structuredContent"]["result"]["result"]["semanticTask"]

        with self.assertLogs("assayer_host.transport", level="ERROR"):
            with self.assertRaises(HostError) as rejected:
                transport.call_tool("advance_plugin_run", {
                    "reviewSubmission": _submission(task),
                })

        error = rejected.exception
        self.assertEqual(error.code, "INVARIANT_EXECUTION_FAILED")
        self.assertEqual(error.owner, "plugin")
        self.assertEqual(error.retry_disposition, "none")
        self.assertEqual(error.terminal_status, "partial")
        self.assertEqual(error.correction_budget["maximumCorrections"], 0)

    def test_multiple_common_batches_commit_and_finish_without_plugin_hooks(self):
        controller = self.controller(_ManyDimensionsPlugin)
        self.start(controller, "run:common-multiple")

        first_boundary = controller.advance("run:common-multiple")
        first_task = first_boundary["result"]["semanticTask"]
        self.assertEqual(first_task["kind"], "common_review")
        first_count = len(first_task["items"])
        self.assertLessEqual(first_count, 64)
        self.assertGreater(first_count, 0)
        self.assertEqual(first_task["items"][0]["itemRef"], "I1")
        self.assertNotIn("domainContract", first_task)
        self.assertIn("observations", first_task["items"][0])

        second_boundary = controller.advance(
            "run:common-multiple", review_submission=_submission(first_task),
        )
        second_task = second_boundary["result"]["semanticTask"]
        self.assertEqual(len(second_task["items"]), 70 - first_count)
        self.assertEqual(
            second_task["items"][0]["itemRef"], f"I{first_count + 1}",
        )

        terminal = controller.advance(
            "run:common-multiple", review_submission=_submission(second_task),
        )

        self.assertEqual(terminal["status"], "completed")
        coverage = JsonCoverageLedgerStore(
            self.root / "output" / "run:common-multiple",
        ).load("run:common-multiple")
        self.assertEqual(coverage.terminal_status, "completed")
        self.assertEqual(len(coverage.verdicts), 2)

    def test_long_formal_report_is_retrievable_and_matches_durable_artifact(self):
        controller = self.controller(_ManyDimensionsPlugin)
        run_id = "run:long-formal-report"
        self.start(controller, run_id)

        boundary = controller.advance(run_id)
        while boundary["status"] == "awaiting_agent_decision":
            task = boundary["result"]["semanticTask"]
            submission = _submission(task)
            for index, decision in enumerate(submission["decisions"]):
                if decision["kind"] != "dimension":
                    continue
                decision["value"]["verdict"] = "violated"
                decision["value"]["reason"] = (
                    f"检查项 {decision['value']['dimension']} 未满足要求："
                    f"已冻结证据显示第 {index + 1} 项必要约束未形成可验证定义。"
                )
            boundary = controller.advance(run_id, review_submission=submission)

        self.assertEqual(boundary["status"], "completed")
        report_value = boundary["result"]["auditReport"]
        self.assertIsInstance(report_value, dict)
        self.assertEqual(report_value["delivery"], "chunked_text")
        report = _delivered_text(controller, run_id, report_value)
        report_path = self.root / "output" / run_id / f"{run_id}.audit-report.md"
        self.assertEqual(report, report_path.read_text(encoding="utf-8"))
        self.assertIn(f"{run_id}.audit-report.md", boundary["result"]["artifacts"])

        replayed = controller.terminal_status(run_id)
        self.assertEqual(
            _delivered_text(controller, run_id, replayed["result"]["auditReport"]),
            report,
        )
        controller.close()

        restored = self.controller(_ManyDimensionsPlugin)
        self.assertEqual(restored.terminal_run_id, run_id)
        restored_terminal = restored.terminal_status(run_id)
        self.assertEqual(
            _delivered_text(restored, run_id, restored_terminal["result"]["auditReport"]),
            report,
        )

    def test_reviewer_origin_finding_reaches_one_terminal_report_row(self):
        controller = self.controller()
        run_id = "run:reviewer-origin-report"
        self.start(controller, run_id)
        task = controller.advance(run_id)["result"]["semanticTask"]
        submission = _submission(task)
        dimension_decisions = [
            item for item in submission["decisions"] if item["kind"] == "dimension"
        ]
        self.assertGreaterEqual(len(dimension_decisions), 2)
        primary, related = dimension_decisions[:2]
        primary_dimension = primary["value"]["dimension"]
        related_dimension = related["value"]["dimension"]
        primary["value"].update({
            "verdict": "violated",
            "reason": "Failure handling statements conflict.",
            "findings": [{
                "title": "Failure handling is contradictory",
                "message": "One statement forbids retry while another requires retry.",
                "severity": "P2",
                "recommendation": "Define one authoritative failure-handling rule.",
                "support": primary["value"]["support"],
                "affectedDimensions": [primary_dimension, related_dimension],
            }],
        })
        related["value"].update({
            "verdict": "violated",
            "reason": "Recovery behavior is affected by the same contradiction.",
        })

        terminal = controller.advance(run_id, review_submission=submission)

        self.assertEqual(terminal["status"], "completed")
        report = _delivered_text(
            controller, run_id, terminal["result"]["auditReport"],
        )
        self.assertIn("| 已完成 | 不通过 | 1 | 0 | 完整 |", report)
        self.assertEqual(report.count("Failure handling is contradictory"), 1)
        self.assertIn("2 个检查项未通过，归并为 1 个独立问题。", report)
        ledger = JsonPlatformLedgerStore(
            self.root / "output" / run_id,
        ).load(run_id)
        persisted = ledger["decisions"][0]["details"]["commonReview"]
        persisted_refs = persisted[0]["value"]["findings"][0]["support"]["refs"]
        self.assertEqual(len(persisted_refs), 1)
        self.assertTrue(persisted_refs[0].startswith("evidence:configuration:"))
        self.assertNotEqual(persisted_refs[0], "R1")

    def test_offered_common_task_is_identical_after_process_resume(self):
        controller = self.controller()
        self.start(controller, "run:common-resume")
        before = controller.advance("run:common-resume")["result"]["semanticTask"]
        controller.close()

        resumed_controller = self.controller()
        resumed = resumed_controller.resume("run:common-resume")
        after = resumed["result"]["semanticTask"]

        self.assertEqual(after, before)
        terminal = resumed_controller.advance(
            "run:common-resume", review_submission=_submission(after),
        )
        self.assertEqual(terminal["status"], "completed")

    def test_mcp_transport_accepts_review_submission_without_platform_ids(self):
        transport = InteractivePlatformMcpToolTransport(
            self.root / "transport-output",
            plugin_registry=PluginRegistry((_registration(),)),
        )
        transport.call_tool("start_plugin_run", {
            "pluginId": "test.config-quality",
            "checkId": "CFG-001",
            "scope": {"files": [{"path": str(self.source)}]},
        })
        pending = transport.call_tool("advance_plugin_run", {})
        task = pending["structuredContent"]["result"]["result"]["semanticTask"]
        expanded = transport.call_tool("expand_semantic_evidence", {})
        self.assertEqual(
            expanded["structuredContent"]["result"]["status"],
            "semantic_evidence_expanded",
        )
        self.assertEqual(
            expanded["structuredContent"]["result"]["result"]["evidenceRefs"],
            task["evidenceRefs"],
        )

        terminal = transport.call_tool("advance_plugin_run", {
            "reviewSubmission": _submission(task),
        })

        result = terminal["structuredContent"]["result"]
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("runId", json.dumps(_submission(task)))
        self.assertNotIn("workItemId", json.dumps(_submission(task)))

    def test_compiled_candidates_and_relationships_use_the_same_common_lifecycle(self):
        controller = self.controller(_CompiledItemsPlugin)
        self.start(controller, "run:compiled-items")
        task = controller.advance(
            "run:compiled-items",
        )["result"]["semanticTask"]
        self.assertEqual(
            [item["kind"] for item in task["items"]],
            ["dimension", "dimension", "dimension", "candidate", "relationship"],
        )
        encoded = json.dumps(task)
        for internal_value in (
            "evidence:configuration", "review-atom:", "review-batch:",
            "work:document", "run:compiled-items",
        ):
            self.assertNotIn(internal_value, encoded)
        submission = _submission(task)
        candidate = next(
            item for item in submission["decisions"] if item["kind"] == "candidate"
        )
        support = candidate["value"]["support"]
        candidate["value"] = {
            "disposition": "confirmed",
            "reason": "The candidate is supported.",
            "support": support,
            "finding": {
                "title": "Denial behavior is incomplete",
                "message": "Denial behavior is missing.",
                "severity": "P2",
                "recommendation": "Define denial behavior.",
                "support": support,
            },
        }
        relationship = next(
            item for item in submission["decisions"] if item["kind"] == "relationship"
        )
        relationship["value"]["verdict"] = "rejected"
        relationship["value"]["reason"] = "The declared mapping is incomplete."
        dimension = next(
            item for item in submission["decisions"] if item["kind"] == "dimension"
        )
        dimension["value"]["verdict"] = "violated"
        dimension["value"]["reason"] = "The candidate confirms a dimension violation."

        terminal = controller.advance(
            "run:compiled-items", review_submission=submission,
        )

        self.assertEqual(terminal["status"], "completed")
        ledger = JsonPlatformLedgerStore(
            self.root / "output" / "run:compiled-items",
        ).load("run:compiled-items")
        self.assertEqual(ledger["decisions"][0]["result"], "issue_found")
        self.assertEqual(len(ledger["decisions"][0]["findings"]), 3)
        statuses = {item["status"] for item in ledger["decisions"][0]["findings"]}
        self.assertIn("violated", statuses)

    def test_compiled_support_outside_the_packet_fails_closed(self):
        controller = self.controller(_OutOfScopeCompiledItemsPlugin)
        self.start(controller, "run:compiled-out-of-scope")

        with self.assertRaises(PlatformContractError) as rejected:
            controller.advance("run:compiled-out-of-scope")

        self.assertEqual(
            rejected.exception.code, "COMPILED_REVIEW_EVIDENCE_OUT_OF_SCOPE",
        )

    def test_decorated_simple_scan_uses_frozen_snapshot_and_typed_context(self):
        controller = self.controller(_SimpleAuthorPlugin)
        self.start(controller, "run:simple-scan")
        before = controller.advance("run:simple-scan")["result"]["semanticTask"]
        self.assertEqual(
            before["invariantRules"][0]["ruleId"], "decision-reason-required",
        )
        self.assertEqual(before["evidencePaging"]["total"], len(before["evidenceRefs"]))
        self.assertEqual(before["evidencePaging"]["pageSize"], 8)
        expanded_before = controller.expand_semantic_evidence(
            "run:simple-scan", page_size=100,
        )
        self.assertLessEqual(expanded_before["result"]["page"]["count"], 8)
        self.assertEqual(
            set(expanded_before["result"]["evidenceRefs"]), set(before["evidenceRefs"]),
        )
        expanded_texts = [
            item.get("content", {}).get("text")
            for item in expanded_before["result"]["evidence"]
            if isinstance(item.get("content"), dict)
        ]
        self.assertIn(self.source.read_text(encoding="utf-8"), expanded_texts)
        expanded_encoded = json.dumps(expanded_before)
        self.assertLessEqual(len(expanded_encoded.encode("utf-8")), 24 * 1024)
        self.assertNotIn(self.source.read_text(encoding="utf-8"), json.dumps(before))
        for internal_value in (
            "document-chunk:", "document-snapshot:", "document-evidence:",
            str(self.source),
        ):
            self.assertNotIn(internal_value, expanded_encoded)
        controller.close()

        resumed = self.controller(_SimpleAuthorPlugin)
        task = resumed.resume("run:simple-scan")["result"]["semanticTask"]
        self.assertEqual(task, before)
        self.assertEqual(
            resumed.expand_semantic_evidence(
                "run:simple-scan", page_size=100,
            )["result"],
            expanded_before["result"],
        )
        self.assertEqual(
            [item["kind"] for item in task["items"]],
            ["dimension", "dimension", "dimension", "candidate", "relationship"],
        )
        contexts = {item["contextRef"]: item for item in task["contexts"]}
        self.assertTrue(all(item["contextRef"] in contexts for item in task["items"]))
        fact_contexts = [item for item in contexts.values() if item["facts"]]
        self.assertEqual(len(fact_contexts), 1)
        fact = fact_contexts[0]["facts"][0]
        self.assertEqual(fact["name"], "containsEnabled")
        self.assertIs(fact["value"], True)
        self.assertEqual(len(fact["support"]), 1)
        self.assertRegex(fact["support"][0], r"^R[1-9][0-9]*$")
        self.assertTrue(all(
            item["unknowns"][0]["missingInformation"] == ["denial response"]
            for item in contexts.values()
        ))
        candidate_item = next(item for item in task["items"] if item["kind"] == "candidate")
        relationship_item = next(
            item for item in task["items"] if item["kind"] == "relationship"
        )
        self.assertEqual(contexts[candidate_item["contextRef"]]["facts"], [])
        self.assertEqual(
            contexts[relationship_item["contextRef"]]["facts"][0]["name"],
            "containsEnabled",
        )
        encoded = json.dumps(task)
        self.assertEqual(encoded.count("containsEnabled"), 1)
        for internal_value in (
            "evidence:configuration", "supportIds", "documentSnapshot",
            "document-chunk:", "document-snapshot:", "review-atom:",
            "review-batch:", "work:document", "run:simple-scan",
        ):
            self.assertNotIn(internal_value, encoded)
        ledger_text = (
            self.root / "output" / "run:simple-scan"
            / "run:simple-scan.platform-ledger.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn(self.source.read_text(encoding="utf-8"), ledger_text)

        submission = _submission(task)
        dimension = next(
            item for item in submission["decisions"] if item["kind"] == "dimension"
        )
        dimension["value"]["verdict"] = "unresolved"
        dimension["value"]["reason"] = "The declared unknown requires review."
        dimension["value"]["unknown"] = {
            "reason": "Denial behavior is not explicit.",
            "missingInformation": ["denial response"],
        }
        candidate = next(
            item for item in submission["decisions"] if item["kind"] == "candidate"
        )
        candidate["value"]["disposition"] = "needs_review"
        candidate["value"]["reason"] = "The missing denial response remains unknown."
        candidate["value"]["unknown"] = {
            "reason": "Denial behavior is not explicit.",
            "missingInformation": ["denial response"],
        }

        terminal = resumed.advance("run:simple-scan", review_submission=submission)

        self.assertEqual(terminal["status"], "completed")
        ledger = JsonPlatformLedgerStore(
            self.root / "output" / "run:simple-scan",
        ).load("run:simple-scan")
        decision = ledger["decisions"][0]
        self.assertEqual(decision["result"], "needs_review")
        self.assertEqual(len(decision["findings"]), 3)

    def test_simple_invariant_rejects_batch_at_its_declared_location(self):
        controller = self.controller(_SimpleAuthorPlugin)
        self.start(controller, "run:simple-invariant")
        task = controller.advance(
            "run:simple-invariant",
        )["result"]["semanticTask"]
        submission = _submission(task)
        submission["decisions"][0]["value"]["reason"] = ""

        with self.assertRaises(PlatformContractError) as rejected:
            controller.submit_common_review("run:simple-invariant", submission)

        self.assertEqual(rejected.exception.code, "DOMAIN_INVARIANT_VIOLATION")
        self.assertEqual(rejected.exception.errors[0]["ruleId"], "decision-reason-required")
        self.assertEqual(rejected.exception.errors[0]["location"], "/decisions")
        coverage = JsonCoverageLedgerStore(
            self.root / "output" / "run:simple-invariant",
        ).load("run:simple-invariant")
        self.assertEqual(coverage.batches[0].status, "offered")

    def test_host_document_source_rejects_changes_after_discovery(self):
        controller = self.controller(_SimpleAuthorPlugin)
        self.start(controller, "run:simple-source-changed")
        controller.discover("run:simple-source-changed")
        self.source.write_text(json.dumps({"enabled": False}), encoding="utf-8")

        inspected = controller.inspect("run:simple-source-changed")

        failures = inspected["result"]["inspectionFailures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["code"], "DOCUMENT_SOURCE_CHANGED")

    def test_simple_fact_context_cannot_bypass_the_agent_task_byte_limit(self):
        controller = self.controller(_SimpleOversizedContextPlugin)
        self.start(controller, "run:simple-oversized-context")

        with self.assertRaises(PlatformContractError) as rejected:
            controller.advance("run:simple-oversized-context")

        self.assertEqual(rejected.exception.code, "REVIEW_ATOM_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
