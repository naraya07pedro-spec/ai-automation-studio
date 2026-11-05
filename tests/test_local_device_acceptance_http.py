"""Slice 59: authenticated real-device acceptance path with explicit fixture label."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_multi_doc_smoke import smoke
from local_ollama_agent import AgentError
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler

PASSWORD="fixture_only_device_acceptance_password_59"


def fixture_model(payload):
    return {"message":{"content":json.dumps({
        "answer":"The maintenance review meeting starts at 18:30 WIB.",
        "insufficient_evidence":False,
        "citations":[{"chunk_id":"F2C1",
                      "quote":"Maintenance review meeting starts at 18:30 WIB"}]})}}


class DeviceAcceptanceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.calls=[]
        self.entered=threading.Event()
        self.release=threading.Event()
        self.pause=False
        def acceptance(model):
            self.calls.append(model)
            self.entered.set()
            if self.pause:self.release.wait(3)
            return smoke(model,catalog=lambda:["fixture:model"],
                         transport=fixture_model,memory=lambda:None)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                local_agent_runner=lambda prompt,model:{
                    "mode":"LOCAL_MODEL","answer":"fixture",
                    "tools_used":[],"external_dispatch_count":0},
                local_device_acceptance=acceptance))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.release.set)
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,method,path,body=None,auth=True,csrf=True,origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        conn.request(method,path,
                     body=json.dumps(body).encode() if body is not None else None,
                     headers=headers)
        response=conn.getresponse()
        result=(response.status,json.loads(response.read()))
        conn.close()
        return result

    def test_owner_csrf_origin_then_explicit_synthetic_result(self):
        url="/api/local-agent-device-acceptance"
        body={"model":"fixture:model"}
        self.assertEqual(401,self.request("POST",url,body,auth=False)[0])
        self.assertEqual(403,self.request("POST",url,body,csrf=False)[0])
        self.assertEqual(403,self.request("POST",url,body,
                                         origin="http://attacker.invalid")[0])
        self.assertEqual([],self.calls)
        code,result=self.request("POST",url,body)
        self.assertEqual(200,code)
        self.assertEqual("SYNTHETIC_MODEL_SMOKE_PASSED",result["status"])
        self.assertEqual("INJECTED_TEST_TRANSPORT",result["inference_source"])
        self.assertTrue(result["source_evidence_verified"])
        self.assertEqual(1,result["model_calls"])
        self.assertEqual("schedule.md",result["citations"][0]["filename"])
        self.assertIn("18:30 WIB",result["citations"][0]["quote"])
        self.assertEqual(0,result["external_write_count"])
        self.assertFalse(result["history_saved"])
        self.assertEqual(["fixture:model"],self.calls)
        self.assertTrue(self.request("GET","/api/local-agent-capabilities")[1][
                        "device_acceptance"])

    def test_disabled_flag_never_dispatches_callback(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                local_agent_runner=lambda prompt,model:None))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            conn.request("POST","/api/local-agent-device-acceptance",
                body=json.dumps({"model":"fixture:model"}),
                headers={"Content-Type":"application/json",
                         "X-AAS-CSRF":self.csrf,"Cookie":self.cookie})
            response=conn.getresponse()
            self.assertEqual(403,response.status)
            response.read();conn.close()
        finally:
            server.shutdown();server.server_close();worker.join(4)
        self.assertEqual([],self.calls)

    def test_rejects_invalid_model_shape_without_invoking(self):
        for body in ({},[],{"model":True},{"model":"../escape"},
                     {"model":"fixture:model","extra":"x"}):
            with self.subTest(body=str(body)):
                code,_=self.request("POST","/api/local-agent-device-acceptance",body)
                self.assertEqual(422,code)
        self.assertEqual([],self.calls)

    def test_missing_installed_model_reports_409(self):
        code,data=self.request("POST","/api/local-agent-device-acceptance",
                               {"model":"missing:model"})
        self.assertEqual(409,code)
        self.assertEqual("model_not_installed",data["error"])

    def test_one_operation_shares_global_model_gate(self):
        self.pause=True
        outcome=[]
        t=threading.Thread(target=lambda:outcome.append(self.request(
            "POST","/api/local-agent-device-acceptance",{"model":"fixture:model"})))
        t.start()
        self.assertTrue(self.entered.wait(2))
        code,body=self.request("POST","/api/local-agent-device-acceptance",
                               {"model":"fixture:model"})
        self.assertEqual(429,code)
        self.assertEqual("local_agent_busy",body["error"])
        code,body=self.request("POST","/api/local-agent-run",
                               {"model":"fixture:model","prompt":"hello"})
        self.assertEqual(429,code)
        self.release.set()
        t.join(5)
        self.assertEqual(200,outcome[0][0])
        self.assertEqual(["fixture:model"],self.calls)


if __name__=="__main__":
    unittest.main()
