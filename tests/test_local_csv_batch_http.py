"""HTTP and SQLite acceptance for explicit preview-then-commit CSV intake.

Tests use the real loopback handler, strict categorical events and ephemeral DB.
No external endpoints, user identities, or provider credentials.
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler

BASE_CSV = ("event_id,service_code,budget_tier,timeline\n"
            "case_001,automation,3,soon\n"
            "case_002,integration,1,flexible\n")
CONFIG = {"workflow_id": "csv_flow", "display_name": "CSV Intake Rules",
          "service_filter": "automation", "min_budget_tier": 2,
          "followup_policy": "review_required"}


class LocalCSVBatchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "events.sqlite3"
        self.core = LocalWorkflowCore(self.db_path)
        self.addCleanup(self.core.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          create_handler(local_core=self.core))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method, route, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        encoded = json.dumps(body) if body is not None else None
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        conn.request(method, route, body=encoded, headers=request_headers)
        response = conn.getresponse()
        code, value = response.status, json.loads(response.read())
        conn.close()
        return code, value

    def ready(self, **settings):
        code, out = self.http("POST", "/api/local-create", {**CONFIG, **settings})
        self.assertEqual(201, code, out)
        code, out = self.http("POST", "/api/local-activate",
                              {"workflow_id": "csv_flow", "expected_version": 1})
        self.assertEqual(200, code, out)

    def preview(self, csv_text=BASE_CSV, workflow_id="csv_flow"):
        return self.http("POST", "/api/local-batch-preview",
                         {"workflow_id": workflow_id, "csv_text": csv_text})

    def commit(self, preview, csv_text=BASE_CSV, **changes):
        request = {"workflow_id": "csv_flow",
                   "csv_text": csv_text,
                   "expected_version": preview["workflow_version"],
                   "preview_token": preview["preview_token"]}
        request.update(changes)
        return self.http("POST", "/api/local-batch-commit", request)

    def count(self):
        code, result = self.http("GET", "/api/local-history?workflow_id=csv_flow")
        self.assertEqual(200, code)
        return result["total"]

    def test_preview_reports_two_sanitized_rule_decisions_no_write(self):
        self.ready()
        code, out = self.preview()
        self.assertEqual(200, code)
        self.assertEqual("PREVIEW_READY", out["status"])
        self.assertEqual((2, 0), (out["new_count"], out["replay_count"]))
        self.assertEqual("NEEDS_REVIEW_LOCAL", out["rows"][0]["decision"])
        self.assertEqual("SERVICE_MISMATCH", out["rows"][1]["reason"])
        self.assertEqual(0, out["external_dispatch_count"])
        self.assertEqual(0, self.count())

    def test_explicit_commit_is_atomic_and_persists(self):
        self.ready()
        code, out = self.commit(self.preview()[1])
        self.assertEqual(200, code)
        self.assertEqual("BATCH_COMMITTED_LOCAL", out["status"])
        self.assertEqual((2, 0, 2), (
            out["new_count"], out["replay_count"], out["saved_total"]))
        self.assertEqual(0, out["external_dispatch_count"])
        self.assertEqual(2, self.count())

    def test_sqlite_reopen_keeps_records_and_no_raw_csv(self):
        self.ready()
        self.commit(self.preview()[1])
        another = LocalWorkflowCore(self.db_path)
        self.addCleanup(another.close)
        code, history = another.history("csv_flow")
        self.assertEqual(200, code)
        self.assertEqual(2, history["total"])
        self.assertEqual({"event_id", "workflow_version", "service_code",
                          "budget_tier", "timeline", "decision", "reason",
                          "created_at"}, set(history["runs"][0]))
        self.assertNotIn("csv_text", json.dumps(history))

    def test_preview_again_displays_replays_without_new_rows(self):
        self.ready()
        self.commit(self.preview()[1])
        code, out = self.preview()
        self.assertEqual(200, code)
        self.assertEqual(0, out["new_count"])
        self.assertEqual(2, out["replay_count"])
        self.assertTrue(all(r["disposition"] == "REPLAYED_LOCAL" for r in out["rows"]))
        self.assertEqual(2, self.count())

    def test_new_preview_after_import_can_commit_noop_replays(self):
        self.ready()
        self.commit(self.preview()[1])
        preview = self.preview()[1]
        code, result = self.commit(preview)
        self.assertEqual(200, code)
        self.assertEqual(0, result["new_count"])
        self.assertEqual(2, result["replay_count"])
        self.assertEqual(2, self.count())

    def test_manual_run_fingerprint_matches_csv_replay(self):
        self.ready()
        self.http("POST", "/api/local-run", {
            "workflow_id": "csv_flow", "event_id": "case_001",
            "service_code": "automation", "budget_tier": 3,
            "timeline": "soon"})
        _, report = self.preview()
        self.assertEqual(1, report["replay_count"])
        self.assertEqual(1, report["new_count"])
        self.commit(report)
        self.assertEqual(2, self.count())

    def test_changed_payload_with_same_event_fails_whole_preview(self):
        self.ready()
        self.http("POST", "/api/local-run", {
            "workflow_id": "csv_flow", "event_id": "case_002",
            "service_code": "automation", "budget_tier": 3,
            "timeline": "soon"})
        code, out = self.preview()
        self.assertEqual(409, code)
        self.assertEqual("event_id_payload_conflict", out["error"])
        self.assertEqual(1, self.count())

    def test_duplicate_id_inside_file_is_rejected_before_writing(self):
        self.ready()
        csv_text = ("event_id,service_code,budget_tier,timeline\n"
                    "case_001,automation,3,soon\n"
                    "case_001,automation,3,soon\n")
        code, out = self.preview(csv_text)
        self.assertEqual(422, code)
        self.assertEqual("invalid_csv_content", out["error"])
        self.assertEqual(0, self.count())

    def test_csv_wrong_headers_and_extra_column_rejected(self):
        self.ready()
        for csv_text in (
            "event_id,service_code,timeline,budget_tier\ncase_001,automation,soon,3\n",
            "event_id,service_code,budget_tier,timeline,secret\ncase_001,automation,3,soon,hidden\n",
            "event_id,service_code,budget_tier,timeline\ncase_001,automation,3,soon,extra\n",
        ):
            self.assertEqual("invalid_csv_content", self.preview(csv_text)[1]["error"])
        self.assertEqual(0, self.count())

    def test_empty_row_and_unterminated_quote_rejected(self):
        self.ready()
        for csv_text in (
            "event_id,service_code,budget_tier,timeline\n",
            'event_id,service_code,budget_tier,timeline\n"bad,automation,3,soon\n',
            "event_id,service_code,budget_tier,timeline\ncase_001,automation,3\n",
        ):
            self.assertEqual(422, self.preview(csv_text)[0])

    def test_piilike_columns_and_formula_injection_rejected(self):
        self.ready()
        for field in ("someone@example.com", "=HYPERLINK(1)", "+1987654321",
                      "<script>"):
            csv_text = ("event_id,service_code,budget_tier,timeline\n"
                        + field + ",automation,3,soon\n")
            code, out = self.preview(csv_text)
            self.assertEqual(422, code)
            self.assertNotIn(field, json.dumps(out))

    def test_service_budget_timeline_allowlists_reject_raw_text(self):
        self.ready()
        for values in ("case_001,send-email,3,soon",
                       "case_001,automation,yes,soon",
                       "case_001,automation,3,confidential",
                       "case_001,automation,0,soon"):
            code, out = self.preview("event_id,service_code,budget_tier,timeline\n" + values + "\n")
            self.assertEqual(422, code, out)

    def test_unicode_and_nul_rejected(self):
        self.ready()
        for csv_text in (BASE_CSV + "💬", BASE_CSV.replace("case_001", "case_\x001")):
            self.assertEqual(422, self.preview(csv_text)[0])

    def test_more_than_25_rows_rejected(self):
        self.ready()
        csv_text = "event_id,service_code,budget_tier,timeline\n" + "".join(
            f"case_{n:03d},automation,3,soon\n" for n in range(26))
        self.assertEqual(422, self.preview(csv_text)[0])
        self.assertEqual(0, self.count())

    def test_more_than_four_kib_rejected(self):
        self.ready()
        self.assertEqual(422, self.preview(BASE_CSV + "x" * 4096)[0])
        self.assertEqual(0, self.count())

    def test_non_active_and_unknown_workflow_fail_closed(self):
        self.assertEqual(404, self.preview()[0])
        self.http("POST", "/api/local-create", CONFIG)
        self.assertEqual(409, self.preview()[0])
        self.assertEqual(404, self.preview(workflow_id="another_flow")[0])

    def test_invalid_csv_shape_never_reflects_raw_content(self):
        self.ready()
        for req in (
            {"workflow_id": "csv_flow", "csv_text": BASE_CSV, "secret": "private"},
            {"workflow_id": "csv_flow"},
            {"workflow_id": "csv_flow", "csv_text": 17},
            {"workflow_id": "not/valid", "csv_text": BASE_CSV},
        ):
            code, result = self.http("POST", "/api/local-batch-preview", req)
            self.assertEqual(422, code)
            self.assertNotIn("case_001", json.dumps(result))

    def test_commit_requires_exact_preview_token_fields(self):
        self.ready()
        p = self.preview()[1]
        for changes in ({"preview_token": "x" * 64},
                        {"preview_token": "not-a-token"},
                        {"preview_token": True},
                        {"expected_version": True},
                        {"expected_version": 2}):
            code, result = self.commit(p, **changes)
            self.assertIn(code, (409, 422))
            self.assertEqual(0, result["external_dispatch_count"])
        self.assertEqual(0, self.count())

    def test_commit_changed_csv_with_old_token_rejected_atomically(self):
        self.ready()
        p = self.preview()[1]
        changed = BASE_CSV.replace("case_002", "case_003")
        code, out = self.commit(p, csv_text=changed)
        self.assertEqual(409, code)
        self.assertEqual("stale_batch_preview", out["error"])
        self.assertEqual(0, self.count())

    def test_ledger_change_after_preview_requires_new_preview(self):
        self.ready()
        p = self.preview()[1]
        self.http("POST", "/api/local-run", {
            "workflow_id": "csv_flow", "event_id": "external_001",
            "service_code": "consulting", "budget_tier": 3,
            "timeline": "urgent"})
        code, out = self.commit(p)
        self.assertEqual(409, code)
        self.assertEqual("stale_batch_preview", out["error"])
        self.assertEqual(1, self.count())

    def test_capacity_rejects_entire_batch_without_partial_commit(self):
        self.ready()
        import local_csv_batch
        with patch.object(local_csv_batch, "MAX_RUNS_PER_WORKFLOW", 1):
            code, out = self.preview()
        self.assertEqual(409, code)
        self.assertEqual("run_capacity_reached", out["error"])
        self.assertEqual(0, self.count())

    def test_same_preview_concurrent_commit_only_one_wins(self):
        self.ready()
        p = self.preview()[1]
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: self.commit(p)[0], range(2)))
        self.assertEqual([200, 409], sorted(statuses))
        self.assertEqual(2, self.count())

    def test_partial_existing_rows_can_commit_new_rows(self):
        self.ready()
        self.http("POST", "/api/local-run", {
            "workflow_id": "csv_flow", "event_id": "case_001",
            "service_code": "automation", "budget_tier": 3,
            "timeline": "soon"})
        p = self.preview()[1]
        code, result = self.commit(p)
        self.assertEqual(200, code)
        self.assertEqual((1, 1), (result["new_count"], result["replay_count"]))
        self.assertEqual(2, self.count())

    def test_unauthorized_host_and_origin_rejected_for_both_routes(self):
        self.ready()
        for route in ("/api/local-batch-preview", "/api/local-batch-commit"):
            self.assertEqual(403, self.http("POST", route, {},
                           {"Origin": "http://evil.example"})[0])
            self.assertEqual(403, self.http("POST", route, {},
                           {"Host": "evil.example"})[0])
        self.assertEqual(0, self.count())

    def test_unknown_route_cannot_dispatch_and_history_stays_empty(self):
        self.ready()
        self.assertEqual(404, self.http("POST", "/api/local-batch-send", {})[0])
        self.assertEqual(0, self.count())


if __name__ == "__main__":
    unittest.main()
