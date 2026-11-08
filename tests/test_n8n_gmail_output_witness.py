"""Stage 122: synthetic official n8n GET, real Stage 114-121 local ledgers.

No real n8n, Gmail account, webhook send or paid model request.
"""
import http.cookiejar
import importlib.util
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, HTTPCookieProcessor, build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"tests"))

import test_signed_gmail_artifact as stage121
from local_gmail_sent_evidence import GmailSentEvidence, GmailSentEvidenceError
from local_n8n_gmail_output_witness import (
    N8NGmailOutputWitness, N8NGmailOutputError,extract_send_node_message_id)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler

PLAN=stage121.PLAN
GMAIL_ID=stage121.GMAIL_ID
RECIPIENT=stage121.RECIPIENT
SUBJECT=stage121.SUBJECT
NODE_NAME="Send Gmail"
HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO,"Dedicated CI includes pinned cryptography")
class Stage122WitnessTests(unittest.TestCase):
    def setUp(self):
        self.prior=stage121.Stage121SignedBindingTests()
        self.prior.setUp()
        self.prior.binding.import_claim(self.prior.claim())
        self.n8n_calls=self.prior.previous.api_calls
        self.prior.previous.fake_response=self.response()
        self.witness=N8NGmailOutputWitness(
            self.prior.previous.verifier,self.prior.binding,
            gmail_node_name=NODE_NAME)
        self.evidence=GmailSentEvidence(
            self.prior.previous.verifier,self.prior.gmail,
            signed_artifact_binding=self.prior.binding,
            official_n8n_output_witness=self.witness)

    def tearDown(self):
        self.prior.tearDown()

    def response(self):
        body=self.prior.previous.execution()
        body["workflowData"]={"nodes":[
            {"name":"Incoming Webhook","type":"n8n-nodes-base.webhook",
             "parameters":{}},
            {"name":NODE_NAME,"type":"n8n-nodes-base.gmail",
             "parameters":{"resource":"message","operation":"send"}},
        ]}
        body["data"]["resultData"]["runData"][NODE_NAME]=[
            {"data":{"main":[[{"json":{"id":GMAIL_ID,"threadId":"fake-thread"}}]]}}
        ]
        return body

    def request(self):
        return {"plan_id":PLAN,"gmail_message_id":GMAIL_ID,
                "expected_to":RECIPIENT,"expected_subject":SUBJECT}

    def test_real_signed_n8n_chain_and_provider_gmail_sent(self):
        self.assertEqual(len(self.n8n_calls),1)
        with self.assertRaisesRegex(GmailSentEvidenceError,
                                    "n8n_gmail_output_witness_required"):
            self.evidence.verify_one(self.request())
        self.assertEqual(len(self.n8n_calls),1)
        self.assertEqual(self.prior.gmail.calls,[])
        result=self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(result["status"],"PROVIDER_SEND_NODE_ID_CORRELATED")
        self.assertEqual(len(self.n8n_calls),2)
        self.assertTrue(self.witness.require_match(PLAN,GMAIL_ID))
        self.assertFalse(result["recipient_delivery_verified"])
        self.assertFalse(result["causal_business_effect_verified"])
        gmail=self.evidence.verify_one(self.request())
        self.assertEqual(gmail["status"],"SENT_ARTIFACT_MATCHED")
        self.assertEqual(self.prior.gmail.calls,[GMAIL_ID])
        for val in (GMAIL_ID,RECIPIENT,SUBJECT,stage121.stage117.SIGN_KEY):
            self.assertNotIn(val,json.dumps(self.witness.history()))

    def test_only_genuine_gmail_send_node_definition_is_accepted(self):
        for change in ("fake-type","fake-operation","fake-resource",
                       "duplicate-node","missing-snapshot"):
            with self.subTest(change=change):
                example=self.response()
                node=example["workflowData"]["nodes"][1]
                if change=="fake-type": node["type"]="n8n-nodes-base.code"
                if change=="fake-operation": node["parameters"]["operation"]="get"
                if change=="fake-resource": node["parameters"]["resource"]="draft"
                if change=="duplicate-node": example["workflowData"]["nodes"].append(node.copy())
                if change=="missing-snapshot":example.pop("workflowData")
                self.assertIsNone(extract_send_node_message_id(
                    example,NODE_NAME,stage121.stage117.N8N_EXECUTION,
                    stage121.stage117.N8N_WORKFLOW))

    def test_forged_or_ambiguous_node_outputs_never_match(self):
        for change in ("wrong-id","invalid-id","two-items",
                       "two-runs","two-branches","missing-output","node-error"):
            with self.subTest(change=change):
                example=self.response()
                items=example["data"]["resultData"]["runData"][NODE_NAME]
                if change=="wrong-id":items[0]["data"]["main"][0][0]["json"]["id"]="different"
                if change=="invalid-id":items[0]["data"]["main"][0][0]["json"]["id"]="../traversal"
                if change=="two-items":items[0]["data"]["main"][0].append(items[0]["data"]["main"][0][0])
                if change=="two-runs":items.append(items[0])
                if change=="two-branches":items[0]["data"]["main"].append([])
                if change=="missing-output":items.clear()
                if change=="node-error":items[0]["error"]={"message":"failed"}
                got=extract_send_node_message_id(
                    example,NODE_NAME,stage121.stage117.N8N_EXECUTION,
                    stage121.stage117.N8N_WORKFLOW)
                if change=="wrong-id":self.assertEqual(got,"different")
                else:self.assertIsNone(got)

    def test_different_message_id_is_not_correlated_and_no_retry(self):
        self.prior.previous.fake_response["data"]["resultData"]["runData"][NODE_NAME][0]["data"]["main"][0][0]["json"]["id"]="unrelated"
        result=self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(result["status"],"PROVIDER_SEND_NODE_NOT_CORRELATED")
        with self.assertRaisesRegex(N8NGmailOutputError,
                                    "n8n_gmail_witness_already_checked"):
            self.witness.verify_one({"plan_id":PLAN})
        with self.assertRaises(N8NGmailOutputError):
            self.witness.require_match(PLAN,GMAIL_ID)
        self.assertEqual(self.prior.gmail.calls,[])
        self.assertEqual(len(self.n8n_calls),2)

    def test_swapped_incoming_webhook_cannot_pass_second_get(self):
        example=self.prior.previous.fake_response
        input_json=example["data"]["resultData"]["runData"]["Incoming Webhook"][0]["data"]["main"][0][0]["json"]
        input_json["body"]["delivery_id"]="f"*32
        result=self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(result["status"],"PROVIDER_SEND_NODE_NOT_CORRELATED")

    def test_provider_error_quarantines_and_blocks_gmail(self):
        self.prior.previous.fake_response={"_api_error":"AUTH_REQUIRED"}
        result=self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(result["status"],"LOOKUP_UNCERTAIN")
        with self.assertRaises(N8NGmailOutputError):
            self.witness.require_match(PLAN,GMAIL_ID)
        with self.assertRaises(N8NGmailOutputError):
            self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(self.prior.gmail.calls,[])

    def test_restart_converts_in_flight_to_uncertain_without_get(self):
        self.prior.previous.handoff.db.execute("""INSERT INTO n8n_gmail_output_witnesses
            (plan_id,execution_id,delivery_id,pinned_send_node_sha256,
             signed_gmail_id_sha256,status,checked_at_utc)
             VALUES(?,?,?,?,?,'IN_FLIGHT','2026-11-08T00:00:00Z')""",
             (PLAN,"12345","d"*32,"a"*64,"b"*64))
        self.prior.previous.handoff.db.commit()
        reopened=N8NGmailOutputWitness(
            self.prior.previous.verifier,self.prior.binding,
            gmail_node_name=NODE_NAME)
        self.assertEqual(reopened.history()["records"][0]["status"],"LOOKUP_UNCERTAIN")
        self.assertEqual(len(self.n8n_calls),1)

    def test_unbound_legacy_gmail_check_cannot_be_rechecked(self):
        old=GmailSentEvidence(self.prior.previous.verifier,self.prior.gmail,
                              signed_artifact_binding=self.prior.binding)
        self.assertEqual(old.verify_one(self.request())["status"],"SENT_ARTIFACT_MATCHED")
        with self.assertRaisesRegex(N8NGmailOutputError,
                                    "gmail_already_checked_no_retroactive_witness"):
            self.witness.verify_one({"plan_id":PLAN})
        self.assertEqual(len(self.n8n_calls),1)

    def test_wrong_credential_or_node_configuration_blocks_startup(self):
        with self.assertRaisesRegex(N8NGmailOutputError,"pinned_gmail_send_node_required"):
            N8NGmailOutputWitness(self.prior.previous.verifier,self.prior.binding,
                                  gmail_node_name="Incoming Webhook")
        with self.assertRaisesRegex(N8NGmailOutputError,"pinned_gmail_send_node_required"):
            N8NGmailOutputWitness(self.prior.previous.verifier,self.prior.binding,
                                  gmail_node_name="bad"+chr(10)+"name")

    def test_http_auth_csrf_password_consent_one_read_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            password="Owner-Stage122-Test-Passphrase!"
            auth=LocalOperatorAuth.provision(root/"operator.json",password)
            if auth is None: auth=LocalOperatorAuth.load(root/"operator.json")
            audit=LocalSecurityAudit(root/"audit.sqlite3")
            core=LocalWorkflowCore(root/"core.sqlite3")
            server=ThreadingHTTPServer(("127.0.0.1",0),
                create_handler(local_core=core,operator_auth=auth,
                               security_audit=audit,n8n_gmail_output_witness=self.witness))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            origin="http://127.0.0.1:"+str(server.server_port)
            opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def post(path,body,csrf=None):
                h={"Content-Type":"application/json"}
                if csrf is not None:h["X-AAS-CSRF"]=csrf
                req=Request(origin+path,json.dumps(body).encode(),
                            headers=h,method="POST")
                with opener.open(req,timeout=5) as response:return json.load(response)
            values={"plan_id":PLAN,"current_password":password,
                    "consent":"VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET"}
            try:
                with self.assertRaises(HTTPError) as e:
                    opener.open(origin+"/api/n8n-gmail-output-witness-history")
                self.assertEqual(e.exception.code,401)
                csrf=post("/api/operator-login",{"password":password})["csrf_token"]
                with self.assertRaises(HTTPError) as e:
                    post("/api/n8n-gmail-output-witness",values)
                self.assertEqual(e.exception.code,403)
                with self.assertRaises(HTTPError) as e:
                    post("/api/n8n-gmail-output-witness",
                         {**values,"consent":"YES"},csrf)
                self.assertEqual(e.exception.code,422)
                with self.assertRaises(HTTPError) as e:
                    post("/api/n8n-gmail-output-witness",
                         {**values,"current_password":"wrong"},csrf)
                self.assertEqual(e.exception.code,401)
                self.assertEqual(len(self.n8n_calls),1)
                received=post("/api/n8n-gmail-output-witness",values,csrf)
                self.assertEqual(received["status"],"PROVIDER_SEND_NODE_ID_CORRELATED")
                self.assertEqual(len(self.n8n_calls),2)
                with self.assertRaises(HTTPError) as e:
                    post("/api/n8n-gmail-output-witness",values,csrf)
                self.assertEqual(e.exception.code,409)
                with opener.open(origin+"/api/n8n-gmail-output-witness-history") as response:
                    history=json.load(response)
                self.assertEqual(history["records"][0]["status"],
                                 "PROVIDER_SEND_NODE_ID_CORRELATED")
                self.assertTrue(any(x["action"]=="N8N_GMAIL_SEND_OUTPUT_VERIFY"
                                    and x["outcome"]=="SUCCESS" for x in audit.recent()))
                for secret in (GMAIL_ID,RECIPIENT,SUBJECT,stage121.stage117.API_KEY):
                    self.assertNotIn(secret,json.dumps(history))
                    self.assertNotIn(secret,json.dumps(audit.recent()))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                audit.close();core.close()


