import tempfile
import unittest
from pathlib import Path

from assayer_host import (
    DeterministicLoginAdapter,
    DeterministicObjectIdentityAdapter,
    DeterministicPageAdapter,
    HostCore,
    HostError,
    RuntimeRouter,
)


def start_request(key="bootstrap-one", *, url="https://test.example.com", output_dir="auto"):
    return {
        "protocolVersion": "1.0", "requestId": f"request-{key}", "agentTurnId": "turn-start",
        "tool": "start_audit", "idempotencyKey": key,
        "input": {"url": url, "ruleRegistryVersion": "1.0.0", "outputDir": output_dir,
                  "browserProfile": "default", "authMode": "anonymous"},
    }


def session_request(scan, *, tool="get_audit_progress", key="progress", input_data=None):
    return {
        "protocolVersion": "1.0", "requestId": f"request-{key}", "agentTurnId": f"turn-{key}",
        "scanId": scan["scanId"], "runId": scan["runId"], "tool": tool,
        "idempotencyKey": key, "expectedRunRevision": scan["runRevision"],
        "input": input_data or {},
    }


class FakeRuntime:
    def __init__(self, url, output_dir, scan_id):
        self.url = url
        self.output_dir = Path(output_dir)
        self.scan_id = scan_id
        self.run_id = f"run-{scan_id.removeprefix('scan-')}"
        self.revision = 1
        self.status = "exploring"
        self.requests = []
        self.failures = []
        self.close_calls = 0
        self.runtime_events = []
        self.refresh_calls = 0

    def handle(self, request):
        self.requests.append(request)
        if request["tool"] == "start_audit":
            return {"protocolVersion": "1.0", "requestId": request["requestId"],
                    "scanId": self.scan_id, "runId": self.run_id, "runRevision": self.revision,
                    "status": "ok", "result": {"scanId": self.scan_id, "runId": self.run_id,
                    "runRevision": self.revision}, "evidenceRefs": [], "diagnosticRefs": []}
        if request["tool"] == "complete_audit":
            self.revision += 1
            self.status = "completed"
            result = {"scanStatus": "completed", "runRevision": self.revision}
        else:
            result = {"operationId": f"operation-{len(self.requests)}", "runRevision": self.revision,
                      "scanStatus": self.status}
        return {"protocolVersion": "1.0", "requestId": request["requestId"],
                "scanId": self.scan_id, "runId": self.run_id, "runRevision": self.revision,
                "status": "ok", "result": result, "evidenceRefs": [], "diagnosticRefs": []}

    def protocol_state(self):
        return {"scanId": self.scan_id, "runId": self.run_id,
                "runRevision": self.revision, "scanStatus": self.status}

    def fail_scan(self, code, message):
        self.revision += 1
        self.status = "failed"
        self.failures.append((code, message))
        return {"scanId": self.scan_id, "runId": self.run_id, "runRevision": self.revision,
                "scanStatus": "failed", "failureEventId": "supervision-fake"}

    def close(self):
        self.close_calls += 1

    def record_runtime_event(self, event):
        self.runtime_events.append(event)
        return event

    def refresh_observability(self):
        self.refresh_calls += 1


class RuntimeFactory:
    def __init__(self):
        self.runtimes = []

    def __call__(self, url, output_dir, scan_id):
        runtime = FakeRuntime(url, output_dir, scan_id)
        self.runtimes.append(runtime)
        return runtime


