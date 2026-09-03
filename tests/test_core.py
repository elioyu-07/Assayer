import json
import tempfile
import unittest
from pathlib import Path

from assayer_host import ActionExecution, BrowserSessionFailure, CredentialVault, DerivedReportBuilder, DeterministicActionAdapter, DeterministicEvidenceAdapter, DeterministicLoginAdapter, DeterministicObjectIdentityAdapter, DeterministicPageAdapter, DeterministicRecoveryAdapter, EvidenceCapture, HostCore, HostError, LoginSecret, NetworkRequest, ObjectMatch, ObjectVerification, RawVisualCapture, RecoveryAttempt, RecoveryCheck, SQLiteStore
from assayer_host.page import EntrypointExecution, EntrypointObservation, PageObservation


class ExplodingLoginAdapter:
    def authenticate(self, url, secret):
        raise RuntimeError("adapter crash")


class CapturingLoginAdapter(DeterministicLoginAdapter):
    def __init__(self, *, succeed=True):
        super().__init__(succeed=succeed)
        self.secret_ref = None

    def authenticate(self, url, secret):
        self.secret_ref = secret
        return super().authenticate(url, secret)


class CountingPageAdapter(DeterministicPageAdapter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def observe(self, page_state_id):
        self.calls += 1
        return super().observe(page_state_id)


class ExplodingPageAdapter:
    def observe(self, page_state_id):
        raise RuntimeError("page adapter crash")


class CrashedBrowserPageAdapter:
    def observe(self, page_state_id):
        raise BrowserSessionFailure()


class ExplodingIdentityAdapter:
    def verify_candidate(self, candidate, page_state):
        raise RuntimeError("identity adapter crash")

    def rebind_object(self, audit_object, page_state):
        raise RuntimeError("identity adapter crash")


class FailingIssueStore(SQLiteStore):
    def insert_issue(self, issue):
        raise RuntimeError("injected issue persistence failure")


class SwitchingPageAdapter:
    active_tab = "Overview"

    def observe(self, page_state_id):
        return PageObservation(
            url="https://test.example.com/app", origin="https://test.example.com", route="/app",
            title="App", state_kind="tab", dom_material=f"<main>{self.active_tab}</main>",
            identity_material=f"/app|tab|{self.active_tab}", visible_text=self.active_tab,
            active_tab=self.active_tab, structure_summary={"tabs": 2, "buttons": 0, "fields": 0, "tables": 0},
            entrypoints=(
                EntrypointObservation("tab", "Overview", "switch_tab", status="processed" if self.active_tab == "Overview" else "unprocessed", host_locator_id="tab-browser-0"),
                EntrypointObservation("tab", "Details", "switch_tab", status="processed" if self.active_tab == "Details" else "unprocessed", host_locator_id="tab-browser-1"),
            ), network_summary={"pendingReadRequests": 0, "observedWrites": 0},
        )


class SwitchingEntrypointAdapter:
    def __init__(self, page): self.page = page
    def explore(self, entrypoint, page_state, operation_id):
        self.page.active_tab = entrypoint["label"]
        return EntrypointExecution("succeeded", page_changed=True)


def bootstrap(core, key="boot-001"):
    return core.handle({"protocolVersion":"1.0","requestId":"req-001","agentTurnId":"turn-001","tool":"start_audit","idempotencyKey":key,"input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-001"}})


def session(scan, tool="inspect_page", key="op-001", revision=1, input=None):
    return {"protocolVersion":"1.0","requestId":"req-002","scanId":scan["scanId"],"runId":scan["runId"],"agentTurnId":"turn-002","tool":tool,"idempotencyKey":key,"expectedRunRevision":revision,"input":input if input is not None else {"pageStateId":"page-001","include":["objects"]}}


