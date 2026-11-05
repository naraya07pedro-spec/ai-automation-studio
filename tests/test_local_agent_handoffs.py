"""Slice 71 typed numeric handoffs; local synthetic fixtures only.

The tests do not run Ollama on the owner's Windows PC.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_handoffs import HandoffError, resolve_calculator_handoffs
from local_agent_task_plans import (
    LocalAgentPlanStore, PlanError, propose_readonly_plan, validate_steps)
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler
from local_source_policy import LocalPublicSourcePolicy

PASSWORD = "slice_71_test_only_owner_password"
REPO = "Octocat/Hello-World"


def calc(a, b, operation="multiply"):
    return {"tool": "calculate", "args": {
        "a": a, "b": b, "operation": operation}}


def ref(step, field):
    return {"from_step": step, "field": field}


def plan(steps):
    return {"goal": "Compute a result from approved previous steps",
            "model": "fixture:model", "steps": steps}


def chained_steps():
    return [calc("7", "8"), calc(ref(1, "output"), "2")]


def triage_steps():
    return [
        {"tool":"github_issue_triage","args":{"repo":REPO}},
        calc(ref(1,"review_flagged_count"),
             ref(1,"returned_count"), "divide"),
        calc(ref(2,"output"), "100")
    ]


def issue(n, labels):
    return {"state": "open", "number": n, "title": "Synthetic public issue",
            "labels": [{"name":x} for x in labels]}


class NumericHandoffPlanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "plans.sqlite3"
        self.policy = LocalPublicSourcePolicy()
        self.fetches = []
        def fetch(repo):
            self.fetches.append(repo)
            return [issue(1, ["priority:high"]), issue(2, []),
                    issue(3, ["bug"]), issue(4, ["P1"]), issue(5, [])]
        self.reader = AllowedGitHubIssuesReader(self.policy, fetcher=fetch)
        self.store = LocalAgentPlanStore(self.path, github_reader=self.reader)
        self.addCleanup(self.store.close)

    def test_literal_to_previous_calculation_is_manual_and_durable(self):
        saved = self.store.create(plan(chained_steps()))
        self.assertEqual("PREPARED", saved["status"])
        self.assertEqual([], saved["results"])
        self.assertEqual([], saved["handoffs"])
        with self.assertRaisesRegex(PlanError, "plan_state_conflict"):
            self.store.advance(saved["plan_id"])
        self.store.approve(saved["plan_id"])
        self.assertEqual([], self.store.get(saved["plan_id"])["results"])
        first = self.store.advance(saved["plan_id"])
        self.assertEqual("NEXT_READY", first["status"])
        self.assertEqual("56", first["results"][0]["output"])
        self.assertEqual([], first["handoffs"])
        # A new process can load the plan and use only recorded source output.
        restarted = LocalAgentPlanStore(self.path)
        try:
            loaded = restarted.get(saved["plan_id"])
            self.assertEqual("NEXT_READY", loaded["status"])
            self.assertEqual([], loaded["handoffs"])
            done = restarted.advance(saved["plan_id"])
            self.assertEqual("COMPLETED", done["status"])
            self.assertEqual("112", done["results"][1]["output"])
            audit = done["handoffs"]
            self.assertEqual(1, len(audit))
            self.assertEqual(2, audit[0]["step_index"])
            self.assertEqual("56", audit[0]["resolved_args"]["a"])
            self.assertEqual("2", audit[0]["resolved_args"]["b"])
            self.assertEqual("output", audit[0]["bindings"][0]["field"])
            self.assertEqual("calculate", audit[0]["bindings"][0]["source_tool"])
            self.assertEqual(64, len(audit[0]["bindings"][0]["source_sha256"]))
            self.assertEqual(audit, restarted.get(saved["plan_id"])["handoffs"])
        finally:
            restarted.close()

    def test_two_numeric_fields_from_one_authorized_public_triage(self):
        self.policy.grant(REPO)
        created = self.store.create(plan(triage_steps()))
        self.assertEqual([], self.fetches)
        self.store.approve(created["plan_id"])
        self.assertEqual([], self.fetches)
        triaged = self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY", triaged["status"])
        self.assertEqual([REPO], self.fetches)
        counts = json.loads(triaged["results"][0]["output"])
        self.assertEqual((2,5), (counts["review_flagged_count"],
                                 counts["returned_count"]))
        pct = self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY", pct["status"])
        self.assertEqual(Decimal("0.4"), Decimal(pct["results"][1]["output"]))
        self.assertEqual(["review_flagged_count","returned_count"],
                         [b["field"] for b in pct["handoffs"][0]["bindings"]])
        finished = self.store.advance(created["plan_id"])
        self.assertEqual(Decimal("40"), Decimal(finished["results"][2]["output"]))
        self.assertEqual(2, len(finished["handoffs"]))
        self.assertEqual("0.4", finished["handoffs"][1]["bindings"][0]["value"])
        self.assertEqual([REPO], self.fetches)  # zero further GitHub calls
        self.assertEqual("COMPLETED", finished["status"])
        self.assertEqual(finished["handoffs"],
                         self.store.get(created["plan_id"])["handoffs"])

    def test_revoked_github_permission_blocks_cached_numeric_handoff(self):
        self.policy.grant(REPO)
        created=self.store.create(plan(triage_steps()))
        self.store.approve(created["plan_id"])
        first=self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY",first["status"])
        self.assertEqual([REPO],self.fetches)
        self.policy.revoke(REPO)
        # A regrant cannot revive the previous approved permission epoch.
        self.policy.grant(REPO)
        with self.assertRaisesRegex(PlanError,
                                    "public_source_permission_changed"):
            self.store.advance(created["plan_id"])
        stopped=self.store.get(created["plan_id"])
        self.assertEqual("NEXT_READY",stopped["status"])
        self.assertEqual(1,stopped["next_index"])
        self.assertEqual(1,len(stopped["results"]))
        self.assertEqual([],stopped["handoffs"])
        self.assertEqual([REPO],self.fetches)

    def test_missing_github_adapter_blocks_cached_numeric_handoff(self):
        self.policy.grant(REPO)
        created=self.store.create(plan(triage_steps()))
        self.store.approve(created["plan_id"])
        self.store.advance(created["plan_id"])
        without_source=LocalAgentPlanStore(self.path)
        try:
            with self.assertRaisesRegex(PlanError,
                                        "public_source_permission_changed"):
                without_source.advance(created["plan_id"])
            self.assertEqual("NEXT_READY",
                             without_source.get(created["plan_id"])["status"])
        finally:
            without_source.close()

    def test_model_cannot_invent_forward_references_paths_or_text_handoffs(self):
        invalid = [
            [calc(ref(1,"output"),"2")],  # self
            [calc("1","2"), calc(ref(2,"output"),"3")],  # self
            [calc("1","2"), calc(ref(3,"output"),"3")],  # future
            [calc("1","2"), calc(ref(True,"output"),"3")],
            [calc("1","2"), calc(ref(0,"output"),"3")],
            [calc("1","2"), calc(ref(1,"answer"),"3")],
            [calc("1","2"), calc({"from_step":1,"field":"output",
                                   "path":"../secret"},"3")],
            [{"tool":"utc_now","args":{}},
             calc(ref(1,"output"),"2")],
            [{"tool":"local_document_question","args":{"question":"What?"}},
             calc(ref(1,"answer"),"2")],
            [{"tool":"github_public_issues","args":{"repo":REPO}},
             calc(ref(1,"returned_count"),"2")],
            [{"tool":"github_issue_analysis","args":{"repo":REPO}},
             calc(ref(1,"returned_count"),"2")],
            [calc("1","2"), calc(ref(1,"output"),"0","divide")],
        ]
        for steps in invalid:
            with self.subTest(steps=steps),self.assertRaises(PlanError):
                validate_steps(steps)
        self.assertEqual([], self.store.list())

    def test_planner_must_produce_explicit_backward_only_json(self):
        captured = []
        def synthetic(payload):
            captured.append(payload)
            return {"message": {"content": json.dumps({
                "steps": chained_steps()})}}
        result = propose_readonly_plan(
            "Multiply 7 by 8, then double the previous output",
            "fixture:model", transport=synthetic)
        self.assertEqual(chained_steps(), result["steps"])
        self.assertEqual(1,len(captured))
        self.assertNotIn("tools", captured[0])
        self.assertIn("from_step", captured[0]["messages"][0]["content"])
        self.assertIn("ONE-BASED",captured[0]["messages"][0]["content"])
        self.assertEqual([],self.store.list())  # model suggestion alone isn't a plan execution

    def test_missing_or_changed_result_fails_before_next_step(self):
        created = self.store.create(plan(chained_steps()))
        self.store.approve(created["plan_id"])
        self.store.advance(created["plan_id"])
        with self.store.lock,self.store.db:
            self.store.db.execute(
                "UPDATE agent_task_plans SET results_json=? WHERE plan_id=?",
                (json.dumps([{"tool":"calculate","output":"not a number"}]),
                 created["plan_id"]))
        self.assertEqual("NEXT_READY",self.store.get(created["plan_id"])["status"])
        with self.assertRaisesRegex(PlanError, "plan_handoff_invalid_source"):
            self.store.advance(created["plan_id"])
        after = self.store.get(created["plan_id"])
        self.assertEqual("NEXT_READY",after["status"])
        self.assertEqual(1,after["next_index"])
        self.assertEqual([],after["handoffs"])
        self.assertEqual(1,len(after["results"]))
        self.assertEqual("CANCELLED",self.store.cancel(created["plan_id"])["status"])

    def test_zero_divisor_from_previous_result_blocks_without_execution(self):
        steps=[calc("0","7"),calc("1",ref(1,"output"),"divide")]
        created=self.store.create(plan(steps))
        self.store.approve(created["plan_id"])
        self.store.advance(created["plan_id"])
        with self.assertRaisesRegex(PlanError,"plan_handoff_invalid_source"):
            self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY",self.store.get(created["plan_id"])["status"])
        self.assertEqual(1,len(self.store.get(created["plan_id"])["results"]))

    def test_prior_result_outside_operand_limit_is_rejected(self):
        created=self.store.create(plan([calc("1000000000000","1000000000000"),
                                        calc(ref(1,"output"),"2")]))
        self.store.approve(created["plan_id"])
        first=self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY",first["status"])
        with self.assertRaisesRegex(PlanError,"plan_handoff_invalid_source"):
            self.store.advance(created["plan_id"])
        self.assertEqual(1,self.store.get(created["plan_id"])["next_index"])

    def test_tampered_final_lineage_is_rejected_on_load(self):
        created=self.store.create(plan(chained_steps()))
        self.store.approve(created["plan_id"])
        self.store.advance(created["plan_id"])
        self.store.advance(created["plan_id"])
        with self.store.lock,self.store.db:
            self.store.db.execute(
                "UPDATE agent_task_plans SET handoff_log_json=? WHERE plan_id=?",
                (json.dumps([{"step_index":2,"bindings":[]}]),
                 created["plan_id"]))
        with self.assertRaisesRegex(PlanError,"plan_storage_invalid"):
            self.store.get(created["plan_id"])

    def test_invalid_triage_source_count_provenance_fails_closed(self):
        self.policy.grant(REPO)
        created=self.store.create(plan(triage_steps()))
        self.store.approve(created["plan_id"])
        self.store.advance(created["plan_id"])
        with self.store.lock,self.store.db:
            raw=self.store.db.execute(
                "SELECT results_json FROM agent_task_plans WHERE plan_id=?",
                (created["plan_id"],)).fetchone()[0]
            results=json.loads(raw)
            data=json.loads(results[0]["output"])
            data["review_flagged_count"]=5  # inconsistent with saved issue signals
            results[0]["output"]=json.dumps(data)
            self.store.db.execute(
                "UPDATE agent_task_plans SET results_json=? WHERE plan_id=?",
                (json.dumps(results),created["plan_id"]))
        with self.assertRaisesRegex(PlanError,"plan_handoff_invalid_source"):
            self.store.advance(created["plan_id"])
        self.assertEqual("NEXT_READY",self.store.get(created["plan_id"])["status"])
        self.assertEqual(1,len(self.fetches))

    def test_old_schema_migrates_without_new_handoff_authority(self):
        legacy=self.path.parent/"legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE agent_task_plans (
                plan_id TEXT PRIMARY KEY,model TEXT NOT NULL,goal TEXT NOT NULL,
                steps_json TEXT NOT NULL,results_json TEXT NOT NULL,
                next_index INTEGER NOT NULL,status TEXT NOT NULL,
                created_utc TEXT NOT NULL,approval_epoch INTEGER,
                memory_refs_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT NOT NULL DEFAULT '',
                document_json TEXT NOT NULL DEFAULT '',
                document_set_json TEXT NOT NULL DEFAULT '')""")
            db.execute("""INSERT INTO agent_task_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("0"*24,"fixture:model","Old arithmetic",
                 json.dumps([calc("2","3")]),"[]",0,"PREPARED",
                 "2026-11-05T00:00:00+00:00",None,"[]","","",""))
        migrated=LocalAgentPlanStore(legacy)
        try:
            self.assertEqual([],migrated.get("0"*24)["handoffs"])
            migrated.approve("0"*24)
            self.assertEqual("COMPLETED",migrated.advance("0"*24)["status"])
            self.assertEqual("6",migrated.get("0"*24)["results"][0]["output"])
            with sqlite3.connect(legacy) as db:
                self.assertIn("handoff_log_json",
                    [col[1] for col in db.execute(
                        "PRAGMA table_info(agent_task_plans)")])
        finally:
            migrated.close()


class NumericHandoffHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store=LocalAgentPlanStore(Path(tmp.name)/"plans.sqlite3")
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.proposals=[]
        def planner(goal,model,memory_context=None):
            self.proposals.append(goal)
            return plan(chained_steps())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda p,m:{"mode":"LOCAL_MODEL",
                                                          "answer":"fixture"},
                           local_agent_planner=planner,
                           local_agent_plan_store=self.store))
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,method,path,body=None,auth=True,csrf=True,origin=None):
        connection=http.client.HTTPConnection("127.0.0.1",
                         self.server.server_port,timeout=8)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        connection.request(method,path,
                    json.dumps(body).encode() if body is not None else None,
                    headers=headers)
        response=connection.getresponse()
        result=(response.status,json.loads(response.read()))
        connection.close()
        return result

    def test_owner_auth_and_manual_handoffs_return_saved_lineage(self):
        endpoint="/api/local-agent-plan-create"
        payload={"goal":"Chain two calculations","model":"fixture:model"}
        self.assertEqual(401,self.request("POST",endpoint,payload,auth=False)[0])
        self.assertEqual(403,self.request("POST",endpoint,payload,csrf=False)[0])
        self.assertEqual(403,self.request("POST",endpoint,payload,
                         origin="http://attacker.invalid")[0])
        self.assertEqual([],self.proposals)
        code,created=self.request("POST",endpoint,payload)
        self.assertEqual(200,code)
        pid=created["plan_id"]
        self.assertEqual([],created["results"])
        self.assertEqual([],created["handoffs"])
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                                         {"plan_id":pid})[0])
        self.assertEqual(200,self.request(
            "POST","/api/local-agent-plan-approve",{"plan_id":pid})[0])
        self.assertEqual([],self.store.get(pid)["results"])
        first=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,first[0])
        self.assertEqual("NEXT_READY",first[1]["status"])
        self.assertEqual([],first[1]["handoffs"])
        second=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,second[0])
        self.assertEqual("112",second[1]["results"][1]["output"])
        self.assertEqual(1,len(second[1]["handoffs"]))
        code,reloaded=self.request("GET","/api/local-agent-plan?plan_id="+pid)
        self.assertEqual(200,code)
        self.assertEqual(second[1]["handoffs"],reloaded["handoffs"])
        code,capabilities=self.request("GET","/api/local-agent-capabilities")
        self.assertEqual(200,code)
        self.assertTrue(capabilities["plan_handoffs"])


if __name__=="__main__":
    unittest.main()
