"""Slice 51: live probe endpoints follow owner authorization, opt-in and budget gates."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_operator_auth import LocalOperatorAuth
from local_ollama_agent import AgentError
from local_server import create_handler

PASSWORD="fixture_only_readiness_password_51"


class ReadinessHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.probes=[]
        self.models=[]
        self.entered=threading.Event()
        self.release=threading.Event()
        self.block=False
        def list_models():
            self.models.append("called")
            return ["fixture:model"]
        def probe(model):
            self.probes.append(model)
            self.entered.set()
            if self.block:self.release.wait(3)
            if model=="missing":
                raise AgentError("model_not_installed")
            if model=="bad":
                raise AgentError("invalid_model_name")
            if model=="offline":
                raise AgentError("local_model_unavailable")
            return {"status":"MODEL_RESPONDED","model":model,
                    "tool_calling":"OBSERVED","answer_present":True,
                    "history_saved":False,"external_dispatch_count":0}
        self.list_models=list_models
        self.probe=probe
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,local_agent_runner=lambda p,m:{
                "mode":"LOCAL_MODEL","answer":"fixture","tools_used":[],
                "external_dispatch_count":0},
                local_agent_models=list_models,local_agent_probe=probe))
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
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        h={"Content-Type":"application/json"}
        if auth:h["Cookie"]=self.cookie
        if csrf:h["X-AAS-CSRF"]=self.csrf
        if origin:h["Origin"]=origin
        c.request(method,path,body=(json.dumps(data).encode() if data is not None else None),
                  headers=h)
        r=c.getresponse()
        out=(r.status,json.loads(r.read()))
        c.close()
        return out

    def test_models_are_authenticated_and_return_filtered_names(self):
        self.assertEqual(401,self.request("GET","/api/local-agent-models",auth=False)[0])
        self.assertEqual([],self.models)
        code,data=self.request("GET","/api/local-agent-models")
        self.assertEqual(200,code)
        self.assertEqual(["fixture:model"],data["models"])
        self.assertEqual(0,data["external_dispatch_count"])
        self.assertEqual(["called"],self.models)
        _,caps=self.request("GET","/api/local-agent-capabilities")
        self.assertTrue(caps["readiness"])

    def test_probe_is_explicit_authenticated_and_csrf_bound(self):
        body={"model":"fixture:model"}
        self.assertEqual(401,self.request("POST","/api/local-agent-probe",body,auth=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-probe",body,csrf=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-probe",body,
                                         origin="http://evil.example")[0])
        self.assertEqual([],self.probes)
        code,data=self.request("POST","/api/local-agent-probe",body)
        self.assertEqual(200,code)
        self.assertEqual("OBSERVED",data["tool_calling"])
        self.assertFalse(data["history_saved"])
        self.assertEqual(["fixture:model"],self.probes)

    def test_disabled_probe_and_catalog_never_access_model(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,local_agent_runner=None))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=4)
            c.request("GET","/api/local-agent-models",headers={"Cookie":self.cookie})
            response=c.getresponse()
            self.assertEqual(403,response.status)
            response.read();c.close()
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=4)
            c.request("POST","/api/local-agent-probe",body='{"model":"fixture:model"}',
                      headers={"Cookie":self.cookie,"X-AAS-CSRF":self.csrf,
                               "Content-Type":"application/json"})
            resp=c.getresponse()
            self.assertEqual(403,resp.status)
            resp.read();c.close()
        finally:
            server.shutdown();server.server_close();worker.join(4)
        self.assertEqual([],self.probes)
        self.assertEqual([],self.models)

    def test_invalid_probe_requests_do_not_invoke_model(self):
        for data in ({},[],{"model":42},{"model":"fixture:model","extra":"payload"}):
            with self.subTest(value=data):
                self.assertEqual(422,self.request("POST","/api/local-agent-probe",data)[0])
        self.assertEqual([],self.probes)

    def test_probe_errors_are_stable_and_not_untrusted_content(self):
        for model,code in (("missing",409),("bad",422),("offline",503)):
            with self.subTest(model=model):
                status,data=self.request("POST","/api/local-agent-probe",
                                         {"model":model})
                self.assertEqual(code,status)
                self.assertNotIn("fixture_only_readiness_password",json.dumps(data))

    def test_probe_shares_single_in_flight_model_gate(self):
        self.block=True
        results=[]
        t=threading.Thread(target=lambda:results.append(
            self.request("POST","/api/local-agent-probe",{"model":"fixture:model"})))
        t.start()
        self.assertTrue(self.entered.wait(2))
        code,data=self.request("POST","/api/local-agent-probe",{"model":"fixture:model"})
        self.assertEqual(429,code)
        self.assertEqual("local_agent_busy",data["error"])
        code,_=self.request("POST","/api/local-agent-run",
                            {"model":"fixture:model","prompt":"hello"})
        self.assertEqual(429,code)
        self.release.set();t.join(5)
        self.assertEqual(200,results[0][0])
        self.assertEqual(1,len(self.probes))


if __name__=="__main__":
    unittest.main()