class Stage122ClientContractTests(unittest.TestCase):
    def setUp(self):
        from owner_n8n_gmail_output_acceptance import OwnerN8NGmailOutputClient
        self.client=object.__new__(OwnerN8NGmailOutputClient)
        self.calls=[]
        self.client._login=lambda p:self.calls.append(("login",None))
        self.client._logout=lambda:self.calls.append(("logout",None))
        self.fail=False
        def request(path,values=None):
            self.calls.append((path,values))
            if path=="/api/local-agent-capabilities":
                return {"n8n_execution_readonly_verify":True,
                        "signed_gmail_artifact_binding_required":True,
                        "official_n8n_gmail_output_required":True}
            if path=="/api/n8n-execution-check-history":
                return {"records":[{"plan_id":PLAN,"status":"EXECUTION_SUCCESS_CORRELATED",
                                    "inbound_delivery_correlated":True}]}
            if path=="/api/signed-gmail-artifact-bindings":
                return {"records":[{"plan_id":PLAN}]}
            if path=="/api/n8n-gmail-output-witness-history":
                return {"records":[]}
            if path=="/api/n8n-gmail-output-witness":
                if self.fail:
                    from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
                    raise OwnerLiveAcceptanceError("response_lost")
                return {"plan_id":PLAN,"status":"PROVIDER_SEND_NODE_ID_CORRELATED",
                        "external_write_count":0,"recipient_delivery_verified":False,
                        "causal_business_effect_verified":False,"automatic_retry":False}
            raise AssertionError(path)
        self.client._request=request

    def test_inspect_issues_no_remote_read(self):
        report=self.client.execute(mode="inspect",plan_id=PLAN,password="pw")
        self.assertEqual(report["status"],"READY_FOR_ONE_OFFICIAL_N8N_GMAIL_OUTPUT_READ")
        self.assertFalse(any(p=="/api/n8n-gmail-output-witness" for p,_ in self.calls))

    def test_verify_one_local_post_and_no_raw_report(self):
        report=self.client.execute(mode="verify",plan_id=PLAN,password="pw",
                                    confirmation="VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET")
        self.assertEqual(report["status"],"PROVIDER_SEND_NODE_ID_CORRELATED")
        self.assertEqual(sum(p=="/api/n8n-gmail-output-witness" for p,_ in self.calls),1)
        self.assertFalse(report["causal_business_effect_verified"])

    def test_lost_local_response_no_blind_retry(self):
        self.fail=True
        report=self.client.execute(mode="verify",plan_id=PLAN,password="pw",
                                    confirmation="VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET")
        self.assertEqual(report["status"],"LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY")
        self.assertEqual(sum(p=="/api/n8n-gmail-output-witness" for p,_ in self.calls),1)

    def test_explicit_consent_required_pre_post(self):
        from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
        with self.assertRaisesRegex(OwnerLiveAcceptanceError,"explicit_n8n_gmail_output_consent_required"):
            self.client.execute(mode="verify",plan_id=PLAN,password="pw",confirmation="YES")
        self.assertFalse(any(p=="/api/n8n-gmail-output-witness" for p,_ in self.calls))


if __name__=="__main__":
    unittest.main()
