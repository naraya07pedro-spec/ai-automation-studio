"""Slice 52: durable, bounded human-approved local agent steps (synthetic tests)."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_agent_task_plans import (LocalAgentPlanStore, PlanError,
                                    propose_readonly_plan, validate_steps)


def proposal(steps=None):
    if steps is None:
        steps=[{"tool":"calculate","args":{"a":"7","b":"8","operation":"multiply"}},
               {"tool":"utc_now","args":{}}]
    return {"model":"fixture:small","goal":"Multiply then check UTC time","steps":steps}


class TaskPlanUnitTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"agent-tasks.sqlite3"
        self.store=LocalAgentPlanStore(self.path)
        self.addCleanup(self.store.close)

    def test_model_proposes_json_without_invoke_permissions(self):
        calls=[]
        def fake(payload):
            calls.append(payload)
            self.assertEqual("json",payload["format"])
            self.assertNotIn("tools",payload)
            self.assertEqual("fixture:small",payload["model"])
            return {"message":{"content":json.dumps({"steps":proposal()["steps"]})}}
        result=propose_readonly_plan("Multiply then check UTC time","fixture:small",fake)
        self.assertEqual(2,len(result["steps"]))
        self.assertEqual(1,len(calls))

    def test_model_rejects_malformed_unauthorized_or_empty_plans(self):
        cases=[
            '{"steps":[]}', '{"steps":[{"tool":"send_email","args":{}}]}',
            '{"steps":[{"tool":"run_shell","args":{"cmd":"ls"}}]}',
            '{"steps":[{"tool":"calculate","args":{"a":"1","b":"0","operation":"divide"}}]}',
            '{"steps":[{"tool":"utc_now","args":{"extra":"no"}}]}',
            '{"steps":[{"tool":"utc_now","args":{},"unapproved":true}]}',
            '{"steps":[{"tool":"utc_now","args":{}},{"tool":"utc_now","args":{}},'
            '{"tool":"utc_now","args":{}},{"tool":"utc_now","args":{}}]}',
            '{"steps":"not-a-list"}', '{"unknown":[]}', 'not-json']
        for raw in cases:
            with self.subTest(value=raw[:60]),self.assertRaises(PlanError):
                propose_readonly_plan("Read-only goal","fixture:small",
                                      lambda _,raw=raw:{"message":{"content":raw}})
        for malformed in ({}, {"message":{}},{"message":{"content":6}},
                          {"message":{"content":"x"*8001}}):
            with self.assertRaises(PlanError):
                propose_readonly_plan("read UTC","fixture:small",lambda _:malformed)

    def test_prepared_approval_and_stepwise_execution(self):
        plan=self.store.create(proposal())
        self.assertEqual("PREPARED",plan["status"])
        self.assertEqual([],plan["results"])
        with self.assertRaisesRegex(PlanError,"plan_state_conflict"):
            self.store.advance(plan["plan_id"])
        accepted=self.store.approve(plan["plan_id"])
        self.assertEqual("APPROVED",accepted["status"])
        self.assertEqual([],accepted["results"])
        first=self.store.advance(plan["plan_id"])
        self.assertEqual("NEXT_READY",first["status"])
        self.assertEqual("56",first["results"][0]["output"])
        self.assertEqual(1,first["next_index"])
        second=self.store.advance(plan["plan_id"])
        self.assertEqual("COMPLETED",second["status"])
        self.assertEqual("utc_now",second["results"][1]["tool"])
        self.assertEqual(2,second["next_index"])
        with self.assertRaises(PlanError):self.store.advance(plan["plan_id"])
        with self.assertRaises(PlanError):self.store.approve(plan["plan_id"])

    def test_power_loss_marks_interrupted_never_auto_resumes(self):
        plan=self.store.create(proposal())
        pid=plan["plan_id"]
        self.store.approve(pid)
        with self.store.lock,self.store.db:
            self.store.db.execute("""UPDATE agent_task_plans SET status='IN_FLIGHT'
                                      WHERE plan_id=?""",(pid,))
        # Reopening the file simulates process death during the read-only step.
        recovered=LocalAgentPlanStore(self.path)
        try:
            self.assertEqual("INTERRUPTED",recovered.get(pid)["status"])
            self.assertEqual([],recovered.get(pid)["results"])
            with self.assertRaises(PlanError):recovered.advance(pid)
            resumed=recovered.resume_interrupted(pid)
            self.assertEqual("APPROVED",resumed["status"])
            self.assertEqual("NEXT_READY",recovered.advance(pid)["status"])
        finally:recovered.close()

    def test_state_survives_restart_and_explicit_delete(self):
        plan=self.store.create(proposal())
        pid=plan["plan_id"]
        self.store.approve(pid)
        self.store.advance(pid)
        other=LocalAgentPlanStore(self.path)
        try:
            self.assertEqual("NEXT_READY",other.get(pid)["status"])
            self.assertEqual("56",other.get(pid)["results"][0]["output"])
            self.assertEqual("CANCELLED",other.cancel(pid)["status"])
            self.assertEqual("DELETED",other.delete(pid)["status"])
            self.assertEqual([],other.list())
        finally:other.close()

    def test_capacity_and_cancellation_do_not_evict(self):
        ids=[self.store.create(proposal())["plan_id"] for _ in range(12)]
        with self.assertRaisesRegex(PlanError,"plan_capacity_reached"):
            self.store.create(proposal())
        self.assertEqual(12,len(self.store.list()))
        self.assertEqual("CANCELLED",self.store.cancel(ids[0])["status"])
        with self.assertRaises(PlanError):self.store.create(proposal())
        self.store.delete(ids[0])
        self.assertEqual("PREPARED",self.store.create(proposal())["status"])

    def test_invalid_ids_and_unauthorized_actions(self):
        plan=self.store.create(proposal())
        for pid in ("","../bad",None,True,4,"x"*24,"A"*24):
            with self.subTest(pid=pid),self.assertRaises(PlanError):
                self.store.get(pid)
        with self.assertRaises(PlanError):self.store.delete(plan["plan_id"])
        with self.assertRaises(PlanError):self.store.resume_interrupted(plan["plan_id"])
        self.store.cancel(plan["plan_id"])
        with self.assertRaises(PlanError):self.store.approve(plan["plan_id"])

    def test_readonly_tools_are_the_only_allowlisted_steps(self):
        self.assertEqual(1,len(validate_steps([{"tool":"utc_now","args":{}}])))
        for args in ({"operation":"add","a":"NaN","b":"1"},
                     {"operation":"multiply","a":"9999999999999999","b":"2"}):
            with self.assertRaises(PlanError):
                validate_steps([{"tool":"calculate","args":args}])
        with self.assertRaisesRegex(PlanError,"tool_not_allowed"):
            validate_steps([{"tool":"write_file","args":{}}])


if __name__=="__main__":
    unittest.main()
