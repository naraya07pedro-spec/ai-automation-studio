"""First functional local workflow lifecycle: SQLite + loopback HTTP acceptance.

All data is synthetic labels and categorical values. No third-party effects.
"""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import local_workflow_core as core_module
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler


CREATE = {
    "workflow_id": "sample_flow",
    "display_name": "Sales Rules",
    "service_filter": "automation",
    "min_budget_tier": 2,
    "followup_policy": "review_required",
}
EVENT = {
    "workflow_id": "sample_flow", "event_id": "event_100",
    "service_code": "automation", "budget_tier": 3, "timeline": "soon",
}


class LocalCoreHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "durable.sqlite3"
        self.core = LocalWorkflowCore(self.path)
        self.addCleanup(self.core.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          create_handler(local_core=self.core))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port,
                                          timeout=5)
        body = json.dumps(payload) if payload is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        status, raw = res.status, res.read()
        conn.close()
        return status, json.loads(raw)

    def create(self, **overrides):
        return self.send("POST", "/api/local-create", {**CREATE, **overrides})

    def activate(self, version=1, workflow_id="sample_flow"):
        return self.send("POST", "/api/local-activate",
                         {"workflow_id": workflow_id, "expected_version": version})

    def run(self, **overrides):
        return self.send("POST", "/api/local-run", {**EVENT, **overrides})

    def ready(self):
        self.assertEqual(201, self.create()[0])
        self.assertEqual(200, self.activate()[0])

    def test_create_draft_and_list_count(self):
        status, item = self.create()
        self.assertEqual(201, status)
        self.assertEqual("CREATED_DRAFT", item["status"])
        status, listing = self.send("GET", "/api/local-workflows")
        self.assertEqual(200, status)
        self.assertEqual(1, len(listing["workflows"]))
        self.assertEqual("DRAFT", listing["workflows"][0]["status"])
        self.assertEqual(0, listing["workflows"][0]["run_count"])

    def test_cannot_execute_inactive_workflow(self):
        self.create()
        status, result = self.run()
        self.assertEqual(409, status)
        self.assertEqual("workflow_not_active", result["error"])
        self.assertEqual(0, self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]["total"])

    def test_activate_requires_pinned_version(self):
        self.create()
        self.assertEqual("workflow_version_conflict", self.activate(2)[1]["error"])
        self.assertEqual("ACTIVE_LOCAL", self.activate()[1]["status"])
        self.assertEqual("workflow_already_activated", self.activate()[1]["error"])

    def test_local_rule_match_review_required(self):
        self.ready()
        status, result = self.run()
        self.assertEqual(200, status)
        self.assertEqual("RECORDED_LOCAL", result["status"])
        self.assertEqual("NEEDS_REVIEW_LOCAL", result["decision"])
        self.assertEqual("POLICY_REQUIRES_REVIEW", result["reason"])
        self.assertEqual(0, result["external_dispatch_count"])

    def test_draft_only_match_not_a_send(self):
        self.create(followup_policy="draft_only")
        self.activate()
        status, result = self.run()
        self.assertEqual(200, status)
        self.assertEqual("QUALIFIED_DRAFT_ONLY", result["decision"])
        self.assertEqual("MATCHED_LOCAL_RULES", result["reason"])
        self.assertEqual(0, result["external_dispatch_count"])

    def test_service_mismatch_does_not_qualify(self):
        self.ready()
        self.assertEqual("SERVICE_MISMATCH",
                         self.run(service_code="integration")[1]["reason"])

    def test_low_budget_blocks_qualification(self):
        self.ready()
        self.assertEqual("BUDGET_BELOW_THRESHOLD",
                         self.run(budget_tier=1)[1]["reason"])

    def test_flexible_timeline_needs_review(self):
        self.ready()
        result = self.run(timeline="flexible")[1]
        self.assertEqual("NEEDS_REVIEW_LOCAL", result["decision"])
        self.assertEqual("TIMELINE_UNSPECIFIED", result["reason"])

    def test_matching_any_service(self):
        self.create(service_filter="any", min_budget_tier=1,
                    followup_policy="draft_only")
        self.activate()
        result = self.run(service_code="consulting", budget_tier=1)[1]
        self.assertEqual("QUALIFIED_DRAFT_ONLY", result["decision"])

    def test_duplicate_replays_without_new_history(self):
        self.ready()
        first = self.run()[1]
        second = self.run()[1]
        self.assertEqual("REPLAYED_LOCAL", second["status"])
        self.assertEqual(first["created_at"], second["created_at"])
        history = self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]
        self.assertEqual(1, history["total"])
        self.assertEqual(1, len(history["runs"]))
        self.assertEqual("event_100", history["runs"][0]["event_id"])

    def test_changed_payload_same_id_is_conflict(self):
        self.ready()
        self.run()
        status, result = self.run(budget_tier=1)
        self.assertEqual(409, status)
        self.assertEqual("event_id_payload_conflict", result["error"])
        self.assertEqual(1, self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]["total"])

    def test_durable_workflow_and_history_survive_new_connection(self):
        self.ready()
        self.run()
        reopened = LocalWorkflowCore(self.path)
        self.addCleanup(reopened.close)
        listing = reopened.list_workflows()
        self.assertEqual("ACTIVE", listing["workflows"][0]["status"])
        self.assertEqual(1, listing["workflows"][0]["run_count"])
        self.assertEqual(1, reopened.history("sample_flow")[1]["total"])
        self.assertEqual("REPLAYED_LOCAL", reopened.run(EVENT)[1]["status"])

    def test_run_history_is_bounded_and_not_silently_trimmed(self):
        self.ready()
        with mock.patch.object(core_module, "MAX_RUNS_PER_WORKFLOW", 1):
            self.run()
            status, result = self.run(event_id="event_101")
        self.assertEqual(409, status)
        self.assertEqual("run_capacity_reached", result["error"])
        self.assertEqual(1, self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]["total"])

    def test_name_rejects_email_or_html_data(self):
        for bad in ("test@example.com", "<script>hi</script>", "Name\nExtra"):
            self.assertEqual(422, self.create(display_name=bad)[0])
        self.assertEqual(0, len(self.send("GET", "/api/local-workflows")[1]["workflows"]))

    def test_strict_shape_and_boolean_as_int_rejected(self):
        for override in (
            {"min_budget_tier": True},
            {"min_budget_tier": 0},
            {"service_filter": "gpt"},
            {"workflow_id": "a'; DROP TABLE local_runs;"},
            {"followup_policy": "send_email"},
        ):
            self.assertEqual(422, self.create(**override)[0])

    def test_invalid_event_and_untrusted_fields_not_echoed(self):
        self.ready()
        code, response = self.run(event_id="private@example.com")
        self.assertEqual(422, code)
        self.assertNotIn("private@", json.dumps(response))
        self.assertEqual(422, self.run(budget_tier=True)[0])
        self.assertEqual(422, self.run(timeline="unknown")[0])
        self.assertEqual(422, self.run(credential="secret")[0])

    def test_unknown_and_invalid_history(self):
        self.assertEqual(404, self.send("GET", "/api/local-history?workflow_id=missing")[0])
        for q in ("", "?workflow_id=wrong%2Fid", "?workflow_id=sample_flow&extra=1",
                  "?workflow_id=x&workflow_id=y"):
            self.assertEqual(422, self.send("GET", "/api/local-history" + q)[0])

    def test_get_durable_data_rejects_cross_origin_and_host(self):
        self.ready()
        for route in ("/api/local-workflows", "/api/local-history?workflow_id=sample_flow"):
            self.assertEqual(403, self.send("GET", route, headers={"Host": "evil.example"})[0])
            self.assertEqual(403, self.send("GET", route, headers={"Origin": "https://evil.example"})[0])
        self.assertEqual(403, self.send("POST", "/api/local-run", EVENT,
                                       headers={"Origin": "https://evil.example"})[0])

    def test_no_spoofed_external_actions_or_unknown_routes(self):
        self.ready()
        self.assertEqual(404, self.send("POST", "/api/local-dispatch", EVENT)[0])
        result = self.run()[1]
        self.assertEqual(0, result["external_dispatch_count"])
        history = self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]
        self.assertEqual(0, history["external_dispatch_count"])

    def test_concurrent_same_event_is_atomic(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.run()[1]["status"], range(12)))
        self.assertEqual(1, results.count("RECORDED_LOCAL"))
        self.assertEqual(11, results.count("REPLAYED_LOCAL"))
        self.assertEqual(1, self.send("GET", "/api/local-history?workflow_id=sample_flow")[1]["total"])

    def test_up_to_fifty_workflows_fail_closed_at_limit(self):
        with mock.patch.object(core_module, "MAX_WORKFLOWS", 1):
            self.create()
            status, result = self.create(workflow_id="second_flow")
        self.assertEqual(409, status)
        self.assertEqual("workflow_capacity_reached", result["error"])

    def test_history_excludes_raw_message_credentials_and_private_memory(self):
        self.ready()
        self.run()
        _, hist = self.send("GET", "/api/local-history?workflow_id=sample_flow")
        self.assertEqual({"event_id", "workflow_version", "service_code",
                          "budget_tier", "timeline", "decision", "reason",
                          "created_at"}, set(hist["runs"][0]))
        self.assertNotIn("secret", json.dumps(hist).lower())


if __name__ == "__main__":
    unittest.main()
