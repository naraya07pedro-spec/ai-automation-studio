"""Slice 50: migration, thread isolation, bounds and authenticated HTTP."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_conversation import LocalAgentConversation, ConversationError
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler

PASSWORD = "fixture_only_conversation_password_50"


class ThreadStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "thread-history.sqlite3"

    def test_non_destructive_v1_migration_and_restart(self):
        db = sqlite3.connect(self.path)
        with db:
            db.execute("""CREATE TABLE agent_conversation(
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,prompt TEXT NOT NULL,answer TEXT NOT NULL,
                CHECK(length(prompt)<=3000),CHECK(length(answer)<=3000))""")
            db.execute("INSERT INTO agent_conversation(model,prompt,answer) VALUES(?,?,?)",
                       ("old-model","legacy-prompt","legacy-answer"))
        db.close()
        store = LocalAgentConversation(self.path)
        self.assertEqual("legacy-prompt", store.snapshot()[0]["prompt"])
        self.assertEqual(["default"], store.list_conversations())
        store.append("new-model","new-prompt","new-answer","new_thread")
        store.close()
        again = LocalAgentConversation(self.path)
        try:
            self.assertEqual("legacy-answer", again.snapshot("default")[0]["answer"])
            self.assertEqual("new-answer", again.snapshot("new_thread")[0]["answer"])
            self.assertEqual(2,len(again.list_conversations()))
        finally:
            again.close()

    def test_bounded_per_thread_not_global(self):
        store=LocalAgentConversation(self.path)
        try:
            for i in range(9):
                store.append("m",str(i),"reply"+str(i),"work")
            for i in range(3):
                store.append("m","personal"+str(i),"private","personal")
            self.assertEqual(6,len(store.snapshot("work")))
            self.assertEqual("3",store.snapshot("work")[0]["prompt"])
            self.assertEqual(3,len(store.snapshot("personal")))
            self.assertEqual([],store.snapshot("default"))
            self.assertEqual(["personal","work"],store.list_conversations())
        finally:store.close()

    def test_selected_delete_keeps_other_ids(self):
        store=LocalAgentConversation(self.path)
        try:
            store.append("m","a","A","alpha")
            store.append("m","b","B","beta")
            store.clear("alpha")
            self.assertEqual([],store.snapshot("alpha"))
            self.assertEqual("B",store.snapshot("beta")[0]["answer"])
            store.clear()
            self.assertEqual([],store.snapshot("beta"))
        finally:store.close()

    def test_eight_thread_cap_is_fail_closed_without_eviction(self):
        store=LocalAgentConversation(self.path)
        try:
            for i in range(8):
                store.append("m","x","y","thread"+str(i))
            with self.assertRaisesRegex(ConversationError,"conversation_capacity_reached"):
                store.append("m","x","y","thread8")
            store.append("m","more","more","thread1")
            self.assertEqual(8,len(store.list_conversations()))
            store.clear("thread0")
            store.append("m","x","y","thread8")
            self.assertEqual(8,len(store.list_conversations()))
        finally:store.close()

    def test_ids_reject_dangerous_and_ambiguous_values(self):
        store=LocalAgentConversation(self.path)
        try:
            for bad in (None,"","UPPER","../secret","a b","x"*33,
                        "a' OR 1=1 --",True,{}, "é", "a/b", "_invalid"):
                with self.subTest(value=bad),self.assertRaisesRegex(
                    ConversationError,"invalid_conversation_id"):
                    store.snapshot(bad)
                if bad is not None:
                    with self.assertRaises(ConversationError):
                        store.clear(bad)
                with self.assertRaises(ConversationError):
                    store.append("m","p","a",bad)
            self.assertEqual([],store.list_conversations())
        finally:store.close()


class ThreadHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=LocalAgentConversation(Path(self.tmp.name)/"storage.sqlite3")
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.calls=[]
        def runner(prompt, model, history=None):
            self.calls.append((prompt,model,history))
            return {"mode":"LOCAL_MODEL","answer":"response:"+prompt,
                    "tools_used":[],"external_dispatch_count":0}
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
                    create_handler(operator_auth=self.auth,local_agent_runner=runner,
                                   agent_conversation=self.store))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login(PASSWORD)
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,method,path,payload=None,auth=True,csrf=True):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        conn.request(method,path,body=(json.dumps(payload).encode()
                    if payload is not None else None),headers=headers)
        res=conn.getresponse()
        result=(res.status,json.loads(res.read()))
        conn.close()
        return result

    def run_thread(self,id,prompt,model="m"):
        return self.request("POST","/api/local-agent-run",
                            {"model":model,"prompt":prompt,"remember":True,
                             "conversation_id":id})

    def test_live_context_partition_and_per_thread_model_pin(self):
        self.assertEqual(200,self.run_thread("work","work-1")[0])
        self.assertEqual(200,self.run_thread("home","home-1")[0])
        self.assertEqual(200,self.run_thread("work","work-2")[0])
        self.assertEqual("work-1",self.calls[-1][2][0]["prompt"])
        self.assertNotIn("home-1",str(self.calls[-1][2]))
        self.assertEqual(409,self.run_thread("home","home-2","another")[0])
        self.assertEqual(200,self.run_thread("home","home-2")[0])
        self.assertEqual("home-1",self.calls[-1][2][0]["prompt"])
        self.assertEqual(2,len(self.store.snapshot("work")))
        self.assertEqual(2,len(self.store.snapshot("home")))

    def test_history_and_list_require_owner(self):
        self.run_thread("team","synthetic")
        self.assertEqual(401,self.request("GET","/api/local-agent-conversations",
                                         auth=False)[0])
        self.assertEqual(401,self.request("GET","/api/local-agent-history?conversation_id=team",
                                         auth=False)[0])
        code,payload=self.request("GET","/api/local-agent-conversations")
        self.assertEqual(200,code)
        self.assertIn("team",payload["conversation_ids"])
        code,payload=self.request("GET","/api/local-agent-history?conversation_id=team")
        self.assertEqual(200,code)
        self.assertEqual("synthetic",payload["turns"][0]["prompt"])

    def test_selective_delete_requires_csrf(self):
        self.run_thread("thread1","a")
        self.run_thread("thread2","b")
        delete={"conversation_id":"thread1"}
        self.assertEqual(401,self.request("POST","/api/local-agent-clear",
                                         delete,auth=False)[0])
        self.assertEqual(403,self.request("POST","/api/local-agent-clear",
                                         delete,csrf=False)[0])
        self.assertEqual(200,self.request("POST","/api/local-agent-clear",delete)[0])
        self.assertEqual([],self.store.snapshot("thread1"))
        self.assertEqual("b",self.store.snapshot("thread2")[0]["prompt"])
        self.assertEqual(200,self.request("POST","/api/local-agent-clear",{})[0])
        self.assertEqual([],self.store.list_conversations())

    def test_rejected_identifiers_never_reach_model(self):
        cases=[{"model":"m","prompt":"p","remember":True,
                "conversation_id":"../x"},
               {"model":"m","prompt":"p","remember":False,
                "conversation_id":"default"},
               {"model":"m","prompt":"p","conversation_id":"default"},
               {"model":"m","prompt":"p","remember":True,
                "conversation_id":False}]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(422,self.request("POST","/api/local-agent-run",payload)[0])
        self.assertEqual([],self.calls)
        for q in ("conversation_id=../x","conversation_id=a&conversation_id=b",
                  "unexpected=1","conversation_id="):
            with self.subTest(q=q):
                self.assertEqual(422,self.request(
                    "GET","/api/local-agent-history?"+q)[0])

    def test_capacity_denied_before_model_request(self):
        for i in range(8):
            self.assertEqual(200,self.run_thread("id"+str(i),"x")[0])
        old=len(self.calls)
        code,result=self.run_thread("id8","x")
        self.assertEqual(409,code)
        self.assertEqual("conversation_capacity_reached",result["error"])
        self.assertEqual(old,len(self.calls))

if __name__ == "__main__":
    unittest.main()