class RuntimeRouterTest(unittest.TestCase):
    def make_router(self, root, *, clock=lambda: 0.0, max_runtimes=4, **router_options):
        factory = RuntimeFactory()
        counter = iter(range(1, 20))
        router = RuntimeRouter(
            root, max_runtimes=max_runtimes, lease_timeout_seconds=10,
            runtime_factory=factory, scan_id_factory=lambda: f"scan-{next(counter):03d}",
            clock=clock, start_monitor=False, **router_options,
        )
        self.addCleanup(router.close)
        return router, factory

    def test_start_binds_url_at_request_time_and_rewrites_output_to_fixed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            request = start_request()
            response = router.handle(request)
            runtime = factory.runtimes[0]
            self.assertEqual(response["result"]["scanId"], "scan-001")
            self.assertEqual(runtime.url, "https://test.example.com")
            self.assertEqual(runtime.output_dir, Path(tmp).resolve() / "scan-001")
            self.assertEqual(runtime.requests[0]["input"]["outputDir"], str(runtime.output_dir))
            self.assertEqual(request["input"]["outputDir"], "auto")
            self.assertEqual(router.active_scan_count, 1)
            self.assertIn("lease.started", [item["name"] for item in runtime.runtime_events])

    def test_arbitrary_output_paths_and_profiles_are_rejected_before_runtime_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            with self.assertRaises(HostError) as path_error:
                router.handle(start_request(output_dir="../../escape"))
            self.assertEqual(path_error.exception.code, "OUTPUT_PATH_REJECTED")
            request = start_request(key="profile")
            request["input"]["browserProfile"] = "custom"
            with self.assertRaises(HostError) as profile_error:
                router.handle(request)
            self.assertEqual(profile_error.exception.code, "INVALID_REQUEST")
            self.assertEqual(factory.runtimes, [])

    def test_bootstrap_retry_is_idempotent_and_conflicting_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            first = router.handle(start_request())
            second = router.handle(start_request())
            self.assertEqual(first["result"], second["result"])
            self.assertEqual(len(factory.runtimes), 1)
            conflict = start_request(url="https://other.example.com")
            with self.assertRaises(HostError) as caught:
                router.handle(conflict)
            self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_session_requests_are_isolated_by_scan_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            first = router.handle(start_request("bootstrap-one"))["result"]
            second = router.handle(start_request("bootstrap-two", url="https://other.example.com"))["result"]
            router.handle(session_request(first, key="first-progress"))
            router.handle(session_request(second, key="second-progress"))
            self.assertEqual(factory.runtimes[0].requests[-1]["scanId"], first["scanId"])
            self.assertEqual(factory.runtimes[1].requests[-1]["scanId"], second["scanId"])
            wrong = session_request(first, key="wrong-run")
            wrong["runId"] = second["runId"]
            with self.assertRaises(HostError) as caught:
                router.handle(wrong)
            self.assertEqual(caught.exception.code, "UNKNOWN_REFERENCE")

    def test_capacity_is_released_only_after_terminal_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, max_runtimes=1)
            scan = router.handle(start_request())["result"]
            with self.assertRaises(HostError) as caught:
                router.handle(start_request("bootstrap-two", url="https://other.example.com"))
            self.assertEqual(caught.exception.code, "RUN_CAPACITY_EXCEEDED")
            completion = session_request(scan, tool="complete_audit", key="complete")
            router.handle(completion)
            self.assertEqual(router.active_scan_count, 0)
            self.assertEqual(factory.runtimes[0].close_calls, 1)
            self.assertIn("lease.released", [item["name"] for item in factory.runtimes[0].runtime_events])
            self.assertEqual(factory.runtimes[0].refresh_calls, 1)
            router.handle(start_request("bootstrap-two", url="https://other.example.com"))
            self.assertEqual(router.active_scan_count, 1)

    def test_expired_agent_lease_fails_scan_and_closes_runtime(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, clock=lambda: now[0])
            scan = router.handle(start_request())["result"]
            now[0] = 11.0
            self.assertEqual(router.sweep_expired(), 1)
            runtime = factory.runtimes[0]
            self.assertEqual(runtime.failures[0][0], "AGENT_LEASE_EXPIRED")
            self.assertEqual(runtime.status, "failed")
            self.assertEqual(runtime.close_calls, 1)
            self.assertIn("lease.expired", [item["name"] for item in runtime.runtime_events])
            self.assertIn("supervisor.terminal", [item["name"] for item in runtime.runtime_events])
            self.assertEqual(runtime.refresh_calls, 1)
            self.assertEqual(router.protocol_state(scan["scanId"], scan["runId"])["scanStatus"], "failed")

    def test_each_valid_request_renews_the_agent_lease(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, clock=lambda: now[0])
            scan = router.handle(start_request())["result"]
            now[0] = 9.0
            progress = router.handle(session_request(scan, key="lease-renew"))
            self.assertEqual(progress["status"], "ok")
            self.assertIn("transport.request.finished", [item["name"] for item in factory.runtimes[0].runtime_events])
            started_event = next(item for item in factory.runtimes[0].runtime_events if item["name"] == "transport.request.started")
            finished_event = next(item for item in factory.runtimes[0].runtime_events if item["name"] == "transport.request.finished")
            self.assertGreater(started_event["attributes"]["requestBytes"], 0)
            self.assertGreater(finished_event["attributes"]["responseBytes"], 0)
            now[0] = 18.5
            self.assertEqual(router.sweep_expired(), 0)
            self.assertEqual(factory.runtimes[0].status, "exploring")
            now[0] = 19.1
            self.assertEqual(router.sweep_expired(), 1)
            self.assertEqual(factory.runtimes[0].status, "failed")

    def test_transport_heartbeat_renews_lease_during_model_thinking(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, clock=lambda: now[0])
            scan = router.handle(start_request())["result"]
            now[0] = 9.0
            self.assertEqual(router.heartbeat(), 1)
            self.assertIn("lease.heartbeat", [item["name"] for item in factory.runtimes[0].runtime_events])
            now[0] = 18.5
            self.assertEqual(router.sweep_expired(), 0)
            self.assertEqual(router.protocol_state(scan["scanId"], scan["runId"])["scanStatus"], "exploring")

    def test_failed_completion_requires_progress_before_any_other_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            scan = router.handle(start_request())["result"]
            runtime = factory.runtimes[0]
            original_handle = runtime.handle

            def reject_completion(request):
                if request["tool"] == "complete_audit":
                    runtime.requests.append(request)
                    return {
                        "protocolVersion": "1.0", "requestId": request["requestId"],
                        "scanId": scan["scanId"], "runId": scan["runId"], "runRevision": 1,
                        "status": "rejected",
                        "error": {"code": "COVERAGE_INVALID", "message": "coverage incomplete",
                                  "retryable": False, "requiredNextStep": "fix_tool_input"},
                        "evidenceRefs": [], "diagnosticRefs": [],
                    }
                return original_handle(request)

            runtime.handle = reject_completion
            rejected = router.handle(session_request(scan, tool="complete_audit", key="complete-bad"))
            self.assertEqual(rejected["error"]["code"], "COVERAGE_INVALID")
            with self.assertRaises(HostError) as blocked:
                router.handle(session_request(scan, tool="inspect_page", key="bypass"))
            self.assertEqual(blocked.exception.code, "AUDIT_PROGRESS_REQUIRED")
            self.assertEqual(blocked.exception.next_step, "call_get_audit_progress")
            self.assertEqual(runtime.requests[-1]["tool"], "complete_audit")
            progress = router.handle(session_request(scan, key="required-progress"))
            self.assertEqual(progress["status"], "ok")
            allowed = router.handle(session_request(scan, tool="inspect_page", key="after-progress"))
            self.assertEqual(allowed["status"], "ok")

    def test_repeated_rejection_exhausts_control_budget_and_fails_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, max_repeated_failures=2)
            scan = router.handle(start_request())["result"]
            runtime = factory.runtimes[0]

            def reject(request):
                runtime.requests.append(request)
                return {
                    "protocolVersion": "1.0", "requestId": request["requestId"],
                    "scanId": scan["scanId"], "runId": scan["runId"], "runRevision": 1,
                    "status": "rejected",
                    "error": {"code": "ACTION_BLOCKED", "message": "same blocked action",
                              "retryable": False, "requiredNextStep": "stop"},
                    "evidenceRefs": [], "diagnosticRefs": [],
                }

            runtime.handle = reject
            for index in range(2):
                response = router.handle(session_request(scan, tool="perform_action", key=f"blocked-{index}"))
                self.assertEqual(response["status"], "rejected")
            with self.assertRaises(HostError) as stopped:
                router.handle(session_request(scan, tool="perform_action", key="blocked-3"))
            self.assertEqual(stopped.exception.code, "AGENT_CONTROL_BUDGET_EXCEEDED")
            self.assertEqual(runtime.failures[-1][0], "AGENT_CONTROL_BUDGET_EXCEEDED")
            self.assertEqual(router.protocol_state(scan["scanId"], scan["runId"])["scanStatus"], "failed")
            names = [item["name"] for item in runtime.runtime_events]
            self.assertIn("agent.control.budget_exhausted", names)

    def test_repeated_success_without_revision_progress_exhausts_stall_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp, max_repeated_successes=2)
            scan = router.handle(start_request())["result"]
            runtime = factory.runtimes[0]
            for index in range(2):
                response = router.handle(session_request(scan, key=f"progress-{index}"))
                self.assertEqual(response["status"], "ok")
            with self.assertRaises(HostError) as stopped:
                router.handle(session_request(scan, key="progress-3"))
            self.assertEqual(stopped.exception.code, "AGENT_CONTROL_BUDGET_EXCEEDED")
            self.assertEqual(runtime.failures[-1][0], "AGENT_CONTROL_BUDGET_EXCEEDED")
            self.assertIn("agent.control.stalled", [item["name"] for item in runtime.runtime_events])

    def test_router_close_marks_active_scans_failed_instead_of_publishing_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, factory = self.make_router(tmp)
            router.handle(start_request())
            router.close()
            runtime = factory.runtimes[0]
            self.assertEqual(runtime.failures[0][0], "AGENT_RUNTIME_EXITED")
            self.assertEqual(runtime.status, "failed")
            self.assertEqual(runtime.close_calls, 1)

    def test_core_supervision_failure_invalidates_active_case(self):
        core = HostCore(login_adapter=DeterministicLoginAdapter(), page_adapter=DeterministicPageAdapter(),
                        object_identity_adapter=DeterministicObjectIdentityAdapter())
        self.addCleanup(core.close)
        started_response = core.handle(start_request("core-start"))
        started = started_response["result"]
        page = core.handle(session_request(started, tool="inspect_page", key="page", input_data={
            "pageStateId": started["currentPageStateId"], "include": ["objects"],
        }))["result"]
        verified = core.handle(session_request(started, tool="inspect_object", key="object", input_data={
            "candidateId": page["candidateRefs"][0],
        }))["result"]
        case = core.handle(session_request(started, tool="begin_case", key="case", input_data={
            "objectId": verified["objectId"], "rule": verified["potentialRules"][0],
            "kind": "observation", "purpose": "supervision test",
            "plannedCoverageDimensions": ["filter_present"],
        }))["result"]
        failed = core.fail_scan(started["scanId"], started["runId"],
                                "AGENT_RUNTIME_EXITED", "Agent runtime exited")
        self.assertEqual(failed["scanStatus"], "failed")
        self.assertEqual(failed["runRevision"], case["runRevision"] + 1)
        stored = core._store.get_case(case["caseId"])
        self.assertEqual(stored["status"], "invalidated")
        self.assertEqual(stored["restoreReason"]["code"], "AGENT_RUNTIME_EXITED")
        operation = core._store.get_operation(failed["failureEventId"])
        self.assertEqual(operation["tool"], "agent_supervision")
        self.assertEqual(operation["status"], "failed_known")


if __name__ == "__main__":
    unittest.main()
