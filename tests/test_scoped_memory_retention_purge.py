"""Owner-controlled expired note purge, correct TTL privacy boundary."""
import base64
from datetime import datetime, timedelta, timezone
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_agent_profiles import LocalAgentProfiles
from local_scoped_agent_memory import LocalScopedAgentMemory, ScopedMemoryError
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

PASS="synthetic-scoped-purge-owner-pass-2026!"
KEY=base64.urlsafe_b64encode(b"L"*32).decode("ascii")

def profile(identifier):
    return {"agent_id":identifier,"name":"Test Agent","category":"research",
            "description":"Scoped tests","instructions":"Only reviewed tasks",
            "allowed_tools":["calculate","utc_now"]}

def note(note_id,retention=7,agent="research_agent"):
    return {"project_id":"test_project","agent_id":agent,"note_id":note_id,
            "text":"Sensitive owner context for "+note_id,
            "source_kind":"OWNER_NOTE","source_label":"owner",
            "retention_days":retention,"expected_revision":0}

class TTLDeletionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now=datetime(2026,11,1,tzinfo=timezone.utc)
        self.path=Path(self.tmp.name)/"scoped.sqlite3"
        self.store=LocalScopedAgentMemory(self.path,clock=lambda:self.now)
        self.addCleanup(self.store.close)

    def test_expired_text_hidden_without_purge_and_live_plan_revoked(self):
        first=self.store.put(note("expired_note"))
        self.store.put(note("active_note",retention=90))
        refs=self.store.select("test_project","research_agent",["expired_note"])[1]
        self.now+=timedelta(days=8)
        items=self.store.list("test_project","research_agent")
        expired=next(n for n in items if n["note_id"]=="expired_note")
        active=next(n for n in items if n["note_id"]=="active_note")
        self.assertTrue(expired["expired"])
        self.assertNotIn("text",expired)
        self.assertEqual(expired["revision"],first["revision"])
        self.assertIn("text",active)
        with self.assertRaisesRegex(ScopedMemoryError,"context_changed"):
            self.store.require_revisions(refs,{"agent_id":"research_agent"})
        outcome=self.store.purge_expired("test_project","research_agent")
        self.assertEqual(outcome["status"],"PURGED_EXPIRED_FROM_LIVE_LEDGER")
        self.assertEqual(outcome["deleted_count"],1)
        self.assertTrue(outcome["backup_copies_may_remain"])
        self.assertFalse(outcome["physical_erasure_verified"])
        remaining=self.store.list("test_project","research_agent")
        self.assertEqual([x["note_id"] for x in remaining],["active_note"])
        self.assertEqual(self.store.purge_expired("test_project","research_agent")["deleted_count"],0)
        self.store.put(note("expired_note"))
        with self.assertRaisesRegex(ScopedMemoryError,"context_changed"):
            self.store.require_revisions(refs,{"agent_id":"research_agent"})

    def test_never_purges_other_agent_or_project(self):
        self.store.put(note("one_note",agent="research_agent"))
        self.store.put(note("other_note",agent="review_agent"))
        self.now+=timedelta(days=8)
        self.assertEqual(self.store.purge_expired("test_project","research_agent")["deleted_count"],1)
        self.assertEqual(len(self.store.list("test_project","review_agent")),1)
        with self.assertRaisesRegex(ScopedMemoryError,"invalid_scoped_memory_scope"):
            self.store.purge_expired("../escape","review_agent")

    def test_server_reauthentication_csrf_and_exact_confirmation(self):
        tmp=Path(self.tmp.name)
        profiles=LocalAgentProfiles(tmp/"profiles.sqlite3")
        profiles.create(profile("research_agent"))
        self.addCleanup(profiles.close)
        self.store.put(note("ttl_note"))
        self.now+=timedelta(days=8)
        auth=LocalOperatorAuth.ephemeral_for_tests(PASS)
        audit=LocalSecurityAudit(tmp/"audit.sqlite3")
        self.addCleanup(audit.close)
        server=ThreadingHTTPServer(("127.0.0.1",0),create_handler(
            operator_auth=auth,security_audit=audit,
            agent_profiles=profiles,scoped_memory_store=self.store))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def req(method,route,body=None,cookie="",csrf=""):
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            headers={"Content-Type":"application/json"}
            if cookie: headers["Cookie"]=cookie
            if csrf: headers["X-AAS-CSRF"]=csrf
            conn.request(method,route,
                body=json.dumps(body).encode() if body is not None else None,
                headers=headers)
            response=conn.getresponse()
            out=response.status,json.loads(response.read())
            conn.close()
            return out

        payload={"project_id":"test_project","agent_id":"research_agent",
                 "confirm_expired_purge":"PURGE_EXPIRED_SCOPED_NOTES",
                 "current_password":PASS}
        path="/api/agent-scoped-memory-purge-expired"
        self.assertEqual(req("POST",path,payload)[0],401)
        status,auth_session=auth.login(PASS)
        self.assertEqual(status,200)
        cookie="aas_session="+auth_session["cookie_token"]
        csrf=auth_session["csrf_token"]
        self.assertEqual(req("POST",path,payload,cookie=cookie)[0],403)
        self.assertEqual(req("POST",path,{**payload,"confirm_expired_purge":"YES"},
            cookie=cookie,csrf=csrf)[0],422)
        self.assertEqual(req("POST",path,{**payload,"current_password":"incorrect"},
            cookie=cookie,csrf=csrf)[0],401)
        # Before any purge, an expired entry has no plaintext in GET.
        status,visible=req("GET","/api/agent-scoped-memory?project_id=test_project&agent_id=research_agent",cookie=cookie)
        self.assertEqual(status,200)
        self.assertTrue(visible["notes"][0]["expired"])
        self.assertNotIn("text",visible["notes"][0])
        status,result=req("POST",path,payload,cookie=cookie,csrf=csrf)
        self.assertEqual(status,200)
        self.assertEqual(result["deleted_count"],1)
        status,visible=req("GET","/api/agent-scoped-memory?project_id=test_project&agent_id=research_agent",cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(visible["notes"],[])
        self.assertIn("SCOPED_AGENT_MEMORY_CONFIG",
                      [x["action"] for x in audit.recent(50)])

@unittest.skipUnless(importlib.util.find_spec("cryptography"),
                     "PyCA is optional in default regression suite")
class EncryptedTTLDeletionContracts(unittest.TestCase):
    def test_expired_encrypted_text_redacted_and_purge_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[datetime(2026,11,1,tzinfo=timezone.utc)]
            path=Path(tmp)/"scoped.sqlite3"
            store=LocalScopedAgentMemory(path,clock=lambda:now[0],encryption_key=KEY)
            store.put(note("secret_note"))
            now[0]+=timedelta(days=8)
            expired=store.list("test_project","research_agent")[0]
            self.assertTrue(expired["expired"])
            self.assertNotIn("text",expired)
            self.assertEqual(store.purge_expired("test_project","research_agent")["deleted_count"],1)
            store.close()
            again=LocalScopedAgentMemory(path,encryption_key=KEY)
            self.assertEqual(again.list("test_project","research_agent"),[])
            again.close()

if __name__=="__main__":
    unittest.main()
