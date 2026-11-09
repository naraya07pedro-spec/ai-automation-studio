"""Durable cloud plan provenance against provider/request prefix collisions.

No live provider credentials, shell, mail, n8n dispatch or real billing.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from local_agent_task_plans import LocalAgentPlanStore, PlanError
from local_cloud_plan_bridge import CloudPlanBridge, CloudPlanError, paid_planning_prompt
from local_model_safety import LocalModelSafetyState
from local_on_demand_remote_ai import LocalOnDemandRemoteAI


GOAL = "Calculate 8 + 9"
MODEL = "claude-haiku-5-5"
PROVIDER = "anthropic_messages"
STEPS = [{"tool": "calculate",
          "args": {"a": "8", "b": "9", "operation": "add"}}]
RID = "a" * 24 + "0" * 8
RID_COLLIDE = "a" * 24 + "1" * 8


def outcome(prompt, model):
    return {"mode": "REMOTE_ANTHROPIC_READ_ONLY",
            "answer": json.dumps({"steps": STEPS}),
            "usage": {"input_tokens": 25, "output_tokens": 20},
            "tools_used": [], "external_dispatch_count": 0}


def request(rid):
    return {"request_id": rid, "provider": PROVIDER, "model": MODEL,
            "goal": GOAL, "paid_api_consent": True}


class PersistedCloudProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.safety = LocalModelSafetyState(self.base / "safety.sqlite3")
        self.calls = []
        def runner(prompt, model):
            self.calls.append((prompt, model))
            return outcome(prompt, model)
        self.remote = LocalOnDemandRemoteAI(
            self.base / "remote.sqlite3",
            additional_runners={PROVIDER: runner})
        self.remote.attach_gate(self.safety, threading.BoundedSemaphore(1),
                                threading.Lock())
        self.plans = LocalAgentPlanStore(self.base / "plans.sqlite3")
        self.bridge = CloudPlanBridge(self.remote, self.plans)

    def tearDown(self):
        self.plans.close()
        self.remote.close()
        self.safety.close()
        self.tmp.cleanup()

    def test_full_origin_survives_store_restart_and_local_execution(self):
        result = self.bridge.propose(request(RID))
        self.assertEqual(result["source_provenance"], "BOUND")
        pid = result["plan"]["plan_id"]
        self.assertEqual(self.plans.get(pid)["cloud_origin"], {
            "provider": PROVIDER, "request_id": RID})
        self.plans.close()
        self.plans = LocalAgentPlanStore(self.base / "plans.sqlite3")
        self.bridge = CloudPlanBridge(self.remote, self.plans)
        self.assertEqual(self.plans.get(pid)["cloud_origin"]["request_id"], RID)
        self.assertEqual(self.plans.approve(pid)["status"], "APPROVED")
        self.assertEqual(self.plans.advance(pid)["status"], "COMPLETED")
        old_calls = len(self.calls)
        recovered = self.bridge.recover({
            "request_id": RID, "model": MODEL, "provider": PROVIDER,
            "goal": GOAL})
        self.assertEqual(recovered["source_provenance"], "BOUND")
        self.assertEqual(recovered["plan"]["status"], "COMPLETED")
        self.assertEqual(len(self.calls), old_calls)

    def test_different_full_id_with_same_prefix_and_identical_steps_is_rejected(self):
        self.bridge.propose(request(RID))
        # A second independently claimed paid request with an intentional
        # 24-hex prefix collision may never adopt the first plan's identity.
        self.remote.run({"request_id": RID_COLLIDE, "provider": PROVIDER,
                         "model": MODEL, "prompt": paid_planning_prompt(GOAL),
                         "paid_api_consent": True})
        before = len(self.calls)
        with self.assertRaisesRegex(CloudPlanError, "identity_conflict"):
            self.bridge.recover({
                "request_id": RID_COLLIDE, "provider": PROVIDER,
                "model": MODEL, "goal": GOAL})
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.plans.get(RID[:24])["cloud_origin"]["request_id"], RID)

    def test_legacy_plan_remains_usable_but_is_not_misrepresented_as_bound(self):
        self.remote.run({"request_id": RID, "provider": PROVIDER,
                         "model": MODEL, "prompt": paid_planning_prompt(GOAL),
                         "paid_api_consent": True})
        legacy = self.plans.create({
            "model": MODEL, "goal": GOAL, "steps": STEPS},
            plan_id=RID[:24])
        self.assertIsNone(legacy["cloud_origin"])
        result = self.bridge.recover({
            "request_id": RID, "provider": PROVIDER,
            "model": MODEL, "goal": GOAL})
        self.assertEqual(result["source_provenance"], "LEGACY_UNBOUND")
        self.assertIsNone(result["plan"]["cloud_origin"])

    def test_invalid_origin_and_corrupt_database_fail_closed(self):
        proposal = {"model": MODEL, "goal": GOAL, "steps": STEPS}
        invalid = (
            {"provider": "unauthorized_provider", "request_id": RID},
            {"provider": PROVIDER, "request_id": "a" * 23},
            {"provider": PROVIDER, "request_id": RID_COLLIDE},
            {"provider": PROVIDER, "request_id": RID, "extra": "bad"},
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(PlanError):
                self.plans.create(proposal, plan_id=RID[:24], cloud_origin=item)
        self.assertEqual(self.plans.list(), [])
        plan = self.plans.create(proposal, plan_id=RID[:24],
                                 cloud_origin={"provider": PROVIDER, "request_id": RID})
        self.assertEqual(plan["cloud_origin"]["provider"], PROVIDER)
        with self.plans.lock, self.plans.db:
            self.plans.db.execute(
                "UPDATE agent_task_plans SET cloud_origin_json=? WHERE plan_id=?",
                (json.dumps({"provider": PROVIDER, "request_id": RID_COLLIDE}),
                 RID[:24]))
        # A changed full request ID with same 24-character prefix must still
        # fail provenance verification at bridge-level (different receipt).
        self.assertEqual(self.plans.get(RID[:24])["cloud_origin"]["request_id"],
                         RID_COLLIDE)
        with self.plans.lock, self.plans.db:
            self.plans.db.execute(
                "UPDATE agent_task_plans SET cloud_origin_json=? WHERE plan_id=?",
                ('{"provider":"malicious","request_id":"a"}', RID[:24]))
        with self.assertRaisesRegex(PlanError, "storage_invalid"):
            self.plans.get(RID[:24])


if __name__ == "__main__":
    unittest.main()
