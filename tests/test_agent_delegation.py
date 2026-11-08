"""Stage 129: real two-Agent approved-plan coordination, owner-ticked only."""
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
from local_scoped_agent_memory import LocalScopedAgentMemory
from local_agent_task_plans import LocalAgentPlanStore
from local_durable_plan_queue import LocalDurablePlanQueue, DurableQueueError
from local_model_safety import LocalModelSafetyState
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

PASSWORD = "Stage129-owner-test-password-synthetic-only!"
SOURCE = "research_agent"
TARGET = "review_agent"
PROJECT = "engineering_ops"
SOURCE_PID = "a" * 24
TARGET_PID = "b" * 24


def agent(agent_id, name, tools):
    return {"agent_id": agent_id, "name": name, "category": "engineering",
            "description": "Bounded review", "instructions": "Use only selected notes.",
            "allowed_tools": tools}


def note(agent_id, note_id, text, project=PROJECT):
    return {"project_id": project, "agent_id": agent_id, "note_id": note_id,
            "text": text, "source_kind": "OWNER_NOTE", "source_label": "owner test fixture",
            "retention_days": 30, "expected_revision": 0}


def plan(tool="calculate"):
    return {"model": "fixture:model", "goal": "Owner-reviewed read-only task",
            "steps": [{"tool": tool, "args": (
                {"a": "7", "b": "8", "operation": "multiply"}
                if tool == "calculate" else {})}]}


def request(job_id="delegation_job", delegation=True):
    job = {"job_id": job_id, "label": "Agent completion handoff",
           "priority": 2, "nodes": [
                {"plan_id": SOURCE_PID, "depends_on": []},
                {"plan_id": TARGET_PID, "depends_on": [SOURCE_PID]}]}
    if delegation:
        job["delegation"] = {
            "project_id": PROJECT, "source_agent_id": SOURCE,
            "target_agent_id": TARGET, "handoff_mode": "COMPLETION_PROOF_ONLY"}
    return job


class DelegationContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profiles = LocalAgentProfiles(self.root / "profiles.sqlite3")
        self.profiles.create(agent(SOURCE, "Research", ["calculate", "utc_now"]))
        self.profiles.create(agent(TARGET, "Review", ["utc_now", "calculate"]))
        self.memory = LocalScopedAgentMemory(self.root / "scoped.sqlite3")
        self.memory.put(note(SOURCE, "src_note", "Only project research context."))
        self.memory.put(note(TARGET, "dst_note", "Only project review context."))
        self.plans = LocalAgentPlanStore(
            self.root / "plans.sqlite3", agent_profiles=self.profiles,
            scoped_memory_store=self.memory)
        self.plan_for(SOURCE_PID, SOURCE, "src_note")
        self.plan_for(TARGET_PID, TARGET, "dst_note", tool="utc_now")
        self.safety = LocalModelSafetyState(self.root / "safety.sqlite3")
        self.gate = threading.BoundedSemaphore(1)
        self.lock = threading.Lock()
        self.queue = LocalDurablePlanQueue(
            self.plans, self.safety, self.gate, self.lock, self.root / "queue.sqlite3")
        self.addCleanup(self.profiles.close)
        self.addCleanup(self.memory.close)
        self.addCleanup(self.plans.close)
        self.addCleanup(self.safety.close)
        self.addCleanup(self.queue.close)

    def plan_for(self, pid, agent_id, note_id, tool="calculate", project=PROJECT):
        binding = self.profiles.context(agent_id)["binding"]
        refs = self.memory.select(project, agent_id, [note_id])[1]
        self.plans.create(plan(tool), plan_id=pid, agent_binding=binding,
                          scoped_memory_refs=refs)
        self.plans.approve(pid)

    def test_real_two_agent_dag_and_digest_only_handoff(self):
        job = self.queue.create(request())
        self.assertEqual(job["status"], "DRAFT")
        self.assertEqual(job["delegation"]["target_agent_id"], TARGET)
        self.assertEqual(job["handoff_payload"], "NONE")
        self.assertIsNone(job["completion_handoff_sha256"])
        self.assertEqual(self.plans.get(TARGET_PID)["results"], [])
        self.assertEqual(self.queue.arm("delegation_job")["status"], "ACTIVE")
        self.assertFalse(self.queue.get("delegation_job")["nodes"][1]["dependencies_satisfied"])
        source = self.queue.run_one("delegation_job")
        self.assertEqual(source["plan_id"], SOURCE_PID)
        self.assertEqual(self.plans.get(SOURCE_PID)["results"][0]["output"], "56")
        current = self.queue.get("delegation_job")
        self.assertEqual(len(current["completion_handoff_sha256"]), 64)
        self.assertTrue(current["nodes"][1]["dependencies_satisfied"])
        self.assertNotIn("Only project research context.", json.dumps(current))
        self.assertNotIn("Only project review context.", json.dumps(current))
        self.assertNotIn('"56"', json.dumps(current))
        target = self.queue.run_one("delegation_job")
        self.assertEqual(target["plan_id"], TARGET_PID)
        self.assertEqual(target["job"]["status"], "COMPLETED")
        self.assertEqual(self.queue.history("delegation_job")["events"][0]["event"],
                         "JOB_COMPLETED")
        self.assertEqual(self.queue.get("delegation_job")["handoff_payload"], "NONE")

    def test_agent_policy_revocation_blocks_target_after_source_completion(self):
        self.queue.create(request())
        self.queue.arm("delegation_job")
        self.queue.run_one("delegation_job")
        self.profiles.update({**agent(TARGET, "Review", ["utc_now"]),
                              "expected_revision": 1})
        with self.assertRaisesRegex(DurableQueueError,
                                    "delegation_authorization_changed"):
            self.queue.run_one("delegation_job")
        self.assertEqual(self.plans.get(TARGET_PID)["results"], [])
        self.assertEqual(self.queue.get("delegation_job")["status"], "ACTIVE")

    def test_source_memory_revoked_blocks_delegation_after_source_complete(self):
        self.queue.create(request())
        self.queue.arm("delegation_job")
        self.queue.run_one("delegation_job")
        self.memory.delete(PROJECT, SOURCE, "src_note", 1)
        with self.assertRaisesRegex(DurableQueueError,
                                    "delegation_authorization_changed"):
            self.queue.run_one("delegation_job")
        self.assertEqual(self.plans.get(TARGET_PID)["next_index"], 0)

    def test_invalid_roles_graph_project_and_no_legacy_handoff(self):
        cases = [
            {**request(), "delegation": {**request()["delegation"],
                "source_agent_id": TARGET}},
            {**request(), "delegation": {**request()["delegation"],
                "target_agent_id": SOURCE}},
            {**request(), "delegation": {**request()["delegation"],
                "project_id": "other_project"}},
            {**request(), "delegation": {**request()["delegation"],
                "handoff_mode": "SEND_RAW_RESULTS"}},
            {**request(), "nodes": [
                {"plan_id": SOURCE_PID, "depends_on": []},
                {"plan_id": TARGET_PID, "depends_on": []}]},
            {**request(), "delegation": {**request()["delegation"], "unknown": "x"}},
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(DurableQueueError):
                self.queue.create(item)
        self.assertEqual(self.queue.list()["jobs"], [])

    def test_per_plan_security_fingerprint_detects_mutated_database_binding(self):
        self.queue.create(request(delegation=False))
        with self.plans.db:
            self.plans.db.execute("""UPDATE agent_task_plans SET
                scoped_memory_refs_json='[]' WHERE plan_id=?""", (SOURCE_PID,))
        with self.assertRaisesRegex(DurableQueueError,
                                    "job_plan_permission_binding_changed"):
            self.queue.arm("delegation_job")
        self.assertEqual(self.queue.get("delegation_job")["status"], "DRAFT")

    def test_revoked_agent_denies_ordinary_queue_before_any_claim(self):
        self.queue.create(request(delegation=False))
        self.profiles.disable(SOURCE, 1)
        with self.assertRaisesRegex(DurableQueueError,
                                    "job_plan_permission_or_memory_changed"):
            self.queue.arm("delegation_job")
        self.assertEqual(self.plans.get(SOURCE_PID)["results"], [])

    def test_restart_preserves_delegation_provenance_no_auto_dispatch(self):
        self.queue.create(request())
        self.queue.arm("delegation_job")
        self.queue.run_one("delegation_job")
        self.queue.close()
        self.queue = LocalDurablePlanQueue(
            self.plans, self.safety, self.gate, self.lock,
            self.root / "queue.sqlite3")
        prior = self.queue.get("delegation_job")
        self.assertEqual(prior["status"], "ACTIVE")
        self.assertEqual(prior["nodes"][1]["status"], "WAITING")
        self.assertEqual(len(prior["completion_handoff_sha256"]), 64)
        self.assertEqual(self.plans.get(TARGET_PID)["results"], [])
        self.queue.run_one("delegation_job")
        self.assertEqual(self.queue.get("delegation_job")["status"], "COMPLETED")

    def test_http_owner_auth_csrf_and_password_for_delegation(self):
        auth = LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        audit = LocalSecurityAudit(self.root / "audit.sqlite3")
        self.addCleanup(audit.close)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(operator_auth=auth, security_audit=audit,
                           local_agent_plan_store=self.plans,
                           model_safety_state=self.safety,
                           durable_jobs=self.queue,
                           shared_agent_gate=self.gate,
                           shared_agent_state_lock=self.lock))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def ask(method, route, body=None, cookie=None, csrf=None):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5)
            h = {"Content-Type": "application/json"}
            if cookie: h["Cookie"] = cookie
            if csrf: h["X-AAS-CSRF"] = csrf
            connection.request(method, route, body=json.dumps(body).encode()
                               if body is not None else None, headers=h)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result

        self.assertEqual(ask("POST", "/api/durable-job-create",
                             {**request(), "current_password": PASSWORD})[0], 401)
        status, sess = auth.login(PASSWORD)
        self.assertEqual(status, 200)
        cookie = "aas_session=" + sess["cookie_token"]
        csrf = sess["csrf_token"]
        status, capabilities = ask("GET", "/api/local-agent-capabilities", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(capabilities["bounded_agent_delegation"])
        self.assertEqual(ask("POST", "/api/durable-job-create",
                  {**request(), "current_password": PASSWORD},
                  cookie=cookie)[0], 403)
        self.assertEqual(ask("POST", "/api/durable-job-create",
                  {**request(), "current_password": "bad"},
                  cookie=cookie, csrf=csrf)[0], 401)
        self.assertEqual(ask("POST", "/api/durable-job-create",
                  {**request(), "current_password": PASSWORD},
                  cookie=cookie, csrf=csrf)[0], 200)
        self.assertEqual(ask("POST", "/api/durable-job-arm", {
                  "job_id":"delegation_job","current_password":PASSWORD},
                  cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(ask("POST", "/api/durable-job-run-one", {
                  "job_id":"delegation_job","current_password":PASSWORD},
                  cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(len(self.plans.get(SOURCE_PID)["results"]),1)
        self.assertEqual(self.plans.get(TARGET_PID)["results"], [])
        self.assertIn("DURABLE_JOB_STEP",
                      [e["action"] for e in audit.recent(100)])


if __name__ == "__main__":
    unittest.main()
