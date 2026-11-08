"""Stage #128: owner-scoped Agent/project memory with real SQLite and HTTP gates.

Provider model callbacks are fixtures; no paid model or owner-PC calls occur.
"""
from datetime import datetime, timedelta, timezone
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
from local_agent_profiles import LocalAgentProfiles
from local_scoped_agent_memory import LocalScopedAgentMemory, ScopedMemoryError
from local_agent_task_plans import LocalAgentPlanStore, PlanError
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

PASSWORD = "Test-stage128-long-owner-password-2026!"
PROFILE = {
    "agent_id":"research_agent",
    "name":"Research",
    "category":"research",
    "description":"Research selected data",
    "instructions":"Only use given context",
    "allowed_tools":["calculate","utc_now"],
}
NOTE = {
    "project_id":"launch_project",
    "agent_id":"research_agent",
    "note_id":"baseline_note",
    "text":"Owner note: 7 multiplied by 8. Do not send email.",
    "source_kind":"OWNER_NOTE",
    "source_label":"owner typed note",
    "retention_days":30,
    "expected_revision":0,
}
PLAN = {"goal":"Use selected read-only memory for a short calculation",
        "model":"fixture:model",
        "steps":[{"tool":"calculate","args":{
            "a":"7","b":"8","operation":"multiply"}},
            {"tool":"utc_now","args":{}}]}


class ScopedMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.now=datetime(2026,11,8,7,0,tzinfo=timezone.utc)
        self.store=LocalScopedAgentMemory(self.folder/"scoped.sqlite3",
                                           clock=lambda:self.now)
        self.addCleanup(self.store.close)

    def test_create_restart_provenance_scope_and_revision(self):
        original=self.store.put(NOTE)
        self.assertEqual(original["revision"],1)
        self.assertFalse(original["expired"])
        self.assertEqual(original["source_kind"],"OWNER_NOTE")
        self.assertEqual(len(self.store.list("launch_project","research_agent")),1)
        self.assertEqual(self.store.list("other_project","research_agent"),[])
        self.assertEqual(self.store.list("launch_project","review_agent"),[])
        context,refs=self.store.select("launch_project","research_agent",
                                        ["baseline_note"])
        self.assertEqual(context,[{"id":"baseline_note","text":NOTE["text"]}])
        self.assertTrue(self.store.require_revisions(refs,
                       {"agent_id":"research_agent","revision":1}))
        self.assertEqual(self.store.db.execute("PRAGMA secure_delete").fetchone()[0],1)
        other=LocalScopedAgentMemory(self.folder/"scoped.sqlite3",
                                      clock=lambda:self.now)
        self.addCleanup(other.close)
        self.assertEqual(other.select("launch_project","research_agent",
                                      ["baseline_note"])[1],refs)
        updated=other.put({**NOTE,"text":"Revised instruction",
                           "expected_revision":1})
        self.assertEqual(updated["revision"],2)
        with self.assertRaisesRegex(ScopedMemoryError,"context_changed"):
            self.store.require_revisions(refs,
                       {"agent_id":"research_agent","revision":1})
        with self.assertRaisesRegex(ScopedMemoryError,"revision_conflict"):
            other.put({**NOTE,"expected_revision":1})
        self.assertEqual(self.store.list("launch_project","research_agent")[0]
                         ["text"],"Revised instruction")

    def test_expiry_is_fail_closed_without_silent_retention_extension(self):
        self.store.put({**NOTE,"retention_days":7})
        _,refs=self.store.select("launch_project","research_agent",
                                  ["baseline_note"])
        self.now+=timedelta(days=8)
        with self.assertRaisesRegex(ScopedMemoryError,"expired"):
            self.store.select("launch_project","research_agent",["baseline_note"])
        with self.assertRaisesRegex(ScopedMemoryError,"context_changed"):
            self.store.require_revisions(refs,
                       {"agent_id":"research_agent","revision":1})
        self.assertTrue(self.store.list("launch_project","research_agent")[0]["expired"])
        with self.assertRaisesRegex(ScopedMemoryError,"revision_conflict"):
            self.store.put({**NOTE,"expected_revision":1})
        self.assertEqual(self.store.delete("launch_project","research_agent",
                                         "baseline_note",1)["status"],
                         "DELETED_FROM_LIVE_LEDGER")

    def test_delete_recreate_same_text_and_revision_never_revives_old_ref(self):
        original=self.store.put(NOTE)
        _,refs=self.store.select("launch_project","research_agent",["baseline_note"])
        self.store.delete("launch_project","research_agent","baseline_note",1)
        fresh=self.store.put(NOTE)
        self.assertNotEqual(original["generation"],fresh["generation"])
        self.assertEqual(original["digest"],fresh["digest"])
        with self.assertRaisesRegex(ScopedMemoryError,"context_changed"):
            self.store.require_revisions(refs,
                       {"agent_id":"research_agent","revision":1})
        self.assertTrue(self.store.require_revisions(
            self.store.select("launch_project","research_agent",
                              ["baseline_note"])[1],
            {"agent_id":"research_agent","revision":1}))

    def test_cross_agent_or_project_scope_denied_without_text(self):
        self.store.put(NOTE)
        _,refs=self.store.select("launch_project","research_agent",["baseline_note"])
        for agent in ["other_agent", "", "../escape"]:
            with self.subTest(agent=agent),self.assertRaises(ScopedMemoryError):
                self.store.require_revisions(refs,
                     {"agent_id":agent,"revision":1})
        with self.assertRaisesRegex(ScopedMemoryError,"not_found"):
            self.store.select("other_project","research_agent",["baseline_note"])
        with self.assertRaisesRegex(ScopedMemoryError,"not_found"):
            self.store.select("launch_project","other_agent",["baseline_note"])
        bad=[{**refs[0],"agent_id":"other_agent"}]
        with self.assertRaisesRegex(ScopedMemoryError,"scope_denied"):
            self.store.require_revisions(bad,
                       {"agent_id":"research_agent","revision":1})

    def test_invalid_payloads_do_not_write(self):
        invalid=[
            {**NOTE,"project_id":"../escape"},
            {**NOTE,"agent_id":"research_agent/other"},
            {**NOTE,"note_id":"a"},
            {**NOTE,"text":"secret\x00payload"},
            {**NOTE,"text":"x"*1001},
            {**NOTE,"retention_days":True},
            {**NOTE,"retention_days":99999},
            {**NOTE,"expected_revision":False},
            {**NOTE,"source_kind":"VERIFIED_PROVIDER"},
            {**NOTE,"source_label":"line\nbreak"},
            {**NOTE,"another":1},
        ]
        for value in invalid:
            with self.subTest(value=str(value)[:30]),self.assertRaises(ScopedMemoryError):
                self.store.put(value)
        self.assertEqual(self.store.list("launch_project","research_agent"),[])
        with self.assertRaisesRegex(ScopedMemoryError,"invalid_scoped_memory_selection"):
            self.store.select("launch_project","research_agent",
                              ["baseline_note","baseline_note"])

    def test_capacity_per_scope_and_cross_project_counts(self):
        for i in range(24):
            self.store.put({**NOTE,"note_id":f"note_{i:03d}"})
        with self.assertRaisesRegex(ScopedMemoryError,"capacity_reached"):
            self.store.put({**NOTE,"note_id":"over_capacity"})
        self.assertEqual(len(self.store.list("launch_project","research_agent")),24)
        self.store.put({**NOTE,"note_id":"other_note","project_id":"other_project"})
        self.assertEqual(len(self.store.list("other_project","research_agent")),1)


class ScopedPlanBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.now=datetime(2026,11,8,7,0,tzinfo=timezone.utc)
        self.profiles=LocalAgentProfiles(self.folder/"agents.sqlite3")
        self.profiles.create(PROFILE)
        self.vault=LocalScopedAgentMemory(self.folder/"memory.sqlite3",
                                           clock=lambda:self.now)
        self.vault.put(NOTE)
        self.plans=LocalAgentPlanStore(self.folder/"plans.sqlite3",
                                      agent_profiles=self.profiles,
                                      scoped_memory_store=self.vault)
        self.addCleanup(self.plans.close)
        self.addCleanup(self.vault.close)
        self.addCleanup(self.profiles.close)
        self.binding=self.profiles.context("research_agent")["binding"]

    def refs(self):
        return self.vault.select("launch_project","research_agent",
                                 ["baseline_note"])[1]

    def create(self):
        return self.plans.create(PLAN,agent_binding=self.binding,
                                  scoped_memory_refs=self.refs())

    def test_real_approval_next_and_version_change_block_remainder(self):
        created=self.create()
        pid=created["plan_id"]
        self.assertEqual(created["agent_binding"],self.binding)
        self.assertEqual(len(created["scoped_memory_refs"]),1)
        self.assertNotIn(NOTE["text"],json.dumps(created))
        self.assertEqual(self.plans.approve(pid)["status"],"APPROVED")
        first=self.plans.advance(pid)
        self.assertEqual(first["results"][0]["output"],"56")
        self.vault.put({**NOTE,"text":"Changed after step 1",
                        "expected_revision":1})
        with self.assertRaisesRegex(PlanError,"scoped_memory_context_changed"):
            self.plans.advance(pid)
        self.assertEqual(self.plans.get(pid)["next_index"],1)
        self.assertEqual(self.plans.get(pid)["status"],"NEXT_READY")

    def test_expiry_and_delete_block_approval_and_resume(self):
        created=self.create()
        self.vault.delete("launch_project","research_agent","baseline_note",1)
        with self.assertRaisesRegex(PlanError,"scoped_memory_context_changed"):
            self.plans.approve(created["plan_id"])
        self.vault.put({**NOTE,"text":"different"})
        with self.assertRaisesRegex(PlanError,"scoped_memory_context_changed"):
            self.plans.approve(created["plan_id"])

    def test_expiry_blocks_step_after_long_pause(self):
        created=self.create()
        self.plans.approve(created["plan_id"])
        self.now+=timedelta(days=31)
        with self.assertRaisesRegex(PlanError,"scoped_memory_context_changed"):
            self.plans.advance(created["plan_id"])
        self.assertEqual(self.plans.get(created["plan_id"])["results"],[])

    def test_cross_agent_and_legacy_global_refs_fail_closed(self):
        self.profiles.create({**PROFILE,"agent_id":"other_agent","name":"Other"})
        refs=self.refs()
        with self.assertRaisesRegex(PlanError,"scoped_memory_scope_denied"):
            self.plans.create(PLAN,agent_binding={
                "agent_id":"other_agent","revision":1},scoped_memory_refs=refs)
        with self.assertRaisesRegex(PlanError,"scoped_memory_scope_denied"):
            self.plans.create(PLAN,scoped_memory_refs=refs)
        with self.assertRaisesRegex(PlanError,"global_memory_not_allowed_for_agent"):
            self.plans.create(PLAN,agent_binding=self.binding,
                memory_refs=[{"id":"legacy","revision":1,"digest":"a"*64}])
        self.assertEqual(self.plans.list(),[])

    def test_interrupted_restart_and_disabled_agent_remain_blocked(self):
        created=self.create()
        self.plans.approve(created["plan_id"])
        with self.plans.lock,self.plans.db:
            self.plans.db.execute("UPDATE agent_task_plans SET status='IN_FLIGHT' WHERE plan_id=?",
                                  (created["plan_id"],))
        reopened=LocalAgentPlanStore(self.folder/"plans.sqlite3",
             agent_profiles=self.profiles,scoped_memory_store=self.vault)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get(created["plan_id"])["status"],"INTERRUPTED")
        self.profiles.disable("research_agent",1)
        with self.assertRaisesRegex(PlanError,"agent_profile_changed_or_disabled"):
            reopened.resume_interrupted(created["plan_id"])
        self.assertEqual(reopened.get(created["plan_id"])["status"],"INTERRUPTED")


class ScopedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.profiles=LocalAgentProfiles(base/"profiles.sqlite3")
        self.profiles.create(PROFILE)
        self.profiles.create({**PROFILE,"agent_id":"review_agent","name":"Review"})
        self.vault=LocalScopedAgentMemory(base/"scoped.sqlite3")
        self.plans=LocalAgentPlanStore(base/"plans.sqlite3",
             agent_profiles=self.profiles,scoped_memory_store=self.vault)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.audit=LocalSecurityAudit(base/"audit.sqlite3")
        self.planner_calls=[]
        def planner(goal,model,**kwargs):
            self.planner_calls.append(kwargs)
            return {**PLAN,"goal":goal,"model":model}
        handler=create_handler(operator_auth=self.auth,security_audit=self.audit,
               agent_profiles=self.profiles,scoped_memory_store=self.vault,
               local_agent_plan_store=self.plans,local_agent_planner=planner,
               local_agent_runner=lambda *args,**kwargs: {})
        self.server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.plans.close)
        self.addCleanup(self.vault.close)
        self.addCleanup(self.profiles.close)
        self.addCleanup(self.audit.close)
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        status,sess=self.auth.login(PASSWORD)
        self.assertEqual(status,200)
        self.cookie="aas_session="+sess["cookie_token"]
        self.csrf=sess["csrf_token"]

    def request(self,method,path,data=None,auth=True,csrf=True,origin=None):
        con=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=6)
        headers={"Content-Type":"application/json"}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        con.request(method,path,body=json.dumps(data).encode() if data is not None else None,
                    headers=headers)
        response=con.getresponse()
        result=response.status,json.loads(response.read())
        con.close()
        return result

    def save(self):
        return self.request("POST","/api/agent-scoped-memory-save",
                            {**NOTE,"current_password":PASSWORD})

    def test_owner_only_auth_csrf_reauth_and_no_anon_note_leak(self):
        payload={**NOTE,"current_password":PASSWORD}
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",
                                      payload,auth=False)[0],401)
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",
                                      payload,csrf=False)[0],403)
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",
                        payload,origin="http://malicious.invalid")[0],403)
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",
                    {**payload,"current_password":"wrong"})[0],401)
        self.assertEqual(self.request("GET","/api/agent-scoped-memory?"
                 "project_id=launch_project&agent_id=research_agent",auth=False)[0],401)
        self.assertEqual(self.vault.list("launch_project","research_agent"),[])
        status,saved=self.save()
        self.assertEqual(status,200)
        self.assertEqual(saved["text"],NOTE["text"])
        status,list_result=self.request("GET","/api/agent-scoped-memory?"
                "project_id=launch_project&agent_id=research_agent")
        self.assertEqual(status,200)
        self.assertEqual(len(list_result["notes"]),1)
        self.assertEqual(self.request("GET","/api/agent-scoped-memory?"
                "project_id=launch_project&project_id=evil&agent_id=research_agent")[0],422)
        self.assertIn("SCOPED_AGENT_MEMORY_CONFIG",
                      [x["action"] for x in self.audit.recent()])

    def test_plan_uses_only_exact_scope_and_change_stops_step(self):
        self.assertEqual(self.save()[0],200)
        status,result=self.request("POST","/api/local-agent-plan-create",{
             "goal":"compute 7*8","model":"fixture:model",
             "agent_id":"research_agent","project_id":"launch_project",
             "scoped_memory_ids":["baseline_note"]})
        self.assertEqual(status,200)
        self.assertEqual(result["scoped_memory_refs"][0]["project_id"],"launch_project")
        self.assertEqual(self.planner_calls[0]["memory_context"][0]["text"],NOTE["text"])
        self.assertEqual(self.request("POST","/api/local-agent-plan-approve",
                                     {"plan_id":result["plan_id"]})[0],200)
        self.assertEqual(self.request("POST","/api/local-agent-plan-next",
                                     {"plan_id":result["plan_id"]})[0],200)
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",{
              **NOTE,"text":"edited","expected_revision":1,
              "current_password":PASSWORD})[0],200)
        code,failure=self.request("POST","/api/local-agent-plan-next",
                                  {"plan_id":result["plan_id"]})
        self.assertEqual((code,failure["error"]),(409,"scoped_memory_context_changed"))
        self.assertEqual(len(self.plans.get(result["plan_id"])["results"]),1)

    def test_cross_agent_blocked_before_planner_and_legacy_global_denied(self):
        self.assertEqual(self.save()[0],200)
        common={"goal":"compute","model":"fixture:model",
                "project_id":"launch_project",
                "scoped_memory_ids":["baseline_note"]}
        status,_=self.request("POST","/api/local-agent-plan-create",
                              {**common,"agent_id":"review_agent"})
        self.assertEqual(status,404)
        self.assertEqual(self.planner_calls,[])
        status,body=self.request("POST","/api/local-agent-plan-create",{
             "goal":"compute","model":"fixture:model","agent_id":"research_agent",
             "memory_ids":["global_note"]})
        self.assertEqual((status,body["error"]),(422,"global_memory_not_allowed_for_agent"))
        self.assertEqual(self.planner_calls,[])
        status,_=self.request("POST","/api/local-agent-plan-create",{
              "goal":"compute","model":"fixture:model","agent_id":"research_agent",
              "project_id":"other_project","scoped_memory_ids":["baseline_note"]})
        self.assertEqual(status,404)
        self.assertEqual(self.planner_calls,[])

    def test_memory_deletion_after_agent_disabled(self):
        status,save=self.save()
        self.assertEqual(status,200)
        self.profiles.disable("research_agent",1)
        self.assertEqual(self.request("POST","/api/agent-scoped-memory-save",{
            **NOTE,"note_id":"new_note",
            "current_password":PASSWORD})[0],409)
        status,deleted=self.request("POST","/api/agent-scoped-memory-delete",{
            "project_id":"launch_project","agent_id":"research_agent",
            "note_id":"baseline_note","expected_revision":1,
            "current_password":PASSWORD})
        self.assertEqual(status,200)
        self.assertTrue(deleted["backup_copies_may_remain"])
        self.assertEqual(self.vault.list("launch_project","research_agent"),[])


if __name__ == "__main__":
    unittest.main()
