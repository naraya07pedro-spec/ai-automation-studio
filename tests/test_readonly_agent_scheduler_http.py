"""Authenticated REAL localhost HTTP controls for opt-in recurring Ollama tasks."""
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
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_model_safety import LocalModelSafetyState
from local_readonly_agent_scheduler import LocalReadOnlyAgentSchedules
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore

PASSWORD="DisposableReadonlySchedulerOwner2026!"
class Clock:
    def __init__(self):
        self.now=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self,**v):
        self.now+=timedelta(**v)


class ReadOnlyAIHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.clock=Clock()
        LocalOperatorAuth.provision(self.base/"operator.json",PASSWORD)
        self.operator=LocalOperatorAuth.load(self.base/"operator.json")
        self.core=LocalWorkflowCore(self.base/"core.sqlite3")
        self.audit=LocalSecurityAudit(self.base/"audit.sqlite3")
        self.safety=LocalModelSafetyState(self.base/"safety.sqlite3")
        self.called=[]
        def runner(prompt,model):
            self.called.append((prompt,model))
            return {"answer":"Fixture-only response","mode":"LOCAL_MODEL",
                    "tools_used":["calculate"],"external_dispatch_count":0}
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.base/"agent-schedules.sqlite3",
            runner=runner,clock=self.clock)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(local_core=self.core,operator_auth=self.operator,
                           security_audit=self.audit,model_safety_state=self.safety,
                           scheduled_ai=self.scheduled))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.baseurl="http://127.0.0.1:"+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}),
                 HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.scheduled.close()
        self.core.close()
        self.safety.close()
        self.audit.close()
        self.tmp.cleanup()

    def get(self,path):
        with self.opener.open(self.baseurl+path,timeout=5) as reply:
            return json.load(reply)

    def post(self,path,data,use_csrf=True):
        headers={"Content-Type":"application/json"}
        if use_csrf and self.csrf:
            headers["X-AAS-CSRF"]=self.csrf
        req=Request(self.baseurl+path,json.dumps(data).encode(),
                    headers=headers,method="POST")
        with self.opener.open(req,timeout=5) as reply:
            return json.load(reply)

    def login(self):
        self.csrf=self.post("/api/operator-login",{"password":PASSWORD})["csrf_token"]

    def body(self):
        return {"prompt":"What time is it in UTC?","model":"fixture:1",
            "cadence":{"cadence":"minutes","timezone":"Asia/Jakarta","every":1},
            "max_runs":2,"expires_days":7,"current_password":PASSWORD}

    def test_protected_get_and_csrf_and_password_for_creation(self):
        with self.assertRaises(HTTPError) as unauth:
            self.get("/api/read-only-ai-schedules")
        self.assertEqual(unauth.exception.code,401)
        self.login()
        self.assertTrue(self.get("/api/read-only-ai-schedules")["enabled"])
        with self.assertRaises(HTTPError) as csrf:
            self.post("/api/read-only-ai-schedule-create",self.body(),use_csrf=False)
        self.assertEqual(csrf.exception.code,403)
        wrong=self.body()
        wrong["current_password"]="wrong"
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/read-only-ai-schedule-create",wrong)
        self.assertEqual(denied.exception.code,401)
        task=self.post("/api/read-only-ai-schedule-create",self.body())
        self.assertEqual(task["max_runs"],2)
        self.assertEqual(self.called,[],"Creating never starts inference")
        self.assertEqual(self.get("/api/read-only-ai-schedules")["tasks"][0]["task_id"],
                         task["task_id"])
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),1)
        hist=self.get("/api/read-only-ai-history?task_id="+task["task_id"])
        self.assertEqual(hist["runs"][0]["answer"],"Fixture-only response")
        self.assertEqual(hist["runs"][0]["external_dispatch_count"],0)
        events=self.audit.recent()
        self.assertTrue(any(x["action"]=="SCHEDULED_AI_OWNER_CHANGE" and
                            x["outcome"]=="SUCCESS" for x in events))
        self.assertNotIn(PASSWORD,json.dumps(events))

    def test_pause_resume_require_current_password_and_epoch(self):
        self.login()
        task=self.post("/api/read-only-ai-schedule-create",self.body())
        params={"task_id":task["task_id"],"expected_epoch":0,
                "current_password":PASSWORD}
        paused=self.post("/api/read-only-ai-schedule-pause",params)
        self.assertEqual(paused["status"],"PAUSED")
        with self.assertRaises(HTTPError) as conflict:
            self.post("/api/read-only-ai-schedule-resume",params)
        self.assertEqual(conflict.exception.code,409)
        params["expected_epoch"]=1
        resumed=self.post("/api/read-only-ai-schedule-resume",params)
        self.assertEqual(resumed["status"],"ACTIVE")

    def test_invalid_submission_does_not_create_local_task(self):
        self.login()
        body=self.body()
        body["model"]="../../danger"
        with self.assertRaises(HTTPError) as invalid:
            self.post("/api/read-only-ai-schedule-create",body)
        self.assertEqual(invalid.exception.code,422)
        self.assertEqual(self.get("/api/read-only-ai-schedules")["tasks"],[])
