"""Versioned queue hash binds paid plan source while accepting pre-existing rows."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from local_agent_task_plans import LocalAgentPlanStore
from local_durable_plan_queue import (LocalDurablePlanQueue, DurableQueueError,
                                      _security_fingerprint)
from local_model_safety import LocalModelSafetyState

A = "a" * 24
B = "b" * 24
PROVIDER = "anthropic_messages"
MODEL = "claude-haiku-5-5"
PLAN = {"model": MODEL, "goal": "Read-only calculation",
        "steps": [{"tool": "calculate", "args": {
            "a": "7", "b": "9", "operation": "add"}}]}


class QueueCloudOriginSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.safety = LocalModelSafetyState(root / "safety.sqlite")
        self.plans = LocalAgentPlanStore(root / "plans.sqlite")
        self.queue_file = root / "queue.sqlite"
        self.gate = threading.BoundedSemaphore(1)
        self.gate_lock = threading.Lock()
        self.queue = LocalDurablePlanQueue(
            self.plans, self.safety, self.gate, self.gate_lock, self.queue_file)
        self.plans.create(PLAN, plan_id=A,
                          cloud_origin={"provider": PROVIDER,
                                        "request_id": A + "00000000"})
        self.plans.create(PLAN, plan_id=B)
        self.plans.approve(A)
        self.plans.approve(B)

    def tearDown(self):
        self.queue.close()
        self.plans.close()
        self.safety.close()

    def create(self):
        return self.queue.create({"job_id": "source_job", "label": "Source verified job",
            "priority": 1, "nodes": [
                {"plan_id": A, "depends_on": []},
                {"plan_id": B, "depends_on": [A]}]})

    def change_origin(self, value):
        with self.plans.lock, self.plans.db:
            self.plans.db.execute(
                "UPDATE agent_task_plans SET cloud_origin_json=? WHERE plan_id=?",
                (json.dumps(value, sort_keys=True), A))

    def test_new_queue_pins_full_original_request_before_arm(self):
        self.assertTrue(self.create()["nodes"][0]["security_sha256"].startswith("v2:"))
        self.change_origin({"provider": PROVIDER, "request_id": A + "11111111"})
        with self.assertRaisesRegex(DurableQueueError,
                                    "job_plan_permission_binding_changed"):
            self.queue.arm("source_job")
        self.assertEqual(self.plans.get(A)["next_index"], 0)

    def test_changed_provider_detected_before_queued_step(self):
        self.create()
        self.queue.arm("source_job")
        self.change_origin({"provider": "gemini_generate_content",
                            "request_id": A + "00000000"})
        with self.assertRaisesRegex(DurableQueueError,
                                    "job_plan_permission_binding_changed"):
            self.queue.run_one("source_job")
        self.assertEqual(self.plans.get(A)["next_index"], 0)

    def test_restart_preserves_pin_and_allows_unchanged_execution(self):
        self.create()
        self.queue.close()
        self.queue = LocalDurablePlanQueue(
            self.plans, self.safety, self.gate, self.gate_lock, self.queue_file)
        self.queue.arm("source_job")
        self.assertEqual(self.queue.run_one("source_job")["plan_id"], A)

    def test_legacy_unprefixed_jobs_still_load_without_silent_rehash(self):
        self.create()
        old = _security_fingerprint(self.plans.get(A))
        with self.queue.lock, self.queue.db:
            self.queue.db.execute(
                "UPDATE durable_job_nodes SET security_sha256=? WHERE plan_id=?",
                (old, A))
        self.queue.close()
        self.queue = LocalDurablePlanQueue(
            self.plans, self.safety, self.gate, self.gate_lock, self.queue_file)
        self.assertEqual(self.queue.get("source_job")["nodes"][0]["security_sha256"],
                         old)
        self.queue.arm("source_job")


if __name__ == "__main__":
    unittest.main()
