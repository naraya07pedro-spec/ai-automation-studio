"""Stage #112 genuine localhost HTTP and durable one-shot model gate acceptance.

All model calls are injected in-process fixtures. NO real provider bill/request.
"""
from datetime import datetime, timezone
import http.cookiejar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import local_server
from local_model_safety import LocalModelSafetyState
from local_on_demand_remote_ai import (
    LocalOnDemandRemoteAI, OnDemandRemoteAIError, MAX_DAILY_ATTEMPTS
)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from openai_readonly_responses import RemoteInferenceError

PASSWORD = "Owner-Only-Strong-Password-2026"
PROMPT = "Give one short factual explanation without using tools."


def good_result():
    return {"mode":"REMOTE_OPENAI_READ_ONLY", "answer":"Fixture-only answer",
            "usage":{"input_tokens":18,"output_tokens":9},
            "external_dispatch_count":0,"tools_used":[]}


def payload(identifier="a"*32, **changes):
    return {"request_id":identifier, "prompt":PROMPT,
            "model":"test-model", "paid_api_consent":True, **changes}


class DirectModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.safety=LocalModelSafetyState(self.path/"model-safety.sqlite3")
        self.gate=threading.BoundedSemaphore(1)
        self.state_lock=threading.Lock()
        self.calls=[]
        def runner(prompt,model):
            self.calls.append((prompt,model))
            return good_result()
        self.store=LocalOnDemandRemoteAI(self.path/"on-demand-remote-ai.sqlite3",
                                         runner=runner)
        self.store.attach_gate(self.safety,self.gate,self.state_lock)

    def tearDown(self):
        self.store.close()
        self.safety.close()
        self.tmp.cleanup()

    def test_exactly_once_with_reopen_and_no_prompt_or_key_in_sqlite(self):
        answer=self.store.run(payload())
        self.assertEqual(answer["status"],"COMPLETED")
        self.assertEqual(answer["answer"],"Fixture-only answer")
        self.assertEqual(answer["usage_tokens"],{"input_tokens":18,"output_tokens":9})
        self.assertFalse(answer["provider_billing_independently_verified"])
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(len(self.calls),1)
        with self.assertRaisesRegex(OnDemandRemoteAIError,"already_used"):
            self.store.run(payload())
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.recent()["receipts"][0]["status"],"COMPLETED")
        self.assertNotIn("Fixture-only answer",json.dumps(self.store.recent()))
        self.store.close()
        db=self.path/"on-demand-remote-ai.sqlite3"
        raw=db.read_bytes()
        self.assertNotIn(PROMPT.encode(),raw)
        self.assertNotIn(b"sk-fixture",raw)
        self.store=LocalOnDemandRemoteAI(db,runner=lambda *_:self.fail("replayed"))
        self.store.attach_gate(self.safety,self.gate,self.state_lock)
        self.assertEqual(self.store.receipt("a"*32)["answer"],"Fixture-only answer")

    def test_invalid_consent_id_model_or_prompt_never_bills(self):
        cases=[
            payload(paid_api_consent=False),
            payload(paid_api_consent="true"),
            payload(paid_api_consent=1),
            payload(identifier="bad-id"),
            payload(model="../evil"),
            payload(prompt=""),
            {**payload(), "unapproved_field": 1},
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(OnDemandRemoteAIError):
                self.store.run(item)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.store.recent()["receipts"],[])
        self.assertEqual(self.safety.phase(),"IDLE")

    def test_network_uncertainty_is_durable_and_quarantines_next_request(self):
        def ambiguous(*_):
            self.calls.append(("sent","maybe"))
            raise RemoteInferenceError("remote_response_uncertain")
        self.store.runner=ambiguous
        with self.assertRaisesRegex(OnDemandRemoteAIError,"check_usage"):
            self.store.run(payload())
        self.assertEqual(self.safety.phase(),"UNCERTAIN")
        self.assertEqual(self.store.receipt("a"*32)["status"],"OUTCOME_UNCERTAIN")
        with self.assertRaisesRegex(OnDemandRemoteAIError,"already_used"):
            self.store.run(payload())
        with self.assertRaisesRegex(OnDemandRemoteAIError,"model_safety_quarantined"):
            self.store.run(payload("b"*32))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.store.recent()["receipts"]),1)
        self.safety.acknowledge()
        self.store.runner=lambda *_:good_result()
        self.assertEqual(self.store.run(payload("b"*32))["status"],"COMPLETED")

    def test_definitive_provider_rejection_never_retries_same_id(self):
        def reject(*_):
            self.calls.append(("once","reject"))
            raise RemoteInferenceError("remote_provider_rejected")
        self.store.runner=reject
        with self.assertRaisesRegex(OnDemandRemoteAIError,"remote_provider_rejected"):
            self.store.run(payload())
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(self.store.receipt("a"*32)["status"],"FAILED_DEFINITE")
        with self.assertRaisesRegex(OnDemandRemoteAIError,"already_used"):
            self.store.run(payload())
        self.assertEqual(len(self.calls),1)

    def test_hard_finite_daily_attempt_cap(self):
        for i in range(MAX_DAILY_ATTEMPTS):
            rid=f"{i:032x}"
            self.assertEqual(self.store.run(payload(rid))["status"],"COMPLETED")
        self.assertEqual(len(self.calls),MAX_DAILY_ATTEMPTS)
        with self.assertRaisesRegex(OnDemandRemoteAIError,"daily_cap_reached"):
            self.store.run(payload("f"*32))
        self.assertEqual(len(self.calls),MAX_DAILY_ATTEMPTS)

    def test_busy_gate_blocks_before_claim(self):
        self.gate.acquire()
        try:
            with self.assertRaisesRegex(OnDemandRemoteAIError,"model_operation_busy"):
                self.store.run(payload())
        finally:
            self.gate.release()
        self.assertEqual(self.store.recent()["receipts"],[])
        self.assertEqual(self.safety.phase(),"IDLE")

    def test_crash_in_flight_receipt_becomes_uncertain_after_reopen(self):
        with self.store.lock,self.store.db:
            self.store.db.execute("""INSERT INTO remote_asks
                (request_id,prompt_sha256,model,status,created_utc)
                VALUES (?,?,?,?,?)""",
                ("f"*32,"0"*64,"test-model","IN_FLIGHT",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.store.close()
        self.store=LocalOnDemandRemoteAI(
            self.path/"on-demand-remote-ai.sqlite3",
            runner=lambda *_:self.fail("unexpected call"))
        self.store.attach_gate(self.safety,self.gate,self.state_lock)
        self.assertEqual(self.store.receipt("f"*32)["status"],"OUTCOME_UNCERTAIN")
        with self.assertRaisesRegex(OnDemandRemoteAIError,"already_used"):
            self.store.run(payload("f"*32))

    def test_untrusted_result_is_latched_uncertain(self):
        self.store.runner=lambda *_:{"answer":"missing guarded fields"}
        with self.assertRaisesRegex(OnDemandRemoteAIError,"check_usage"):
            self.store.run(payload())
        self.assertEqual(self.safety.phase(),"UNCERTAIN")
        self.assertIsNone(self.store.receipt("a"*32)["answer"])


class OwnerAuthenticatedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        LocalOperatorAuth.provision(self.base/"operator.json",PASSWORD)
        self.owner=LocalOperatorAuth.load(self.base/"operator.json")
        self.safety=LocalModelSafetyState(self.base/"model.sqlite3")
        self.core=LocalWorkflowCore(self.base/"core.sqlite3")
        self.audit=LocalSecurityAudit(self.base/"audit.sqlite3")
        self.calls=[]
        def runner(prompt,model):
            self.calls.append((prompt,model))
            return good_result()
        self.store=LocalOnDemandRemoteAI(self.base/"direct.sqlite3",runner=runner)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(local_core=self.core,
                operator_auth=self.owner,security_audit=self.audit,
                model_safety_state=self.safety,on_demand_ai=self.store))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f"http://127.0.0.1:{self.server.server_port}"
        self.opener=build_opener(ProxyHandler({}),
                    HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=None

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.store.close();self.core.close();self.audit.close();self.safety.close()
        self.tmp.cleanup()

    def get(self,route):
        with self.opener.open(self.url+route,timeout=6) as response:
            return json.load(response)

    def post(self,route,body,use_csrf=True):
        headers={"Content-Type":"application/json"}
        if use_csrf and self.csrf:
            headers["X-AAS-CSRF"]=self.csrf
        req=Request(self.url+route,json.dumps(body).encode("utf-8"),
                    headers=headers,method="POST")
        with self.opener.open(req,timeout=6) as response:
            return json.load(response)

    def login(self):
        self.csrf=self.post("/api/operator-login",{"password":PASSWORD})["csrf_token"]

    def test_auth_csrf_password_paid_consent_and_one_real_http_response(self):
        with self.assertRaises(HTTPError) as anon:
            self.get("/api/read-only-ai-ask-history")
        self.assertEqual(anon.exception.code,401)
        self.login()
        info=self.get("/api/local-agent-capabilities")
        self.assertFalse(info["enabled"])
        self.assertTrue(info["remote_on_demand_ai"])
        data={**payload(),"current_password":PASSWORD}
        with self.assertRaises(HTTPError) as missing_csrf:
            self.post("/api/read-only-ai-ask",data,use_csrf=False)
        self.assertEqual(missing_csrf.exception.code,403)
        with self.assertRaises(HTTPError) as wrong:
            self.post("/api/read-only-ai-ask",{**data,"current_password":"wrong"})
        self.assertEqual(wrong.exception.code,401)
        with self.assertRaises(HTTPError) as false_consent:
            self.post("/api/read-only-ai-ask",{**data,"paid_api_consent":1})
        self.assertEqual(false_consent.exception.code,403)
        self.assertEqual(self.calls,[])
        result=self.post("/api/read-only-ai-ask",data)
        self.assertEqual(result["status"],"COMPLETED")
        self.assertEqual(len(self.calls),1)
        lookup=self.get("/api/read-only-ai-ask-receipt?request_id="+result["request_id"])
        self.assertEqual(lookup["answer"],"Fixture-only answer")
        self.assertNotIn("answer",self.get("/api/read-only-ai-ask-history")["receipts"][0])
        with self.assertRaises(HTTPError) as duplicate:
            self.post("/api/read-only-ai-ask",data)
        self.assertEqual(duplicate.exception.code,409)
        self.assertEqual(len(self.calls),1)
        recent=self.audit.recent(30)
        self.assertTrue(any(e["action"]=="ON_DEMAND_REMOTE_AI" and e["outcome"]=="SUCCESS"
                            for e in recent))

    def test_disabled_mode_denies_endpoint_without_provider_call(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(local_core=self.core,
                operator_auth=self.owner,security_audit=self.audit,
                model_safety_state=self.safety))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f"http://127.0.0.1:{self.server.server_port}"
        self.login()
        self.assertFalse(self.get("/api/local-agent-capabilities")["remote_on_demand_ai"])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/read-only-ai-ask",{**payload(),"current_password":PASSWORD})
        self.assertEqual(denied.exception.code,403)
        self.assertEqual(self.calls,[])


if __name__ == "__main__":
    unittest.main()