class HostCoreTest(unittest.TestCase):
    def setUp(self):
        self.cores = []

    def tearDown(self):
        for core in self.cores:
            try:
                core.close()
            except Exception:
                pass

    def make_core(self, *, succeed=True, store=None, page_adapter=None, identity_adapter=None, action_adapter=None, recovery_adapter=None, evidence_adapter=None, entrypoint_adapter=None):
        vault = CredentialVault()
        vault.put("cred-001", LoginSecret("test-user", "secret"))
        core = HostCore(store=store, credential_vault=vault, login_adapter=DeterministicLoginAdapter(succeed=succeed), page_adapter=page_adapter if page_adapter is not None else DeterministicPageAdapter(), object_identity_adapter=identity_adapter if identity_adapter is not None else DeterministicObjectIdentityAdapter(), action_adapter=action_adapter, recovery_adapter=recovery_adapter, evidence_adapter=evidence_adapter, entrypoint_adapter=entrypoint_adapter)
        self.cores.append(core)
        return core

    @staticmethod
    def platform_projection_fixture():
        assessment = {
            "assessmentId": "assessment-001", "objectRef": "object-001",
            "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
            "result": "scanned_no_issue",
        }
        platform = {
            "decision_authority": "platform",
            "receipts": [{
                "commit_id": "platform-commit:001", "work_item_id": "work-001",
                "check_id": "FUA-10", "check_version": "1.1.0",
                "result": "scanned_no_issue", "authority": "platform",
                "metadata": {
                    "hostAssessmentId": "assessment-001",
                    "hostObjectId": "object-001",
                },
            }],
            "decisions": [{
                "work_item_id": "work-001", "check_id": "FUA-10",
                "check_version": "1.1.0", "result": "scanned_no_issue",
            }],
            "investigations": [{
                "work_item": {"work_item_id": "work-001"},
                "metadata": {"objectId": "object-001"},
            }],
        }
        return platform, [assessment]

    def test_platform_receipt_reconciles_one_host_assessment_projection(self):
        platform, assessments = self.platform_projection_fixture()
        HostCore._validate_platform_assessment_projections(platform, assessments)

    def test_platform_receipt_rejects_mismatched_projection_fields(self):
        scenarios = (
            ("result", lambda platform: platform["receipts"][0].update(result="issue_found")),
            ("rule", lambda platform: platform["decisions"][0].update(check_version="2.0.0")),
            ("work item", lambda platform: platform["receipts"][0].update(work_item_id="work-002")),
            ("authority", lambda platform: platform["receipts"][0].update(authority="legacy_host")),
        )
        for label, mutate in scenarios:
            with self.subTest(field=label):
                platform, assessments = self.platform_projection_fixture()
                mutate(platform)
                with self.assertRaises(ValueError):
                    HostCore._validate_platform_assessment_projections(platform, assessments)

    def test_platform_projection_rejects_host_only_assessment(self):
        platform, assessments = self.platform_projection_fixture()
        platform["receipts"] = []
        platform["decisions"] = []
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            HostCore._validate_platform_assessment_projections(platform, assessments)

    def test_bootstrap_is_idempotent(self):
        core = self.make_core()
        first = bootstrap(core)
        second = bootstrap(core)
        self.assertEqual(first["result"]["scanId"], second["result"]["scanId"])
        self.assertEqual(first["result"]["operationId"], second["result"]["operationId"])

    def test_bootstrap_conflict_is_rejected(self):
        core = self.make_core()
        bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle({"protocolVersion":"1.0","requestId":"req-003","agentTurnId":"turn-003","tool":"start_audit","idempotencyKey":"boot-001","input":{"url":"https://other.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-002"}})
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_stale_revision_fails_before_adapter(self):
        core = self.make_core()
        started = bootstrap(core)
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], revision=99))
        self.assertEqual(caught.exception.code, "STALE_STATE")

    def test_idempotent_retry_wins_over_later_revision(self):
        core = self.make_core()
        started = bootstrap(core)
        request = session(started["result"], tool="inspect_object", input={"candidateId":"candidate-001"})
        first = core.handle(request)
        with core._store.transaction() as connection:
            connection.execute("UPDATE scans SET run_revision=2 WHERE scan_id=?", (started["result"]["scanId"],))
        second = core.handle(request)
        self.assertEqual(first["error"]["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(second["error"]["code"], "UNKNOWN_REFERENCE")

    def test_same_key_on_different_tool_conflicts(self):
        core = self.make_core()
        started = bootstrap(core)
        core.handle(session(started["result"], key="same-key"))
        with self.assertRaises(HostError) as caught:
            core.handle(session(started["result"], tool="inspect_object", key="same-key", input={"objectId":"obj-001"}))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_unknown_operation_reference_is_rejected(self):
        core = self.make_core()
        started = bootstrap(core)
        req = session(started["result"], tool="get_operation", input={"operationId":"operation-missing"})
        with self.assertRaises(HostError) as caught:
            core.handle(req)
        self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")

    def test_credential_handle_is_one_shot(self):
        core = self.make_core()
        first = bootstrap(core)
        self.assertEqual(first["status"], "ok")
        second = bootstrap(core, key="boot-002")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["error"]["code"], "CREDENTIAL_CHANNEL_FAILED")

    def test_anonymous_bootstrap_requires_no_credential_handle(self):
        core = HostCore(login_adapter=DeterministicLoginAdapter(), page_adapter=DeterministicPageAdapter(),
                        object_identity_adapter=DeterministicObjectIdentityAdapter())
        self.cores.append(core)
        request = {"protocolVersion":"1.0","requestId":"anonymous-start","agentTurnId":"turn-anonymous",
                   "tool":"start_audit","idempotencyKey":"anonymous-start",
                   "input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out",
                            "browserProfile":"default","authMode":"anonymous"}}
        result = core.handle(request)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["loginStatus"], "succeeded")

    def test_anonymous_bootstrap_rejects_credential_handle(self):
        core = self.make_core()
        request = {"protocolVersion":"1.0","requestId":"anonymous-secret","agentTurnId":"turn-anonymous",
                   "tool":"start_audit","idempotencyKey":"anonymous-secret",
                   "input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out",
                            "browserProfile":"default","authMode":"anonymous","credentialHandle":"cred-001"}}
        with self.assertRaises(HostError) as caught:
            core.handle(request)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_explore_entrypoint_creates_new_current_page_state(self):
        page_adapter = SwitchingPageAdapter()
        core = self.make_core(page_adapter=page_adapter, entrypoint_adapter=SwitchingEntrypointAdapter(page_adapter))
        started = bootstrap(core)["result"]
        inspected = core.handle(session(started, input={"pageStateId": started["currentPageStateId"],
                                                        "include": ["route", "safeEntrypoints"]}))["result"]
        detail = next(core._store.get_entrypoint(ref) for ref in inspected["entrypointRefs"]
                      if core._store.get_entrypoint(ref)["label"] == "Details")
        explored = core.handle(session(started, tool="explore_entrypoint", key="explore-detail", revision=1,
                                       input={"pageStateId": started["currentPageStateId"],
                                              "entrypointId": detail["entrypointId"]}))
        self.assertEqual(explored["status"], "ok")
        self.assertEqual(explored["result"]["activeTab"], "Details")
        self.assertEqual(explored["runRevision"], 2)
        self.assertNotEqual(explored["result"]["pageStateId"], started["currentPageStateId"])
        stored = core._store.get_page_state(explored["result"]["pageStateId"])
        self.assertEqual(stored["parentPageStateId"], started["currentPageStateId"])
        current = core._store.get_scan(started["scanId"])
        self.assertEqual(current["current_page_state_id"], explored["result"]["pageStateId"])

    def test_build_discovery_plan_batches_candidates_and_logical_entrypoints(self):
        core = self.make_core()
        started = bootstrap(core)
        page = core.handle(session(started["result"], key="scope-page", input={
            "pageStateId": started["result"]["currentPageStateId"],
            "include": ["route", "objects", "safeEntrypoints"],
        }))
        plan = core.build_discovery_plan(started["result"]["scanId"], started["result"]["runId"])
        self.assertEqual(plan["currentPage"]["pageStateId"], page["result"]["pageStateId"])
        self.assertEqual(plan["entrypointCounts"], {
            "physical": 1, "logical": 1, "duplicates": 0,
            "processed": 0, "skipped": 0, "unprocessed": 1,
        })
        self.assertEqual(plan["nextAction"]["type"], "investigate_objects")
        self.assertEqual(len(plan["candidates"]), 1)

    def test_build_discovery_plan_deduplicates_reobserved_tabs_without_mutation(self):
        page_adapter = SwitchingPageAdapter()
        core = self.make_core(
            page_adapter=page_adapter,
            entrypoint_adapter=SwitchingEntrypointAdapter(page_adapter),
        )
        started = bootstrap(core)["result"]
        initial = core.handle(session(started, key="scope-tabs-initial", input={
            "pageStateId": started["currentPageStateId"], "include": ["safeEntrypoints"],
        }))["result"]
        details = next(item for item in initial["entrypoints"] if item["label"] == "Details")
        explored = core.handle(session(started, tool="explore_entrypoint", key="scope-tabs-detail", revision=1, input={
            "pageStateId": started["currentPageStateId"], "entrypointId": details["entrypointId"],
        }))["result"]
        before = core._store.get_scan(started["scanId"])["run_revision"]
        first = core.build_discovery_plan(started["scanId"], started["runId"])
        second = core.build_discovery_plan(started["scanId"], started["runId"])
        after = core._store.get_scan(started["scanId"])["run_revision"]
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first["currentPage"]["pageStateId"], explored["pageStateId"])
        self.assertEqual(first["entrypointCounts"], {
            "physical": 4, "logical": 2, "duplicates": 2,
            "processed": 2, "skipped": 0, "unprocessed": 0,
        })
        self.assertEqual(first["nextAction"]["type"], "ready_to_complete")

    def test_build_discovery_plan_stops_at_host_owned_page_budget(self):
        page_adapter = SwitchingPageAdapter()
        core = self.make_core(
            page_adapter=page_adapter,
            entrypoint_adapter=SwitchingEntrypointAdapter(page_adapter),
        )
        started = bootstrap(core)["result"]
        core.handle(session(started, key="scope-budget-page", input={
            "pageStateId": started["currentPageStateId"], "include": ["safeEntrypoints"],
        }))
        plan = core.build_discovery_plan(started["scanId"], started["runId"], max_pages=1)
        self.assertTrue(plan["budget"]["exhausted"])
        self.assertEqual(plan["nextAction"]["type"], "budget_exhausted")
        self.assertEqual(plan["nextAction"]["remainingLogicalEntrypoints"], 1)

    def test_completion_merges_reobserved_logical_tabs_without_hiding_new_destinations(self):
        page_adapter = SwitchingPageAdapter()
        core = self.make_core(page_adapter=page_adapter, entrypoint_adapter=SwitchingEntrypointAdapter(page_adapter))
        started = bootstrap(core)["result"]
        initial = core.handle(session(started, key="logical-tabs-initial", input={
            "pageStateId": started["currentPageStateId"], "include": ["safeEntrypoints"],
        }))["result"]
        detail = next(item for item in initial["entrypoints"] if item["label"] == "Details")

        before = core.build_completion_input(started["scanId"], started["runId"])
        self.assertEqual(len(before["processedEntrypointRefs"]), 1)
        self.assertEqual(len(before["unprocessedEntrypointRefs"]), 1)
        self.assertEqual(
            before["completionReason"],
            "Host assembled partial coverage from the durable ledger; unfinished scope remains explicit.",
        )

        core.handle(session(started, tool="explore_entrypoint", key="logical-tabs-detail", revision=1, input={
            "pageStateId": started["currentPageStateId"], "entrypointId": detail["entrypointId"],
        }))
        after = core.build_completion_input(started["scanId"], started["runId"])
        self.assertEqual(len(after["processedEntrypointRefs"]), 4)
        self.assertEqual(after["unprocessedEntrypointRefs"], [])

    def test_explore_entrypoint_blocks_navigation_until_case_decision_is_committed(self):
        core = self.make_core(entrypoint_adapter=SwitchingEntrypointAdapter(SwitchingPageAdapter()))
        started, _ = self.discover_candidate(core)
        page = core._store.get_page_state(started["currentPageStateId"])
        candidate = core._store.get_candidates(started["currentPageStateId"])[0]
        object_id = core.handle(session(started, tool="inspect_object", key="barrier-verify",
                                         input={"candidateId": candidate["candidateId"]}))["result"]["objectId"]
        case = core.handle(session(started, tool="begin_case", key="barrier-case",
                                   input={"objectId": object_id, "rule": {"ruleId": "FUA-10", "version": "1.1.0"},
                                          "kind": "observation", "purpose": "Verify the page object",
                                          "plannedCoverageDimensions": ["filter_present"]}))
        entrypoint = core._store.get_entrypoints(started["currentPageStateId"])[0]
        revision = core._store.get_scan(started["scanId"])["run_revision"]
        result = core.handle(session(started, tool="explore_entrypoint", key="barrier-explore", revision=revision,
                                      input={"pageStateId": page["pageStateId"], "entrypointId": entrypoint["entrypointId"]}))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "CASE_ACTIVE")
        self.assertEqual(core._store.get_scan(started["scanId"])["current_page_state_id"], page["pageStateId"])

    def test_build_completion_input_closes_candidate_backed_entry_after_formal_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, _ = self.committed_no_issue(core, started)
            default_payload = core.build_completion_input(started["scanId"], started["runId"])
            self.assertEqual(
                default_payload["completionReason"],
                "Host assembled complete coverage from the durable ledger; no unfinished scope remains.",
            )
            payload = core.build_completion_input(started["scanId"], started["runId"], "Automatic convergence")
            self.assertEqual(payload["processedObjectRefs"], [object_id])
            self.assertEqual(len(payload["processedEntrypointRefs"]), 1)
            self.assertEqual(payload["unprocessedEntrypointRefs"], [])
            self.assertEqual(payload["ruleSummaries"][0]["assessmentCount"], 1)
            self.assertEqual(payload["ruleSummaries"][0]["resultCounts"], {"scanned_no_issue": 1})
            result = core.handle(session(started, tool="complete_audit", key="auto-complete", revision=7, input=payload))
            self.assertEqual(result["result"]["scanStatus"], "completed")

    def test_bootstrap_clears_consumed_secret_after_success(self):
        vault = CredentialVault()
        secret = LoginSecret("test-user", "secret")
        vault.put("cred-001", secret)
        adapter = CapturingLoginAdapter()
        core = HostCore(credential_vault=vault, login_adapter=adapter)
        self.cores.append(core)
        result = bootstrap(core)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(secret.is_cleared)
        self.assertTrue(adapter.secret_ref.is_cleared)
        self.assertEqual(len(vault), 0)

    def test_host_close_clears_unconsumed_secrets(self):
        vault = CredentialVault()
        secret = LoginSecret("unused-user", "unused-secret")
        vault.put("unused-credential", secret)
        core = HostCore(credential_vault=vault)
        core.close()
        self.assertTrue(secret.is_cleared)
        self.assertEqual(len(vault), 0)

    def test_login_failure_invalidates_scan(self):
        core = self.make_core(succeed=False)
        result = bootstrap(core)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "LOGIN_FAILED")
        self.assertEqual(result["runRevision"], 1)

    def test_login_adapter_exception_is_fail_closed(self):
        vault = CredentialVault()
        vault.put("cred-001", LoginSecret("test-user", "secret"))
        core = HostCore(credential_vault=vault, login_adapter=ExplodingLoginAdapter())
        self.cores.append(core)
        result = bootstrap(core)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_browser_session_crash_transitions_scan_to_failed(self):
        core = self.make_core(page_adapter=CrashedBrowserPageAdapter())
        started = bootstrap(core)["result"]
        result = core.handle(session(started, key="browser-crash", input={"pageStateId": started["currentPageStateId"], "include": ["objects"]}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "BROWSER_SESSION_FAILED")
        self.assertEqual(result["runRevision"], 2)
        self.assertEqual(core._store.get_scan(started["scanId"])["status"], "failed")
        completion = core.build_completion_input(started["scanId"], started["runId"])
        self.assertIn("Audit failed: BROWSER_SESSION_FAILED", completion["completionReason"])
        self.assertIn("browser", completion["completionReason"].lower())

    def test_registry_version_mismatch_fails_before_scan(self):
        core = self.make_core()
        request = {"protocolVersion":"1.0","requestId":"req-version","agentTurnId":"turn-version","tool":"start_audit","idempotencyKey":"boot-version","input":{"url":"https://test.example.com","ruleRegistryVersion":"9.9.9","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-001"}}
        with self.assertRaises(HostError) as caught:
            core.handle(request)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_default_login_adapter_fails_closed(self):
        vault = CredentialVault()
        vault.put("cred-001", LoginSecret("test-user", "secret"))
        core = HostCore(credential_vault=vault)
        self.cores.append(core)
        result = bootstrap(core)
        self.assertEqual(result["error"]["code"], "LOGIN_FAILED")

    def test_operation_cannot_be_read_from_another_scan(self):
        core = self.make_core()
        first = bootstrap(core)
        core.credential_vault.put("cred-002", LoginSecret("test-user", "secret"))
        second = core.handle({"protocolVersion":"1.0","requestId":"req-second","agentTurnId":"turn-second","tool":"start_audit","idempotencyKey":"boot-second","input":{"url":"https://test.example.com","ruleRegistryVersion":"1.0.0","outputDir":"/tmp/out","browserProfile":"default","credentialHandle":"cred-002"}})
        req = session(second["result"], tool="get_operation", input={"operationId":first["result"]["operationId"]})
        with self.assertRaises(HostError) as caught:
            core.handle(req)
        self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")

    def test_sqlite_store_survives_core_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store)
            started = bootstrap(first)
            first_store.close()
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store)
            retry = bootstrap(second)
            self.assertEqual(retry["result"]["scanId"], started["result"]["scanId"])
            self.assertEqual(retry["result"]["operationId"], started["result"]["operationId"])
            second_store.close()

    def test_inspect_page_persists_read_only_facts_without_revision_change(self):
        adapter = CountingPageAdapter()
        core = self.make_core(page_adapter=adapter)
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":started["currentPageStateId"],"include":["route","visibleText","objects","safeEntrypoints","networkSummary"]}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["runRevision"], 1)
        self.assertEqual(len(result["result"]["candidateRefs"]), 1)
        self.assertEqual(len(result["result"]["entrypointRefs"]), 1)
        self.assertEqual(adapter.calls, 1)
        candidate = core._store.get_candidates(started["currentPageStateId"])[0]
        self.assertEqual(candidate["potentialRules"], [{"ruleId":"FUA-10","version":"1.1.0"}])

    def test_inspect_page_reuses_immutable_snapshot_for_new_read(self):
        adapter = CountingPageAdapter()
        core = self.make_core(page_adapter=adapter)
        started = bootstrap(core)["result"]
        first = session(started, key="inspect-1", input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
        second = session(started, key="inspect-2", input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
        core.handle(first)
        core.handle(second)
        self.assertEqual(adapter.calls, 1)

    def test_inspect_page_idempotency_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store)
            started = bootstrap(first)["result"]
            request = session(started, input={"pageStateId":started["currentPageStateId"],"include":["objects"]})
            response = first.handle(request)
            first_store.close()
            self.cores.remove(first)
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store, page_adapter=ExplodingPageAdapter())
            retry = second.handle(request)
            self.assertEqual(retry["result"], response["result"])
            second_store.close()
            self.cores.remove(second)

    def test_inspect_page_adapter_failure_is_fail_closed(self):
        core = self.make_core(page_adapter=ExplodingPageAdapter())
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":started["currentPageStateId"],"include":["objects"]}))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_inspect_page_rejects_non_current_state(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        result = core.handle(session(started, input={"pageStateId":"page-foreign","include":["objects"]}))
        self.assertEqual(result["error"]["code"], "UNKNOWN_REFERENCE")

    def discover_candidate(self, core):
        started = bootstrap(core)["result"]
        page = core.handle(session(started, key="discover", input={"pageStateId":started["currentPageStateId"],"include":["objects"]}))["result"]
        return started, page["candidateRefs"][0]

    def inspect_candidate(self, core, started, candidate_id, key="verify"):
        return core.handle(session(started, tool="inspect_object", key=key, input={"candidateId":candidate_id}))

    def begin_case(self, core, started, key="case-1", revision=1):
        page = core.handle(session(started, key=f"{key}-discover", revision=revision, input={"pageStateId":started["currentPageStateId"],"include":["objects"]}))["result"]
        candidate_id = page["candidateRefs"][0]
        object_id = core.handle(session(started, tool="inspect_object", key=f"{key}-verify", revision=revision, input={"candidateId":candidate_id}))["result"]["objectId"]
        response = core.handle(session(started, tool="begin_case", key=key, revision=revision, input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},"kind":"observation","purpose":"Verify filter capabilities","plannedCoverageDimensions":["filter_present","query_action","reset_action","binding_to_list"]}))
        return object_id, response

    def perform_action(self, core, started, object_id, case_id, *, key="action-1", revision=2, action_type="focus", intent="Observe the filter region", parameters=None):
        return core.handle(session(started, tool="perform_action", key=key, revision=revision, input={"pageStateId":started["currentPageStateId"],"caseId":case_id,"objectId":object_id,"type":action_type,"intent":intent,"parameters":parameters or {}}))

    def restore_case(self, core, started, object_id, case_id, *, key="restore-1", revision=3):
        return core.handle(session(started, tool="restore_case", key=key, revision=revision, input={"caseId":case_id,"pageStateId":started["currentPageStateId"],"objectId":object_id,"fallback":"refresh_and_replay_safe_entrypoints"}))

    def capture_evidence(self, core, started, object_id, *, case_id=None, key="evidence-1", revision=2, raw=False):
        input_data = {"pageStateId":started["currentPageStateId"],"objectId":object_id,"includeRawVisual":raw}
        if case_id:
            input_data["caseId"] = case_id
        return core.handle(session(started, tool="capture_evidence", key=key, revision=revision, input=input_data))

    def observe_page(self, core, started, object_id, *, case_id=None, key="observe-page-1", revision=2):
        input_data = {"pageStateId": started["currentPageStateId"], "objectId": object_id}
        if case_id:
            input_data["caseId"] = case_id
        return core.handle(session(started, tool="observe_page", key=key, revision=revision, input=input_data))

    def record_findings(self, core, started, object_id, case_id, evidence_id, *, result="scanned_no_issue", revision=4, dimensions=None, key="findings"):
        dimensions = dimensions or ["filter_present", "query_action", "reset_action", "binding_to_list"]
        statuses = {dimension:"satisfied" for dimension in dimensions}
        if result == "issue_found": statuses["reset_action"] = "violated"
        if result == "needs_review": statuses[dimensions[-1]] = "unresolved"
        response = core.handle(session(started, tool="record_findings", key=key, revision=revision,
            input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},
                   "findings":[{"dimension":dimension,"status":status,"reasonText":"Test Evidence supports the dimension state",
                                "evidenceRefs":[evidence_id],"caseRefs":[case_id]} for dimension, status in statuses.items()]}))
        return response["result"]["findingRefs"]

    def prepare_input(self, object_id, case_id, evidence_id, *, finding_refs=None, result="issue_found", raw_visual_ref=None):
        data = {"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},"result":result,
                "reasonText":"Host evidence supports this decision","findingRefs":finding_refs or ["finding-placeholder"],
                "evidenceRefs":[evidence_id],"caseRefs":[case_id]}
        if result == "issue_found":
            data.update({"rawVisualRef":raw_visual_ref,"severity":"P2","title":"Filter region lacks reset",
                         "message":"The filter region provides only a query action.","impact":"Users cannot restore filter conditions in one action.","recommendation":"Add a reset action bound to the same list."})
        return data

    def committed_no_issue(self, core, started):
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"])
        prepared = core.handle(session(started, tool="prepare_decision", key="complete-prepare", revision=5,
                                       input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")))
        committed = core.handle(session(started, tool="commit_decision", key="complete-commit", revision=6,
                                        input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
        return object_id, committed

    def completion_input(self, core, started, object_id, *, partial=False, assessment_count=1):
        entrypoint_id = core._store.get_entrypoints(started["currentPageStateId"])[0]["entrypointId"]
        return {"visitedPageStateRefs":[started["currentPageStateId"]],"processedObjectRefs":[object_id],
                "processedEntrypointRefs":[] if partial else [entrypoint_id],"skippedEntrypoints":[],
                "ruleSummaries":[{"rule":{"ruleId":"FUA-10","version":"1.1.0"},"assessmentCount":assessment_count,
                                  "resultCounts":{"scanned_no_issue":assessment_count},"coverageComplete":True}],
                "unprocessedEntrypointRefs":[entrypoint_id] if partial else [],"completionReason":"Coverage verification completed"}

    def test_inspect_object_matched_upgrades_candidate(self):
        core = self.make_core()
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        self.assertEqual(result["result"]["rebindStatus"], "matched")
        self.assertEqual(result["runRevision"], 1)
        audit_object = core._store.get_audit_object(result["result"]["objectId"])
        self.assertEqual(audit_object["status"], "eligible")
        self.assertEqual(audit_object["potentialRules"], [{"ruleId":"FUA-10","version":"1.1.0"}])
        self.assertTrue(result["result"]["controls"])
        self.assertTrue(result["result"]["lists"])
        self.assertNotIn("selector", json.dumps(result["result"]))
        self.assertNotIn("hostLocatorId", json.dumps(result["result"]))

    def test_observe_page_persists_model_context_and_visual_reference(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter())
        started, candidate_id = self.discover_candidate(core)
        object_id = self.inspect_candidate(core, started, candidate_id)["result"]["objectId"]
        result = self.observe_page(core, started, object_id, revision=1)
        self.assertEqual(result["status"], "ok")
        observation = result["result"]["observation"]
        self.assertEqual(observation["kind"], "runtime_visual")
        self.assertEqual(observation["visual"]["status"], "captured")
        self.assertEqual(observation["visual"]["sanitizationStatus"], "sanitized")
        evidence = core._store.get_evidence(result["result"]["evidenceId"])
        screenshot = core._store.get_screenshot(result["result"]["screenshotRef"])
        self.assertEqual(evidence["kind"], "runtime_visual")
        self.assertEqual(evidence["payload"]["content"]["observationScope"], "viewport")
        self.assertEqual(screenshot["kind"], "raw_visual")
        self.assertEqual(screenshot["status"], "captured")

    def test_get_rule_contract_reads_frozen_digest_without_revision_change(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        request = session(started, tool="get_rule_contract", key="rule-contract",
                          input={"rule":{"ruleId":"FUA-10","version":"1.1.0"}})
        result = core.handle(request)
        self.assertEqual(result["status"], "ok")
        self.assertIn("Minimum Coverage Contract", result["result"]["content"])
        self.assertEqual(result["result"]["contentDigest"], core._rules[("FUA-10", "1.1.0")]["contentDigest"])
        self.assertEqual(result["runRevision"], 1)
        core._rules[("FUA-10", "1.1.0")]["contentDigest"] = "0" * 64
        failed = core.handle(session(started, tool="get_rule_contract", key="rule-contract-tampered",
                                     input={"rule":{"ruleId":"FUA-10","version":"1.1.0"}}))
        self.assertEqual(failed["error"]["code"], "RULE_CONTRACT_INTEGRITY_FAILED")

    def test_fua_10_current_contract_is_frontend_only(self):
        core = self.make_core()
        rule = core._rules[("FUA-10", "1.1.0")]
        self.assertEqual(rule["requiredCapabilities"], ["runtime", "dom"])
        content = (Path(__file__).parents[1] / rule["document"]).read_text(encoding="utf-8")
        self.assertIn("This rule audits frontend behavior only", content)
        self.assertIn("whether the list content changes after an action", content)
        self.assertIn("Backend unavailability", content)

    def test_progress_rebuilds_investigation_from_effective_findings(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"],
                                            revision=3, dimensions=["filter_present"], key="progress-finding")
        staged = core.handle(session(started, tool="get_audit_progress", key="progress-staged", revision=4, input={}))
        investigation = staged["result"]["investigations"][0]
        self.assertEqual(investigation["attemptedDimensions"], [])
        self.assertEqual(investigation["stagedFindingRefs"], finding_refs)
        self.restore_case(core, started, object_id, case_id, revision=4)
        effective = core.handle(session(started, tool="get_audit_progress", key="progress-effective", revision=5, input={}))
        investigation = effective["result"]["investigations"][0]
        self.assertEqual(investigation["attemptedDimensions"], ["filter_present"])
        self.assertEqual(investigation["latestFindingRefs"], finding_refs)
        self.assertFalse(investigation["complete"])

    def test_superseded_finding_is_retained_but_not_current(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        old_ref = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"],
                                       dimensions=["binding_to_list"], key="finding-old")[0]
        replacement = core.handle(session(started, tool="record_findings", key="finding-new", revision=5,
            input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},
                   "findings":[{"dimension":"binding_to_list","status":"unresolved","reasonText":"Current evidence cannot confirm the binding",
                                "evidenceRefs":[evidence["result"]["evidenceId"]],"caseRefs":[case_id],"supersedesRef":old_ref}]}))
        latest = core._store.get_latest_findings(started["scanId"], object_id, {"ruleId":"FUA-10","version":"1.1.0"})
        self.assertEqual([item["findingId"] for item in latest], replacement["result"]["findingRefs"])
        self.assertEqual(len(core._store.list_entities("dimension_findings", started["scanId"])), 2)

    def test_inspect_object_not_found_does_not_create_object(self):
        adapter = DeterministicObjectIdentityAdapter(ObjectVerification("not_found", 0, excluded_reasons=("no_required_dimensions",)))
        core = self.make_core(identity_adapter=adapter)
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        self.assertEqual(result["result"]["rebindStatus"], "not_found")
        self.assertNotIn("objectId", result["result"])

    def test_inspect_object_ambiguous_never_selects_match(self):
        adapter = DeterministicObjectIdentityAdapter(ObjectVerification("ambiguous", 2, matched_dimensions=("role",)))
        core = self.make_core(identity_adapter=adapter)
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        self.assertEqual(result["result"]["candidateCount"], 2)
        self.assertNotIn("objectId", result["result"])

    def test_inspect_object_changed_returns_replacement_candidate(self):
        match = ObjectMatch("locator-new","filter_region|search|new-filter-region","search","New filter region","New filter region",20,80,640,120,1280,800)
        adapter = DeterministicObjectIdentityAdapter(ObjectVerification("changed", 1, changed_dimensions=("accessible_name",), match=match))
        core = self.make_core(identity_adapter=adapter)
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        replacement = result["result"]["replacementCandidateRef"]
        self.assertNotEqual(replacement, candidate_id)
        self.assertIsNotNone(core._store.get_candidate(replacement))
        self.assertNotIn("objectId", result["result"])

    def test_invalid_identity_outcome_is_fail_closed(self):
        adapter = DeterministicObjectIdentityAdapter(ObjectVerification("ambiguous", 1))
        core = self.make_core(identity_adapter=adapter)
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_identity_adapter_failure_is_fail_closed(self):
        core = self.make_core(identity_adapter=ExplodingIdentityAdapter())
        started, candidate_id = self.discover_candidate(core)
        result = self.inspect_candidate(core, started, candidate_id)
        self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_begin_case_freezes_baseline_and_increments_revision(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        object_id, result = self.begin_case(core, started)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["runRevision"], 2)
        case = core._store.get_case(result["result"]["caseId"])
        self.assertEqual(case["beforePageStateRef"], started["currentPageStateId"])
        self.assertEqual(core._store.get_audit_object(object_id)["status"], "investigating")

    def test_begin_case_rejects_second_active_case_for_same_rule(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        object_id, first = self.begin_case(core, started)
        second = core.handle(session(started, tool="begin_case", key="case-2", revision=2, input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},"kind":"observation","purpose":"Repeat verification","plannedCoverageDimensions":["filter_present"]}))
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["error"]["code"], "CASE_ALREADY_ACTIVE")
        self.assertEqual(second["runRevision"], 2)

    def test_perform_action_requires_real_case(self):
        core = self.make_core(action_adapter=DeterministicActionAdapter())
        started, candidate_id = self.discover_candidate(core)
        object_id = self.inspect_candidate(core, started, candidate_id)["result"]["objectId"]
        result = self.perform_action(core, started, object_id, "case-invented", revision=1)
        self.assertEqual(result["error"]["code"], "UNKNOWN_REFERENCE")

    def test_perform_safe_action_increments_revision_once(self):
        adapter = DeterministicActionAdapter()
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.perform_action(core, started, object_id, case_result["result"]["caseId"])
        self.assertEqual(result["result"]["resultStatus"], "succeeded")
        self.assertEqual(result["runRevision"], 3)
        self.assertEqual(adapter.calls, 1)

    def test_action_parameters_are_redacted_before_persistence(self):
        core = self.make_core(action_adapter=DeterministicActionAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.perform_action(core, started, object_id, case_result["result"]["caseId"], action_type="focus", parameters={"value":"do-not-persist","nested":{"label":"private"}})
        stored = core._store.get_action_attempt(result["result"]["actionId"])
        serialized = str(stored)
        self.assertNotIn("do-not-persist", serialized)
        self.assertNotIn("private", serialized)
        self.assertEqual(stored["parameters"]["value"]["valueClass"], "redacted_string")

    def test_synthetic_input_requires_owned_control_ref_and_records_interaction_evidence(self):
        interaction = {
            "actionType": "input_synthetic_value", "controlRef": "control-filter-input-001",
            "syntheticValueClass": "valid",
            "before": {"controls": [{"controlRef": "control-filter-input-001", "valueClass": "empty"}], "lists": [], "page": {}},
            "after": {"controls": [{"controlRef": "control-filter-input-001", "valueClass": "non_empty"}], "lists": [], "page": {}},
            "diff": {"changedListRefs": [], "controlStateChanged": True, "loadingStateChanged": False},
        }
        adapter = DeterministicActionAdapter(ActionExecution(interaction=interaction))
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.perform_action(
            core, started, object_id, case_result["result"]["caseId"],
            action_type="input_synthetic_value",
            parameters={"controlRef": "control-filter-input-001", "valueClass": "valid"},
        )
        self.assertEqual(result["status"], "ok")
        evidence_ref = result["result"]["interactionEvidenceRef"]
        evidence = core._store.get_evidence(evidence_ref)
        self.assertEqual(evidence["kind"], "runtime_interaction")
        self.assertEqual(evidence["caseRef"], case_result["result"]["caseId"])
        action = core._store.get_action_attempt(result["result"]["actionId"])
        self.assertEqual(action["parameters"], {"controlRef": "control-filter-input-001", "valueClass": "valid"})
        case = core._store.get_case(case_result["result"]["caseId"])
        self.assertEqual(case["actions"][0]["inverseAction"]["type"], "restore_value")
        self.assertEqual(case["actions"][0]["resultEvidenceRefs"], [evidence_ref])

    def test_synthetic_input_rejects_unknown_control_and_raw_value(self):
        adapter = DeterministicActionAdapter()
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.perform_action(
            core, started, object_id, case_result["result"]["caseId"],
            action_type="input_synthetic_value",
            parameters={"controlRef": "control-invented", "valueClass": "valid", "value": "unsafe"},
        )
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(adapter.calls, 0)

    def test_write_request_is_blocked_before_send_and_not_replayed(self):
        adapter = DeterministicActionAdapter(ActionExecution(requests=(NetworkRequest("POST", "https://test.example.com/orders/save"),)))
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        request = session(started, tool="perform_action", key="write", revision=2, input={"pageStateId":started["currentPageStateId"],"caseId":case_result["result"]["caseId"],"objectId":object_id,"type":"focus","intent":"Observe the filter region","parameters":{}})
        first = core.handle(request)
        second = core.handle(request)
        self.assertEqual(first["error"]["code"], "REQUEST_BLOCKED")
        self.assertEqual(second["error"]["code"], "REQUEST_BLOCKED")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(first["runRevision"], 3)

    def test_unknown_request_status_is_locked_and_queryable(self):
        adapter = DeterministicActionAdapter(ActionExecution(requests=(NetworkRequest("GET", "https://test.example.com/api/orders", attributable=False),)))
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        request = session(started, tool="perform_action", key="unknown", revision=2, input={"pageStateId":started["currentPageStateId"],"caseId":case_result["result"]["caseId"],"objectId":object_id,"type":"focus","intent":"Observe the filter region","parameters":{}})
        first = core.handle(request)
        retry = core.handle(request)
        self.assertEqual(first["error"]["code"], "REQUEST_RESULT_UNKNOWN")
        self.assertEqual(retry["error"]["code"], "REQUEST_RESULT_UNKNOWN")
        self.assertEqual(adapter.calls, 1)
        operation_id = core._store.get_by_idempotency(started["scanId"], "unknown")["operation_id"]
        queried = core.handle(session(started, tool="get_operation", key="query", revision=3, input={"operationId":operation_id}))
        self.assertEqual(queried["result"]["status"], "result_unknown")
        self.assertEqual(queried["result"]["result"]["resultStatus"], "result_unknown")

    def test_result_unknown_is_not_replayed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_adapter = DeterministicActionAdapter(ActionExecution(requests=(NetworkRequest("GET", "https://test.example.com/api/orders", attributable=False),)))
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store, action_adapter=first_adapter)
            started = bootstrap(first)["result"]
            object_id, case_result = self.begin_case(first, started)
            request = session(started, tool="perform_action", key="restart-unknown", revision=2, input={"pageStateId":started["currentPageStateId"],"caseId":case_result["result"]["caseId"],"objectId":object_id,"type":"focus","intent":"Observe the filter region","parameters":{}})
            first.handle(request)
            first_store.close(); self.cores.remove(first)
            second_adapter = DeterministicActionAdapter()
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store, action_adapter=second_adapter)
            retry = second.handle(request)
            self.assertEqual(retry["error"]["code"], "REQUEST_RESULT_UNKNOWN")
            self.assertEqual(second_adapter.calls, 0)
            second_store.close(); self.cores.remove(second)

    def test_restore_targeted_success_crosses_recovery_barrier(self):
        adapter = DeterministicRecoveryAdapter()
        core = self.make_core(action_adapter=DeterministicActionAdapter(), recovery_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        self.perform_action(core, started, object_id, case_result["result"]["caseId"])
        result = self.restore_case(core, started, object_id, case_result["result"]["caseId"])
        self.assertEqual(result["result"]["finalStatus"], "restored")
        self.assertEqual(result["runRevision"], 4)
        self.assertEqual(adapter.calls, ["targeted_inverse"])
        self.assertEqual(core._store.get_case(case_result["result"]["caseId"])["status"], "completed")

    def test_restore_uncertain_requires_refresh_replay(self):
        dims = tuple(RecoveryCheck(d, "match") for d in ("url_route", "page_layer", "active_tab", "overlay_state", "control_state", "object_identity", "pending_requests", "write_request"))
        targeted = RecoveryAttempt("targeted_inverse", "uncertain", (RecoveryCheck("url_route", "match"), RecoveryCheck("page_layer", "match"), RecoveryCheck("active_tab", "match"), RecoveryCheck("overlay_state", "match"), RecoveryCheck("control_state", "match"), RecoveryCheck("object_identity", "match"), RecoveryCheck("pending_requests", "unknown"), RecoveryCheck("write_request", "match")))
        refresh = RecoveryAttempt("refresh_replay", "restored", dims)
        adapter = DeterministicRecoveryAdapter(targeted=targeted, refresh=refresh)
        core = self.make_core(recovery_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.restore_case(core, started, object_id, case_result["result"]["caseId"], revision=2)
        self.assertEqual(result["result"]["finalStatus"], "restored")
        self.assertEqual(adapter.calls, ["targeted_inverse", "refresh_replay"])

    def test_restore_failure_with_unknown_pending_fails_scan(self):
        failed = RecoveryAttempt("targeted_inverse", "failed", (RecoveryCheck("url_route", "match"), RecoveryCheck("page_layer", "match"), RecoveryCheck("active_tab", "match"), RecoveryCheck("overlay_state", "match"), RecoveryCheck("control_state", "match"), RecoveryCheck("object_identity", "match"), RecoveryCheck("pending_requests", "unknown"), RecoveryCheck("write_request", "unknown")), "Pending requests did not converge")
        refresh = RecoveryAttempt("refresh_replay", "failed", (RecoveryCheck("url_route", "match"), RecoveryCheck("page_layer", "match"), RecoveryCheck("active_tab", "match"), RecoveryCheck("overlay_state", "match"), RecoveryCheck("control_state", "match"), RecoveryCheck("object_identity", "match"), RecoveryCheck("pending_requests", "unknown"), RecoveryCheck("write_request", "unknown")), "State remained unknown after refresh")
        adapter = DeterministicRecoveryAdapter(targeted=failed, refresh=refresh)
        core = self.make_core(recovery_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.restore_case(core, started, object_id, case_result["result"]["caseId"], revision=2)
        self.assertEqual(result["result"]["finalStatus"], "failed")
        self.assertEqual(core._store.get_scan(started["scanId"])["status"], "failed")
        self.assertEqual(core._store.get_case(case_result["result"]["caseId"])["status"], "restore_failed")

    def test_default_recovery_adapter_fails_closed(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.restore_case(core, started, object_id, case_result["result"]["caseId"], revision=2)
        self.assertEqual(result["result"]["finalStatus"], "failed")
        self.assertEqual(core._store.get_scan(started["scanId"])["status"], "failed")

    def test_host_restart_fails_running_action_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            store = SQLiteStore(path)
            core = self.make_core(store=store)
            started = bootstrap(core)["result"]
            object_id, case_result = self.begin_case(core, started)
            request = session(started, tool="perform_action", key="interrupted", revision=2, input={"pageStateId":started["currentPageStateId"],"caseId":case_result["result"]["caseId"],"objectId":object_id,"type":"focus","intent":"Observe","parameters":{}})
            operation = core._new_operation(request, started["scanId"], "browser_action", core._request_digest(request), "running", 2)
            with core._store.transaction():
                core._store.insert_operation(operation)
            core._store.close(); self.cores.remove(core)
            restarted_store = SQLiteStore(path)
            restarted = self.make_core(store=restarted_store)
            interrupted = restarted._store.get_by_idempotency(started["scanId"], "interrupted")
            self.assertEqual(interrupted["status"], "result_unknown")
            self.assertEqual(restarted._store.get_scan(started["scanId"])["status"], "failed")
            restarted_store.close(); self.cores.remove(restarted)

    def test_capture_structured_evidence_binds_object_and_increments_revision(self):
        adapter = DeterministicEvidenceAdapter()
        core = self.make_core(evidence_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.capture_evidence(core, started, object_id, case_id=case_result["result"]["caseId"], revision=2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["runRevision"], 3)
        evidence = core._store.get_evidence(result["result"]["evidenceId"])
        self.assertEqual(evidence["objectRef"], object_id)
        self.assertTrue(evidence["sanitized"])
        self.assertEqual(core._store.get_case(case_result["result"]["caseId"])["status"], "evidence_captured")

    def test_capture_raw_visual_writes_immutable_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = DeterministicEvidenceAdapter()
            core = self.make_core(evidence_adapter=adapter)
            started = bootstrap(core)["result"]
            core._store.get_scan(started["scanId"])
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            result = self.capture_evidence(core, started, object_id, case_id=case_result["result"]["caseId"], raw=True)
            self.assertIn("screenshotRef", result["result"])
            screenshot = core._store.get_screenshot(result["result"]["screenshotRef"])
            self.assertEqual(screenshot["status"], "captured")
            self.assertTrue((Path(tmp) / screenshot["path"]).exists())

    def test_capture_visual_rejection_is_persisted_not_promoted(self):
        capture = EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={"status":"failed"}, raw_visual=RawVisualCapture(status="ambiguous", reason="Object location is not unique"))
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(capture))
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.capture_evidence(core, started, object_id, case_id=case_result["result"]["caseId"], raw=True)
        screenshot = core._store.get_screenshot(result["result"]["screenshotRef"])
        self.assertEqual(screenshot["status"], "ambiguous")
        self.assertNotIn("path", screenshot)

    def test_capture_rejects_visual_bound_to_wrong_box(self):
        raw = RawVisualCapture(image_bytes=b"\x89PNG\r\n\x1a\nwrong-box", width=1280, height=800,
                               bounding_box={"x":0,"y":0,"width":10,"height":10}, annotation="Wrong location")
        capture = EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={}, raw_visual=raw)
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(capture))
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.capture_evidence(core, started, object_id, case_id=case_result["result"]["caseId"], raw=True)
        screenshot = core._store.get_screenshot(result["result"]["screenshotRef"])
        self.assertEqual(screenshot["status"], "rejected")
        self.assertNotIn("path", screenshot)

    def test_capture_rejects_cross_scan_object(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter())
        started = bootstrap(core)["result"]
        result = self.capture_evidence(core, started, "object-foreign", revision=1)
        self.assertEqual(result["error"]["code"], "UNKNOWN_REFERENCE")

    def test_capture_sanitizes_sensitive_payload(self):
        capture = EvidenceCapture(payload={"password":"secret-value", "accessToken":"nested-secret", "headers":{"Authorization":"Bearer abc"}, "text":"token=xyz email test@example.com phone 13812345678 id 123456789012"})
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(capture))
        started = bootstrap(core)["result"]
        object_id, _ = self.begin_case(core, started)
        result = self.capture_evidence(core, started, object_id, revision=2)
        payload = core._store.get_evidence(result["result"]["evidenceId"])["payload"]["content"]
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["accessToken"], "[REDACTED]")
        self.assertNotIn("Bearer abc", str(payload))
        self.assertNotIn("test@example.com", str(payload))
        self.assertNotIn("13812345678", str(payload))
        self.assertNotIn("123456789012", str(payload))

    def test_capture_evidence_is_idempotent_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.sqlite"
            first_store = SQLiteStore(path)
            first = self.make_core(store=first_store)
            started = bootstrap(first)["result"]
            object_id, case_result = self.begin_case(first, started)
            request = session(started, tool="capture_evidence", key="evidence-restart", revision=2, input={"pageStateId":started["currentPageStateId"],"objectId":object_id,"caseId":case_result["result"]["caseId"],"includeRawVisual":False})
            original = first.handle(request)
            first_store.close(); self.cores.remove(first)
            second_store = SQLiteStore(path)
            second = self.make_core(store=second_store, evidence_adapter=DeterministicEvidenceAdapter(capture=EvidenceCapture(payload={"unexpected":"not-used"})))
            retry = second.handle(request)
            self.assertEqual(retry["result"], original["result"])
            self.assertEqual(second._store.get_evidence(original["result"]["evidenceId"])["evidenceId"], original["result"]["evidenceId"])
            second_store.close(); self.cores.remove(second)

    def test_screenshot_file_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            request = session(started, tool="capture_evidence", key="evidence-conflict", revision=2, input={"pageStateId":started["currentPageStateId"],"objectId":object_id,"caseId":case_result["result"]["caseId"],"includeRawVisual":True})
            scan = core._scan_from_row(core._store.get_scan(started["scanId"]))
            operation, _ = core._accept_operation(request, scan, implemented=True)
            screenshot_id = core._stable_id("screenshot", operation["operationId"])
            conflict = Path(tmp) / "screenshots" / f"{screenshot_id}.png"
            conflict.parent.mkdir(parents=True)
            conflict.write_bytes(b"tampered")
            result = core.handle(request)
            self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")

    def test_prepare_issue_creates_pending_and_independent_issue_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            case_id = case_result["result"]["caseId"]
            evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
            self.restore_case(core, started, object_id, case_id, revision=3)
            finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
            request = session(started, tool="prepare_decision", key="prepare-issue", revision=5,
                              input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"]))
            result = core.handle(request)
            self.assertEqual(result["runRevision"], 6)
            self.assertNotEqual(result["result"]["screenshotRef"], evidence["result"]["screenshotRef"])
            screenshot = core._store.get_screenshot(result["result"]["screenshotRef"])
            self.assertEqual(screenshot["kind"], "issue")
            self.assertEqual(screenshot["rawVisualRef"], evidence["result"]["screenshotRef"])
            pending = core._store.get_pending_decision(result["result"]["pendingDecisionId"])
            self.assertEqual(pending["status"], "pending")
            self.assertTrue(pending["coverage"]["complete"])
            self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)
            self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)
            retry = core.handle(request)
            self.assertEqual(retry["result"], result["result"])

    def test_prepare_issue_requires_raw_visual_at_contract_boundary(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        data = self.prepare_input("object-001", "case-001", "evidence-001", raw_visual_ref="screenshot-001")
        data.pop("rawVisualRef")
        with self.assertRaises(HostError) as caught:
            core.handle(session(started, tool="prepare_decision", key="prepare-no-raw", input=data))
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_prepare_needs_review_requires_structured_blocker(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        data = {"objectId":"object-001","rule":{"ruleId":"FUA-10","version":"1.1.0"},"result":"needs_review",
                "reasonText":"Evidence conflicts","findingRefs":["finding-001"],"evidenceRefs":[],"caseRefs":["case-001"]}
        with self.assertRaises(HostError) as caught:
            core.handle(session(started, tool="prepare_decision", key="prepare-no-blocker", input=data))
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_prepare_rejects_binding_review_without_page_observation(self):
        core = self.make_core(
            evidence_adapter=DeterministicEvidenceAdapter(),
            recovery_adapter=DeterministicRecoveryAdapter(),
        )
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        evidence_id = evidence["result"]["evidenceId"]
        finding_refs = self.record_findings(
            core, started, object_id, case_id, evidence_id, result="needs_review",
        )
        data = self.prepare_input(
            object_id, case_id, evidence_id,
            finding_refs=finding_refs, result="needs_review",
        )
        data["blocker"] = {
            "code": "BINDING_UNRESOLVED",
            "message": "The current DOM summary cannot uniquely identify the list bound to the filter region",
        }
        result = core.handle(session(
            started, tool="prepare_decision", key="prepare-binding-without-observation",
            revision=5, input=data,
        ))
        self.assertEqual(result["error"]["code"], "EVIDENCE_INSUFFICIENT")
        self.assertIn("observe_page", result["error"]["message"])

    def test_prepare_accepts_binding_review_with_page_observation(self):
        core = self.make_core(
            evidence_adapter=DeterministicEvidenceAdapter(),
            recovery_adapter=DeterministicRecoveryAdapter(),
        )
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        observation = self.observe_page(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        evidence_id = observation["result"]["evidenceId"]
        finding_refs = self.record_findings(
            core, started, object_id, case_id, evidence_id, result="needs_review",
        )
        data = self.prepare_input(
            object_id, case_id, evidence_id,
            finding_refs=finding_refs, result="needs_review",
        )
        data["blocker"] = {
            "code": "BINDING_UNRESOLVED",
            "message": "Multiple logical lists in the viewport still prevent unique attribution",
        }
        result = core.handle(session(
            started, tool="prepare_decision", key="prepare-binding-with-observation",
            revision=5, input=data,
        ))
        self.assertEqual(result["status"], "ok")
        self.assertIn("pendingDecisionId", result["result"])
        committed = core.handle(session(
            started, tool="commit_decision", key="commit-binding-review",
            revision=6, input={"pendingDecisionId": result["result"]["pendingDecisionId"]},
        ))
        self.assertEqual(committed["status"], "ok")
        completion = core.build_completion_input(started["scanId"], started["runId"])
        summary = completion["ruleSummaries"][0]
        self.assertEqual(summary["resultCounts"], {"needs_review": 1})
        self.assertIn("Multiple logical lists", summary["reason"])
        self.assertIn("filter_region", summary["reason"])

    def test_prepare_rejects_case_before_recovery_barrier(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="not_applicable", revision=3)
        data = self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="not_applicable")
        result = core.handle(session(started, tool="prepare_decision", key="prepare-unrestored", revision=4, input=data))
        self.assertEqual(result["error"]["code"], "CASE_NOT_RESTORED")

    def test_prepare_rejects_incomplete_no_issue_coverage(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        page = core.handle(session(started, key="partial-discover", input={"pageStateId":started["currentPageStateId"],"include":["objects"]}))["result"]
        object_id = self.inspect_candidate(core, started, page["candidateRefs"][0], key="partial-inspect")["result"]["objectId"]
        case = core.handle(session(started, tool="begin_case", key="partial-case", input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},"kind":"observation","purpose":"Partial coverage","plannedCoverageDimensions":["filter_present"]}))["result"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case["caseId"], revision=2)
        self.restore_case(core, started, object_id, case["caseId"], revision=3)
        finding_refs = self.record_findings(core, started, object_id, case["caseId"], evidence["result"]["evidenceId"], dimensions=["filter_present"])
        data = self.prepare_input(object_id, case["caseId"], evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")
        result = core.handle(session(started, tool="prepare_decision", key="prepare-partial", revision=5, input=data))
        self.assertEqual(result["error"]["code"], "COVERAGE_INCOMPLETE")

    def test_prepare_rejects_no_issue_when_latest_finding_is_unresolved(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        statuses = {dimension:"satisfied" for dimension in ["filter_present", "query_action", "reset_action", "binding_to_list"]}
        statuses["binding_to_list"] = "unresolved"
        recorded = core.handle(session(started, tool="record_findings", key="unresolved-findings", revision=4,
            input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},
                   "findings":[{"dimension":dimension,"status":status,"reasonText":"Per-dimension conclusion",
                                "evidenceRefs":[evidence["result"]["evidenceId"]],"caseRefs":[case_id]}
                               for dimension, status in statuses.items()]}))
        data = self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"],
                                  finding_refs=recorded["result"]["findingRefs"], result="scanned_no_issue")
        result = core.handle(session(started, tool="prepare_decision", key="unresolved-no-issue", revision=5, input=data))
        self.assertEqual(result["error"]["code"], "COVERAGE_INCOMPLETE")

    def test_record_finding_rejects_cross_dimension_supersedes(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        old_ref = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"],
                                       revision=3, dimensions=["query_action"], key="supersede-source")[0]
        result = core.handle(session(started, tool="record_findings", key="supersede-wrong-dimension", revision=4,
            input={"objectId":object_id,"rule":{"ruleId":"FUA-10","version":"1.1.0"},
                   "findings":[{"dimension":"reset_action","status":"satisfied","reasonText":"Wrong supersession target",
                                "evidenceRefs":[evidence["result"]["evidenceId"]],"caseRefs":[case_id],"supersedesRef":old_ref}]}))
        self.assertEqual(result["error"]["code"], "INVALID_SUPERSEDES_REFERENCE")

    def test_prepare_rejects_failed_raw_visual(self):
        capture = EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={}, raw_visual=RawVisualCapture(status="ambiguous", reason="Object is ambiguous"))
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(capture), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
        data = self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"])
        result = core.handle(session(started, tool="prepare_decision", key="prepare-bad-raw", revision=5, input=data))
        self.assertEqual(result["error"]["code"], "SCREENSHOT_NOT_CAPTURED")

    def test_prepare_rejects_captured_visual_without_image_sanitization(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = RawVisualCapture(image_bytes=b"\x89PNG\r\n\x1a\nunsanitized", width=1280, height=800,
                                   bounding_box={"x":20,"y":80,"width":640,"height":120}, annotation="Object region",
                                   sanitized=False, sanitization_status="not_performed")
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(EvidenceCapture(kind="runtime_visual", payload_type="image_metadata", payload={}, raw_visual=raw)), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            case_id = case_result["result"]["caseId"]
            evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
            self.restore_case(core, started, object_id, case_id, revision=3)
            finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
            data = self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"])
            result = core.handle(session(started, tool="prepare_decision", key="prepare-unsanitized", revision=5, input=data))
            self.assertEqual(result["error"]["code"], "SCREENSHOT_SANITIZATION_REQUIRED")

    def test_two_issue_preparations_never_reuse_issue_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, first_case = self.begin_case(core, started, key="multi-one")
            first_id = first_case["result"]["caseId"]
            first_ev = self.capture_evidence(core, started, object_id, case_id=first_id, key="multi-ev-one", revision=2, raw=True)
            self.restore_case(core, started, object_id, first_id, key="multi-restore-one", revision=3)
            first_findings = self.record_findings(core, started, object_id, first_id, first_ev["result"]["evidenceId"], result="issue_found", key="multi-findings-one")
            first = core.handle(session(started, tool="prepare_decision", key="multi-prepare-one", revision=5,
                                        input=self.prepare_input(object_id, first_id, first_ev["result"]["evidenceId"], finding_refs=first_findings, raw_visual_ref=first_ev["result"]["screenshotRef"])))
            _, second_case = self.begin_case(core, started, key="multi-two", revision=6)
            second_id = second_case["result"]["caseId"]
            second_ev = self.capture_evidence(core, started, object_id, case_id=second_id, key="multi-ev-two", revision=7, raw=True)
            self.restore_case(core, started, object_id, second_id, key="multi-restore-two", revision=8)
            second_findings = self.record_findings(core, started, object_id, second_id, second_ev["result"]["evidenceId"], result="issue_found", revision=9, key="multi-findings-two")
            second = core.handle(session(started, tool="prepare_decision", key="multi-prepare-two", revision=10,
                                         input=self.prepare_input(object_id, second_id, second_ev["result"]["evidenceId"], finding_refs=second_findings, raw_visual_ref=second_ev["result"]["screenshotRef"])))
            self.assertNotEqual(first["result"]["screenshotRef"], second["result"]["screenshotRef"])

    def test_commit_no_issue_writes_assessment_atomically_and_marks_object(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"])
        prepared = core.handle(session(started, tool="prepare_decision", key="commit-prepare", revision=5,
                                       input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")))
        committed = core.handle(session(started, tool="commit_decision", key="commit-no-issue", revision=6,
                                        input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
        self.assertEqual(committed["runRevision"], 7)
        assessment = core._store.get_assessment(committed["result"]["assessmentId"])
        self.assertEqual(assessment["result"], "scanned_no_issue")
        self.assertEqual(core._store.get_pending_decision(prepared["result"]["pendingDecisionId"])["status"], "committed")
        self.assertEqual(core._store.get_audit_object(object_id)["status"], "decided")
        self.assertIsNone(committed["result"].get("issueId"))
        retry = core.handle(session(started, tool="commit_decision", key="commit-no-issue", revision=6,
                                    input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
        self.assertEqual(retry["result"], committed["result"])

    def test_commit_issue_derives_one_issue_from_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            case_id = case_result["result"]["caseId"]
            evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
            self.restore_case(core, started, object_id, case_id, revision=3)
            finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
            prepared = core.handle(session(started, tool="prepare_decision", key="issue-prepare", revision=5,
                                           input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"])))
            committed = core.handle(session(started, tool="commit_decision", key="issue-commit", revision=6,
                                            input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
            issue = core._store.get_issue(committed["result"]["issueId"])
            self.assertEqual(issue["assessmentRef"], committed["result"]["assessmentId"])
            self.assertEqual(issue["screenshotRef"], prepared["result"]["screenshotRef"])

    def test_commit_rejects_pending_if_case_becomes_unrestored(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"])
        prepared = core.handle(session(started, tool="prepare_decision", key="stale-prepare", revision=5,
                                       input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")))
        with core._store.transaction() as connection:
            case = core._store.get_case(case_id)
            case["status"] = "restore_failed"
            case["recovery"]["finalStatus"] = "failed"
            case["endedAt"] = core._now()
            case["restoreReason"] = {"code":"TEST_INVALIDATION","message":"Test invalidated the recovery barrier"}
            core._store.update_case(case)
        result = core.handle(session(started, tool="commit_decision", key="stale-commit", revision=6,
                                     input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
        self.assertEqual(result["error"]["code"], "CASE_NOT_RESTORED")
        self.assertEqual(core._store.get_pending_decision(prepared["result"]["pendingDecisionId"])["status"], "invalidated")
        self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)

    def test_commit_rechecks_evidence_integrity(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"])
        prepared = core.handle(session(started, tool="prepare_decision", key="integrity-prepare", revision=5,
                                       input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")))
        tampered = core._store.get_evidence(evidence["result"]["evidenceId"])
        tampered["payload"]["content"] = {"tampered": True}
        with core._store.transaction() as connection:
            connection.execute("UPDATE evidence SET entity_json=? WHERE evidence_id=?", (json.dumps(tampered), tampered["evidenceId"]))
        result = core.handle(session(started, tool="commit_decision", key="integrity-commit", revision=6,
                                     input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
        self.assertEqual(result["error"]["code"], "EVIDENCE_INTEGRITY_FAILED")
        self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)

    def test_commit_rolls_back_assessment_when_issue_insert_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FailingIssueStore()
            core = self.make_core(store=store, evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            case_id = case_result["result"]["caseId"]
            evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
            self.restore_case(core, started, object_id, case_id, revision=3)
            finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
            prepared = core.handle(session(started, tool="prepare_decision", key="rollback-prepare", revision=5,
                                           input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"])))
            result = core.handle(session(started, tool="commit_decision", key="rollback-commit", revision=6,
                                         input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
            self.assertEqual(result["error"]["code"], "INTERNAL_FAILURE")
            self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)
            self.assertEqual(core._store._conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 0)
            self.assertEqual(core._store.get_pending_decision(prepared["result"]["pendingDecisionId"])["status"], "pending")
            self.assertEqual(core._store.get_scan(started["scanId"])["run_revision"], 6)

    def test_complete_audit_closes_coverage_and_exports_valid_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, _ = self.committed_no_issue(core, started)
            request = session(started, tool="complete_audit", key="complete-ok", revision=7,
                              input=self.completion_input(core, started, object_id))
            result = core.handle(request)
            self.assertEqual(result["result"]["scanStatus"], "completed")
            self.assertEqual(result["result"]["ledgerPath"], "audit-ledger.json")
            ledger = json.loads((Path(tmp) / "audit-ledger.json").read_text())
            self.assertEqual(ledger["scan"]["status"], "completed")
            self.assertEqual(ledger["scan"]["coverageProof"]["processedObjectRefs"], [object_id])
            self.assertEqual(len(ledger["assessments"]), 1)
            expected = {"audit-ledger.json", "issues.json", "page-element-judgement.json", "run-diagnostics.json", "audit-summary.md", "run-diagnostics.md", "audit.log", "runtime-events.jsonl", "observability-manifest.json", "performance-bill.json", "performance-bill.md"}
            self.assertEqual(set(result["result"]["artifactPaths"]), expected)
            self.assertTrue(all((Path(tmp) / name).exists() for name in expected))
            self.assertFalse(any(path.suffix == ".html" for path in Path(tmp).iterdir()))
            issues = json.loads((Path(tmp) / "issues.json").read_text())
            self.assertEqual(issues["issues"], [])
            diagnostics = json.loads((Path(tmp) / "run-diagnostics.json").read_text())
            self.assertEqual(diagnostics["assessmentTimelines"][0]["assessmentId"], ledger["assessments"][0]["assessmentId"])
            self.assertEqual(diagnostics["attributions"][0]["layer"], "unattributed")
            self.assertIn("recommendation", diagnostics["attributions"][0])
            performance = json.loads((Path(tmp) / "performance-bill.json").read_text())
            self.assertEqual(performance["scanId"], started["scanId"])
            self.assertEqual(performance["activity"]["decisionsCommitted"], 1)
            self.assertGreaterEqual(performance["measurement"]["totalDurationMs"], 0)
            self.assertIn("# Performance Bill", (Path(tmp) / "performance-bill.md").read_text())
            self.assertEqual(DerivedReportBuilder().render(ledger), DerivedReportBuilder().render(ledger))
            retry = core.handle(request)
            self.assertEqual(retry["result"], result["result"])

    def test_complete_audit_marks_unprocessed_entrypoint_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, _ = self.committed_no_issue(core, started)
            result = core.handle(session(started, tool="complete_audit", key="complete-partial", revision=7,
                                         input=self.completion_input(core, started, object_id, partial=True)))
            self.assertEqual(result["result"]["scanStatus"], "partial")
            self.assertTrue(result["result"]["conclusionsValid"])

    def test_complete_audit_rejects_agent_rule_count_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, _ = self.committed_no_issue(core, started)
            result = core.handle(session(started, tool="complete_audit", key="complete-bad-count", revision=7,
                                         input=self.completion_input(core, started, object_id, assessment_count=2)))
            self.assertEqual(result["error"]["code"], "COVERAGE_INVALID")
            self.assertEqual(core._store.get_scan(started["scanId"])["run_revision"], 7)
            self.assertFalse((Path(tmp) / "audit-ledger.json").exists())

    def test_complete_audit_rejects_pending_decision(self):
        core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        case_id = case_result["result"]["caseId"]
        evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2)
        self.restore_case(core, started, object_id, case_id, revision=3)
        finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"])
        core.handle(session(started, tool="prepare_decision", key="pending-at-end", revision=5,
                            input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, result="scanned_no_issue")))
        result = core.handle(session(started, tool="complete_audit", key="complete-with-pending", revision=6,
                                     input=self.completion_input(core, started, object_id)))
        self.assertEqual(result["error"]["code"], "DECISION_PENDING")

    def test_derived_issue_report_contains_only_ledger_issue_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, case_result = self.begin_case(core, started)
            case_id = case_result["result"]["caseId"]
            evidence = self.capture_evidence(core, started, object_id, case_id=case_id, revision=2, raw=True)
            self.restore_case(core, started, object_id, case_id, revision=3)
            finding_refs = self.record_findings(core, started, object_id, case_id, evidence["result"]["evidenceId"], result="issue_found")
            prepared = core.handle(session(started, tool="prepare_decision", key="report-prepare", revision=5,
                                           input=self.prepare_input(object_id, case_id, evidence["result"]["evidenceId"], finding_refs=finding_refs, raw_visual_ref=evidence["result"]["screenshotRef"])))
            committed = core.handle(session(started, tool="commit_decision", key="report-commit", revision=6,
                                            input={"pendingDecisionId":prepared["result"]["pendingDecisionId"]}))
            entrypoint_id = core._store.get_entrypoints(started["currentPageStateId"])[0]["entrypointId"]
            complete_input = {"visitedPageStateRefs":[started["currentPageStateId"]],"processedObjectRefs":[object_id],"processedEntrypointRefs":[entrypoint_id],"skippedEntrypoints":[],"ruleSummaries":[{"rule":{"ruleId":"FUA-10","version":"1.1.0"},"assessmentCount":1,"resultCounts":{"issue_found":1},"coverageComplete":True}],"unprocessedEntrypointRefs":[],"completionReason":"Issue and coverage are confirmed"}
            core.handle(session(started, tool="complete_audit", key="report-complete", revision=7, input=complete_input))
            report = json.loads((Path(tmp) / "issues.json").read_text())
            self.assertEqual(len(report["issues"]), 1)
            self.assertEqual(report["issues"][0]["issueId"], committed["result"]["issueId"])
            self.assertEqual(report["issues"][0]["screenshotRef"], prepared["result"]["screenshotRef"])
            summary = (Path(tmp) / "audit-summary.md").read_text()
            self.assertIn("Filter region lacks reset", summary)

    def test_failed_scan_view_hides_formally_recorded_issue(self):
        ledger = json.loads(Path("examples/issue-ledger.json").read_text())
        ledger["scan"]["status"] = "failed"
        ledger["scan"]["conclusionsValid"] = False
        rendered = DerivedReportBuilder().render(ledger)
        issues = json.loads(rendered["issues.json"])
        self.assertEqual(issues["issues"], [])
        self.assertEqual(issues["invalidatedIssueRefs"], [ledger["issues"][0]["issueId"]])
        summary = rendered["audit-summary.md"].decode("utf-8")
        self.assertIn("These counts are diagnostic only", summary)
        self.assertIn("Conclusion validity: invalidated", summary)

    def test_report_bundle_conflict_rolls_back_new_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.make_core(evidence_adapter=DeterministicEvidenceAdapter(), recovery_adapter=DeterministicRecoveryAdapter())
            started = bootstrap(core)["result"]
            with core._store.transaction() as connection:
                connection.execute("UPDATE scans SET output_dir=? WHERE scan_id=?", (tmp, started["scanId"]))
            object_id, _ = self.committed_no_issue(core, started)
            (Path(tmp) / "issues.json").write_text("conflict")
            result = core.handle(session(started, tool="complete_audit", key="report-conflict", revision=7,
                                         input=self.completion_input(core, started, object_id)))
            self.assertEqual(result["error"]["code"], "LEDGER_EXPORT_FAILED")
            self.assertEqual((Path(tmp) / "issues.json").read_text(), "conflict")
            self.assertFalse((Path(tmp) / "audit-ledger.json").exists())
            self.assertFalse((Path(tmp) / "audit-summary.md").exists())

    def test_sent_request_fails_scan(self):
        adapter = DeterministicActionAdapter(ActionExecution(requests=(NetworkRequest("POST", "https://test.example.com/orders/save", sent=True),)))
        core = self.make_core(action_adapter=adapter)
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        request = session(started, tool="perform_action", key="action-1", revision=2, input={"pageStateId":started["currentPageStateId"],"caseId":case_result["result"]["caseId"],"objectId":object_id,"type":"focus","intent":"Observe the filter region","parameters":{}})
        result = core.handle(request)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(core._store.get_scan(started["scanId"])["status"], "failed")
        retry = core.handle(request)
        self.assertEqual(retry["error"]["code"], "PERSISTENT_WRITE_OBSERVED")
        self.assertEqual(adapter.calls, 1)

    def test_default_action_adapter_fails_closed_without_unknown_claim(self):
        core = self.make_core()
        started = bootstrap(core)["result"]
        object_id, case_result = self.begin_case(core, started)
        result = self.perform_action(core, started, object_id, case_result["result"]["caseId"])
        self.assertEqual(result["error"]["code"], "ACTION_ADAPTER_UNAVAILABLE")
        self.assertEqual(result["runRevision"], 2)


if __name__ == "__main__":
    unittest.main()
