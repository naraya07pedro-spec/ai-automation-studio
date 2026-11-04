"""Authenticated opt-in local-agent HTTP contract. No Ollama installation needed."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_server import create_handler
from local_operator_auth import LocalOperatorAuth

PASSWORD="fixture_only_operator_password_48"

class LocalAgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.spy=[]
        self.block=False
        self.entered=threading.Event()
        self.release=threading.Event()
        def runner(prompt, model):
            self.spy.append((prompt,model))
            self.entered.set()
            if self.block:self.release.wait(3)
            return {"mode":"LOCAL_MODEL","answer":"computed locally",
                    "tools_used":["calculate"],"external_dispatch_count":0}
        self.runner=runner
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,local_agent_runner=self.runner))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.release.set)
        self.addCleanup(self.thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, data=None, cookie=None, csrf=None):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        h={"Content-Type":"application/json"}
        if cookie:h["Cookie"]=cookie
        if csrf:h["X-AAS-CSRF"]=csrf
        payload=json.dumps(data).encode() if data is not None else None
        c.request(method,path,body=payload,headers=h)
        r=c.getresponse()
        result=(r.status,json.loads(r.read()))
        c.close()
        return result

    def credentials(self):
        status,data=self.request("POST","/api/operator-login",{"password":PASSWORD})
        self.assertEqual(200,status)
        # Extract from a second session through direct auth to avoid exposing
        # a session secret in application logs.
        _,s=self.auth.login(PASSWORD)
        return "aas_session="+s["cookie_token"],s["csrf_token"]

    def test_capabilities_require_auth(self):
        self.assertEqual(401,self.request("GET","/api/local-agent-capabilities")[0])
        cookie,_=self.credentials()
        code,res=self.request("GET","/api/local-agent-capabilities",cookie=cookie)
        self.assertEqual(200,code)
        self.assertTrue(res["enabled"])
        self.assertEqual(["calculate","utc_now"],res["tools"])

    def test_mutation_needs_auth_and_csrf(self):
        payload={"model":"qwen2.5:3b","prompt":"2+3"}
        self.assertEqual(401,self.request("POST","/api/local-agent-run",payload)[0])
        cookie,csrf=self.credentials()
        self.assertEqual(403,self.request("POST","/api/local-agent-run",payload,cookie)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-run",payload,cookie,"wrong")[0])
        self.assertEqual([],self.spy)
        code,result=self.request("POST","/api/local-agent-run",payload,cookie,csrf)
        self.assertEqual(200,code)
        self.assertEqual("computed locally",result["answer"])
        self.assertEqual([("2+3","qwen2.5:3b")],self.spy)

    def test_invalid_body_denied_before_runner(self):
        cookie,csrf=self.credentials()
        for payload in ({"model":"m"},["bad"],{"model":"m","prompt":"x","extra":1},
                        {"model":4,"prompt":"x"},{"model":"m","prompt":5}):
            with self.subTest(payload=payload):
                self.assertEqual(422,self.request("POST","/api/local-agent-run",payload,cookie,csrf)[0])
        self.assertEqual([],self.spy)

    def test_disabled_gate_never_calls_provider(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
             create_handler(operator_auth=self.auth,local_agent_runner=None))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            _,session=self.auth.login(PASSWORD)
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            conn.request("POST","/api/local-agent-run",
              body=json.dumps({"model":"m","prompt":"test"}),
              headers={"Content-Type":"application/json",
                       "Cookie":"aas_session="+session["cookie_token"],
                       "X-AAS-CSRF":session["csrf_token"]})
            resp=conn.getresponse()
            self.assertEqual(403,resp.status)
            self.assertEqual("local_agent_disabled",json.loads(resp.read())["error"])
            conn.close()
        finally:
            server.shutdown();server.server_close();thread.join(3)
        self.assertEqual([],self.spy)

    def test_simultaneous_second_request_is_rejected(self):
        cookie,csrf=self.credentials()
        self.block=True
        results=[]
        payload={"model":"m","prompt":"first"}
        t=threading.Thread(target=lambda:results.append(
            self.request("POST","/api/local-agent-run",payload,cookie,csrf)))
        t.start()
        self.assertTrue(self.entered.wait(2))
        code,data=self.request("POST","/api/local-agent-run",payload,cookie,csrf)
        self.assertEqual(429,code)
        self.assertEqual("local_agent_busy",data["error"])
        self.release.set();t.join(5)
        self.assertEqual(200,results[0][0])
        self.assertEqual(1,len(self.spy))

    def test_locked_session_refuses_agent(self):
        cookie,csrf=self.credentials()
        token=cookie.split("=",1)[1]
        self.auth.logout(token)
        self.assertEqual(401,self.request("POST","/api/local-agent-run",
                         {"model":"m","prompt":"test"},cookie,csrf)[0])
        self.assertEqual([],self.spy)

if __name__=="__main__":
    unittest.main()
