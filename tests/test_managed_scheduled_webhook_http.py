"""Disposable real HTTP owner session + CSRF + arm + revoke acceptance.

No network requests leave the test runner: HTTPS transport is injected.
"""
import http.cookiejar
import importlib.util
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_managed_scheduled_webhook import ManagedScheduledWebhookAutopilot
from local_recurring_scheduler import LocalRecurringScheduler
from local_workflow_core import LocalWorkflowCore
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None
PASSWORD="DisposableManagedAutopilotOwner2026!"


@unittest.skipUnless(HAS_CRYPTO,"Dedicated managed-autopilot CI installs cryptography")
class ManagedAutopilotHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        LocalOperatorAuth.provision(root/"operator.json",PASSWORD)
        self.owner=LocalOperatorAuth.load(root/"operator.json")
        self.audit=LocalSecurityAudit(root/"audit.sqlite3")
        self.core=LocalWorkflowCore(root/"core.sqlite3")
        self.core.create({"workflow_id":"http_flow","display_name":"HTTP Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})
        self.core.activate({"workflow_id":"http_flow","expected_version":1})
        self.scheduler=LocalRecurringScheduler(self.core,root/"schedule.sqlite3")
        self.schedule=self.scheduler.create({
            "workflow_id":"http_flow","cadence":"minutes","timezone":"Asia/Jakarta",
            "service_code":"automation","budget_tier":2,"timeline":"soon","every":1})
        self.sent=[]
        def sender(*args):
            self.sent.append(args)
            return 200
        self.manager=LocalConnectorManager(self.core,root/"managed-vault.json",
                sender=sender,vault_key=generate_master_key())
        self.manager.add({"connector_id":"my_n8n","label":"Fixture n8n",
             "url":"https://example.org/webhook/fixture-private",
             "signing_secret":"fixture-hmac"})
        self.managed=ManagedScheduledWebhookAutopilot(
            self.scheduler,self.manager,self.audit,root/"policies")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(local_core=self.core,operator_auth=self.owner,
                security_audit=self.audit,local_scheduler=self.scheduler,
                managed_connectors=self.manager,managed_autopilot=self.managed))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}),
               HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.managed.close()
        self.manager.close()
        self.scheduler.close()
        self.core.close()
        self.audit.close()
        self.tmp.cleanup()

    def get(self,route):
        with self.opener.open(self.base+route,timeout=4) as reply:
            return json.load(reply)

    def post(self,route,data,with_csrf=True):
        headers={"Content-Type":"application/json"}
        if with_csrf and self.csrf:
            headers["X-AAS-CSRF"]=self.csrf
        req=Request(self.base+route,json.dumps(data).encode("utf-8"),
                    headers=headers,method="POST")
        with self.opener.open(req,timeout=4) as reply:
            return json.load(reply)

    def login(self):
        self.csrf=self.post("/api/operator-login",{"password":PASSWORD})["csrf_token"]

    def test_login_csrf_owner_password_and_real_dispatch_callback(self):
        with self.assertRaises(HTTPError) as unauth:
            self.get("/api/managed-autopilot-status")
        self.assertEqual(unauth.exception.code,401)
        self.login()
        self.assertTrue(self.get("/api/managed-autopilot-status")["enabled"])
        self.assertTrue(self.get("/api/managed-connectors")["scheduled_dispatch_enabled"])
        request={"connector_id":"my_n8n",
                 "schedule_id":self.schedule["schedule_id"],
                 "max_sends":1,"expires_days":7}
        with self.assertRaises(HTTPError) as no_csrf:
            self.post("/api/managed-autopilot-preview",request,with_csrf=False)
        self.assertEqual(no_csrf.exception.code,403)
        p=self.post("/api/managed-autopilot-preview",request)
        self.assertEqual(p["external_write_count"],0)
        self.assertEqual(self.sent,[])
        arm={**request,"preview_token":p["preview_token"],
             "current_password":"incorrect"}
        with self.assertRaises(HTTPError) as wrong:
            self.post("/api/managed-autopilot-arm",arm)
        self.assertEqual(wrong.exception.code,401)
        arm["current_password"]=PASSWORD
        created=self.post("/api/managed-autopilot-arm",arm)
        self.assertEqual(created["status"],"ACTIVE")
        self.assertEqual(self.sent,[])
        self.scheduler.run_due() # due time is in the future: no send
        self.assertEqual(self.managed.dispatch_due(),0)
        # Inject exact persisted scheduled receipt for a due tick without real clock waits.
        with self.scheduler.lock,self.scheduler.db:
            self.scheduler.db.execute(
                "UPDATE recurring_schedules SET next_due_utc=? WHERE schedule_id=?",
                ("2026-01-01T00:00:00+00:00",self.schedule["schedule_id"]))
        self.scheduler.run_due()
        self.assertEqual(self.managed.dispatch_due(),0,
                         "Before-grant due slot must never be sent retroactively")
        events=self.audit.recent()
        self.assertTrue(any(x["action"]=="MANAGED_AUTOPILOT_POLICY" and
                            x["outcome"]=="SUCCESS" for x in events))
        self.assertNotIn("fixture-hmac",json.dumps(events))

    def test_owner_can_emergency_stop_via_api(self):
        self.login()
        request={"connector_id":"my_n8n","schedule_id":self.schedule["schedule_id"],
                 "max_sends":2,"expires_days":7}
        p=self.post("/api/managed-autopilot-preview",request)
        armed=self.post("/api/managed-autopilot-arm",{
            **request,"preview_token":p["preview_token"],
            "current_password":PASSWORD})
        stopped=self.post("/api/managed-autopilot-stop",{
            "connector_id":"my_n8n","schedule_id":self.schedule["schedule_id"],
            "expected_epoch":armed["control_epoch"]})
        self.assertEqual(stopped["status"],"STOPPED")
        self.assertEqual(self.sent,[])
        self.assertTrue(self.get("/api/managed-autopilot-status")["policies"])

    def test_encrypted_connector_not_equal_to_automatic_grant(self):
        self.login()
        d=self.get("/api/managed-connectors")
        self.assertTrue(d["scheduled_dispatch_enabled"])
        self.assertEqual(self.get("/api/managed-autopilot-status")["policies"],[])
        self.assertEqual(self.managed.dispatch_due(),0)
        self.assertEqual(self.sent,[])
