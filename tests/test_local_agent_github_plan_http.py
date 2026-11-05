"""Slice 53 HTTP: GitHub reads require owner, CSRF, repo grant and approved step."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_agent_task_plans import LocalAgentPlanStore
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_agent_issue_analysis import analyze_public_issue_metadata
from local_operator_auth import LocalOperatorAuth
from local_source_policy import LocalPublicSourcePolicy
from local_server import create_handler

PASSWORD="fixture_only_external_read_password_53"
REPO="Octocat/Hello-World"


class GitHubPlanHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy=LocalPublicSourcePolicy()
        self.provider_calls=[]
        def fetch(repo):
            self.provider_calls.append(repo)
            return [{"state":"open","number":7,"title":"Bug: sample",
                     "labels":[{"name":"triage"}],
                     "body":"excluded from response"}]
        reader=AllowedGitHubIssuesReader(self.policy,fetcher=fetch)
        self.model_calls=[]
        def analyze(reader,repo,epoch,model,goal):
            def fake(payload):
                self.model_calls.append(payload)
                return {"message":{"content":json.dumps({"items":[{
                    "number":7,"observation":"Could the reported bug need reproduction? No impact evidence."}]})}}
            return analyze_public_issue_metadata(reader,repo,epoch,model,goal,
                                                 transport=fake)
        self.store=LocalAgentPlanStore(
            Path(self.temp.name)/"plans.sqlite3",
            github_reader=reader,issue_analyzer=analyze)
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.plan_calls=[]
        def planner(goal,model):
            self.plan_calls.append((goal,model))
            selected=("github_issue_analysis" if "analy" in goal.lower()
                      else "github_issue_triage" if "triage" in goal.lower()
                      else "github_public_issues")
            return {"goal":goal,"model":model,"steps":[
                {"tool":selected,"args":{"repo":REPO}}]}
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda prompt,model: {
                             "mode":"LOCAL_MODEL","answer":"fixture",
                             "tools_used":[],"external_dispatch_count":0},
                           source_policy=self.policy,
                           local_agent_planner=planner,
                           local_agent_plan_store=self.store))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,s=self.auth.login(PASSWORD)
        self.cookie="aas_session="+s["cookie_token"]
        self.csrf=s["csrf_token"]

    def request(self,method,path,body=None,auth=True,csrf=True,origin=None):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        c.request(method,path,body=json.dumps(body).encode() if body is not None else None,
                  headers=headers)
        r=c.getresponse()
        result=(r.status,json.loads(r.read()))
        c.close()
        return result

    def create(self, goal="Read public GitHub issues"):
        return self.request("POST","/api/local-agent-plan-create",
                            {"goal":goal,"model":"fixture:small"})

    def test_default_deny_and_explicit_grant_before_model_or_fetch(self):
        code,response=self.create()
        self.assertEqual(403,code)
        self.assertEqual("public_source_not_allowed",response["error"])
        self.assertEqual([],self.provider_calls)
        self.policy.grant(REPO)
        code,plan=self.create()
        self.assertEqual(200,code)
        self.assertEqual("PREPARED",plan["status"])
        self.assertEqual([],self.provider_calls)
        self.assertTrue(self.request("GET","/api/local-agent-capabilities")[1][
                        "github_plan_read"])

    def test_approve_then_one_external_get_and_source_provenance(self):
        self.policy.grant(REPO)
        code,plan=self.create()
        self.assertEqual(200,code)
        pid=plan["plan_id"]
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                           {"plan_id":pid})[0])
        self.assertEqual([],self.provider_calls)
        code,approved=self.request("POST","/api/local-agent-plan-approve",
                                   {"plan_id":pid})
        self.assertEqual("APPROVED",approved["status"])
        self.assertEqual([],self.provider_calls)
        code,result=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,code)
        self.assertEqual("COMPLETED",result["status"])
        data=json.loads(result["results"][0]["output"])
        self.assertEqual("GITHUB_PUBLIC_ISSUES_READ_ONLY",data["source"])
        self.assertEqual(7,data["issues"][0]["number"])
        self.assertEqual(1,data["external_read_count"])
        self.assertEqual(0,data["external_write_count"])
        self.assertEqual([REPO],self.provider_calls)
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                             {"plan_id":pid})[0])
        self.assertEqual([REPO],self.provider_calls)

    def test_approved_triage_returns_grounded_report_once(self):
        self.policy.grant(REPO)
        code,plan=self.create("Triage open issues using labels")
        self.assertEqual(200,code)
        self.assertEqual("github_issue_triage",plan["steps"][0]["tool"])
        self.assertEqual([],self.provider_calls)
        pid=plan["plan_id"]
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                                          {"plan_id":pid})[0])
        self.assertEqual(200,self.request("POST","/api/local-agent-plan-approve",
                                          {"plan_id":pid})[0])
        self.assertEqual([],self.provider_calls)
        code,done=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,code)
        report=json.loads(done["results"][0]["output"])
        self.assertEqual("GITHUB_PUBLIC_ISSUE_TRIAGE_READ_ONLY",report["source"])
        self.assertEqual("DETERMINISTIC_LABEL_RULES_V1",report["method"])
        self.assertEqual(1,report["returned_count"])
        self.assertEqual("UNASSESSED",report["issues"][0]["priority_signal"])
        self.assertEqual(0,report["external_write_count"])
        self.assertEqual([REPO],self.provider_calls)

    def test_approved_analysis_requires_auth_and_click_and_cites_source(self):
        self.policy.grant(REPO)
        code,plan=self.create("Analyze public issues with the local model")
        self.assertEqual(200,code)
        self.assertEqual("github_issue_analysis",plan["steps"][0]["tool"])
        pid=plan["plan_id"]
        self.assertEqual([],self.provider_calls)
        self.assertEqual([],self.model_calls)
        self.assertEqual(401,self.request("POST","/api/local-agent-plan-next",
                             {"plan_id":pid},auth=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-plan-next",
                             {"plan_id":pid},csrf=False)[0])
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                             {"plan_id":pid})[0])
        self.assertEqual([],self.provider_calls)
        self.assertEqual(200,self.request("POST","/api/local-agent-plan-approve",
                             {"plan_id":pid})[0])
        self.assertEqual([],self.provider_calls)
        code,done=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,code)
        self.assertEqual("COMPLETED",done["status"])
        report=json.loads(done["results"][0]["output"])
        self.assertEqual("GITHUB_PUBLIC_ISSUE_ANALYSIS_READ_ONLY",report["source"])
        self.assertEqual(7,report["issues"][0]["number"])
        self.assertEqual("https://github.com/Octocat/Hello-World/issues/7",
                         report["issues"][0]["url"])
        self.assertEqual("UNVERIFIED_MODEL_INTERPRETATION",
                         report["issues"][0]["assessment"])
        self.assertEqual([REPO],self.provider_calls)
        self.assertEqual(1,len(self.model_calls))
        self.assertNotIn("excluded from response",json.dumps(self.model_calls))

    def test_policy_revoke_blocks_without_provider_read(self):
        self.policy.grant(REPO)
        plan=self.create()[1]
        self.request("POST","/api/local-agent-plan-approve",{
            "plan_id":plan["plan_id"]})
        self.policy.revoke(REPO)
        code,result=self.request("POST","/api/local-agent-plan-next",{
            "plan_id":plan["plan_id"]})
        self.assertEqual(409,code)
        self.assertEqual("public_source_permission_changed",result["error"])
        self.assertEqual([],self.provider_calls)

    def test_no_auth_or_csrf_means_no_external_read(self):
        self.policy.grant(REPO)
        plan=self.create()[1]
        self.request("POST","/api/local-agent-plan-approve",{
            "plan_id":plan["plan_id"]})
        path="/api/local-agent-plan-next"
        body={"plan_id":plan["plan_id"]}
        self.assertEqual(401,self.request("POST",path,body,auth=False)[0])
        self.assertEqual(403,self.request("POST",path,body,csrf=False)[0])
        self.assertEqual(403,self.request("POST",path,body,
                                        origin="http://attacker.example")[0])
        self.assertEqual([],self.provider_calls)


if __name__=="__main__":
    unittest.main()
