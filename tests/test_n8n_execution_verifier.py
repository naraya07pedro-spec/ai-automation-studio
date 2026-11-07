"""Stage 117: official n8n API GET correlation with local Stage 116 signed proof.

Temporary SQLite + encrypted connector + authenticated real loopback HTTP.
Injected provider responses. No live n8n, OpenAI, user data or Tencent calls.
"""
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import local_server
from local_agent_connector_handoff import AgentConnectorHandoff
from local_agent_task_plans import LocalAgentPlanStore
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_handoff_outcome_proof import SignedManualHandoffOutcome, signing_message
from local_n8n_execution_verifier import (
    N8NExecutionVerifier, ProviderExecutionError,
    fetch_n8n_execution, validate_n8n_config, MAX_BODY_BYTES)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
PLAN = "a" * 24
PASS = "Stage117-Owner-Password-Private-2026!"
CID = "n8n_manual"
WID = "receipt_flow"
EID = "receipt_event_001"
N8N_WORKFLOW = "201"
N8N_EXECUTION = "12345"
NODE = "Incoming Webhook"
API_ORIGIN = "https://stage117.app.n8n.cloud"
API_KEY = "stage117-synthetic-api-token-not-a-real-secret-00000000000000"
SIGN_KEY = "stage117-synthetic-HMAC-connector-secret-more-than-32-chars"

