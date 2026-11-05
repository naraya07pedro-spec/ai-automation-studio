"""Slice 52 authenticated HTTP tests: plans never execute without operator action."""
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
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler, main as server_main

PASSWORD="fixture_only_task_plan_password_52"


class TaskPlanHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=LocalAgentPlanStore(Path(self.temp.name)/"plans.sqlite3")
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.planning_calls=[]
        self.chat_calls=[]
        self.entered=threading.Event()
        self.release=threading.Event()
        self.pause_planner=False
        def planner(goal,model):
            self.planning_calls.append((goal,model))
            self.entered.set()
            if self.pause_planner:self.release.wait(3)
            return {"goal":goal,"model":model,"steps":[
                {"tool":"calculate","args":{
                    "a":"7","b":"8","operation":"multiply"}},
                {"tool":"utc_now","args":{}}]}
        def runner(prompt,model):
            self.chat_calls.append((prompt,model))
            return {"mode":"LOCAL_MODEL","answer":"test","tools_used":[],
                    "external_dispatch_count":0}
        self.planner=planner
        self.runner=runner
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=runner,
                           local_agent_planner=planner,
                           local_agent_plan_store=self.store))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.release.set)
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,method,path,data=None,auth=True,csrf=True,origin=None):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        c.request(method,path,body=json.dumps(data).encode() if data is not None else None,
                  headers=headers)
        r=c.getresponse()
        response=(r.status,json.loads(r.read()))
        c.close()
        return response

    def create(self):
        code,plan=self.request("POST","/api/local-agent-plan-create",
                               {"goal":"multiply then UTC","model":"fixture:small"})
        self.assertEqual(200,code)
        return plan

    def test_create_requires_owner_and_csrf(self):
        payload={"goal":"multiply","model":"fixture:small"}
        self.assertEqual(401,self.request("POST","/api/local-agent-plan-create",
                                          payload,auth=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-plan-create",
                                          payload,csrf=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-plan-create",payload,
                                          origin="http://attacker.invalid")[0])
        self.assertEqual([],self.planning_calls)
        self.assertEqual(401,self.request("GET","/api/local-agent-plans",auth=False)[0])
        self.assertEqual(200,self.request("GET","/api/local-agent-plans")[0])
        self.assertEqual(True,self.request("GET","/api/local-agent-capabilities")[1]["planning"])

    def test_approval_never_dispatches_until_explicit_next_click(self):
        plan=self.create()
        pid=plan["plan_id"]
        self.assertEqual("PREPARED",plan["status"])
        self.assertEqual([],plan["results"])
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                                          {"plan_id":pid})[0])
        code,approved=self.request("POST","/api/local-agent-plan-approve",{"plan_id":pid})
        self.assertEqual(200,code)
        self.assertEqual("APPROVED",approved["status"])
        self.assertEqual([],approved["results"])
        code,first=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,code)
        self.assertEqual("NEXT_READY",first["status"])
        self.assertEqual("56",first["results"][0]["output"])
        self.assertEqual(1,len(first["results"]))
        code,second=self.request("POST","/api/local-agent-plan-next",{"plan_id":pid})
        self.assertEqual(200,code)
        self.assertEqual("COMPLETED",second["status"])
        self.assertEqual(2,len(second["results"]))
        self.assertEqual(409,self.request("POST","/api/local-agent-plan-next",
                                          {"plan_id":pid})[0])
        self.assertEqual([],self.chat_calls)

    def test_auth_enforced_on_approve_cancel_delete_and_read(self):
        plan=self.create()
        pid=plan["plan_id"]
        for action in ("approve","next","cancel","resume","delete"):
            with self.subTest(action=action):
                uri="/api/local-agent-plan-"+action
                self.assertEqual(401,self.request("POST",uri,
                             {"plan_id":pid},auth=False)[0])
                self.assertEqual(403,self.request("POST",uri,
                             {"plan_id":pid},csrf=False)[0])
        self.assertEqual(401,self.request("GET","/api/local-agent-plan?plan_id="+pid,
                                          auth=False)[0])
        self.assertEqual("PREPARED",self.store.get(pid)["status"])
        self.assertEqual(200,self.request("GET","/api/local-agent-plan?plan_id="+pid)[0])
        self.assertEqual(200,self.request("POST","/api/local-agent-plan-cancel",
                                         {"plan_id":pid})[0])
        self.assertEqual(200,self.request("POST","/api/local-agent-plan-delete",
                                         {"plan_id":pid})[0])
        self.assertEqual([],self.store.list())

    def test_bad_payloads_or_unauthorized_tool_never_execute(self):
        bad=[{},{"goal":"test"},{"goal":"test","model":False},
             {"goal":"test","model":"m","other":1},{"goal":"x"*1201,"model":"m"}]
        for data in bad:
            with self.subTest(data=str(data)[:20]):
                code,_=self.request("POST","/api/local-agent-plan-create",data)
                self.assertEqual(422 if data!={"goal":"x"*1201,"model":"m"} else 422,code)
        self.assertEqual([],self.chat_calls)
        for data in ({},{"plan_id":42},{"plan_id":"bad"},
                     {"plan_id":"bad","another":1}):
            code,_=self.request("POST","/api/local-agent-plan-next",data)
            self.assertIn(code,(422,404))
        self.assertEqual([],self.store.list())

    def test_single_in_flight_gate_blocks_parallel_model_chat(self):
        self.pause_planner=True
        result=[]
        t=threading.Thread(target=lambda:result.append(self.request(
            "POST","/api/local-agent-plan-create",
            {"goal":"tool plan","model":"fixture:small"})))
        t.start()
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(429,self.request("POST","/api/local-agent-plan-create",
                     {"goal":"second","model":"fixture:small"})[0])
        self.assertEqual(429,self.request("POST","/api/local-agent-run",
                     {"model":"fixture:small","prompt":"hi"})[0])
        self.assertEqual([],self.chat_calls)
        self.release.set();t.join(5)
        self.assertEqual(200,result[0][0])

    def test_disabled_flag_blocks_plan_ops_even_with_login(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
             create_handler(operator_auth=self.auth,local_agent_runner=self.runner))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            c.request("GET","/api/local-agent-plans",headers={"Cookie":self.cookie})
            response=c.getresponse()
            self.assertEqual(403,response.status)
            response.read();c.close()
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            c.request("POST","/api/local-agent-plan-create",
                      body=json.dumps({"goal":"x","model":"fixture:small"}),
                      headers={"Cookie":self.cookie,"X-AAS-CSRF":self.csrf,
                               "Content-Type":"application/json"})
            response=c.getresponse()
            self.assertEqual(403,response.status)
            response.read();c.close()
        finally:
            server.shutdown();server.server_close();worker.join(4)
        self.assertEqual([],self.planning_calls)

    def test_cli_requires_explicit_agent_enablement(self):
        with self.assertRaises(SystemExit) as ex:
            server_main(["--enable-agent-plans"])
        self.assertNotEqual(0,ex.exception.code)


if __name__=="__main__":
    unittest.main()
