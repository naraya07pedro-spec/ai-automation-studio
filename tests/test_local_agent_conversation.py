"""Slice 49: explicit and bounded plaintext local history. No real model traffic."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_agent_conversation import LocalAgentConversation, ConversationError
from local_ollama_agent import run_agent, AgentError
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler

PASSWORD="fixture_only_history_password_49"

class ConversationStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"local.sqlite3"
        self.store=LocalAgentConversation(self.path)
        self.addCleanup(self.store.close)

    def test_persisted_and_bounded_after_reopen(self):
        for i in range(9):
            self.store.append("model",str(i), "answer"+str(i))
        self.assertEqual(6,len(self.store.snapshot()))
        self.assertEqual("3",self.store.snapshot()[0]["prompt"])
        other=LocalAgentConversation(self.path)
        try:self.assertEqual(self.store.snapshot(),other.snapshot())
        finally:other.close()

    def test_clear_removes_all_turns_on_disk(self):
        self.store.append("model","a","b")
        self.store.clear()
        self.assertEqual([],self.store.snapshot())
        other=LocalAgentConversation(self.path)
        try:self.assertEqual([],other.snapshot())
        finally:other.close()

    def test_malformed_or_oversized_not_saved(self):
        for model,prompt,answer in (("m","","a"),("m","x",""),("m","p","x"*3001),
                                    ("m","p"*3001,"a"),("", "p","a")):
            with self.subTest(prompt=prompt[:8]),self.assertRaises(ConversationError):
                self.store.append(model,prompt,answer)
        self.assertEqual([],self.store.snapshot())

    def test_agent_replays_user_assistant_but_never_prior_tools(self):
        payloads=[]
        def fake(payload):
            payloads.append(payload)
            return {"message":{"content":"complete"}}
        previous=[{"model":"m","prompt":"old instruction","answer":"prior answer"}]
        run_agent("new request","m",fake,history=previous)
        roles=[p["role"] for p in payloads[0]["messages"]]
        self.assertEqual(["system","user","assistant","user"],roles)
        self.assertNotIn("tool",roles)

    def test_reject_invalid_or_mismatched_context_pre_transport(self):
        def blocked(_):raise AssertionError("should not reach model")
        for history in ([[{"model":"other","prompt":"p","answer":"a"}]],
                        [{"model":"m","prompt":"p","answer":""}],
                        [{"model":"m","prompt":"p","answer":"a","extra":1}],
                        [{"model":"m","prompt":"p","answer":"a"}]*7):
            with self.subTest(history=str(history)[:30]),self.assertRaises(AgentError):
                run_agent("hi","m",blocked,history=history)

class ConversationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=LocalAgentConversation(Path(self.tmp.name)/"history.sqlite3")
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.calls=[]
        def fake(prompt,model,history=None):
            self.calls.append((prompt,model,history))
            return {"mode":"LOCAL_MODEL","answer":"answer #"+str(len(self.calls)),
                    "tools_used":[],"external_dispatch_count":0}
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
                    create_handler(operator_auth=self.auth,local_agent_runner=fake,
                                   agent_conversation=self.store))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self, method,path,payload=None,auth=True,csrf=True):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        conn.request(method,path,body=json.dumps(payload).encode() if payload is not None else None,
                     headers=headers)
        response=conn.getresponse()
        code=response.status
        data=json.loads(response.read())
        conn.close()
        return code,data

    def test_opt_in_and_context_progression(self):
        base={"model":"m","prompt":"first"}
        self.assertEqual(200,self.request("POST","/api/local-agent-run",base)[0])
        self.assertEqual([],self.store.snapshot())
        self.assertIsNone(self.calls[0][2])
        base["remember"]=True
        self.assertEqual(200,self.request("POST","/api/local-agent-run",base)[0])
        self.assertEqual(1,len(self.store.snapshot()))
        base["prompt"]="second"
        self.assertEqual(200,self.request("POST","/api/local-agent-run",base)[0])
        self.assertEqual("first",self.calls[-1][2][0]["prompt"])
        self.assertEqual(2,len(self.store.snapshot()))

    def test_history_and_clear_require_auth_and_csrf(self):
        self.store.append("m","p","a")
        self.assertEqual(401,self.request("GET","/api/local-agent-history",auth=False)[0])
        self.assertEqual(401,self.request("POST","/api/local-agent-clear",{},auth=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-clear",{},csrf=False)[0])
        self.assertEqual(1,len(self.store.snapshot()))
        self.assertEqual(200,self.request("GET","/api/local-agent-history")[0])
        self.assertEqual(200,self.request("POST","/api/local-agent-clear",{})[0])
        self.assertEqual([],self.store.snapshot())

    def test_model_switch_requires_clear(self):
        self.store.append("old","p","a")
        code,result=self.request("POST","/api/local-agent-run",
            {"model":"new","prompt":"p","remember":True})
        self.assertEqual(409,code)
        self.assertEqual("agent_history_model_mismatch",result["error"])
        self.assertEqual([],self.calls)

    def test_invalid_remember_flag_denied(self):
        code,_=self.request("POST","/api/local-agent-run",
            {"model":"m","prompt":"p","remember":"yes"})
        self.assertEqual(422,code)
        self.assertEqual([],self.calls)

    def test_snapshot_never_contains_session(self):
        self.store.append("m","p","a")
        data=self.request("GET","/api/local-agent-history")[1]
        self.assertEqual("local_plaintext",data["storage"])
        self.assertNotIn(self.csrf,json.dumps(data))
        self.assertNotIn(self.cookie,json.dumps(data))

if __name__=="__main__":
    unittest.main()
