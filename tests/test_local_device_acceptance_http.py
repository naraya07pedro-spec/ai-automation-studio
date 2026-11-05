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
from local_document_analysis import LocalDocumentError
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
            if model == "fixture:timeout":
                raise AgentError("local_model_timeout")
            if model == "fixture:disconnect":
                raise AgentError("local_model_transport_uncertain")
            if model == "fixture:wrong-time":
                raise LocalDocumentError("multi_document_numeric_evidence_missing")
            if self.pause:self.release.wait(3)
            return smoke(model,catalog=lambda:["fixture:model"],
                         transport=fixture_model,memory=lambda:None)
        self.preflights=[]
        def fake_preflight():
            self.preflights.append(True)
            return {"status":"MODEL_SELECTION_REQUIRED",
                    "source":"LOCAL_OLLAMA_HOST_PREFLIGHT",
                    "host_os":"Windows","python_version":"3.12.0",
                    "selected_model":None,"ollama_reachable":True,
                    "installed_models":["fixture:model"],"installed_model_count":1,
                    "selected_model_installed":False,
                    "system_ram":{"total_ram_mb":8192,
                                  "available_ram_mb":1520,"memory_load_pct":80},
                    "memory_note":"System-wide RAM snapshot, not model usage.",
                    "next_action":"Choose an installed model",
                    "live_model_inference_attempted":False,
                    "history_saved":False,"external_write_count":0}
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                local_agent_runner=lambda prompt,model:{
                    "mode":"LOCAL_MODEL","answer":"fixture",
                    "tools_used":[],"external_dispatch_count":0},
                local_device_acceptance=acceptance,
                local_device_preflight=fake_preflight))
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

    def test_host_preflight_is_authenticated_read_only_and_no_model_call(self):
        path="/api/local-agent-device-preflight"
        self.assertEqual(401,self.request("GET",path,auth=False)[0])
        self.assertEqual([],self.preflights)
        self.assertEqual(422,self.request("GET",path+"?dump=secret")[0])
        self.assertEqual([],self.preflights)
        # Read-only GET does not require CSRF, but always requires owner login.
        code,result=self.request("GET",path,csrf=False)
        self.assertEqual(200,code)
        self.assertEqual("MODEL_SELECTION_REQUIRED",result["status"])
        self.assertEqual(["fixture:model"],result["installed_models"])
        self.assertEqual(1520,result["system_ram"]["available_ram_mb"])
        self.assertFalse(result["live_model_inference_attempted"])
        self.assertEqual(0,result["external_write_count"])
        self.assertEqual([],self.calls)
        self.assertEqual([True],self.preflights)
        self.assertTrue(self.request("GET","/api/local-agent-capabilities")[1][
            "device_preflight"])

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

    def test_timeout_is_503_with_stable_diagnostic_and_no_retry(self):
        code,body=self.request("POST","/api/local-agent-device-acceptance",
                               {"model":"fixture:timeout"})
        self.assertEqual(503,code)
        self.assertEqual("local_model_timeout",body["error"])
        self.assertEqual(["fixture:timeout"],self.calls)

    def test_wrong_numeric_answer_returns_502_not_fake_model_success(self):
        code,body=self.request("POST","/api/local-agent-device-acceptance",
                               {"model":"fixture:wrong-time"})
        self.assertEqual(502,code)
        self.assertEqual("multi_document_numeric_evidence_missing",body["error"])
        self.assertEqual(["fixture:wrong-time"],self.calls)

    def test_timeout_quarantines_all_model_inference_until_owner_ack(self):
        endpoint="/api/local-agent-device-acceptance"
        code,body=self.request("POST",endpoint,{"model":"fixture:timeout"})
        self.assertEqual(503,code)
        self.assertEqual("local_model_timeout",body["error"])
        self.assertEqual(["fixture:timeout"],self.calls)

        # No inference dispatch while backend state is uncertain.
        code,body=self.request("POST",endpoint,{"model":"fixture:model"})
        self.assertEqual(423,code)
        self.assertEqual("local_ollama_timeout_unresolved",body["error"])
        self.assertEqual(["fixture:timeout"],self.calls)
        code,body=self.request("POST","/api/local-agent-run",
                               {"model":"fixture:model","prompt":"hi"})
        self.assertEqual(423,code)
        self.assertEqual("local_ollama_timeout_unresolved",body["error"])

        status_url="/api/local-agent-timeout-status"
        self.assertEqual(401,self.request("GET",status_url,auth=False)[0])
        self.assertEqual(422,self.request("GET",status_url+"?x=1")[0])
        code,state=self.request("GET",status_url,csrf=False)
        self.assertEqual(200,code)
        self.assertTrue(state["timeout_unresolved"])
        self.assertFalse(state["backend_stopped_verified"])
        self.assertIn("Inspect Ollama",state["next_action"])

        ack="/api/local-agent-timeout-acknowledge"
        payload={"acknowledge":"OLLAMA_INSPECTED"}
        self.assertEqual(401,self.request("POST",ack,payload,auth=False)[0])
        self.assertEqual(403,self.request("POST",ack,payload,csrf=False)[0])
        self.assertEqual(403,self.request("POST",ack,payload,
                                         origin="http://attacker.invalid")[0])
        self.assertEqual(422,self.request("POST",ack,{})[0])
        self.assertEqual(422,self.request("POST",ack,{"acknowledge":"yes"})[0])
        self.assertEqual(["fixture:timeout"],self.calls)
        self.assertTrue(self.request("GET",status_url)[1]["timeout_unresolved"])

        code,ack_result=self.request("POST",ack,payload)
        self.assertEqual(200,code)
        self.assertEqual("OPERATOR_ACKNOWLEDGED",ack_result["status"])
        self.assertFalse(ack_result["backend_stopped_verified"])
        self.assertEqual(0,ack_result["model_calls"])
        self.assertFalse(self.request("GET",status_url)[1]["timeout_unresolved"])
        self.assertEqual(["fixture:timeout"],self.calls)
        self.assertEqual(200,self.request("POST",endpoint,
                                          {"model":"fixture:model"})[0])
        self.assertEqual(["fixture:timeout","fixture:model"],self.calls)

    def test_midstream_transport_failure_quarantines_without_retry(self):
        endpoint="/api/local-agent-device-acceptance"
        code,body=self.request("POST",endpoint,{"model":"fixture:disconnect"})
        self.assertEqual(503,code)
        self.assertEqual("local_model_transport_uncertain",body["error"])
        self.assertEqual(["fixture:disconnect"],self.calls)

        code,state=self.request("GET","/api/local-agent-timeout-status")
        self.assertEqual(200,code)
        self.assertEqual("UNCERTAIN",state["checkpoint_state"])
        self.assertTrue(state["timeout_unresolved"])
        self.assertFalse(state["backend_stopped_verified"])

        code,body=self.request("POST",endpoint,{"model":"fixture:model"})
        self.assertEqual(423,code)
        self.assertEqual("local_ollama_timeout_unresolved",body["error"])
        self.assertEqual(["fixture:disconnect"],self.calls)
        code,body=self.request("POST","/api/local-agent-run",
                               {"model":"fixture:model","prompt":"hi"})
        self.assertEqual(423,code)
        self.assertEqual(["fixture:disconnect"],self.calls)

        code,ack=self.request("POST","/api/local-agent-timeout-acknowledge",
                              {"acknowledge":"OLLAMA_INSPECTED"})
        self.assertEqual(200,code)
        self.assertFalse(ack["backend_stopped_verified"])
        self.assertEqual(0,ack["model_calls"])
        self.assertEqual(200,self.request("POST",endpoint,
                                         {"model":"fixture:model"})[0])
        self.assertEqual(["fixture:disconnect","fixture:model"],self.calls)

    def test_timeout_ack_denied_while_active_request_in_flight(self):
        self.pause=True
        replies=[]
        runner=threading.Thread(target=lambda:replies.append(self.request(
            "POST","/api/local-agent-device-acceptance",{"model":"fixture:model"})))
        runner.start()
        self.assertTrue(self.entered.wait(2))
        code,body=self.request("POST","/api/local-agent-timeout-acknowledge",
                               {"acknowledge":"OLLAMA_INSPECTED"})
        self.assertEqual(429,code)
        self.assertEqual("local_agent_busy",body["error"])
        self.release.set()
        runner.join(5)
        self.assertEqual(200,replies[0][0])

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
