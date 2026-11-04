"""Local workflow stage/publish, pause/resume and legacy SQLite migration gates.

Real HTTP + SQLite. Only pseudonymous synthetic categorical events; no sends.
"""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import local_workflow_core as core_module
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore


BASE = {"workflow_id": "version_flow", "display_name": "Version Rules",
        "service_filter": "automation", "min_budget_tier": 2,
        "followup_policy": "draft_only"}
EVENT = {"workflow_id": "version_flow", "event_id": "event_001",
         "service_code": "automation", "budget_tier": 2,
         "timeline": "soon"}
CSV = ("event_id,service_code,budget_tier,timeline\n"
       "csv_001,automation,2,soon\n")


class WorkflowControlHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "v1-core.sqlite3"
        self.core = LocalWorkflowCore(self.database)
        self.addCleanup(self.core.close)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         create_handler(local_core=self.core))
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 4)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, route, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=8)
        data = json.dumps(payload) if payload is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, route, body=data, headers=h)
        resp = conn.getresponse()
        code, raw = resp.status, resp.read()
        conn.close()
        return code, json.loads(raw)

    def ready(self):
        self.assertEqual(201,self.call("POST","/api/local-create",BASE)[0])
        self.assertEqual(200,self.call("POST","/api/local-activate",
            {"workflow_id":"version_flow","expected_version":1})[0])

    def workflow(self):
        data = self.call("GET","/api/local-workflows")[1]
        return next(w for w in data["workflows"]
                    if w["workflow_id"]=="version_flow")

    def stage(self, **options):
        w = self.workflow()
        args = {"workflow_id":"version_flow","expected_version":w["version"],
                "service_filter":"integration","min_budget_tier":3,
                "followup_policy":"review_required"}
        args.update(options)
        return self.call("POST","/api/local-stage",args)

    def publish(self, **options):
        w = self.workflow()
        args = {"workflow_id":"version_flow","expected_version":w["version"],
                "draft_version":w["pending_version"]}
        args.update(options)
        return self.call("POST","/api/local-publish",args)

    def control(self, paused, **options):
        w=self.workflow()
        args={"workflow_id":"version_flow","expected_version":w["version"],
              "expected_epoch":w["control_epoch"]}
        args.update(options)
        return self.call("POST","/api/local-pause" if paused else "/api/local-resume",args)

    def history(self):
        return self.call("GET","/api/local-history?workflow_id=version_flow")[1]

    def revisions(self):
        return self.call("GET","/api/local-revisions?workflow_id=version_flow")[1]

    def test_initial_draft_and_active_version_recorded(self):
        self.ready()
        w=self.workflow()
        self.assertEqual((1,"ACTIVE",None),(w["version"],w["status"],w["pending_version"]))
        self.assertEqual(1,w["control_epoch"])
        self.assertEqual([(1,"ACTIVE")],[(x["version"],x["state"]) for x in self.revisions()["revisions"]])

    def test_stage_does_not_modify_active_rules(self):
        self.ready()
        code,out=self.stage()
        self.assertEqual((201,"REVISION_DRAFTED",2),(code,out["status"],out["draft_version"]))
        self.assertEqual("automation",self.workflow()["service_filter"])
        self.assertEqual("QUALIFIED_DRAFT_ONLY",
                         self.call("POST","/api/local-run",EVENT)[1]["decision"])
        self.assertEqual(["ACTIVE","DRAFT"],
                         [r["state"] for r in self.revisions()["revisions"]])

    def test_publish_changes_only_future_new_events(self):
        self.ready()
        first=self.call("POST","/api/local-run",EVENT)[1]
        self.stage()
        code,new=self.publish()
        self.assertEqual(200,code)
        self.assertEqual(("REVISION_PUBLISHED_LOCAL",2),(new["status"],new["version"]))
        self.assertEqual("NOT_QUALIFIED_LOCAL",
                         self.call("POST","/api/local-run",{**EVENT,"event_id":"event_002"})[1]["decision"])
        replay=self.call("POST","/api/local-run",EVENT)[1]
        self.assertEqual(("REPLAYED_LOCAL",1,first["decision"]),
                         (replay["status"],replay["workflow_version"],replay["decision"]))
        h=self.history()["runs"]
        self.assertEqual(2,len(h))
        self.assertEqual({"event_001":1,"event_002":2},
                         {x["event_id"]:x["workflow_version"] for x in h})
        states=[x["state"] for x in self.revisions()["revisions"]]
        self.assertEqual(["ARCHIVED","ACTIVE"],states)

    def test_workflow_paused_blocks_new_manual_run(self):
        self.ready()
        self.assertEqual("PAUSED_LOCAL",self.control(True)[1]["status"])
        self.assertEqual("PAUSED",self.workflow()["status"])
        code,out=self.call("POST","/api/local-run",EVENT)
        self.assertEqual((409,"workflow_not_active"),(code,out["error"]))
        self.assertEqual(0,self.history()["total"])

    def test_pause_then_resume_restores_execution(self):
        self.ready();self.control(True)
        self.assertEqual("RESUMED_LOCAL",self.control(False)[1]["status"])
        self.assertEqual("ACTIVE",self.workflow()["status"])
        self.assertEqual(200,self.call("POST","/api/local-run",EVENT)[0])

    def test_pause_is_not_history_rollback(self):
        self.ready()
        original=self.call("POST","/api/local-run",EVENT)[1]
        self.control(True)
        self.assertEqual(1,self.history()["total"])
        self.assertEqual(original["decision"],self.history()["runs"][0]["decision"])

    def test_paused_workflow_stays_paused_after_publish(self):
        self.ready();self.control(True);self.stage()
        self.assertEqual("PAUSED",self.publish()[1]["execution_state"])
        self.assertEqual(("PAUSED",2),(self.workflow()["status"],self.workflow()["version"]))
        self.assertEqual(409,self.call("POST","/api/local-run",EVENT)[0])
        self.control(False)
        self.assertEqual(200,self.call("POST","/api/local-run",EVENT)[0])
        self.assertEqual(2,self.history()["runs"][0]["workflow_version"])

    def test_stage_rejected_when_pending_draft_exists(self):
        self.ready();self.stage()
        self.assertEqual("pending_revision_exists",self.stage()[1]["error"])
        self.assertEqual(2,len(self.revisions()["revisions"]))

    def test_outdated_publish_and_control_versions_fail_closed(self):
        self.ready();self.stage()
        self.assertEqual(409,self.publish(expected_version=9)[0])
        self.assertEqual(409,self.publish(draft_version=9)[0])
        self.publish()
        self.assertEqual("workflow_version_conflict",self.stage(expected_version=1)[1]["error"])
        self.assertEqual("workflow_control_conflict",self.control(True,expected_version=1)[1]["error"])

    def test_stale_control_epoch_conflicts(self):
        self.ready()
        epoch=self.workflow()["control_epoch"]
        self.control(True)
        self.assertEqual("workflow_control_conflict",
                         self.control(False,expected_epoch=epoch)[1]["error"])
        self.assertEqual("PAUSED",self.workflow()["status"])

    def test_double_pause_and_double_resume_are_denied(self):
        self.ready();self.control(True)
        self.assertEqual("invalid_workflow_transition",self.control(True)[1]["error"])
        self.control(False)
        self.assertEqual("invalid_workflow_transition",self.control(False)[1]["error"])

    def test_unactivated_draft_cannot_stage_or_pause(self):
        self.call("POST","/api/local-create",BASE)
        self.assertEqual("workflow_not_activated",self.stage()[1]["error"])
        self.assertEqual("invalid_workflow_transition",self.control(True)[1]["error"])

    def test_change_is_durable_across_second_sqlite_connection(self):
        self.ready();self.stage();self.publish();self.control(True)
        reopened=LocalWorkflowCore(self.database)
        self.addCleanup(reopened.close)
        self.assertEqual((2,"PAUSED"),
          (reopened.list_workflows()["workflows"][0]["version"],
           reopened.list_workflows()["workflows"][0]["status"]))
        self.assertEqual(["ARCHIVED","ACTIVE"],
                         [row["state"] for row in reopened.revisions("version_flow")[1]["revisions"]])

    def test_csv_preview_invalidated_after_pause_then_resume(self):
        self.ready()
        request={"workflow_id":"version_flow","csv_text":CSV}
        pre=self.call("POST","/api/local-batch-preview",request)[1]
        self.control(True)
        self.assertEqual("workflow_not_active",self.call("POST","/api/local-batch-commit",{
            **request,"expected_version":1,"preview_token":pre["preview_token"]})[1]["error"])
        self.control(False)
        code,out=self.call("POST","/api/local-batch-commit",{
            **request,"expected_version":1,"preview_token":pre["preview_token"]})
        self.assertEqual((409,"stale_batch_preview"),(code,out["error"]))
        self.assertEqual(0,self.history()["total"])

    def test_csv_preview_invalidated_after_rule_publish(self):
        self.ready()
        request={"workflow_id":"version_flow","csv_text":CSV}
        pre=self.call("POST","/api/local-batch-preview",request)[1]
        self.stage();self.publish()
        code,out=self.call("POST","/api/local-batch-commit",{
            **request,"expected_version":1,"preview_token":pre["preview_token"]})
        self.assertEqual((409,"workflow_version_conflict"),(code,out["error"]))
        self.assertEqual(0,self.history()["total"])

    def test_csv_preview_still_valid_during_unpublished_draft(self):
        self.ready()
        req={"workflow_id":"version_flow","csv_text":CSV}
        pre=self.call("POST","/api/local-batch-preview",req)[1]
        self.stage()
        code,_=self.call("POST","/api/local-batch-commit",{
            **req,"expected_version":1,"preview_token":pre["preview_token"]})
        self.assertEqual(200,code)
        self.assertEqual(1,self.history()["total"])

    def test_concurrent_pause_and_run_are_serialized(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda fn:fn(),[
                lambda:self.call("POST","/api/local-run",EVENT),
                lambda:self.control(True)]))
        self.assertEqual(200,results[1][0])
        # Either the run committed before pause or was denied after pause.
        self.assertIn(results[0][0],(200,409))
        self.assertEqual(1 if results[0][0]==200 else 0,self.history()["total"])
        self.assertEqual("PAUSED",self.workflow()["status"])

    def test_host_and_origin_rejection_for_new_controls(self):
        self.ready()
        for route in ("/api/local-stage","/api/local-publish",
                      "/api/local-pause","/api/local-resume"):
            for headers in ({"Host":"bad.example"},{"Origin":"https://bad.example"}):
                self.assertEqual(403,self.call("POST",route,{},headers)[0])
        self.assertEqual(403,self.call("GET",
             "/api/local-revisions?workflow_id=version_flow",
             headers={"Host":"bad.example"})[0])

    def test_invalid_shapes_and_booleans_fail_closed(self):
        self.ready()
        for opts in ({"min_budget_tier":True},
                     {"expected_version":True},
                     {"followup_policy":"send_message"},
                     {"service_filter":"unknown"}):
            self.assertEqual(422,self.stage(**opts)[0])
        self.assertEqual(422,self.control(True,expected_epoch=True)[0])
        self.assertEqual(422,self.publish(draft_version=True)[0])
        self.assertEqual(0,self.history()["total"])

    def test_revision_history_query_and_missing_workflow(self):
        self.ready()
        self.assertEqual(422,self.call("GET","/api/local-revisions?workflow_id=no/slashes")[0])
        self.assertEqual(404,self.call("GET","/api/local-revisions?workflow_id=missing")[0])
        self.assertEqual(422,self.call("GET","/api/local-revisions?workflow_id=version_flow&x=1")[0])
        self.assertEqual(200,self.call("GET","/api/local-revisions?workflow_id=version_flow")[0])

    def test_revision_capacity_fails_without_modifying_active_rules(self):
        self.ready()
        with patch.object(core_module,"MAX_REVISIONS",1):
            code,out=self.stage()
        self.assertEqual((409,"workflow_revision_capacity_reached"),(code,out["error"]))
        self.assertEqual(1,self.workflow()["version"])

    def test_existing_v1_database_is_migrated_without_rewriting_history(self):
        legacy=Path(self.tmp.name)/"legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE local_workflows (
                workflow_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                service_filter TEXT NOT NULL, min_budget_tier INTEGER NOT NULL,
                followup_policy TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, activated_at TEXT)""")
            db.execute("""CREATE TABLE local_runs (
                workflow_id TEXT NOT NULL,event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,workflow_version INTEGER NOT NULL,
                service_code TEXT NOT NULL,budget_tier INTEGER NOT NULL,
                timeline TEXT NOT NULL,decision TEXT NOT NULL,
                reason TEXT NOT NULL,created_at TEXT NOT NULL,
                PRIMARY KEY(workflow_id,event_id),
                FOREIGN KEY(workflow_id) REFERENCES local_workflows(workflow_id))""")
            db.execute("""INSERT INTO local_workflows VALUES
                ('legacy_wf','Legacy Rules','automation',2,'draft_only',
                 1,'ACTIVE','2026-01-01','2026-01-01')""")
            db.execute("""INSERT INTO local_runs VALUES
                ('legacy_wf','old_evt','fingerprint_v1',1,'automation',2,
                 'soon','QUALIFIED_DRAFT_ONLY','MATCHED_LOCAL_RULES','2026-01-01')""")
        migrated=LocalWorkflowCore(legacy)
        self.addCleanup(migrated.close)
        self.assertEqual(1,migrated.history("legacy_wf")[1]["total"])
        self.assertEqual(1,migrated.list_workflows()["workflows"][0]["version"])
        self.assertEqual(["ACTIVE"],[r["state"] for r in
                                    migrated.revisions("legacy_wf")[1]["revisions"]])
        result=migrated.stage_revision({
            "workflow_id":"legacy_wf","expected_version":1,
            "service_filter":"consulting","min_budget_tier":3,
            "followup_policy":"review_required"})
        self.assertEqual(201,result[0])
        self.assertEqual(1,migrated.history("legacy_wf")[1]["runs"][0]["workflow_version"])

    def test_pause_persists_across_restart_and_blocks_new_work(self):
        self.ready();self.control(True)
        another=LocalWorkflowCore(self.database)
        self.addCleanup(another.close)
        self.assertEqual("PAUSED",another.list_workflows()["workflows"][0]["status"])
        self.assertEqual("workflow_not_active",another.run(EVENT)[1]["error"])

if __name__ == "__main__":
    unittest.main()
