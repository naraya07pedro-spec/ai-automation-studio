"""Focused acceptance tests for a REAL durable minute-to-month local worker."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_workflow_core import LocalWorkflowCore
from local_recurring_scheduler import (
    LocalRecurringScheduler, ScheduleError, next_occurrence, validate_config,
)

BASE = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now=BASE):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def config(cadence="minutes", **extra):
    data = {"workflow_id": "studio_automation", "cadence": cadence,
            "timezone": "Asia/Jakarta", "service_code": "automation",
            "budget_tier": 2, "timeline": "soon"}
    if cadence in ("minutes", "hours"):
        data["every"] = 1
    else:
        data["at"] = "08:30"
        if cadence == "weekly":
            data["weekday"] = 0
        if cadence == "monthly":
            data["monthday"] = 31
    data.update(extra)
    return data


def workflow(core):
    status, row = core.create({
        "workflow_id": "studio_automation",
        "display_name": "Studio Automation",
        "service_filter": "automation",
        "min_budget_tier": 2,
        "followup_policy": "review_required"})
    assert status == 201, row
    status, row = core.activate({"workflow_id": "studio_automation",
                                 "expected_version": 1})
    assert status == 200, row


class CalendarTests(unittest.TestCase):
    def test_minute_and_hour_intervals_are_real_future_instants(self):
        self.assertEqual(next_occurrence(config("minutes", every=1), BASE),
                         BASE + timedelta(minutes=1))
        self.assertEqual(next_occurrence(config("hours", every=3), BASE),
                         BASE + timedelta(hours=3))

    def test_daily_and_weekly_wib_timezone(self):
        t = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)  # Fri 09:00 WIB
        self.assertEqual(next_occurrence(config("daily", at="08:30"), t),
                         datetime(2026, 11, 7, 1, 30, tzinfo=timezone.utc))
        self.assertEqual(next_occurrence(config("weekly", weekday=0, at="08:30"), t),
                         datetime(2026, 11, 9, 1, 30, tzinfo=timezone.utc))

    def test_month_end_31_clamps_including_leap_february(self):
        jan = datetime(2027, 1, 31, 17, 0, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(config("monthly", monthday=31), jan),
                         datetime(2027, 2, 28, 1, 30, tzinfo=timezone.utc))
        leap = datetime(2028, 1, 31, 17, 0, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(config("monthly", monthday=31), leap),
                         datetime(2028, 2, 29, 1, 30, tzinfo=timezone.utc))

    def test_validation_does_not_expand_into_external_actions(self):
        for payload in (
            dict(config(), command="rm -rf /"),
            dict(config(), webhook="https://attacker.example/"),
            dict(config(), every=0),
            dict(config(), every=1441),
            dict(config("daily"), at="25:61"),
            dict(config("weekly"), weekday=7),
            dict(config("monthly"), monthday=32),
            dict(config(), budget_tier=True),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ScheduleError):
                    validate_config(payload)


class DurableWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.core_db = base / "workflows.sqlite"
        self.scheduler_db = base / "schedule.sqlite"
        self.core = LocalWorkflowCore(self.core_db)
        workflow(self.core)
        self.clock = Clock()
        self.engine = LocalRecurringScheduler(self.core, self.scheduler_db, self.clock)

    def tearDown(self):
        self.engine.close()
        self.core.close()
        self.temp.cleanup()

    def test_actual_minute_due_saves_decision_and_counts(self):
        row = self.engine.create(config())
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["completed_runs"], 0)
        self.assertEqual(self.engine.run_due(), 0)
        self.clock.advance(minutes=1)
        self.assertEqual(self.engine.run_due(), 1)
        after = self.engine.get(row["schedule_id"])
        self.assertEqual(after["completed_runs"], 1)
        history = self.engine.history(row["schedule_id"])["runs"]
        self.assertEqual(history[0]["status"], "COMPLETED")
        self.assertEqual(history[0]["decision"], "NEEDS_REVIEW_LOCAL")
        self.assertEqual(history[0]["reason"], "POLICY_REQUIRES_REVIEW")
        self.assertEqual(self.engine.run_due(), 0)

    def test_pause_resume_is_versioned_and_prevents_due(self):
        row = self.engine.create(config())
        sid = row["schedule_id"]
        paused = self.engine.change(sid, 0, pause=True)
        self.assertEqual(paused["status"], "PAUSED")
        with self.assertRaises(ScheduleError) as stale:
            self.engine.change(sid, 0, pause=False)
        self.assertEqual(stale.exception.status, 409)
        self.clock.advance(hours=2)
        self.assertEqual(self.engine.run_due(), 0)
        resumed = self.engine.change(sid, 1, pause=False)
        self.assertEqual(resumed["status"], "ACTIVE")
        self.assertEqual(resumed["completed_runs"], 0)
        self.clock.advance(minutes=1)
        self.assertEqual(self.engine.run_due(), 1)

    def test_restart_survives_and_catches_up_once(self):
        sid = self.engine.create(config())["schedule_id"]
        self.clock.advance(hours=4)
        self.engine.close()
        self.engine = LocalRecurringScheduler(self.core, self.scheduler_db, self.clock)
        self.assertEqual(self.engine.run_due(), 1)
        after = self.engine.get(sid)
        self.assertEqual(after["completed_runs"], 1)
        self.assertEqual(self.engine.run_due(), 0)
        self.clock.advance(minutes=1)
        self.assertEqual(self.engine.run_due(), 1)
        self.assertEqual(self.engine.get(sid)["completed_runs"], 2)

    def test_inflight_recovery_uses_frozen_snapshot_not_live_rule(self):
        row = self.engine.create(config())
        sid = row["schedule_id"]
        slot = row["next_due_utc"]
        original_snapshot = {
            "status": "ACTIVE", "service_filter": "automation",
            "min_budget_tier": 2, "followup_policy": "draft_only",
            "version": 1, "service_code": "automation",
            "budget_tier": 2, "timeline": "soon"}
        with self.engine.db:
            self.engine.db.execute("""INSERT INTO recurring_schedule_runs
                (schedule_id,slot_utc,status,snapshot_json)
                VALUES(?,?,'IN_FLIGHT',?)""",
                (sid, slot, json.dumps(original_snapshot)))
            self.engine.db.execute("""UPDATE recurring_schedules
                SET next_due_utc=? WHERE schedule_id=?""",
                ("2030-01-01T00:00:00+00:00",sid))
        self.engine.close()
        self.engine = LocalRecurringScheduler(self.core, self.scheduler_db, self.clock)
        self.assertEqual(self.engine.run_due(), 1)
        result = self.engine.history(sid)["runs"][0]
        self.assertEqual(result["decision"], "QUALIFIED_DRAFT_ONLY")
        self.assertEqual(result["workflow_version"], 1)
        self.assertEqual(self.engine.run_due(), 0)

    def test_nonactive_source_pauses_recurring_schedule(self):
        sid = self.engine.create(config())["schedule_id"]
        row = self.core.list_workflows()["workflows"][0]
        status, _ = self.core.set_paused({
            "workflow_id": row["workflow_id"],
            "expected_version": row["version"],
            "expected_epoch": row["control_epoch"]}, True)
        self.assertEqual(status, 200)
        self.clock.advance(minutes=1)
        self.assertEqual(self.engine.run_due(), 1)
        self.assertEqual(self.engine.get(sid)["status"], "PAUSED")
        self.assertEqual(self.engine.get(sid)["blocked_runs"], 1)
        self.assertEqual(self.engine.history(sid)["runs"][0]["reason"],
                         "WORKFLOW_NOT_ACTIVE")

    def test_cannot_schedule_draft_or_unknown_workflow(self):
        with self.assertRaises(ScheduleError) as err:
            self.engine.create(config(workflow_id="no_such_workflow"))
        self.assertEqual(err.exception.status, 404)

    def test_no_unbounded_burst_after_long_downtime(self):
        sid = self.engine.create(config())["schedule_id"]
        self.clock.advance(days=40)
        self.assertEqual(self.engine.run_due(), 1)
        self.assertEqual(self.engine.run_due(), 0)
        self.assertEqual(self.engine.get(sid)["completed_runs"], 1)


if __name__ == "__main__":
    unittest.main()
