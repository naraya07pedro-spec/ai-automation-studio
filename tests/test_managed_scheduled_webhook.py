"""Integration tests for owner-gated recurring managed HTTPS connector delivery.

Transport is injected; no real n8n endpoint or customer credential is touched.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_recurring_scheduler import LocalRecurringScheduler
from local_workflow_core import LocalWorkflowCore
from local_managed_scheduled_webhook import ManagedScheduledWebhookAutopilot
from local_scheduled_webhook import AutopilotError

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None


class Clock:
    def __init__(self):
        self.time=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.time
    def advance(self, **kwargs):
        self.time+=timedelta(**kwargs)


@unittest.skipUnless(HAS_CRYPTO,"Dedicated managed-autopilot CI installs cryptography")
class ManagedAutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.core=LocalWorkflowCore(self.base/"core.sqlite3")
        code,_=self.core.create({"workflow_id":"managed_flow",
            "display_name":"Managed Flow","service_filter":"automation",
            "min_budget_tier":2,"followup_policy":"review_required"})
        self.assertEqual(code,201)
        self.assertEqual(self.core.activate({
            "workflow_id":"managed_flow","expected_version":1})[0],200)
        self.clock=Clock()
        self.scheduler=LocalRecurringScheduler(
            self.core,self.base/"schedules.sqlite3",clock=self.clock)
        self.schedule=self.scheduler.create({
            "workflow_id":"managed_flow","cadence":"minutes",
            "timezone":"Asia/Jakarta","service_code":"automation",
            "budget_tier":2,"timeline":"soon","every":1})
        self.key=generate_master_key()
        self.events=[]
        def send(url,payload,did,secret):
            self.events.append((url,payload,did,secret))
            return 202
        self.send=send
        self.registry=self.base/"managed-webhooks-vault.json"
        self.manager=LocalConnectorManager(
            self.core,self.registry,sender=self.send,
            clock=self.clock,vault_key=self.key)
        self.manager.add({"connector_id":"target_a","label":"Target A",
                          "url":"https://n8n.example.org/webhook/a-private",
                          "signing_secret":"secret-a"})
        self.manager.add({"connector_id":"target_b","label":"Target B",
                          "url":"https://n8n.example.org/webhook/b-private",
                          "signing_secret":"secret-b"})
        self.policies=self.base/"managed-autopilots"
        self.autopilot=ManagedScheduledWebhookAutopilot(
            self.scheduler,self.manager,None,self.policies,clock=self.clock)

    def tearDown(self):
        self.autopilot.close()
        self.manager.close()
        self.scheduler.close()
        self.core.close()
        self.tmp.cleanup()

    def arm(self,cid="target_a",limit=2,days=7):
        config={"connector_id":cid,
                "schedule_id":self.schedule["schedule_id"],
                "max_sends":limit,"expires_days":days}
        p=self.autopilot.preview(config)
        self.assertEqual(p["status"],"PREVIEW_ONLY")
        self.assertEqual(p["connector_id"],cid)
        self.assertEqual(p["external_write_count"],0)
        return self.autopilot.arm({**config,"preview_token":p["preview_token"]})

    def tick(self,**kwargs):
        self.clock.advance(**kwargs)
        self.scheduler.run_due()
        return self.autopilot.dispatch_due()

    def test_requires_owner_grant_before_any_external_http(self):
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(self.events,[])
        self.assertEqual(self.manager.list()["scheduled_dispatch_enabled"],True)

    def test_one_managed_connector_auto_sends_actual_saved_receipt(self):
        self.arm()
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(len(self.events),1)
        url,payload,did,secret=self.events[0]
        self.assertEqual(url,"https://n8n.example.org/webhook/a-private")
        self.assertEqual(payload["schema"],"aas.scheduled_workflow_receipt.v1")
        self.assertEqual(payload["connector_id"],"target_a")
        self.assertEqual(did,payload["delivery_id"])
        self.assertEqual(secret,"secret-a")
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(self.tick(minutes=1),0)
        state=self.autopilot.list()["policies"]
        self.assertEqual(state[0]["status"],"EXHAUSTED")
        self.assertEqual(state[0]["attempted_posts"],2)

    def test_two_targets_are_separately_granted_with_unique_delivery_ids(self):
        self.arm("target_a")
        self.arm("target_b")
        self.assertEqual(self.tick(minutes=1),2)
        self.assertEqual(len(self.events),2)
        self.assertNotEqual(self.events[0][2],self.events[1][2])
        self.assertEqual({x[1]["connector_id"] for x in self.events},
                         {"target_a","target_b"})
        self.assertEqual(len(self.autopilot.list()["policies"]),2)

    def test_unapproved_connector_never_receives_a_send(self):
        self.arm("target_a")
        self.tick(minutes=1)
        self.assertEqual(len(self.events),1)
        self.assertEqual(self.events[0][1]["connector_id"],"target_a")

    def test_revocation_stops_sends_and_preserves_ledger(self):
        self.arm("target_a")
        self.tick(minutes=1)
        self.manager.revoke("target_a")
        self.assertEqual(self.tick(minutes=1),0)
        self.assertEqual(len(self.events),1)
        records=self.autopilot.list()["policies"]
        self.assertEqual(records[0]["status"],"STOPPED_DESTINATION_CHANGED")
        self.assertFalse(records[0]["destination_matches_configuration"])
        self.assertEqual(self.manager.list()["connectors"][0]["status"],"REVOKED")
        with self.assertRaises(AutopilotError):
            self.autopilot.preview({"connector_id":"target_a",
                "schedule_id":self.schedule["schedule_id"],
                "max_sends":1,"expires_days":1})

    def test_stop_blocks_future_claim_and_uses_epoch(self):
        state=self.arm()
        stop=self.autopilot.stop({
            "connector_id":"target_a",
            "schedule_id":self.schedule["schedule_id"],
            "expected_epoch":state["control_epoch"]})
        self.assertEqual(stop["status"],"STOPPED")
        self.tick(minutes=1)
        self.assertEqual(self.events,[])
        with self.assertRaises(AutopilotError):
            self.autopilot.stop({"connector_id":"target_a",
                "schedule_id":self.schedule["schedule_id"],
                "expected_epoch":state["control_epoch"]})

    def test_restart_preserves_attempt_claim_and_never_replays(self):
        self.arm()
        self.tick(minutes=1)
        self.autopilot.close()
        self.autopilot=ManagedScheduledWebhookAutopilot(
            self.scheduler,self.manager,None,self.policies,clock=self.clock)
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(len(self.events),1)
        self.tick(minutes=1)
        self.assertEqual(len(self.events),2)

    def test_uncertain_delivery_pauses_without_retries(self):
        self.manager._sender=lambda *args: (_ for _ in ()).throw(TimeoutError())
        self.arm()
        self.assertEqual(self.tick(minutes=1),1)
        self.assertEqual(self.tick(minutes=1),0)
        state=self.autopilot.list()
        self.assertEqual(state["policies"][0]["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(state["attempts"][0]["status"],"OUTCOME_UNCERTAIN")

    def test_schema_and_invalid_connector_are_denied(self):
        with self.assertRaises(AutopilotError):
            self.autopilot.preview({"connector_id":"../../escape",
                "schedule_id":self.schedule["schedule_id"],
                "max_sends":1,"expires_days":1})
        with self.assertRaises(AutopilotError):
            self.autopilot.arm({"connector_id":"target_a","schedule_id":
                self.schedule["schedule_id"],"max_sends":1,"expires_days":1,
                "preview_token":"not-reviewed"})
        self.assertEqual(self.events,[])

    def test_plaintext_registry_is_not_eligible_for_unattended_sends(self):
        legacy=LocalConnectorManager(self.core,self.base/"legacy.json",
                                     sender=self.send,clock=self.clock)
        try:
            with self.assertRaisesRegex(AutopilotError,"encrypted_managed"):
                ManagedScheduledWebhookAutopilot(
                    self.scheduler,legacy,None,self.base/"legacy-policies",
                    clock=self.clock)
        finally:
            legacy.close()

    def test_no_bypass_if_a_send_and_revoke_race(self):
        entered=threading.Event()
        release=threading.Event()
        def blocking_send(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            self.events.append(args)
            return 202
        self.manager._sender=blocking_send
        self.arm()
        self.clock.advance(minutes=1)
        self.scheduler.run_due()
        thread=threading.Thread(target=self.autopilot.dispatch_due)
        thread.start()
        self.assertTrue(entered.wait(3))
        revoked=[]
        revoke_thread=threading.Thread(
            target=lambda:revoked.append(self.manager.revoke("target_a")))
        revoke_thread.start()
        self.assertTrue(revoke_thread.is_alive(),
                        "Revocation must serialize with in-flight send")
        release.set()
        thread.join(5)
        revoke_thread.join(5)
        self.assertTrue(revoked)
        self.clock.advance(minutes=1)
        self.scheduler.run_due()
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(len(self.events),1)

    def test_invalid_storage_is_not_silently_recreated(self):
        self.arm()
        self.autopilot.close()
        self.autopilot._policies={}
        path=self.policies/"target_a.sqlite3"
        path.unlink()
        # The policy is physically gone; no automatic replacement by scanning.
        self.assertEqual(self.autopilot.list()["policies"],[])
        self.assertEqual(self.autopilot.dispatch_due(),0)
