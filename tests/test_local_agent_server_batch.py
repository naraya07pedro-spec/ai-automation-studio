"""Slice 73: synthetic durable server-owned reviewed batch verification."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_agent_task_plans import LocalAgentPlanStore, PlanError
from local_agent_batch_runner import LocalAgentBatchRunner
from local_model_safety import LocalModelSafetyState


def proposal():
    return {"model": "fixture:small", "goal": "Calculate and check UTC",
            "steps": [{"tool": "calculate",
                       "args": {"a": "7", "b": "8", "operation": "multiply"}},
                      {"tool": "utc_now", "args": {}}]}


class ServerReviewedBatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "plans.sqlite3"
        self.store = LocalAgentPlanStore(self.db_path)
        self.addCleanup(self.store.close)
        self.safety = LocalModelSafetyState()
        self.addCleanup(self.safety.close)
        self.gate = threading.BoundedSemaphore(1)
        self.runner = LocalAgentBatchRunner(
            self.store, self.safety, self.gate, threading.Lock())
        self.pid = self.store.create(proposal())["plan_id"]

    def wait_for(self, state, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.store.get(self.pid)
            if row["batch_run_status"] == state:
                return row
            time.sleep(0.02)
        self.fail("batch did not reach " + state +
                  "; actual="+self.store.get(self.pid)["batch_run_status"])

    def test_explicit_approval_required_and_no_dispatch_on_approval(self):
        with self.assertRaises(PlanError):
            self.runner.start(self.pid)
        self.store.approve(self.pid)
        self.assertEqual("IDLE", self.store.get(self.pid)["batch_run_status"])
        self.assertEqual([], self.store.get(self.pid)["results"])
        row = self.runner.start(self.pid)
        self.assertEqual("RUNNING", row["batch_run_status"])
        completed = self.wait_for("COMPLETED")
        self.assertEqual("COMPLETED", completed["status"])
        self.assertEqual("56", completed["results"][0]["output"])
        self.assertEqual(2, len(completed["results"]))
        self.assertEqual("IDLE", self.safety.phase())
        with self.assertRaises(PlanError):
            self.runner.start(self.pid)

    def test_stop_is_durable_and_does_not_abort_inflight(self):
        self.store.approve(self.pid)
        entered, release = threading.Event(), threading.Event()
        from local_agent_task_plans import execute_tool
        def blocking(tool, args):
            if tool == "calculate":
                entered.set()
                self.assertTrue(release.wait(4))
            return execute_tool(tool, args)
        with patch("local_agent_task_plans.execute_tool", side_effect=blocking):
            self.runner.start(self.pid)
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(PlanError, "plan_batch_active"):
                self.store.advance(self.pid)  # Manual execution cannot race worker.
            stopping = self.runner.stop(self.pid)
            self.assertEqual("STOPPING", stopping["batch_run_status"])
            release.set()
            saved = self.wait_for("STOPPED")
        self.assertEqual(1, saved["next_index"])
        self.assertEqual("NEXT_READY", saved["status"])
        self.assertEqual("56", saved["results"][0]["output"])
        self.assertEqual("IDLE", self.safety.phase())
        with self.assertRaises(PlanError):
            self.runner.stop(self.pid)
        # A fresh owner-authorized run is required for the one saved step.
        self.assertEqual("RUNNING", self.runner.start(self.pid)["batch_run_status"])
        self.assertEqual("COMPLETED", self.wait_for("COMPLETED")["status"])

    def test_two_simultaneous_starts_do_not_duplicate_steps(self):
        self.store.approve(self.pid)
        entered, release = threading.Event(), threading.Event()
        from local_agent_task_plans import execute_tool
        def blocking(tool, args):
            if tool == "calculate":
                entered.set()
                self.assertTrue(release.wait(4))
            return execute_tool(tool, args)
        with patch("local_agent_task_plans.execute_tool", side_effect=blocking):
            self.runner.start(self.pid)
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(PlanError, "local_agent_busy"):
                self.runner.start(self.pid)
            release.set()
            self.assertEqual(2, len(self.wait_for("COMPLETED")["results"]))

    def test_reopen_marks_running_interrupted_without_auto_resume(self):
        self.store.approve(self.pid)
        self.store.begin_batch(self.pid)  # Persist a run intent; simulate death.
        self.assertEqual([], self.store.get(self.pid)["results"])
        recovered = LocalAgentPlanStore(self.db_path)
        try:
            saved = recovered.get(self.pid)
            self.assertEqual("INTERRUPTED", saved["batch_run_status"])
            self.assertEqual("APPROVED", saved["status"])
            self.assertEqual(0, saved["next_index"])
            self.assertEqual([], saved["results"])
        finally:
            recovered.close()

    def test_uncertain_transport_quarantines_without_retry(self):
        from local_ollama_agent import AgentError
        self.store.approve(self.pid)
        with patch("local_agent_task_plans.execute_tool",
                   side_effect=AgentError("local_model_timeout")):
            self.runner.start(self.pid)
            failed = self.wait_for("FAILED")
        self.assertEqual("BLOCKED", failed["status"])
        self.assertEqual([], failed["results"])
        self.assertEqual("UNCERTAIN", self.safety.phase())
        with self.assertRaises(PlanError):
            self.runner.start(self.pid)


if __name__ == "__main__":
    unittest.main()
