"""Slice 56 HTTP: owner-only transient selected-document inference."""
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
from local_document_analysis import analyze_local_document
from local_server import create_handler,main as server_main

PASSWORD="fixture_only_document_password_56"


class DocumentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.calls=[]
        self.started=threading.Event()
        self.release=threading.Event()
        self.pause=False
        def analyze(filename,content,question,model):
            self.calls.append((filename,content,question,model))
            self.started.set()
            if self.pause:self.release.wait(3)
            return analyze_local_document(filename,content,question,model,
                transport=lambda _:{"message":{"content":json.dumps({
                    "answer":"It starts at 09:00.",
                    "insufficient_evidence":False,
                    "citations":[{"chunk_id":"S1","quote":"starts at 09:00"}]
                })}})
        self.analyze=analyze
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                local_agent_runner=lambda prompt,model:{
                    "mode":"LOCAL_MODEL","answer":"mock","tools_used":[],
                    "external_dispatch_count":0},
                local_document_analyzer=analyze))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.release.set)
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,method,path,body=None,authenticated=True,csrf=True,origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if authenticated:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        conn.request(method,path,body=json.dumps(body).encode() if body is not None else None,
                     headers=headers)
        resp=conn.getresponse()
        output=resp.status,json.loads(resp.read())
        conn.close()
        return output

    def payload(self):
        return {"filename":"notes.txt","content":"Service starts at 09:00 on Monday.",
                "question":"When does service start?","model":"fixture:model"}

    def test_owner_csrf_same_origin_and_transient_answer(self):
        body=self.payload()
        self.assertEqual(401,self.request("POST","/api/local-agent-document-analyze",
                                          body,authenticated=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-document-analyze",
                                          body,csrf=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-document-analyze",
                                          body,origin="http://evil.example")[0])
        self.assertEqual([],self.calls)
        code,data=self.request("POST","/api/local-agent-document-analyze",body)
        self.assertEqual(200,code)
        self.assertEqual("DOCUMENT_ANALYZED_LOCAL",data["status"])
        self.assertFalse(data["history_saved"])
        self.assertEqual("starts at 09:00",data["citations"][0]["quote"])
        self.assertEqual(0,data["external_write_count"])
        self.assertEqual(1,len(self.calls))
        code,caps=self.request("GET","/api/local-agent-capabilities")
        self.assertEqual(200,code)
        self.assertTrue(caps["documents"])

    def test_no_flag_means_no_model_call(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                local_agent_runner=lambda prompt,model:None))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            conn.request("POST","/api/local-agent-document-analyze",
                body=json.dumps(self.payload()),
                headers={"Content-Type":"application/json",
                         "Cookie":self.cookie,"X-AAS-CSRF":self.csrf})
            resp=conn.getresponse()
            self.assertEqual(403,resp.status)
            resp.read();conn.close()
        finally:
            server.shutdown();server.server_close();worker.join(4)
        self.assertEqual([],self.calls)

    def test_strict_body_shapes_fail_before_inference(self):
        for payload in ({},{"filename":"notes.txt"},["x"],{"model":1},
                        {**self.payload(),"unexpected":"x"}):
            with self.subTest(payload=str(payload)[:50]):
                code,_=self.request("POST","/api/local-agent-document-analyze",payload)
                self.assertEqual(422,code)
        self.assertEqual([],self.calls)
        code,data=self.request("POST","/api/local-agent-document-analyze",
                   {**self.payload(),"content":"x"*6001})
        self.assertEqual(413,code)
        self.assertEqual([],self.calls)

    def test_model_gate_blocks_concurrent_chat(self):
        self.pause=True
        result=[]
        t=threading.Thread(target=lambda:result.append(
            self.request("POST","/api/local-agent-document-analyze",self.payload())))
        t.start()
        self.assertTrue(self.started.wait(2))
        code,data=self.request("POST","/api/local-agent-document-analyze",self.payload())
        self.assertEqual(429,code)
        self.assertEqual("local_agent_busy",data["error"])
        code,_=self.request("POST","/api/local-agent-run",
             {"model":"fixture:model","prompt":"hello"})
        self.assertEqual(429,code)
        self.release.set()
        t.join(5)
        self.assertEqual(200,result[0][0])
        self.assertEqual(1,len(self.calls))

    def test_cli_refuses_documents_without_local_model_opt_in(self):
        with self.assertRaises(SystemExit) as e:
            server_main(["--enable-local-documents"])
        self.assertNotEqual(0,e.exception.code)

if __name__=="__main__":
    unittest.main()
