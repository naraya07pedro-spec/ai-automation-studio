"""Actual scheduled rule -> owner-granted outbound HTTPS transport acceptance.

No internet access: injected transport records intended HTTP sends. Tests do
not install plugins or contact a real n8n endpoint.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_recurring_scheduler import LocalRecurringScheduler
from local_workflow_core import LocalWorkflowCore
from local_outbound_webhook import LocalOutboundWebhook
from local_scheduled_webhook import ScheduledWebhookAutopilot, AutopilotError

ENDPOINT = "https://hooks.n8n.cloud/webhook/owned-receiver-for-test"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class AutopilotContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.core = LocalWorkflowCore(base / "core.sqlite")
        status, row = self.core.create({
            "workflow_id": "scheduled_outbound", "display_name": "Scheduled Outbound",
            "service_filter": "automation", "min_budget_tier": 2,
            "followup_policy": "review_required"})
        self.assertEqual(status, 201)
        self.assertEqual(self.core.activate({
            "workflow_id": "scheduled_outbound", "expected_version": 1})[0], 200)
        self.clock = Clock()
        self.scheduler = LocalRecurringScheduler(self.core, base / "schedules.sqlite",
                                                 clock=self.clock)
        self.schedule = self.scheduler.create({
            "workflow_id": "scheduled_outbound", "cadence": "minutes",
            "timezone": "Asia/Jakarta", "service_code": "automation",
            "budget_tier": 2, "timeline": "soon", "every": 1})
        self.events = []
        def fake_send(url, payload, did, secret):
            self.events.append((url, payload, did, secret))
            return 202
        self.send = fake_send
        self.outbound = LocalOutboundWebhook(
            self.core, base / "outbound.sqlite", url=ENDPOINT,
            sender=self.send, secret="fixture-secret")
        self.policydb = base / "autopilot.sqlite"
        self.autopilot = ScheduledWebhookAutopilot(
            self.scheduler,self.outbound,None,self.policydb,self.clock)

    def tearDown(self):
        self.autopilot.close()
        self.outbound.close()
        self.scheduler.close()
        self.core.close()
        self.tmp.cleanup()

    def arm(self, cap=2, days=7):
        req={"schedule_id":self.schedule["schedule_id"],
             "max_sends":cap,"expires_days":days}
        preview=self.autopilot.preview(req)
        self.assertEqual(preview["external_write_count"],0)
        self.assertEqual(self.events,[])
        self.assertEqual(preview["destination_hostname"],"hooks.n8n.cloud")
        return self.autopilot.arm({**req,"preview_token":preview["preview_token"]})

    def tick(self, **elapsed):
        self.clock.advance(**elapsed)
        self.scheduler.run_due()
        return self.autopilot.dispatch_due()

    def test_one_armed_due_slot_really_calls_sender_and_respects_hard_cap(self):
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.arm(cap=2)
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(len(self.events),1)
        url,payload,did,secret=self.events[0]
        self.assertEqual(url,ENDPOINT)
        self.assertEqual(payload["schema"],"aas.scheduled_workflow_receipt.v1")
        self.assertEqual(payload["decision"],"NEEDS_REVIEW_LOCAL")
        self.assertEqual(payload["workflow_version"],1)
        self.assertEqual(secret,"fixture-secret")
        self.assertEqual(did,payload["delivery_id"])
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(len(self.events),2)
        pol=self.autopilot.get(self.schedule["schedule_id"])
        self.assertEqual(pol["status"],"EXHAUSTED")
        self.assertEqual(pol["attempted_posts"],2)
        history=self.autopilot.list()["attempts"]
        self.assertEqual(len(history),2)
        self.assertTrue(all(a["status"]=="REMOTE_ACCEPTED" for a in history))

    def test_never_sends_until_owner_arms(self):
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(self.events,[])
        self.arm()
        # Policy starts only from the *next* due slot.
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(self.tick(minutes=1),1)

    def test_emergency_stop_blocks_new_claims(self):
        self.arm(cap=5)
        sid=self.schedule["schedule_id"]
        response=self.autopilot.stop(sid,0)
        self.assertEqual(response["status"],"STOPPED")
        self.assertEqual(self.tick(minutes=30),0)
        self.assertEqual(self.events,[])
        with self.assertRaises(AutopilotError) as replay:
            self.autopilot.stop(sid,0)
        self.assertEqual(replay.exception.status,409)

    def test_timeout_pauses_policy_and_never_automatically_retries_after_restart(self):
        self.arm(cap=5)
        def ambiguous(*args):
            self.events.append("HTTP_ATTEMPT")
            raise TimeoutError("may already have reached n8n")
        self.outbound.sender=ambiguous
        self.assertEqual(self.tick(minutes=1),1)
        sid=self.schedule["schedule_id"]
        self.assertEqual(self.autopilot.get(sid)["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(len(self.events),1)
        self.clock.advance(minutes=5)
        self.autopilot.close()
        self.autopilot=ScheduledWebhookAutopilot(
            self.scheduler,self.outbound,None,self.policydb,self.clock)
        self.scheduler.run_due()
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(len(self.events),1)

    def test_restart_inflight_is_marked_unknown_without_send(self):
        self.arm()
        sid=self.schedule["schedule_id"]
        with self.autopilot.db:
            self.autopilot.db.execute("""INSERT INTO scheduled_webhook_attempts
                (schedule_id,slot_utc,delivery_id,status,payload_sha256,claimed_utc)
                VALUES(?,?,'a','IN_FLIGHT','b',?)""",
                (sid,self.schedule["next_due_utc"],self.schedule["created_utc"]))
        self.autopilot.close()
        self.autopilot=ScheduledWebhookAutopilot(
            self.scheduler,self.outbound,None,self.policydb,self.clock)
        self.assertEqual(self.autopilot.get(sid)["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(self.autopilot.list()["attempts"][0]["status"],"OUTCOME_UNCERTAIN")
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(self.events,[])

    def test_schedule_pause_or_source_version_drift_revokes_future_send(self):
        self.arm(cap=3)
        sid=self.schedule["schedule_id"]
        self.scheduler.change(sid,0,pause=True)
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(self.autopilot.get(sid)["status"],"STOPPED_SOURCE_CHANGED")
        self.assertEqual(self.events,[])

    def test_new_owner_preview_rejected_after_schedule_changes(self):
        req={"schedule_id":self.schedule["schedule_id"],"max_sends":2,
             "expires_days":3}
        p=self.autopilot.preview(req)
        sid=self.schedule["schedule_id"]
        self.scheduler.change(sid,0,pause=True)
        self.scheduler.change(sid,1,pause=False)
        with self.assertRaises(AutopilotError):
            self.autopilot.arm({**req,"preview_token":p["preview_token"]})
        self.assertEqual(self.events,[])

    def test_fails_closed_if_destination_changes(self):
        self.arm()
        self.outbound.url="https://different.n8n.cloud/webhook/another-endpoint"
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(self.autopilot.get(self.schedule["schedule_id"])["status"],
                         "STOPPED_DESTINATION_CHANGED")
        self.assertEqual(self.events,[])

    def test_expiration_and_limit_validation(self):
        req={"schedule_id":self.schedule["schedule_id"],
             "max_sends":31,"expires_days":7}
        with self.assertRaises(AutopilotError):
            self.autopilot.preview(req)
        with self.assertRaises(AutopilotError):
            self.autopilot.preview({**req,"max_sends":2,"expires_days":91})
        self.arm(cap=1,days=1)
        self.assertEqual(self.tick(days=2),0)
        self.assertEqual(self.autopilot.get(self.schedule["schedule_id"])["status"],"EXPIRED")
        self.assertEqual(self.events,[])

    def test_preview_expiry_and_token_replacement(self):
        req={"schedule_id":self.schedule["schedule_id"],
             "max_sends":2,"expires_days":7}
        old=self.autopilot.preview(req)
        newer=self.autopilot.preview(req)
        with self.assertRaises(AutopilotError):
            self.autopilot.arm({**req,"preview_token":old["preview_token"]})
        self.clock.advance(seconds=301)
        with self.assertRaises(AutopilotError):
            self.autopilot.arm({**req,"preview_token":newer["preview_token"]})
        self.assertEqual(self.events,[])

    def test_claimed_attempt_does_not_duplicate_after_tick_or_restart(self):
        self.arm(cap=5)
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.autopilot.close()
        self.autopilot=ScheduledWebhookAutopilot(
            self.scheduler,self.outbound,None,self.policydb,self.clock)
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(len(self.events),1)
        self.assertEqual(self.tick(minutes=1),1)
        self.assertNotEqual(self.events[0][2],self.events[1][2])


if __name__=="__main__":
    unittest.main()
