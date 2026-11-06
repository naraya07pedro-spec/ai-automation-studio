"""Deterministic fixtures for real owner-armed LOCAL Ollama schedule engine."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_model_safety import LocalModelSafetyState
from local_ollama_agent import AgentError
from local_readonly_agent_scheduler import (
    LocalReadOnlyAgentSchedules, AgentScheduleError, validate_cadence)


class Clock:
    def __init__(self):
        self.now=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self,**kwargs):
        self.now+=timedelta(**kwargs)


def config(cadence="minutes",**extra):
    values={"cadence":cadence,"timezone":"Asia/Jakarta"}
    if cadence in ("minutes","hours"):
        values["every"]=extra.pop("every",1)
    else:
        values["at"]=extra.pop("at","08:30")
        if cadence=="weekly":
            values["weekday"]=extra.pop("weekday",0)
        if cadence=="monthly":
            values["monthday"]=extra.pop("monthday",31)
    return {**values,**extra}


class ReadOnlyAIScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.file=Path(self.tmp.name)/"readonly-ai-schedules.sqlite3"
        self.clock=Clock()
        self.answers=[]
        self.called=[]
        def runner(prompt,model):
            self.called.append((prompt,model))
            return {"answer":"A generated answer from local Ollama fixture",
                    "mode":"LOCAL_MODEL","tools_used":["utc_now"],
                    "external_dispatch_count":0}
        self.runner=runner
        self.safety=LocalModelSafetyState()
        self.gate=threading.BoundedSemaphore(1)
        self.gate_lock=threading.Lock()
        self.engine=LocalReadOnlyAgentSchedules(
            self.file,runner=self.runner,clock=self.clock,
            model_safety=self.safety,agent_gate=self.gate,
            agent_state_lock=self.gate_lock)

    def tearDown(self):
        self.engine.close()
        self.safety.close()
        self.tmp.cleanup()

    def make(self,**values):
        inp={"prompt":"Explain the current UTC time using only your tools.",
            "model":"fixture:1","cadence":config(),"max_runs":3,
            "expires_days":7}
        inp.update(values)
        return self.engine.create(inp)

    def due(self,**delta):
        self.clock.advance(**delta)
        return self.engine.run_due()

    def test_real_owner_defined_model_run_and_durable_answer(self):
        row=self.make()
        self.assertEqual(row["status"],"ACTIVE")
        self.assertEqual(self.due(minutes=1),1)
        history=self.engine.history(row["task_id"])["runs"]
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]["status"],"COMPLETED")
        self.assertIn("generated answer",history[0]["answer"])
        self.assertEqual(history[0]["tools_used"],["utc_now"])
        self.assertEqual(self.called,[("Explain the current UTC time using only your tools.","fixture:1")])
        self.assertEqual(self.engine.list()["tasks"][0]["attempted_runs"],1)
        self.assertEqual(self.safety.phase(),"IDLE")

    def test_idle_polling_does_not_write_shared_model_checkpoint(self):
        # Idle polling runs every five seconds and must not write a durable
        # safety transaction thousands of times per day.
        original=self.safety.begin
        def should_not_run():
            raise AssertionError("checkpoint must not begin when no AI task is due")
        self.safety.begin=should_not_run
        try:
            self.assertEqual(self.engine.run_due(),0)
            self.make()
            self.assertEqual(self.engine.run_due(),0)
        finally:
            self.safety.begin=original
        self.assertEqual(self.safety.phase(),"IDLE")

    def test_no_model_call_without_due_and_no_unbounded_catchup(self):
        self.make()
        self.assertEqual(self.engine.run_due(),0)
        self.assertEqual(self.due(days=3),1)
        self.assertEqual(self.engine.run_due(),0)
        self.assertEqual(len(self.called),1)

    def test_hard_max_runs_exhausts(self):
        row=self.make(max_runs=1)
        self.assertEqual(self.due(minutes=1),1)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"EXHAUSTED")
        self.assertEqual(self.due(minutes=1),0)
        self.assertEqual(len(self.called),1)
        with self.assertRaisesRegex(AgentScheduleError,"not_resumable"):
            self.engine.change({"task_id":row["task_id"],"expected_epoch":0},pause=False)

    def test_owner_pause_resume_epoch(self):
        row=self.make()
        paused=self.engine.change({"task_id":row["task_id"],"expected_epoch":0},pause=True)
        self.assertEqual(paused["status"],"PAUSED")
        self.assertEqual(self.due(minutes=1),0)
        with self.assertRaisesRegex(AgentScheduleError,"epoch_conflict"):
            self.engine.change({"task_id":row["task_id"],"expected_epoch":0},pause=False)
        active=self.engine.change({"task_id":row["task_id"],"expected_epoch":1},pause=False)
        self.assertEqual(active["status"],"ACTIVE")
        self.assertEqual(self.engine.run_due(),0)
        self.assertEqual(self.due(minutes=1),1)

    def test_decline_if_manual_agent_owns_inference_gate(self):
        self.make()
        self.gate.acquire()
        try:
            self.assertEqual(self.due(minutes=1),0)
            self.assertEqual(len(self.called),0)
        finally:
            self.gate.release()
        self.assertEqual(self.engine.run_due(),1)

    def test_model_uncertain_quarantines_until_owner_ack(self):
        def timeout(prompt,model):
            raise AgentError("local_model_timeout")
        self.engine.runner=timeout
        row=self.make()
        self.assertEqual(self.due(minutes=1),1)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(self.safety.phase(),"UNCERTAIN")
        self.assertEqual(self.due(minutes=1),0)
        with self.assertRaisesRegex(AgentScheduleError,"checkpoint_not_cleared"):
            self.engine.change({"task_id":row["task_id"],"expected_epoch":1},pause=False)
        self.safety.acknowledge()
        self.engine.runner=self.runner
        self.engine.change({"task_id":row["task_id"],"expected_epoch":1},pause=False)
        self.assertEqual(self.due(minutes=1),1)
        self.assertEqual(self.engine.list()["tasks"][0]["attempted_runs"],2)

    def test_definite_model_unavailable_pauses_without_retry(self):
        self.engine.runner=lambda p,m: (_ for _ in ()).throw(AgentError("local_model_unavailable"))
        row=self.make()
        self.assertEqual(self.due(minutes=1),1)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"PAUSED_ERROR")
        self.assertEqual(self.engine.run_due(),0)
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(self.engine.history(row["task_id"])["runs"][0]["answer"],None)

    def test_reject_model_hallucination_of_external_dispatch(self):
        self.engine.runner=lambda p,m: {"answer":"Sent email!", "mode":"LOCAL_MODEL",
                                        "tools_used":["send_email"],"external_dispatch_count":1}
        row=self.make()
        self.assertEqual(self.due(minutes=1),1)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"PAUSED_ERROR")
        self.assertEqual(self.engine.history(row["task_id"])["runs"][0]["error"],
                         "local_ai_execution_failed")

    def test_reopen_marks_previous_inflight_uncertain_no_replay(self):
        row=self.make()
        with self.engine.lock,self.engine.db:
            self.engine.db.execute("""INSERT INTO ai_schedule_runs(task_id,slot_utc,status,claimed_utc)
                VALUES(?,?,'IN_FLIGHT',?)""",
                (row["task_id"],"2026-11-06T09:01:00+00:00",
                 "2026-11-06T09:01:00+00:00"))
        self.engine.close()
        self.engine=LocalReadOnlyAgentSchedules(
            self.file,runner=self.runner,clock=self.clock,
            model_safety=self.safety,agent_gate=self.gate,
            agent_state_lock=self.gate_lock)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(self.engine.history(row["task_id"])["runs"][0]["status"],
                         "OUTCOME_UNCERTAIN")
        self.assertEqual(self.due(minutes=1),0)

    def test_results_persist_across_restart(self):
        row=self.make()
        self.due(minutes=1)
        self.engine.close()
        self.engine=LocalReadOnlyAgentSchedules(
            self.file,runner=self.runner,clock=self.clock,
            model_safety=self.safety,agent_gate=self.gate,
            agent_state_lock=self.gate_lock)
        self.assertEqual(self.engine.history(row["task_id"])["runs"][0]["status"],
                         "COMPLETED")
        self.assertEqual(self.engine.run_due(),0)

    def test_expired_task_never_calls_model(self):
        self.make(expires_days=1)
        self.assertEqual(self.due(days=2),0)
        self.assertEqual(self.engine.list()["tasks"][0]["status"],"EXPIRED")
        self.assertEqual(self.called,[])

    def test_month_end_and_weekly_use_existing_calendar_math(self):
        self.clock.now=datetime(2026,1,30,9,0,tzinfo=timezone.utc)
        a=self.make(cadence=config("monthly",at="08:00",monthday=31))
        self.assertEqual(a["next_due_utc"][:10],"2026-01-31")
        b=self.make(cadence=config("weekly",at="08:30",weekday=0))
        self.assertEqual(b["cadence"]["weekday"],0)

    def test_invalid_config_model_and_bounds_fail_closed(self):
        for variation in (
            {"model":"../../models"},
            {"max_runs":31},
            {"expires_days":91},
            {"prompt":""},
            {"cadence":{"cadence":"minutes","timezone":"Asia/Jakarta","every":0}},
            {"cadence":{"cadence":"daily","timezone":"Mars","at":"08:00"}},
            {"cadence":{"cadence":"weekly","timezone":"UTC","at":"10:00"}},
        ):
            with self.subTest(variation=variation):
                with self.assertRaises(AgentScheduleError):
                    self.make(**variation)
        self.assertEqual(self.engine.list()["tasks"],[])

    def test_separate_worker_lifecycle_is_independent_of_http(self):
        self.make()
        self.engine.start()
        self.assertTrue(self.engine.list()["worker_running"])
        self.engine.close()
        self.engine=LocalReadOnlyAgentSchedules(
            self.file,runner=self.runner,clock=self.clock,
            model_safety=self.safety,agent_gate=self.gate,
            agent_state_lock=self.gate_lock)
        self.assertEqual(len(self.engine.list()["tasks"]),1)

    def test_capacity_bounded(self):
        for _ in range(12):
            self.make()
        with self.assertRaisesRegex(AgentScheduleError,"capacity_reached"):
            self.make()
