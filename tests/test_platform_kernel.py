import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assayer_platform import (
    Artifact,
    CommitReceipt,
    DecisionProposal,
    DimensionObservation,
    EvidenceRecord,
    Finding,
    InvestigationPacket,
    PlatformContext,
    PlatformContractError,
    PlatformKernel,
    InteractivePlatformSession,
    WorkItem,
    JsonPlatformLedgerStore,
    JsonSummaryPublisher,
    load_plugin_manifest,
)
from assayer_platform.builtin_plugins.config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider
from assayer_host import SQLitePlatformLedgerStore, SQLiteStore


class PlatformKernelTest(unittest.TestCase):
    def context(self, *capabilities):
        return PlatformContext("run-test", frozenset(capabilities))

    def test_interactive_session_checkpoints_agent_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            plugin = ConfigQualityPlugin()
            store = JsonPlatformLedgerStore(Path(directory) / "ledger")
            session = InteractivePlatformSession(plugin.manifest)
            run = session.begin(self.context("structured_read"), str(path), "CFG-001", "1.0.0", store)
            item = plugin.discover(str(path), self.context("structured_read"))[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], self.context("structured_read"))[0]
            proposal = ConfigurationDecisionProvider().decide((packet,), plugin.manifest.checks[0], self.context("structured_read"))[0]
            run.record_discovery((item,))
            run.record_investigation(packet)
            receipt = CommitReceipt("interactive-commit", proposal.work_item_id, "CFG-001", "1.0.0", proposal.result, "durable")
            run.record_commit(proposal, receipt)
            result = run.finish("completed")
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(result.ledger.work_items), 1)
            self.assertEqual(len(result.ledger.investigations), 1)
            self.assertEqual(store.load("run-test")["decision_authority"], "platform")

    def test_interactive_persistence_failure_rolls_back_publishable_commit(self):
        class FailingStore:
            fail = False

            def save(self, ledger):
                if self.fail:
                    raise OSError("storage unavailable")

            def load(self, run_id):
                return None

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            plugin = ConfigQualityPlugin()
            context = self.context("structured_read")
            store = FailingStore()
            run = InteractivePlatformSession(plugin.manifest).begin(
                context, str(path), "CFG-001", "1.0.0", store,
            )
            item = plugin.discover(str(path), context)[0]
            packet = plugin.inspect((item,), plugin.manifest.checks[0], context)[0]
            proposal = ConfigurationDecisionProvider().decide(
                (packet,), plugin.manifest.checks[0], context,
            )[0]
            run.record_discovery((item,))
            run.record_investigation(packet)
            store.fail = True
            receipt = CommitReceipt(
                "interactive-commit", proposal.work_item_id, "CFG-001",
                "1.0.0", proposal.result, "durable",
            )
            with self.assertRaisesRegex(OSError, "storage unavailable"):
                run.record_commit(proposal, receipt)
            self.assertEqual(run.decisions, {})
            self.assertEqual(run.receipts, {})
            self.assertFalse(any(operation.kind == "commit" for operation in run.operations))

    def test_configuration_plugin_is_non_browser_and_commits_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo", "enabled": True}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), {"files": [{"path": str(path), "requiredKeys": ["name"], "expectedTypes": {"enabled": "boolean"}}]},
                "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.decisions[0].result, "scanned_no_issue")
            self.assertEqual(result.receipts[0].durability, "memory")

    def test_committer_failure_never_reports_a_committed_decision(self):
        class FailingCommitter:
            def commit(self, proposal, packet, check, context):
                raise RuntimeError("durable store unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=FailingCommitter(),
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.decisions, ())
            self.assertEqual(result.metrics["commitAttempts"], 1)
            self.assertEqual(result.failures[0].code, "COMMIT_FAILED")

    def test_valid_issue_is_committed_only_after_receipt(self):
        class RecordingCommitter:
            def commit(self, proposal, packet, check, context):
                return CommitReceipt("durable-issue", proposal.work_item_id, check.check_id, check.version, proposal.result, "durable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"enabled": "yes"}', encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), {"files": [{"path": str(path), "requiredKeys": ["name"], "expectedTypes": {"enabled": "boolean"}}]},
                "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"), committer=RecordingCommitter(),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.decisions[0].result, "issue_found")
            self.assertEqual(result.metrics["durableCommits"], 1)

    def test_ledger_correlates_operations_receipts_and_terminal_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"),
            )
            self.assertIsNotNone(result.ledger)
            ledger = result.ledger
            self.assertEqual(ledger.status, "completed")
            self.assertEqual(tuple(event.sequence for event in ledger.events), tuple(range(1, len(ledger.events) + 1)))
            self.assertEqual(ledger.events[0].name, "platform.run.started")
            self.assertEqual(ledger.events[-1].name, "platform.run.terminal")
            self.assertEqual(ledger.decision_authority, "platform")
            self.assertEqual(len(ledger.work_items), 1)
            self.assertEqual(len(ledger.investigations), 1)
            self.assertEqual(len(ledger.decisions), 1)
            self.assertEqual(ledger.failures, ())
            commit_ops = [operation for operation in ledger.operations if operation.kind == "commit"]
            self.assertEqual(len(commit_ops), 1)
            self.assertEqual(commit_ops[0].receipt_id, result.receipts[0].commit_id)

    def test_ledger_store_and_summary_publisher_are_atomic_and_receipt_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            store = JsonPlatformLedgerStore(Path(directory) / "ledger")
            kernel = PlatformKernel(store)
            result = kernel.run(ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            self.assertEqual(store.load(result.run_id)["status"], "completed")
            ledger_root = Path(directory) / "ledger"
            self.assertTrue((ledger_root / f"{result.run_id}.platform-events.jsonl").is_file())
            journal = (ledger_root / f"{result.run_id}.platform-run.log").read_text(encoding="utf-8")
            self.assertIn("platform.run.started", journal)
            self.assertIn("operation.finished", journal)
            self.assertIn("platform.run.terminal", journal)
            publisher = JsonSummaryPublisher(Path(directory) / "reports")
            published = kernel.publish(result, publisher)
            artifact = published.ledger.artifacts[0]
            self.assertTrue(Path(artifact.location).is_file())
            self.assertEqual(store.load(result.run_id)["artifacts"][0]["artifact_id"], artifact.artifact_id)

    def test_failed_result_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

            class FailingCommitter:
                def commit(self, proposal, packet, check, context):
                    raise RuntimeError("store unavailable")

            result = PlatformKernel().run(ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"), committer=FailingCommitter())
            with self.assertRaisesRegex(PlatformContractError, "cannot publish"):
                PlatformKernel().publish(result, JsonSummaryPublisher(directory))

    def test_platform_rejects_non_platform_decision_authority(self):
        class LegacyCommitter:
            def commit(self, proposal, packet, check, context):
                return CommitReceipt(
                    "legacy-receipt", proposal.work_item_id, check.check_id,
                    check.version, proposal.result, "durable", authority="legacy_host",
                )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            result = PlatformKernel().run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=LegacyCommitter(),
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failures[0].code, "INVALID_COMMIT_RECEIPT")

    def test_publication_requires_committed_receipts_and_reorders_terminal_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            kernel = PlatformKernel()
            result = kernel.run(ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            receipt = result.receipts[0]
            artifact = Artifact("report-1", "report", "reports/report.json", "digest", (receipt.commit_id,))
            published = kernel.publish_artifact(result, artifact)
            self.assertEqual(published.ledger.artifacts, (artifact,))
            self.assertEqual(published.ledger.events[-2].name, "artifact.published")
            self.assertEqual(published.ledger.events[-1].name, "platform.run.terminal")
            with self.assertRaisesRegex(PlatformContractError, "receipts"):
                kernel.publish_artifact(result, Artifact("report-2", "report", "reports/other.json", "digest", ("unknown",)))

    def test_external_commit_receipt_is_replayed_without_a_second_commit_call(self):
        class CountingCommitter:
            def __init__(self):
                self.calls = 0

            def commit(self, proposal, packet, check, context):
                self.calls += 1
                return CommitReceipt(
                    "durable-once", proposal.work_item_id, check.check_id,
                    check.version, proposal.result, "durable",
                )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            committer = CountingCommitter()
            kernel = PlatformKernel()
            first = kernel.run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=committer,
            )
            second = kernel.run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=committer,
            )
            self.assertEqual(committer.calls, 1)
            self.assertEqual(second.metrics["commitReplays"], 1)
            self.assertEqual(first.receipts[0].commit_id, second.receipts[0].commit_id)

    def test_external_commit_replay_rejects_changed_semantics(self):
        class CountingCommitter:
            def commit(self, proposal, packet, check, context):
                return CommitReceipt(
                    "durable-once", proposal.work_item_id, check.check_id,
                    check.version, proposal.result, "durable",
                )

        class DifferentDecisionProvider(ConfigurationDecisionProvider):
            def decide(self, packets, check, context):
                proposals = list(super().decide(packets, check, context))
                proposal = proposals[0]
                proposals[0] = DecisionProposal(
                    proposal.work_item_id, proposal.check_id, proposal.check_version,
                    proposal.result, proposal.findings, "A different semantic reason.", proposal.details,
                )
                return tuple(proposals)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            kernel = PlatformKernel()
            first = kernel.run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=CountingCommitter(),
            )
            second = kernel.run(
                ConfigQualityPlugin(), str(path), "CFG-001", DifferentDecisionProvider(),
                self.context("structured_read"), committer=CountingCommitter(),
            )
            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "failed")
            self.assertEqual(second.failures[0].code, "COMMIT_CONFLICT")

    def test_durable_receipt_replays_after_kernel_restart(self):
        class CountingCommitter:
            def __init__(self):
                self.calls = 0

            def commit(self, proposal, packet, check, context):
                self.calls += 1
                return CommitReceipt(
                    "durable-restart", proposal.work_item_id, check.check_id,
                    check.version, proposal.result, "durable",
                )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            store = JsonPlatformLedgerStore(Path(directory) / "ledger")
            first_committer = CountingCommitter()
            first = PlatformKernel(store).run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=first_committer,
            )
            second_committer = CountingCommitter()
            second = PlatformKernel(store).run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"), committer=second_committer,
            )
            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "completed")
            self.assertEqual(first_committer.calls, 1)
            self.assertEqual(second_committer.calls, 0)
            self.assertEqual(second.metrics["commitReplays"], 1)

    def test_host_sqlite_store_can_be_the_platform_ledger_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            host_store = SQLiteStore(Path(directory) / "host.sqlite3")
            platform_store = SQLitePlatformLedgerStore(host_store)
            result = PlatformKernel(platform_store).run(
                ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"),
            )
            stored = platform_store.load(result.run_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["run"]["run_id"], result.run_id)
            self.assertEqual(stored["receipts"][0]["durability"], "memory")
            self.assertEqual(host_store.load_platform_ledger(result.run_id), json.dumps(stored, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            host_store.close()

    def test_platform_ledger_can_append_terminal_checkpoint_after_host_store_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            host_store = SQLiteStore(database)
            platform_store = SQLitePlatformLedgerStore(host_store)
            plugin = ConfigQualityPlugin()
            session = InteractivePlatformSession(plugin.manifest)
            run = session.begin(
                self.context("structured_read"), {"files": []},
                "CFG-001", "1.0.0", platform_store,
            )
            host_store.close()

            result = run.finish("completed")

            self.assertEqual(result.status, "completed")
            reopened = SQLiteStore(database)
            try:
                stored = SQLitePlatformLedgerStore(reopened).load("run-test")
                self.assertEqual(stored["status"], "completed")
                self.assertEqual(stored["events"][-1]["name"], "platform.run.terminal")
            finally:
                reopened.close()

    def test_one_durable_commit_failure_produces_partial_run(self):
        class SelectiveCommitter:
            def __init__(self):
                self.calls = 0

            def commit(self, proposal, packet, check, context):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("durable store rejected this item")
                return CommitReceipt("durable-pass", proposal.work_item_id, check.check_id, check.version, proposal.result, "durable")

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = Path(directory) / f"settings-{index}.json"
                path.write_text('{}', encoding="utf-8")
                paths.append(str(path))
            result = PlatformKernel().run(
                ConfigQualityPlugin(), paths, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"),
                committer=SelectiveCommitter(),
            )
            self.assertEqual(result.status, "partial")
            self.assertEqual(len(result.decisions), 1)
            self.assertEqual(len(result.failures), 1)

    def test_configuration_plugin_reports_issue_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"enabled": "yes"}', encoding="utf-8")
            plugin = ConfigQualityPlugin()
            item = plugin.discover({"files": [{"path": str(path), "requiredKeys": ["name"], "expectedTypes": {"enabled": "boolean"}}]}, self.context())[0]
            packet = plugin.inspect([item], plugin.manifest.checks[0], self.context())[0]
            self.assertEqual(ConfigurationDecisionProvider().decide([packet], plugin.manifest.checks[0], self.context())[0].result, "issue_found")
            payload = packet.evidence[0].payload
            self.assertNotIn("yes", repr(payload))

    def test_missing_capability_follows_manifest_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{}', encoding="utf-8")
            result = PlatformKernel().run(ConfigQualityPlugin(), str(path), "CFG-001", ConfigurationDecisionProvider(), self.context())
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.decisions[0].result, "needs_review")

    def test_cache_reuse_and_source_change_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{}', encoding="utf-8")
            scope = str(path)
            kernel = PlatformKernel()
            first = kernel.run(ConfigQualityPlugin(), scope, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            second = kernel.run(ConfigQualityPlugin(), scope, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            self.assertEqual(first.metrics["cacheHits"], 0)
            self.assertEqual(second.metrics["cacheHits"], 1)
            path.write_text('{"changed": true}', encoding="utf-8")
            third = kernel.run(ConfigQualityPlugin(), scope, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            self.assertEqual(third.metrics["cacheHits"], 0)

            another_run = kernel.run(
                ConfigQualityPlugin(), scope, "CFG-001", ConfigurationDecisionProvider(),
                PlatformContext("another-run", frozenset({"structured_read"})),
            )
            self.assertEqual(another_run.metrics["cacheHits"], 1)

    def test_changed_requirements_invalidate_cached_investigation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{}', encoding="utf-8")
            kernel = PlatformKernel()
            first_scope = {"files": [{"path": str(path), "requiredKeys": []}]}
            second_scope = {"files": [{"path": str(path), "requiredKeys": ["name"]}]}
            first = kernel.run(ConfigQualityPlugin(), first_scope, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            second = kernel.run(
                ConfigQualityPlugin(), second_scope, "CFG-001", ConfigurationDecisionProvider(),
                PlatformContext("requirements-run", frozenset({"structured_read"})),
            )
            self.assertEqual(first.decisions[0].result, "scanned_no_issue")
            self.assertEqual(second.metrics["cacheHits"], 0)
            self.assertEqual(second.decisions[0].result, "issue_found")

    def test_manifest_semantic_duplicate_is_rejected(self):
        manifest = {
            "pluginId": "example.config-quality", "version": "1.0.0", "platformApiVersion": "1.0.0", "domains": ["configuration-quality"], "subjectKinds": ["configuration_file"],
            "checks": [{"checkId": "CFG-001", "version": "1.0.0", "subjectKinds": ["configuration_file"], "dimensions": ["valid"], "decisionStates": ["scanned_no_issue", "needs_review"], "requiredEvidenceKinds": ["structured"], "requiredCapabilities": [], "capabilityMissingOutcome": "needs_review", "invalidationSignals": []}],
            "executionProfile": {"discoverBatching": "allowed", "inspectBatching": "allowed", "decisionBatching": "allowed", "parallelism": "forbidden", "cacheReuse": "allowed", "checkpoint": "required"},
        }
        with self.assertRaisesRegex(PlatformContractError, "unique"):
            load_plugin_manifest({**manifest, "checks": manifest["checks"] * 2})
        with self.assertRaisesRegex(PlatformContractError, "declared"):
            load_plugin_manifest({**manifest, "checks": [{**manifest["checks"][0], "subjectKinds": ["unknown_kind"]}]})

    def test_explicit_resource_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"ASSAYER_RESOURCE_ROOT": directory}, clear=False):
                with self.assertRaisesRegex(PlatformContractError, "does not contain"):
                    load_plugin_manifest({})

    def test_decision_gate_rejects_false_pass(self):
        check = ConfigQualityPlugin.manifest.checks[0]
        item = WorkItem("work-1", "configuration_file", "source-1", "digest-1")
        evidence = EvidenceRecord("evidence-1", "work-1", check.check_id, check.version, "structured", "source-1", {})
        dimensions = tuple(DimensionObservation(name, ("Observed.",), ("evidence-1",), "violated") for name in check.dimensions)
        packet = InvestigationPacket(item, check.check_id, check.version, dimensions, (evidence,), "not_required")
        proposal = DecisionProposal("work-1", check.check_id, check.version, "scanned_no_issue", tuple(Finding(name, "violated", "Observed violation.") for name in check.dimensions), "No issue.")
        with self.assertRaises(PlatformContractError):
            PlatformKernel._validate_proposals((proposal,), {"work-1": packet}, check)

    def test_packet_evidence_closure_is_enforced(self):
        check = ConfigQualityPlugin.manifest.checks[0]
        item = WorkItem("work-1", "configuration_file", "source-1", "digest-1")
        evidence = EvidenceRecord("evidence-1", "another-work", check.check_id, check.version, "structured", "source-1", {})
        dimensions = tuple(DimensionObservation(name, ("Observed.",), ("evidence-1",), "satisfied") for name in check.dimensions)
        packet = InvestigationPacket(item, check.check_id, check.version, dimensions, (evidence,), "not_required")
        with self.assertRaisesRegex(PlatformContractError, "different"):
            PlatformKernel._validate_packets((packet,), (item,), check)

    def test_forbidden_inspection_batching_uses_single_items(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(3):
                path = Path(directory) / f"settings-{index}.json"
                path.write_text('{}', encoding="utf-8")
                paths.append(str(path))

            class RecordingPlugin(ConfigQualityPlugin):
                manifest = replace(ConfigQualityPlugin.manifest, execution_profile=replace(
                    ConfigQualityPlugin.manifest.execution_profile, inspect_batching="forbidden",
                ))

                def __init__(self):
                    self.batch_sizes = []

                def inspect(self, work_items, check, context):
                    self.batch_sizes.append(len(work_items))
                    return super().inspect(work_items, check, context)

            plugin = RecordingPlugin()
            result = PlatformKernel().run(plugin, paths, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            self.assertEqual(result.status, "completed")
            self.assertEqual(plugin.batch_sizes, [1, 1, 1])

    def test_allowed_batching_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(5):
                path = Path(directory) / f"settings-{index}.json"
                path.write_text('{}', encoding="utf-8")
                paths.append(str(path))

            class RecordingPlugin(ConfigQualityPlugin):
                manifest = replace(ConfigQualityPlugin.manifest, execution_profile=replace(
                    ConfigQualityPlugin.manifest.execution_profile, max_batch_size=2,
                ))

                def __init__(self):
                    self.batch_sizes = []

                def inspect(self, work_items, check, context):
                    self.batch_sizes.append(len(work_items))
                    return super().inspect(work_items, check, context)

            plugin = RecordingPlugin()
            result = PlatformKernel().run(plugin, paths, "CFG-001", ConfigurationDecisionProvider(), self.context("structured_read"))
            self.assertEqual(result.status, "completed")
            self.assertEqual(plugin.batch_sizes, [2, 2, 1])

    def test_failed_batch_is_split_to_isolate_one_item(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(3):
                path = Path(directory) / f"settings-{index}.json"
                path.write_text('{}', encoding="utf-8")
                paths.append(str(path))

            class OneBadItemPlugin(ConfigQualityPlugin):
                def __init__(self):
                    self.bad_id = None

                def discover(self, scope, context):
                    items = tuple(super().discover(scope, context))
                    self.bad_id = items[1].work_item_id
                    return items

                def inspect(self, work_items, check, context):
                    if any(item.work_item_id == self.bad_id for item in work_items):
                        raise RuntimeError("One source cannot be inspected")
                    return super().inspect(work_items, check, context)

            result = PlatformKernel().run(
                OneBadItemPlugin(), paths, "CFG-001", ConfigurationDecisionProvider(),
                self.context("structured_read"),
            )
            self.assertEqual(result.status, "partial")
            self.assertEqual(len(result.decisions), 2)
            self.assertEqual(len(result.failures), 1)
            self.assertGreaterEqual(result.metrics["batchSplits"], 1)

    def test_generic_contract_does_not_expose_frontend_types(self):
        import assayer_platform

        public_contract = {name.lower() for name in assayer_platform.__all__}
        for frontend_term in ("page", "dom", "tab", "chromium", "screenshot"):
            self.assertNotIn(frontend_term, public_contract)


if __name__ == "__main__":
    unittest.main()
