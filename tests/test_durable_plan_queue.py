"""Stage 115: genuine local SQLite/HTTP DAG queue, no real provider or outbound call."""
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import local_server
from local_agent_task_plans import LocalAgentPlanStore
from local_durable_plan_queue import LocalDurablePlanQueue, DurableQueueError
from local_model_safety import LocalModelSafetyState
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit

PASSWORD="Stage115-Owner-Password-Synthetic-Only-2026!"
PIDS=["a"*24,"b"*24,"c"*24,"d"*24,"e"*24,"f"*24]
PLAN=lambda tool="utc_now": {"model":"synthetic-model","goal":"Owner approved local read",
                            "steps":[{"tool":tool,"args":{} if tool=="utc_now" else
                                     {"a":"7","b":"8","operation":"multiply"}}]}


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.planfile=base/"agent-plans.sqlite3"
        self.jobfile=base/"jobs.sqlite3"
        self.safety=LocalModelSafetyState(base/"safety.sqlite3")
        self.plans=LocalAgentPlanStore(self.planfile)
        self.gate=threading.BoundedSemaphore(1)
        self.gate_lock=threading.Lock()
        self.queue=LocalDurablePlanQueue(
            self.plans,self.safety,self.gate,self.gate_lock,self.jobfile)
        for pid in PIDS[:5]:
            self.plans.create(PLAN(),plan_id=pid)
            self.plans.approve(pid)

    def tearDown(self):
        self.queue.close()
        self.plans.close()
        self.safety.close()

    def job(self,job_id="test_job",pids=None,priority=2,serial=True):
        selected=pids or PIDS[:2]
        return {"job_id":job_id,"label":"Local durable owner job",
                "priority":priority,"nodes":[{"plan_id":pid,
                  "depends_on":[selected[i-1]] if serial and i else []}
                  for i,pid in enumerate(selected)]}

    def create_and_arm(self,**kwargs):
        data=self.job(**kwargs)
        j=self.queue.create(data)
        self.assertEqual(j["status"],"DRAFT")
        self.assertFalse(j["background_dispatch_enabled"])
        self.assertFalse(j["external_write_permission"])
        self.assertEqual(self.plans.get(data["nodes"][0]["plan_id"])["next_index"],0)
        return self.queue.arm(data["job_id"])

    def test_sequential_dependency_and_completion_with_persisted_provenance(self):
        j=self.create_and_arm(pids=PIDS[:3])
        self.assertEqual(len(j["nodes"]),3)
        self.assertFalse(j["nodes"][1]["dependencies_satisfied"])
        for i in range(3):
            result=self.queue.run_one("test_job")
            self.assertEqual(result["plan_id"],PIDS[i])
            self.assertEqual(result["step_index"],0)
            self.assertEqual(len(result["step_evidence_sha256"]),64)
            self.assertEqual(result["external_write_count"],0)
            self.assertNotIn("synthetic-model",json.dumps(self.queue.history("test_job")))
        self.assertEqual(result["job"]["status"],"COMPLETED")
        with self.assertRaises(DurableQueueError):
            self.queue.run_one("test_job")
        self.assertEqual(self.queue.history("test_job")["events"][0]["event"],"JOB_COMPLETED")

    def test_draft_and_unapproved_plans_cannot_run(self):
        self.queue.create(self.job())
        with self.assertRaisesRegex(DurableQueueError,"job_no_ready_step"):
            self.queue.run_one()
        self.plans.create(PLAN(),plan_id=PIDS[5])
        with self.assertRaisesRegex(DurableQueueError,"job_plan_not_ready"):
            self.queue.create(self.job(job_id="other_job",pids=[PIDS[5],PIDS[4]]))
        self.assertEqual(self.plans.get(PIDS[5])["next_index"],0)

    def test_priority_and_fairness_are_real(self):
        self.create_and_arm(job_id="low_job",pids=PIDS[:2],priority=0,serial=False)
        self.create_and_arm(job_id="top_job",pids=PIDS[2:4],priority=3,serial=False)
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"top_job")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"top_job")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"low_job")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"low_job")
        self.assertEqual(self.queue.get("low_job")["status"],"COMPLETED")

    def test_equal_priority_yields_between_jobs(self):
        self.create_and_arm(job_id="job_one",pids=PIDS[:2],priority=2,serial=False)
        self.create_and_arm(job_id="job_two",pids=PIDS[2:4],priority=2,serial=False)
        # Fairness uses durable last-claim sequence within equal priority.
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"job_one")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"job_two")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"job_one")
        self.assertEqual(self.queue.run_one()["job"]["job_id"],"job_two")

    def test_pause_resume_cancel_no_auto_dispatch(self):
        self.create_and_arm()
        self.queue.pause("test_job")
        with self.assertRaises(DurableQueueError):
            self.queue.run_one()
        self.assertEqual(self.plans.get(PIDS[0])["results"],[])
        self.assertEqual(self.queue.resume("test_job")["status"],"ACTIVE")
        self.queue.run_one()
        self.assertEqual(self.queue.cancel("test_job")["status"],"CANCELLED")
        with self.assertRaises(DurableQueueError):
            self.queue.resume("test_job")
        self.assertEqual(self.plans.get(PIDS[1])["results"],[])

    def test_cycle_missing_duplicate_and_bool_priority_rejected(self):
        cases=[
            self.job(priority=True),
            self.job(pids=[PIDS[0],PIDS[0]]),
            {**self.job(),"nodes":[
                {"plan_id":PIDS[0],"depends_on":[PIDS[1]]},
                {"plan_id":PIDS[1],"depends_on":[PIDS[0]]}]},
            {**self.job(),"nodes":[
                {"plan_id":PIDS[0],"depends_on":[PIDS[2]]},
                {"plan_id":PIDS[1],"depends_on":[]}]},
        ]
        for item in cases:
            with self.subTest(item=item),self.assertRaises(DurableQueueError):
                self.queue.create(item)
        self.assertEqual(self.queue.list()["jobs"],[])

    def test_plan_source_drift_blocks_queue(self):
        self.queue.create(self.job())
        with self.plans.db:
            self.plans.db.execute("UPDATE agent_task_plans SET goal='changed' WHERE plan_id=?",
                                  (PIDS[0],))
        with self.assertRaisesRegex(DurableQueueError,"job_plan_source_changed"):
            self.queue.arm("test_job")
        self.assertEqual(self.queue.get("test_job")["status"],"DRAFT")

    def test_same_plan_cannot_be_enrolled_in_multiple_jobs(self):
        self.queue.create(self.job())
        with self.assertRaisesRegex(DurableQueueError,"job_or_plan_already_registered"):
            self.queue.create(self.job(job_id="other_job",pids=[PIDS[0],PIDS[2]]))

    def test_restart_inflight_quarantines_and_never_replays(self):
        self.create_and_arm()
        with self.queue.db:
            self.queue.db.execute("""UPDATE durable_job_nodes SET node_status='IN_FLIGHT'
                                   WHERE plan_id=?""",(PIDS[0],))
        self.queue.close()
        self.queue=LocalDurablePlanQueue(
            self.plans,self.safety,self.gate,self.gate_lock,self.jobfile)
        row=self.queue.get("test_job")
        self.assertEqual(row["status"],"REVIEW_REQUIRED")
        with self.assertRaisesRegex(DurableQueueError,"job_no_ready_step"):
            self.queue.run_one()
        with self.assertRaisesRegex(DurableQueueError,"job_recorded_step_not_proven"):
            self.queue.reconcile_recorded("test_job",PIDS[0])
        self.assertEqual(self.plans.get(PIDS[0])["results"],[])

    def test_reconcile_only_one_proven_step_then_explicit_resume(self):
        self.create_and_arm()
        with self.queue.db:
            self.queue.db.execute("UPDATE durable_job_nodes SET node_status='IN_FLIGHT' WHERE plan_id=?",
                                  (PIDS[0],))
        self.plans.advance(PIDS[0])  # independently persisted actual result
        self.queue.close()
        self.queue=LocalDurablePlanQueue(
            self.plans,self.safety,self.gate,self.gate_lock,self.jobfile)
        job=self.queue.reconcile_recorded("test_job",PIDS[0])
        self.assertEqual(job["status"],"PAUSED")
        self.assertEqual(job["nodes"][0]["status"],"COMPLETED")
        with self.assertRaises(DurableQueueError):
            self.queue.run_one()
        self.queue.resume("test_job")
        self.assertEqual(self.queue.run_one()["job"]["status"],"COMPLETED")

    def test_two_concurrent_ticks_gate_prevents_double_dispatch(self):
        self.create_and_arm()
        entered,release=threading.Event(),threading.Event()
        from local_agent_task_plans import execute_tool
        errors=[]
        def slow(tool,args):
            entered.set()
            if not release.wait(4):raise RuntimeError("blocked")
            return execute_tool(tool,args)
        def worker():
            try:self.queue.run_one("test_job")
            except Exception as exc:errors.append(str(exc))
        with patch("local_agent_task_plans.execute_tool",side_effect=slow):
            thread=threading.Thread(target=worker,daemon=True)
            thread.start()
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(DurableQueueError,"local_agent_busy"):
                self.queue.run_one("test_job")
            with self.assertRaisesRegex(DurableQueueError,"job_step_in_flight"):
                self.queue.pause("test_job")
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(len(self.plans.get(PIDS[0])["results"]),1)

    def test_model_checkpoint_uncertainty_blocks_next_step(self):
        self.create_and_arm()
        from local_agent_task_plans import execute_tool
        with patch("local_agent_task_plans.execute_tool",
                   side_effect=RuntimeError("simulated provider error")):
            with self.assertRaises(DurableQueueError):
                self.queue.run_one()
        self.assertEqual(self.queue.get("test_job")["status"],"REVIEW_REQUIRED")
        with self.assertRaises(DurableQueueError):
            self.queue.reconcile_recorded("test_job",PIDS[0])
        self.assertEqual(self.plans.get(PIDS[0])["status"],"BLOCKED")

    def test_authenticated_loopback_owner_password_csrf_and_audit(self):
        base=Path(self.tmp.name)
        LocalOperatorAuth.provision(base/"operator.json",PASSWORD)
        owner=LocalOperatorAuth.load(base/"operator.json")
        audit=LocalSecurityAudit(base/"audit.sqlite3")
        server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(operator_auth=owner,security_audit=audit,
                local_agent_plan_store=self.plans,model_safety_state=self.safety,
                durable_jobs=self.queue,shared_agent_gate=self.gate,
                shared_agent_state_lock=self.gate_lock))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        client=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
        url="http://127.0.0.1:"+str(server.server_port)
        csrf=None
        def post(path,data,authorized=True):
            headers={"Content-Type":"application/json"}
            if authorized and csrf:headers["X-AAS-CSRF"]=csrf
            with client.open(Request(url+path,json.dumps(data).encode(),method="POST",
                                     headers=headers),timeout=7) as result:
                return json.load(result)
        try:
            with self.assertRaises(HTTPError) as anon:
                client.open(url+"/api/durable-jobs")
            self.assertEqual(anon.exception.code,401)
            csrf=post("/api/operator-login",{"password":PASSWORD})["csrf_token"]
            caps=client.open(url+"/api/local-agent-capabilities")
            self.assertTrue(json.load(caps)["durable_job_queue"])
            with self.assertRaises(HTTPError) as no_csrf:
                post("/api/durable-job-create",
                     {**self.job(),"current_password":PASSWORD},False)
            self.assertEqual(no_csrf.exception.code,403)
            with self.assertRaises(HTTPError) as bad_pass:
                post("/api/durable-job-create",
                     {**self.job(),"current_password":"wrong"})
            self.assertEqual(bad_pass.exception.code,401)
            self.assertEqual(post("/api/durable-job-create",
                {**self.job(),"current_password":PASSWORD})["status"],"DRAFT")
            self.assertEqual(post("/api/durable-job-arm",
                {"job_id":"test_job","current_password":PASSWORD})["status"],"ACTIVE")
            step=post("/api/durable-job-run-one",
                {"job_id":"test_job","current_password":PASSWORD})
            self.assertEqual(step["status"],"STEP_RECORDED")
            self.assertEqual(step["external_write_count"],0)
            self.assertEqual(self.plans.get(PIDS[0])["next_index"],1)
            with client.open(url+"/api/durable-job-history?job_id=test_job") as r:
                self.assertTrue(json.load(r)["events"])
            self.assertTrue(any(e["action"]=="DURABLE_JOB_STEP"
                                for e in audit.recent(100)))
        finally:
            server.shutdown();server.server_close();worker.join(5);audit.close()


if __name__=="__main__":
    unittest.main()
