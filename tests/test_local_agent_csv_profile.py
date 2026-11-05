"""Slice 75: synthetic only, owner-selected bounded CSV within reviewed plans."""
import http.client
from http.server import ThreadingHTTPServer
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_csv_profile import (
    CSVProfileError, parse_selected_csv, csv_source_metadata,
    profile_selected_csv)
from local_agent_task_plans import (
    LocalAgentPlanStore, PlanError, propose_readonly_plan)
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler
from local_plan_artifacts import LocalPlanArtifactExporter

CSV = "revenue,stage\n10,SYNTHETIC_PERSON_1\n20,SYNTHETIC_PERSON_2\n,SYNTHETIC_PERSON_3\n"
SOURCE = {"filename": "synthetic-ledger.csv", "content": CSV}
TOOL = {"tool": "local_csv_profile", "args": {"columns": ["revenue", "stage"]}}


def proposed():
    return {"model": "fixture:model", "goal": "Profile two selected CSV columns",
            "steps": [TOOL]}


class CSVProfileUnitTests(unittest.TestCase):
    def test_numerical_and_categorical_profile_without_raw_record_echo(self):
        parsed = parse_selected_csv(**SOURCE)
        meta = csv_source_metadata(parsed)
        self.assertEqual(3, meta["row_count"])
        self.assertEqual(["revenue", "stage"], meta["columns"])
        self.assertNotIn("SYNTHETIC_PERSON_1", json.dumps(meta))
        result = profile_selected_csv(parsed, ["revenue", "stage"])
        self.assertEqual(3, result["row_count"])
        self.assertEqual(0, result["model_calls"])
        self.assertEqual(0, result["external_write_count"])
        revenue, stage = result["column_stats"]
        self.assertEqual(2, revenue["nonempty_count"])
        self.assertEqual(1, revenue["missing_count"])
        self.assertEqual({"min": "10", "max": "20", "sum": "30",
                          "mean_6dp": "15.000000"}, revenue["numeric_summary"])
        self.assertEqual("text_or_empty", stage["type"])
        self.assertEqual(3, stage["distinct_count"])
        self.assertIsNone(stage["numeric_summary"])
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(result))

    def test_malformed_csv_and_injection_labels_rejected(self):
        invalid = [
            ("../escape.csv", CSV),
            ("evil.csv", "revenue,revenue\n1,2\n"),
            ("evil.csv", "revenue,REVENUE\n1,2\n"),
            ("evil.csv", "first,hello world\n1,2\n"),
            ("evil.csv", "first,second\n1\n"),
            ("evil.csv", "first,second\n1,2,3\n"),
            ("evil.csv", 'first,second\n"unclosed,2\n'),
            ("evil.csv", "first,second\n=1+2,ok\x00\n"),
            ("evil.csv", "first,second\n1,hello\tworld\n"),
            ("evil.csv", "one\n" + "x\n" * 101),
            ("evil.csv", "one\n" + "x" * 121 + "\n"),
            ("evil.csv", "A,B,C,D,E,F,G,H,I\n1,2,3,4,5,6,7,8,9\n"),
            ("evil.csv", "one\n" + "x" * 6001),
        ]
        for filename, content in invalid:
            with self.subTest(filename=filename, content=content[:20]):
                with self.assertRaises(CSVProfileError):
                    parse_selected_csv(filename, content)
        parsed = parse_selected_csv(**SOURCE)
        for cols in ([], ["unknown"], ["revenue", "revenue"], "revenue",
                     ["stage", 2], ["stage"] * 9):
            with self.subTest(columns=cols):
                with self.assertRaises(CSVProfileError):
                    profile_selected_csv(parsed, cols)

    def test_planner_receives_only_metadata_and_rejects_ungranted_tool(self):
        context = {key: csv_source_metadata(parse_selected_csv(**SOURCE))[key]
                   for key in ("filename", "source_bytes", "sha256",
                               "row_count", "columns")}
        captured = []
        def transport(payload):
            captured.append(payload)
            return {"message": {"content": json.dumps({"steps": [TOOL]})}}
        planned = propose_readonly_plan(
            "Profile selected CSV", "fixture:model",
            transport=transport, csv_context=context)
        self.assertEqual([TOOL], planned["steps"])
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(captured))
        self.assertIn("revenue", json.dumps(captured))
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            propose_readonly_plan("Profile", "fixture:model", transport=transport)
        with self.assertRaisesRegex(PlanError, "invalid_plan_csv"):
            propose_readonly_plan("Profile", "fixture:model", transport=transport,
                                  csv_context={**context, "columns": ["../etc"]})

    def test_plan_requires_selection_approval_and_persists_cited_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plans.sqlite3"
            store = LocalAgentPlanStore(path, csv_profiler=profile_selected_csv)
            try:
                with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
                    store.create(proposed())
                with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
                    store.create({"model":"fixture:model","goal":"do nothing",
                                  "steps":[{"tool":"utc_now","args":{}}]},
                                 csv_snapshot=SOURCE)
                plan = store.create(proposed(), csv_snapshot=SOURCE)
                pid = plan["plan_id"]
                self.assertEqual("PREPARED", plan["status"])
                self.assertEqual(3, plan["csv_source"]["row_count"])
                self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(plan))
                self.assertEqual([], plan["results"])
                store.approve(pid)
                self.assertEqual([], store.get(pid)["results"])
                done = store.advance(pid)
                self.assertEqual("COMPLETED", done["status"])
                out = json.loads(done["results"][0]["output"])
                self.assertEqual("30", out["column_stats"][0]["numeric_summary"]["sum"])
                self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(done))
            finally:
                store.close()
            reopened = LocalAgentPlanStore(path, csv_profiler=profile_selected_csv)
            try:
                self.assertEqual(3, reopened.get(pid)["csv_source"]["row_count"])
                with closing(sqlite3.connect(path)) as db:
                    raw = db.execute("SELECT csv_json FROM agent_task_plans "
                                     "WHERE plan_id=?", (pid,)).fetchone()[0]
                    self.assertIn("SYNTHETIC_PERSON_1", raw)
                    db.execute("UPDATE agent_task_plans SET csv_json=? "
                               "WHERE plan_id=?", (raw.replace("10,SYNTHETIC", "90,SYNTHETIC"), pid))
                    db.commit()
                with self.assertRaisesRegex(PlanError, "plan_storage_invalid"):
                    reopened.get(pid)
            finally:
                reopened.close()

    def test_restart_without_csv_permission_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plans.sqlite3"
            store = LocalAgentPlanStore(path, csv_profiler=profile_selected_csv)
            pid = store.create(proposed(), csv_snapshot=SOURCE)["plan_id"]
            store.close()
            blocked = LocalAgentPlanStore(path)
            try:
                with self.assertRaisesRegex(PlanError, "plan_csv_unavailable"):
                    blocked.approve(pid)
                self.assertEqual("PREPARED", blocked.get(pid)["status"])
            finally:
                blocked.close()


class CSVProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalAgentPlanStore(Path(tmp.name)/"plans.sqlite3",
                                          csv_profiler=profile_selected_csv)
        self.addCleanup(self.store.close)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(
            "fixture_csv_test_only_owner_password")
        self.proposals = []
        def planner(goal, model, **kwargs):
            self.proposals.append(kwargs)
            return {"model": model, "goal": goal, "steps": [TOOL]}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda *_: None,
                           local_agent_planner=planner,
                           local_agent_plan_store=self.store))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _, session = self.auth.login("fixture_csv_test_only_owner_password")
        self.cookie = "aas_session=" + session["cookie_token"]
        self.csrf = session["csrf_token"]

    def req(self, method, path, data=None, *, auth=True, csrf=True, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1",
                                          self.server.server_port, timeout=8)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, json.dumps(data).encode()
                     if data is not None else None, headers)
        res = conn.getresponse()
        result = res.status, json.loads(res.read())
        conn.close()
        return result

    def test_auth_csrf_source_gates_and_no_raw_csv_in_results(self):
        path = "/api/local-agent-plan-create"
        payload = {"goal": "Profile selected CSV", "model": "fixture:model",
                   "csv": SOURCE}
        self.assertEqual(401, self.req("POST", path, payload, auth=False)[0])
        self.assertEqual(403, self.req("POST", path, payload, csrf=False)[0])
        self.assertEqual(403, self.req("POST", path, payload,
                                      origin="https://evil.invalid")[0])
        self.assertEqual(422, self.req("POST", path,
            {**payload, "document": {"filename":"a.txt", "content":"x"}})[0])
        self.assertEqual(422, self.req("POST", path,
            {**payload, "csv": {"filename":"x.csv","content":"x\n"}})[0])
        self.assertEqual([], self.proposals)
        status, created = self.req("POST", path, payload)
        self.assertEqual(200, status)
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(created))
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(self.proposals))
        self.assertEqual("PREPARED", created["status"])
        pid = created["plan_id"]
        self.assertEqual(200, self.req("POST",
            "/api/local-agent-plan-approve", {"plan_id":pid})[0])
        status, next_step = self.req("POST",
            "/api/local-agent-plan-next", {"plan_id":pid})
        self.assertEqual(200, status)
        self.assertEqual("COMPLETED", next_step["status"])
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(next_step))
        self.assertEqual(200, self.req("GET",
            "/api/local-agent-plan?plan_id="+pid)[0])
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(
            self.req("GET", "/api/local-agent-plan?plan_id="+pid)[1]))


if __name__ == "__main__":
    unittest.main()
