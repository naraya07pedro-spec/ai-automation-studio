"""Stage 113: cloud-generated plan, real owner HTTP, and local read-only tools.
Provider model is always a fixture. There are no external API calls in CI.
"""
import http.cookiejar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import local_server
from local_agent_task_plans import LocalAgentPlanStore
from local_cloud_plan_bridge import CloudPlanBridge, CloudPlanError, paid_planning_prompt
from local_model_safety import LocalModelSafetyState
from local_on_demand_remote_ai import LocalOnDemandRemoteAI, OnDemandRemoteAIError
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore

PASSWORD = "Cloud-Plan-Owner-Password-2026!"
GOAL = "Add 17 and 25, then show the current UTC time."
MODEL = "test-model"
RID = "a"*32
PLAN = {"steps": [
    {"tool":"calculate","args":{"a":"17","b":"25","operation":"add"}},
    {"tool":"utc_now","args":{}},
]}


def valid_reply(answer=None):
    return {"mode":"REMOTE_OPENAI_READ_ONLY",
            "answer":json.dumps(PLAN) if answer is None else answer,
            "tools_used":[],"external_dispatch_count":0,
            "usage":{"input_tokens":109,"output_tokens":37}}


def request_data(rid=RID, **changes):
    return {"request_id":rid,"model":MODEL,"goal":GOAL,
            "paid_api_consent":True, **changes}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.safety=LocalModelSafetyState(base/"safety.sqlite3")
        self.gate=threading.BoundedSemaphore(1)
        self.calls=[]
        def runner(prompt,model):
            self.calls.append((prompt,model))
            return valid_reply()
        self.remote=LocalOnDemandRemoteAI(base/"remote.sqlite3",runner=runner)
        self.remote.attach_gate(self.safety,self.gate,threading.Lock())
        self.plans=LocalAgentPlanStore(base/"plans.sqlite3")
        self.bridge=CloudPlanBridge(self.remote,self.plans)
    def tearDown(self):
        self.remote.close();self.plans.close();self.safety.close();self.temp.cleanup()

    def test_paid_proposal_then_owner_approved_real_tools(self):
        response=self.bridge.propose(request_data())
        self.assertEqual(response["provider_calls_this_action"],1)
        self.assertEqual(response["plan"]["status"],"PREPARED")
        self.assertEqual(response["plan"]["plan_id"],RID[:24])
        self.assertEqual(self.plans.list()[0]["plan_id"],RID[:24])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(self.plans.approve(RID[:24])["status"],"APPROVED")
        first=self.plans.advance(RID[:24])
        self.assertEqual(first["results"][0]["tool"],"calculate")
        self.assertIn("42",first["results"][0]["output"])
        final=self.plans.advance(RID[:24])
        self.assertEqual(final["status"],"COMPLETED")
        self.assertEqual(final["results"][1]["tool"],"utc_now")
        same=self.bridge.recover({"request_id":RID,"model":MODEL,"goal":GOAL})
        self.assertEqual(same["plan"]["status"],"COMPLETED")
        self.assertEqual(same["provider_calls_this_action"],0)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.plans.list()),1)

    def test_receipt_recovery_after_network_response_lost_before_plan_write(self):
        receipt=self.remote.run({"request_id":RID,"model":MODEL,
                  "prompt":paid_planning_prompt(GOAL),"paid_api_consent":True})
        self.assertEqual(receipt["status"],"COMPLETED")
        self.assertEqual(len(self.plans.list()),0)
        recovered=self.bridge.recover({"request_id":RID,"model":MODEL,"goal":GOAL})
        self.assertEqual(recovered["plan"]["status"],"PREPARED")
        self.assertEqual(recovered["provider_calls_this_action"],0)
        self.assertEqual(self.bridge.recover(
            {"request_id":RID,"model":MODEL,"goal":GOAL}
        )["plan"]["plan_id"],RID[:24])
        self.assertEqual(len(self.plans.list()),1)
        self.assertEqual(len(self.calls),1)

    def test_invalid_consent_and_id_fail_before_model(self):
        for item in (request_data(paid_api_consent=False),
                     request_data(paid_api_consent="true"),
                     request_data(rid="bad"),request_data(goal="")):
            with self.subTest(item=item),self.assertRaises(CloudPlanError):
                self.bridge.propose(item)
        self.assertEqual(len(self.calls),0)
        self.assertEqual(self.plans.list(),[])

    def test_model_cannot_grant_unreviewed_external_tools(self):
        for obj in ({"steps":[]},
                    {"steps":[{"tool":"email_send","args":{}}]},
                    {"steps":[{"tool":"github_public_issues",
                               "args":{"repo":"any/repo"}}]},
                    {"steps":[{"tool":"utc_now","args":{"shell":"cmd"}}]}):
            with self.subTest(obj=obj),self.assertRaises(CloudPlanError):
                from local_cloud_plan_bridge import _parse_checked_plan
                _parse_checked_plan(json.dumps(obj))
        self.remote.runner=lambda *_:valid_reply(json.dumps({
            "steps":[{"tool":"email_send","args":{}}]}))
        with self.assertRaises(CloudPlanError):
            self.bridge.propose(request_data())
        self.assertEqual(len(self.plans.list()),0)
        self.assertEqual(self.remote.receipt(RID)["status"],"COMPLETED")

    def test_recovery_binding_blocks_different_goal_or_model(self):
        self.bridge.propose(request_data())
        for item in ({"request_id":RID,"model":MODEL,"goal":"Different goal"},
                     {"request_id":RID,"model":"other-model","goal":GOAL}):
            with self.subTest(item=item),self.assertRaisesRegex(
                    OnDemandRemoteAIError,"binding_mismatch"):
                self.bridge.recover(item)
        self.assertEqual(len(self.calls),1)

    def test_paid_request_id_not_replayed(self):
        self.bridge.propose(request_data())
        with self.assertRaisesRegex(OnDemandRemoteAIError,"already_used"):
            self.bridge.propose(request_data())
        self.assertEqual(len(self.calls),1)


class HTTPFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        LocalOperatorAuth.provision(self.base/"operator.json",PASSWORD)
        self.owner=LocalOperatorAuth.load(self.base/"operator.json")
        self.safety=LocalModelSafetyState(self.base/"safety.sqlite3")
        self.audit=LocalSecurityAudit(self.base/"audit.sqlite3")
        self.core=LocalWorkflowCore(self.base/"core.sqlite3")
        self.plans=LocalAgentPlanStore(self.base/"plans.sqlite3")
        self.calls=[]
        def runner(prompt,model):
            self.calls.append((prompt,model));return valid_reply()
        self.remote=LocalOnDemandRemoteAI(self.base/"remote.sqlite3",runner=runner)
        self.start_server(with_store=True)
        self.opener=build_opener(ProxyHandler({}),
                    HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=None
    def start_server(self,with_store):
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
             local_server.create_handler(local_core=self.core,
                 operator_auth=self.owner,security_audit=self.audit,
                 model_safety_state=self.safety,on_demand_ai=self.remote,
                 local_agent_plan_store=self.plans if with_store else None))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.remote.close();self.plans.close();self.core.close()
        self.audit.close();self.safety.close();self.temp.cleanup()
    def get(self,route):
        with self.opener.open(self.url+route,timeout=7) as res:return json.load(res)
    def post(self,route,payload,csrf=True):
        h={"Content-Type":"application/json"}
        if csrf and self.csrf:h["X-AAS-CSRF"]=self.csrf
        with self.opener.open(Request(self.url+route,json.dumps(payload).encode(),
                method="POST",headers=h),timeout=7) as res:return json.load(res)
    def login(self):
        self.csrf=self.post("/api/operator-login",{"password":PASSWORD})["csrf_token"]

    def test_authenticated_request_approval_execution_and_no_cost_recovery(self):
        with self.assertRaises(HTTPError) as denied:
            self.get("/api/local-agent-plans")
        self.assertEqual(denied.exception.code,401)
        self.login()
        cap=self.get("/api/local-agent-capabilities")
        self.assertTrue(cap["cloud_plan_review"])
        self.assertFalse(cap["planning"])
        body={**request_data(),"current_password":PASSWORD}
        with self.assertRaises(HTTPError) as csrf:
            self.post("/api/cloud-ai-plan-create",body,csrf=False)
        self.assertEqual(csrf.exception.code,403)
        with self.assertRaises(HTTPError) as bad:
            self.post("/api/cloud-ai-plan-create",{**body,"current_password":"bad"})
        self.assertEqual(bad.exception.code,401)
        with self.assertRaises(HTTPError) as no_consent:
            self.post("/api/cloud-ai-plan-create",{**body,"paid_api_consent":False})
        self.assertEqual(no_consent.exception.code,403)
        self.assertEqual(len(self.calls),0)
        made=self.post("/api/cloud-ai-plan-create",body)
        self.assertEqual(made["plan"]["status"],"PREPARED")
        self.assertEqual(len(self.calls),1)
        pid=made["plan"]["plan_id"]
        self.assertEqual(self.get("/api/local-agent-plan?plan_id="+pid)["status"],
                         "PREPARED")
        with self.assertRaises(HTTPError) as disabled:
            self.post("/api/local-agent-plan-create",{"goal":GOAL,"model":MODEL})
        self.assertEqual(disabled.exception.code,403)
        self.assertEqual(self.post("/api/local-agent-plan-approve",{
            "plan_id":pid})["status"],"APPROVED")
        self.post("/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(self.post("/api/local-agent-plan-next",{
            "plan_id":pid})["status"],"COMPLETED")
        self.assertEqual(len(self.calls),1)
        recovered=self.post("/api/cloud-ai-plan-recover",{
            "request_id":RID,"model":MODEL,"goal":GOAL,
            "current_password":PASSWORD})
        self.assertEqual(recovered["plan"]["status"],"COMPLETED")
        self.assertEqual(recovered["provider_calls_this_action"],0)
        self.assertEqual(len(self.calls),1)
        self.assertTrue(any(e["action"]=="CLOUD_AI_PLAN_PROPOSAL"
                            for e in self.audit.recent(40)))

    def test_disabled_cloud_bridge_rejects_proposal(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.start_server(with_store=False)
        self.login()
        self.assertFalse(self.get("/api/local-agent-capabilities")["cloud_plan_review"])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/cloud-ai-plan-create",{
                **request_data(),"current_password":PASSWORD})
        self.assertEqual(denied.exception.code,403)
        self.assertEqual(self.calls,[])


if __name__=="__main__":
    unittest.main()