@unittest.skipUnless(HAS_CRYPTO, "Dedicated CI installs pinned AES-GCM dependency")
class Stage117VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.core = LocalWorkflowCore(self.root / "flow.sqlite3")
        self.plans = LocalAgentPlanStore(self.root / "plans.sqlite3")
        self.plans.create({
            "model":"local-synthetic","goal":"17+25 private compute",
            "steps":[{"tool":"calculate","args":{
                "a":"17","b":"25","operation":"add"}}]
        }, plan_id=PLAN)
        self.plans.approve(PLAN)
        self.plans.advance(PLAN)
        self.assertEqual(self.core.create({
            "workflow_id":WID, "display_name":"Stage 117 Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})[0],201)
        self.assertEqual(self.core.activate({
            "workflow_id":WID,"expected_version":1})[0],200)
        self.assertEqual(self.core.run({
            "workflow_id":WID,"event_id":EID,
            "service_code":"automation","budget_tier":2,
            "timeline":"soon"})[0],200)
        self.sent=[]
        def sender(url,payload,delivery_id,secret):
            self.sent.append((url,payload,delivery_id,secret))
            return 202
        self.manager=LocalConnectorManager(
            self.core,self.root/"vault"/"registry.json",
            sender=sender,vault_key=generate_master_key())
        self.manager.add({"connector_id":CID,"label":"Stage117 test",
                          "url":"https://stage117.app.n8n.cloud/webhook/private-test",
                          "signing_secret":SIGN_KEY})
        self.handoff=AgentConnectorHandoff(
            self.plans,self.core,self.manager,
            self.root/"manual-handoff.sqlite3")
        self.signed=SignedManualHandoffOutcome(self.handoff,self.manager)
        preview=self.handoff.preview({
            "plan_id":PLAN,"connector_id":CID,
            "workflow_id":WID,"event_id":EID})
        ack=self.handoff.commit({
            "plan_id":PLAN,"connector_id":CID,
            "workflow_id":WID,"event_id":EID,
            "plan_sha256":preview["plan_sha256"],
            "workflow_sha256":preview["workflow_sha256"],
            "payload_sha256":preview["payload_sha256"],
            "preview_token":preview["preview_token"]})
        self.assertEqual(ack["status"],"REMOTE_ACCEPTED")
        self.delivery_id=ack["delivery_id"]
        message=signing_message(
            CID,WID,EID,self.delivery_id,"COMPLETED",N8N_EXECUTION)
        proof={"plan_id":PLAN,"connector_id":CID,"workflow_id":WID,
               "event_id":EID,"delivery_id":self.delivery_id,
               "outcome":"COMPLETED","execution_id":N8N_EXECUTION,
               "proof_hmac_sha256":hmac.new(
                   SIGN_KEY.encode(),message.encode("ascii"),
                   hashlib.sha256).hexdigest()}
        self.assertEqual(self.signed.import_receipt(proof)["result"],"RECORDED")
        self.api_calls=[]
        self.fake_response=self.execution()
        def fake(host,execution_id,api_key):
            self.api_calls.append((host,execution_id,api_key))
            return self.fake_response
        self.fetcher=fake
        self.verifier=self.new_verifier()

    def tearDown(self):
        self.handoff.close()
        self.manager.close()
        self.plans.close()
        self.core.close()
        self.tmp.cleanup()

    def new_verifier(self, **changes):
        args={
            "api_origin":API_ORIGIN,
            "workflow_id":N8N_WORKFLOW,
            "webhook_node_name":NODE,"api_key":API_KEY,
            "fetcher":self.fetcher,
        }
        args.update(changes)
        return N8NExecutionVerifier(self.signed,**args)

    def execution(self):
        return {"id":N8N_EXECUTION,"workflowId":N8N_WORKFLOW,
                "status":"success","finished":True,
                "stoppedAt":"2026-11-07T12:00:00.000Z",
                "data":{"resultData":{"runData":{NODE:[
                    {"data":{"main":[[{"json":{"headers":{"host":"hidden"},
                       "body":{"schema":"aas.local_workflow_receipt.v1",
                               "workflow_id":WID,"event_id":EID,
                               "delivery_id":self.delivery_id}}}]]}}
                ]}}}}

    def test_full_provider_execution_success_exact_input_bound(self):
        result=self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(result["status"],"EXECUTION_SUCCESS_CORRELATED")
        self.assertTrue(result["provider_api_execution_verified"])
        self.assertEqual(result["provider_hostname"],"stage117.app.n8n.cloud")
        self.assertTrue(result["inbound_delivery_correlated"])
        self.assertTrue(result["provider_execution_verified_with_delivery"])
        self.assertFalse(result["business_effect_verified"])
        self.assertEqual(result["external_write_count"],0)
        self.assertEqual(result["remote_reads"],1)
        self.assertEqual(self.api_calls,[
            ("stage117.app.n8n.cloud",N8N_EXECUTION,API_KEY)])
        self.assertEqual(len(self.sent),1)
        before=self.verifier.history()
        self.assertEqual(before["remote_reads"],0)
        self.assertNotIn(API_KEY,json.dumps(before))
        self.assertNotIn(SIGN_KEY,json.dumps(before))
        self.assertNotIn("17+25",json.dumps(before))
        self.assertNotIn("hidden",json.dumps(before))
        self.assertEqual(before["records"][0]["status"],
                         "EXECUTION_SUCCESS_CORRELATED")

    def test_provider_claim_mismatch_does_not_claim_success(self):
        self.fake_response["status"]="error"
        self.fake_response["finished"]=False
        output=self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(output["status"],"CLAIM_MISMATCH")
        self.assertTrue(output["inbound_delivery_correlated"])
        self.assertFalse(output["business_effect_verified"])
        self.assertFalse(output["provider_execution_verified_with_delivery"] is False)
        self.assertEqual(len(self.sent),1)

    def test_workflow_id_mismatch_rejected(self):
        self.fake_response["workflowId"]="other_workflow"
        r=self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(r["status"],"PROVIDER_IDENTITY_MISMATCH")
        self.assertFalse(r["provider_api_execution_verified"])
        self.assertFalse(r["inbound_delivery_correlated"])

    def test_execution_id_mismatch_rejected(self):
        self.fake_response["id"]="99999"
        self.assertEqual(self.verifier.verify_one(
            {"plan_id":PLAN})["status"],"PROVIDER_IDENTITY_MISMATCH")

    def test_response_missing_or_wrong_webhook_payload_fails_closed(self):
        for change in ["wrong_delivery","missing_node","multiple_items","wrong_schema"]:
            with self.subTest(change=change):
                self.fake_response=self.execution()
                if change=="wrong_delivery":
                    d=self.fake_response["data"]["resultData"]["runData"][NODE][0]["data"]["main"][0][0]["json"]["body"]
                    d["delivery_id"]="0"*32
                elif change=="wrong_schema":
                    d=self.fake_response["data"]["resultData"]["runData"][NODE][0]["data"]["main"][0][0]["json"]["body"]
                    d["schema"]="unspecified"
                elif change=="missing_node":
                    self.fake_response["data"]["resultData"]["runData"]={"Wrong Node":[{}]}
                else:
                    items=self.fake_response["data"]["resultData"]["runData"][NODE][0]["data"]["main"][0]
                    items.append(items[0])
                self.assertEqual(self.verifier.verify_one(
                    {"plan_id":PLAN})["status"],"DELIVERY_NOT_CORRELATED")
                with self.handoff.db:
                    self.handoff.db.execute(
                        "DELETE FROM n8n_provider_execution_checks WHERE plan_id=?",
                        (PLAN,))

    def test_provider_running_not_final_and_no_success(self):
        self.fake_response["status"]="running"
        self.fake_response["finished"]=False
        r=self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(r["status"],"PROVIDER_EXECUTION_NOT_FINAL")
        self.assertFalse(r["provider_execution_verified_with_delivery"])

    def test_lookup_definite_404_no_spoofed_execution(self):
        self.fake_response={"_api_error":"NOT_FOUND"}
        r=self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(r["status"],"LOOKUP_NOT_FOUND")
        self.assertFalse(r["provider_api_execution_verified"])

    def test_lookup_timeout_is_durable_and_no_auto_repeat(self):
        def timeout(*args):
            self.api_calls.append("ONE_READ_ONLY_GET")
            raise TimeoutError("simulated timeout not printed")
        verifier=self.new_verifier(fetcher=timeout)
        outcome=verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(outcome["status"],"LOOKUP_NETWORK_OR_RESPONSE_UNCERTAIN")
        with self.assertRaisesRegex(ProviderExecutionError,"already_checked"):
            verifier.verify_one({"plan_id":PLAN})
        verifier2=self.new_verifier(fetcher=timeout)
        self.assertEqual(verifier2.history()["records"][0]["status"],
                         "LOOKUP_NETWORK_OR_RESPONSE_UNCERTAIN")
        self.assertEqual(self.api_calls,["ONE_READ_ONLY_GET"])
        self.assertEqual(len(self.sent),1)

    def test_restart_crash_inflight_is_quarantined_not_replayed(self):
        with self.handoff.db:
            self.handoff.db.execute("""INSERT INTO n8n_provider_execution_checks
                (plan_id,connector_id,execution_id,delivery_id,
                 provider_workflow_id,pinned_node_sha256,signed_message_sha256,
                 status,checked_at_utc,provider_hostname)
                VALUES(?,?,?,?,?,?,?,'IN_FLIGHT',?,?)""",
                (PLAN,CID,N8N_EXECUTION,self.delivery_id,
                 N8N_WORKFLOW,"a"*64,"b"*64,"2026-11-07T12:00:00+00:00",
                 "stage117.app.n8n.cloud"))
        restarted=self.new_verifier()
        self.assertEqual(restarted.history()["records"][0]["status"],
                         "LOOKUP_UNCERTAIN")
        with self.assertRaises(ProviderExecutionError):
            restarted.verify_one({"plan_id":PLAN})
        self.assertEqual(self.api_calls,[])

    def test_missing_signed_receipt_rejects_without_api(self):
        with self.handoff.db:
            self.handoff.db.execute(
                "DELETE FROM signed_manual_handoff_outcomes WHERE plan_id=?",
                (PLAN,))
        with self.assertRaisesRegex(ProviderExecutionError,"signed_handoff_receipt_required"):
            self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(self.api_calls,[])

    def test_same_receipt_second_owner_click_never_requeries(self):
        self.verifier.verify_one({"plan_id":PLAN})
        with self.assertRaisesRegex(ProviderExecutionError,"already_checked"):
            self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(len(self.api_calls),1)

    def test_daily_budget_denies_before_any_provider_request(self):
        with self.handoff.db:
            from datetime import datetime,timezone
            today=datetime.now(timezone.utc).date().isoformat()
            for n in range(8):
                self.handoff.db.execute("""INSERT INTO n8n_provider_execution_checks
                    (plan_id,connector_id,execution_id,delivery_id,
                     provider_workflow_id,pinned_node_sha256,signed_message_sha256,
                     status,checked_at_utc,provider_hostname)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (f"b{n:023d}",CID,str(1000+n),self.delivery_id,
                     N8N_WORKFLOW,"b"*64,"c"*64,"LOOKUP_NOT_FOUND",
                     today+"T01:00:00+00:00","stage117.app.n8n.cloud"))
        with self.assertRaisesRegex(ProviderExecutionError,"daily_limit"):
            self.verifier.verify_one({"plan_id":PLAN})
        self.assertEqual(self.api_calls,[])

    def test_validator_blocks_ip_local_redirect_and_secret_shapes(self):
        bad_origins=[
            "http://stage117.app.n8n.cloud",
            "https://localhost",
            "https://127.0.0.1",
            "https://stage117.app.n8n.cloud:8080",
            "https://stage117.app.n8n.cloud?query=1",
            "https://stage117.app.n8n.cloud/credentials",
            "https://user@stage117.app.n8n.cloud",
            "https://stage117.app.n8n.cloud#fragment",
        ]
        for origin in bad_origins:
            with self.subTest(origin=origin),self.assertRaises(ProviderExecutionError):
                validate_n8n_config(origin,N8N_WORKFLOW,NODE,API_KEY)
        with self.assertRaises(ProviderExecutionError):
            validate_n8n_config(API_ORIGIN,N8N_WORKFLOW,NODE,"short")
        self.assertEqual(validate_n8n_config(
            API_ORIGIN,N8N_WORKFLOW,NODE,API_KEY),
            "stage117.app.n8n.cloud")

    def test_actual_network_sender_uses_one_dns_pinned_GET_only(self):
        calls=[]
        class DummyResponse:
            status=200
            def read(self,n):
                calls.append(("read",n))
                return json.dumps(self_record).encode("utf-8")
        class DummyTLS:
            def __init__(self,host,ip):
                calls.append(("init",host,ip))
            def request(self,method,path,headers):
                calls.append(("request",method,path,dict(headers)))
            def getresponse(self):
                return DummyResponse()
            def close(self):
                calls.append(("close",))
        self_record=self.execution()
        with patch("local_n8n_execution_verifier._resolve_public_ipv4",
                   return_value="93.184.215.14") as dns,patch(
                    "local_n8n_execution_verifier._PinnedTLS",DummyTLS):
            response=fetch_n8n_execution("stage117.app.n8n.cloud",
                                         N8N_EXECUTION,API_KEY)
        self.assertEqual(response["id"],N8N_EXECUTION)
        self.assertEqual(calls[1][1],"GET")
        self.assertEqual(calls[1][2],
                         "/api/v1/executions/12345?includeData=true")
        self.assertEqual(calls[1][3]["X-N8N-API-KEY"],API_KEY)
        self.assertEqual(calls[2],("read",MAX_BODY_BYTES+1))
        self.assertEqual(calls[-1],("close",))
        dns.assert_called_once_with("stage117.app.n8n.cloud")

    def test_owner_session_password_csrf_readonly_http(self):
        LocalOperatorAuth.provision(self.root/"owner.json",PASS)
        owner=LocalOperatorAuth.load(self.root/"owner.json")
        audit=LocalSecurityAudit(self.root/"audit.sqlite3")
        server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(
                operator_auth=owner,security_audit=audit,
                local_core=self.core,local_agent_plan_store=self.plans,
                agent_connector_handoff=self.handoff,
                handoff_outcomes=self.signed,
                n8n_execution_verifier=self.verifier))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
        base="http://127.0.0.1:"+str(server.server_port)
        csrf=None
        def post(route,data,csrf_auth=True):
            headers={"Content-Type":"application/json"}
            if csrf and csrf_auth:headers["X-AAS-CSRF"]=csrf
            with opener.open(Request(base+route,
                json.dumps(data).encode("utf-8"),
                headers=headers,method="POST"),timeout=7) as r:
                return json.load(r)
        try:
            with self.assertRaises(HTTPError) as no_session:
                opener.open(base+"/api/n8n-execution-check-history",timeout=7)
            self.assertEqual(no_session.exception.code,401)
            csrf=post("/api/operator-login",{"password":PASS})["csrf_token"]
            with opener.open(base+"/api/local-agent-capabilities",timeout=7) as r:
                self.assertTrue(json.load(r)["n8n_execution_readonly_verify"])
            with self.assertRaises(HTTPError) as no_csrf:
                post("/api/n8n-execution-check",
                     {"plan_id":PLAN,"current_password":PASS},False)
            self.assertEqual(no_csrf.exception.code,403)
            with self.assertRaises(HTTPError) as wrong_password:
                post("/api/n8n-execution-check",
                     {"plan_id":PLAN,"current_password":"wrong"})
            self.assertEqual(wrong_password.exception.code,401)
            self.assertEqual(self.api_calls,[])
            receipt=post("/api/n8n-execution-check",
                         {"plan_id":PLAN,"current_password":PASS})
            self.assertEqual(receipt["status"],"EXECUTION_SUCCESS_CORRELATED")
            self.assertEqual(len(self.api_calls),1)
            with opener.open(base+"/api/n8n-execution-check-history",
                             timeout=7) as r:
                history=json.load(r)
            self.assertEqual(history["records"][0]["status"],
                             "EXECUTION_SUCCESS_CORRELATED")
            self.assertEqual(history["remote_reads"],0)
            self.assertTrue(any(x["action"]=="N8N_EXECUTION_READONLY_VERIFY"
                                for x in audit.recent(40)))
            self.assertEqual(len(self.sent),1)
        finally:
            server.shutdown();server.server_close();thread.join(5);audit.close()


@unittest.skipUnless(HAS_CRYPTO,"Dedicated CI includes encryption dependency")
class Stage117LightweightStartup(unittest.TestCase):
    def test_actual_private_server_starts_without_paid_or_local_model(self):
        with tempfile.TemporaryDirectory(prefix="aas-n8n117-") as tmp:
            home=Path(tmp)
            LocalOperatorAuth.provision(home/".ai-automation-studio"/"operator.json",PASS)
            env=dict(os.environ)
            # Do not inherit unrelated paid API flags or private user state.
            for name in ("AAS_OPENAI_API_KEY","AAS_WEBHOOK_URL",
                         "CREDENTIALS_DIRECTORY"):
                env.pop(name,None)
            env.update({
                "HOME":str(home),"USERPROFILE":str(home),
                "PYTHONUNBUFFERED":"1",
                "AAS_VAULT_MASTER_KEY":generate_master_key(),
                "AAS_N8N_API_ORIGIN":API_ORIGIN,
                "AAS_N8N_API_KEY":API_KEY,
                "AAS_N8N_WORKFLOW_ID":N8N_WORKFLOW,
                "AAS_N8N_WEBHOOK_NODE_NAME":NODE,
            })
            proc=subprocess.Popen([
                sys.executable,str(ROOT/"app"/"local_server.py"),
                "--enable-encrypted-managed-webhooks",
                "--enable-n8n-execution-verify"],
                cwd=str(ROOT),env=env,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            host="http://127.0.0.1:8765"
            opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
            try:
                available=False
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if proc.poll() is not None:break
                    try:
                        with opener.open(host+"/mission",timeout=1) as response:
                            available=response.status==200
                            break
                    except OSError:
                        time.sleep(0.2)
                self.assertTrue(available,"private n8n-only local server failed to start")
                with self.assertRaises(HTTPError) as anon:
                    opener.open(host+"/api/n8n-execution-check-history",timeout=3)
                self.assertEqual(anon.exception.code,401)
                with opener.open(Request(
                    host+"/api/operator-login",
                    json.dumps({"password":PASS}).encode(),
                    headers={"Content-Type":"application/json"},method="POST"),
                    timeout=5) as response:
                    self.assertTrue(json.load(response)["authenticated"])
                with opener.open(host+"/api/local-agent-capabilities",timeout=3) as response:
                    caps=json.load(response)
                self.assertTrue(caps["n8n_execution_readonly_verify"])
                self.assertFalse(caps["remote_scheduled_ai"])
                self.assertFalse(caps["remote_on_demand_ai"])
                self.assertFalse(caps["enabled"])
                self.assertEqual(caps["n8n_execution_hostname"],
                                 "stage117.app.n8n.cloud")
                with opener.open(host+"/api/n8n-execution-check-history",timeout=3) as response:
                    self.assertEqual(json.load(response)["records"],[])
                self.assertFalse((home/".ai-automation-studio"/"on-demand-remote-ai.sqlite3").exists())
            finally:
                proc.terminate()
                try:proc.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=5)


if __name__=="__main__":
    unittest.main()
