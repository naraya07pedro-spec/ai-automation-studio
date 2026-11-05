"""Slice 57 HTTP: owner-selected multi-file retrieval never bypasses validation."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_multi_document_analysis import analyze_multiple_documents
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler,main as server_main

PASSWORD="fixture_only_multi_document_password_57"
QUOTE="meeting starts at 18:30 WIB"


class MultiDocumentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.calls=[]
        self.entered=threading.Event()
        self.release=threading.Event()
        self.pause=False
        def analyze(documents,question,model):
            self.calls.append((documents,question,model))
            self.entered.set()
            if self.pause:
                self.release.wait(3)
            return analyze_multiple_documents(
                documents,question,model,
                transport=lambda _:{"message":{"content":json.dumps({
                    "answer":"The review starts at 18:30 WIB.",
                    "insufficient_evidence":False,
                    "citations":[{"chunk_id":"F2C1","quote":QUOTE}]})}})
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda prompt,model:{
                               "mode":"LOCAL_MODEL","answer":"test",
                               "tools_used":[],"external_dispatch_count":0},
                           local_multi_document_analyzer=analyze))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.release.set)
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def payload(self):
        return {"documents":[
            {"filename":"policy.txt","content":"Workflow rules require review."},
            {"filename":"meeting.md","content":
             "Maintenance review meeting starts at 18:30 WIB every Thursday."}],
            "question":"What time does the maintenance review meeting start?",
            "model":"fixture:model"}

    def request(self,method,path,data=None,auth=True,csrf=True,origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        conn.request(method,path,
                     body=json.dumps(data).encode() if data is not None else None,
                     headers=headers)
        response=conn.getresponse()
        answer=(response.status,json.loads(response.read()))
        conn.close()
        return answer

    def test_owner_and_csrf_origin_enforced_before_model(self):
        body=self.payload()
        url="/api/local-agent-multi-document-analyze"
        self.assertEqual(401,self.request("POST",url,body,auth=False)[0])
        self.assertEqual(403,self.request("POST",url,body,csrf=False)[0])
        self.assertEqual(403,self.request("POST",url,body,
                              origin="http://malicious.example")[0])
        self.assertEqual([],self.calls)
        status,result=self.request("POST",url,body)
        self.assertEqual(200,status)
        self.assertEqual("MULTI_DOCUMENT_ANALYZED_LOCAL",result["status"])
        self.assertEqual("meeting.md",result["citations"][0]["filename"])
        self.assertEqual(QUOTE,result["citations"][0]["quote"])
        self.assertFalse(result["history_saved"])
        self.assertEqual(0,result["external_write_count"])
        self.assertEqual(1,len(self.calls))
        self.assertTrue(self.request("GET","/api/local-agent-capabilities")[1]["multi_documents"])

    def test_fail_closed_if_feature_is_not_enabled(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda prompt,model:None))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            c.request("POST","/api/local-agent-multi-document-analyze",
                      body=json.dumps(self.payload()),
                      headers={"Content-Type":"application/json",
                               "X-AAS-CSRF":self.csrf,"Cookie":self.cookie})
            resp=c.getresponse()
            self.assertEqual(403,resp.status)
            resp.read();c.close()
        finally:
            server.shutdown();server.server_close();worker.join(4)
        self.assertEqual([],self.calls)

    def test_invalid_request_rejected_before_any_callback(self):
        url="/api/local-agent-multi-document-analyze"
        bad=[
            {},{"documents":[],"question":"a","model":"fixture:model"},
            {"documents":[self.payload()["documents"][0]],
             "question":"a","model":"fixture:model"},
            {**self.payload(),"unexpected":1},
            {**self.payload(),"documents":[
               {"filename":"policy.txt","content":"a"},
               {"filename":"POLICY.TXT","content":"b"}]},
            {**self.payload(),"documents":[
               self.payload()["documents"][0],
               {"filename":"meeting.md","content":"x"*2501}]},
            {**self.payload(),"documents":[
               self.payload()["documents"][0],
               {"filename":"../../secret.txt","content":"data"}]},
            {**self.payload(),"model":42},
        ]
        for item in bad:
            with self.subTest(body=str(item)[:60]):
                status,_=self.request("POST",url,item)
                self.assertIn(status,(413,422))
        self.assertEqual([],self.calls)

    def test_global_model_gate_denies_parallel_requests(self):
        self.pause=True
        completed=[]
        body=self.payload()
        t=threading.Thread(target=lambda:completed.append(self.request(
            "POST","/api/local-agent-multi-document-analyze",body)))
        t.start()
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(429,self.request("POST",
            "/api/local-agent-multi-document-analyze",body)[0])
        self.assertEqual(429,self.request("POST","/api/local-agent-run",
            {"model":"fixture:model","prompt":"hello"})[0])
        self.release.set()
        t.join(5)
        self.assertEqual(200,completed[0][0])
        self.assertEqual(1,len(self.calls))

    def test_flag_requires_local_agent(self):
        with self.assertRaises(SystemExit) as caught:
            server_main(["--enable-multi-documents"])
        self.assertNotEqual(0,caught.exception.code)


if __name__=="__main__":
    unittest.main()
