"""Disposable SQLite proof of worker-origin deterministic slots after reboot.

No real cloud/systemd/owner task is used by CI. The production CLI requires
the actual on-host baseline, systemd service invocation and Linux boot ID.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
sys.path.insert(0,str(ROOT/"deploy"))
import local_recurring_scheduler as schedule
import verify_post_reboot_rule_run as watcher
import verify_private_vps_reboot as reboot
from local_workflow_core import LocalWorkflowCore

INVOC="a"*32
OTHER="b"*32
BOOT_BEFORE="9a32b40f-2f11-4738-a569-129de6baf001"
BOOT_AFTER="9a32b40f-2f11-4738-a569-129de6baf002"


class Clock:
    def __init__(self,now):
        self.now=now
    def __call__(self):
        return self.now
    def advance(self,**changes):
        self.now+=timedelta(**changes)


class LiveWorkerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.state=self.root/".ai-automation-studio"
        self.state.mkdir(mode=0o700)
        self.core=LocalWorkflowCore(self.root/"core.sqlite3")
        code,_=self.core.create({
            "workflow_id":"postboot_rule", "display_name":"Postboot Rule",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})
        self.assertEqual(code,201)
        code,_=self.core.activate({"workflow_id":"postboot_rule",
                                   "expected_version":1})
        self.assertEqual(code,200)
        now=datetime.now(timezone.utc).replace(microsecond=0)
        self.clock=Clock(now-timedelta(minutes=10))
        self.boot=now-timedelta(minutes=5)
        self.baseline=now-timedelta(minutes=6)
        with patch.object(schedule,"_systemd_worker_identity",
                          return_value=(INVOC,BOOT_AFTER)):
            self.engine=schedule.LocalRecurringScheduler(
                self.core,self.state/"recurring-schedules.sqlite3",
                clock=self.clock)
        self.db=self.state/"recurring-schedules.sqlite3"
        self.db.chmod(0o600)
        self.row=self.engine.create({
            "workflow_id":"postboot_rule",
            "cadence":"minutes","timezone":"Asia/Jakarta",
            "every":1,"service_code":"automation","budget_tier":2,
            "timeline":"soon"})

    def tearDown(self):
        self.engine.close()
        self.core.close()
        self.tmp.cleanup()

    def tick_preboot_slot(self,worker=True):
        self.clock.advance(minutes=6)  # first historical due slot precedes boot
        if worker:
            return self.engine.run_due(
                _origin_token=self.engine._worker_origin_token)
        return self.engine.run_due()

    def tick_postboot_slot(self,worker=True):
        self.clock.advance(minutes=2)
        if worker:
            return self.engine.run_due(
                _origin_token=self.engine._worker_origin_token)
        return self.engine.run_due()

    def scan(self, invocation=INVOC, boot_id=BOOT_AFTER):
        return watcher._read_observations(
            self.state,invocation,boot_id,self.boot,self.baseline)

    def test_real_worker_token_captures_systemd_and_boot_on_completed_rule(self):
        self.assertEqual(self.tick_preboot_slot(),1)
        self.assertEqual(self.tick_postboot_slot(),1)
        with sqlite3.connect(self.db) as db:
            rows=db.execute("""SELECT status,completed_by_invocation,
                completed_boot_id FROM recurring_schedule_runs
                ORDER BY slot_utc""").fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1],("COMPLETED",INVOC,BOOT_AFTER))
        result=self.scan()
        self.assertEqual(result["matching_completed_receipts"],1)
        self.assertNotIn(self.row["schedule_id"],json.dumps(result))
        self.assertNotIn(INVOC,json.dumps(result))

    def test_first_replayed_preboot_slot_is_not_false_new_execution(self):
        self.tick_preboot_slot()
        self.assertEqual(self.scan()["matching_completed_receipts"],0)

    def test_manual_due_invocation_remains_unattested(self):
        self.tick_preboot_slot(worker=False)
        self.tick_postboot_slot(worker=False)
        self.assertEqual(self.scan()["matching_completed_receipts"],0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("""SELECT count(*) FROM
                recurring_schedule_runs WHERE completed_by_invocation IS NULL"""
                ).fetchone()[0],2)

    def test_wrong_service_invocation_or_boot_id_rejected(self):
        self.tick_preboot_slot()
        self.tick_postboot_slot()
        self.assertEqual(self.scan(invocation=OTHER)["matching_completed_receipts"],0)
        self.assertEqual(self.scan(boot_id=BOOT_BEFORE)["matching_completed_receipts"],0)

    def test_current_worker_does_not_attest_when_not_real_systemd(self):
        self.engine._systemd_invocation=None
        self.tick_preboot_slot()
        self.tick_postboot_slot()
        self.assertEqual(self.scan()["matching_completed_receipts"],0)

    def test_worker_loop_uses_private_token_manual_calls_do_not(self):
        observed=[]
        original=self.engine.run_due
        def fake_due(*, _origin_token=None):
            observed.append(_origin_token is self.engine._worker_origin_token)
            self.engine.stop_signal.set()
            return 0
        self.engine.run_due=fake_due
        try:
            self.engine._loop()
        finally:
            self.engine.run_due=original
            self.engine.stop_signal.clear()
        self.assertEqual(observed,[True])

    def test_absent_new_provenance_columns_refuse_postboot_verification(self):
        with sqlite3.connect(self.db) as db:
            db.execute("ALTER TABLE recurring_schedule_runs DROP COLUMN completed_by_invocation")
        with self.assertRaisesRegex(watcher.PostBootRuleError,
                                    "worker_receipt_schema_missing_or_unreadable"):
            self.scan()

    def test_existing_preupgrade_db_migrates_without_inventing_receipts(self):
        old=self.state/"old-rule-ledger.sqlite3"
        with sqlite3.connect(old) as db:
            db.execute("""CREATE TABLE recurring_schedule_runs (
                schedule_id TEXT,slot_utc TEXT,status TEXT,
                snapshot_json TEXT,decision TEXT,reason TEXT,
                workflow_version INTEGER,finished_utc TEXT,
                PRIMARY KEY(schedule_id,slot_utc))""")
            db.execute("""INSERT INTO recurring_schedule_runs
                (schedule_id,slot_utc,status,snapshot_json)
                VALUES ('fixture','2026-11-01T00:00:00+00:00',
                        'COMPLETED','{}')""")
        old.chmod(0o600)
        with patch.object(schedule,"_systemd_worker_identity",
                          return_value=(INVOC,BOOT_AFTER)):
            migrated=schedule.LocalRecurringScheduler(
                self.core,old,clock=self.clock)
        try:
            with migrated.lock:
                columns={r[1] for r in migrated.db.execute(
                    "PRAGMA table_info(recurring_schedule_runs)")}
                prior=migrated.db.execute("""SELECT completed_by_invocation,
                    completed_boot_id FROM recurring_schedule_runs
                    WHERE schedule_id='fixture'""").fetchone()
            self.assertIn("completed_by_invocation",columns)
            self.assertIn("completed_boot_id",columns)
            self.assertEqual(prior,(None,None))
        finally:
            migrated.close()

    def test_reject_future_captured_schedule_even_with_valid_service_tag(self):
        self.tick_preboot_slot()
        self.tick_postboot_slot()
        earlier=self.clock.now-timedelta(hours=3)
        self.assertEqual(watcher._read_observations(
            self.state,INVOC,BOOT_AFTER,self.boot,earlier
        )["matching_completed_receipts"],0)

    def test_read_only_scanner_does_not_change_owned_db(self):
        self.tick_preboot_slot()
        self.tick_postboot_slot()
        before=self.db.read_bytes()
        self.scan()
        self.assertEqual(self.db.read_bytes(),before)


class ServiceObservationContracts(unittest.TestCase):
    def setUp(self):
        self.boot=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=5)
        self.before={
            "schema":1,
            "boot_id":BOOT_BEFORE,
            "captured_utc":(self.boot-timedelta(minutes=1)).isoformat(),
            "state":{"stores":{"recurring-schedules.sqlite3":{
                "present":True,"task_count":1,"ids_sha256":"c"*64,"run_count":2}}},
        }
        self.after={
            "schema":1,"boot_id":BOOT_AFTER,
            "state":{"stores":{"recurring-schedules.sqlite3":{
                "present":True,"task_count":1,"ids_sha256":"c"*64,"run_count":3}}},
        }

    def patches(self, **kwargs):
        return contextlib.ExitStack()

    def test_live_observation_passes_only_matching_current_worker_and_postboot_slot(self):
        with patch.object(reboot,"live_snapshot",return_value=self.after) as live, \
             patch.object(reboot,"baseline_path",return_value=Path("/fixture/baseline")), \
             patch.object(reboot,"read_baseline",return_value=self.before), \
             patch.object(reboot,"compare_reboot",
                          return_value={"schedule_persistence_proven":True}), \
             patch.object(watcher,"_state_path",return_value=Path("/fixture")), \
             patch.object(watcher,"linux_boot_time",return_value=self.boot), \
             patch.object(watcher,"current_invocation",return_value=INVOC), \
             patch.object(watcher,"_read_observations",
                          return_value={"matching_completed_receipts":1,
                                        "latest_completed_utc":self.boot.isoformat()}):
            result=watcher.observe(0)
        self.assertEqual(result["status"],watcher.STATUS_OBSERVED)
        self.assertTrue(result["worker_invocation_matched_active_systemd"])
        self.assertFalse(result["independent_tencent_identity_verified"])
        self.assertFalse(result["service_autostart_without_intervention_verified"])
        self.assertFalse(result["continued_24h_operation_verified"])
        self.assertEqual(live.call_count,2)

    def test_without_receipt_cannot_claim_scheduler_recovery(self):
        with patch.object(reboot,"live_snapshot",return_value=self.after), \
             patch.object(reboot,"baseline_path",return_value=Path("/fixture/baseline")), \
             patch.object(reboot,"read_baseline",return_value=self.before), \
             patch.object(reboot,"compare_reboot",
                          return_value={"schedule_persistence_proven":True}), \
             patch.object(watcher,"_state_path",return_value=Path("/fixture")), \
             patch.object(watcher,"linux_boot_time",return_value=self.boot), \
             patch.object(watcher,"current_invocation",return_value=INVOC), \
             patch.object(watcher,"_read_observations",
                          return_value={"matching_completed_receipts":0,
                                        "latest_completed_utc":None}):
            result=watcher.observe(0)
        self.assertEqual(result["status"],watcher.STATUS_PENDING)
        self.assertEqual(result["observed_completed_local_rule_slots"],0)

    def test_no_preexisting_deterministic_schedule_fails_even_if_ai_schedule_exists(self):
        self.after["state"]["stores"]["recurring-schedules.sqlite3"]["task_count"]=0
        with patch.object(reboot,"live_snapshot",return_value=self.after), \
             patch.object(reboot,"baseline_path",return_value=Path("/fixture/baseline")), \
             patch.object(reboot,"read_baseline",return_value=self.before), \
             patch.object(reboot,"compare_reboot",
                          return_value={"schedule_persistence_proven":True}), \
             patch.object(watcher,"_state_path",return_value=Path("/fixture")), \
             patch.object(watcher,"linux_boot_time",return_value=self.boot), \
             patch.object(watcher,"current_invocation",return_value=INVOC):
            with self.assertRaisesRegex(watcher.PostBootRuleError,
                                         "no_local_deterministic_schedule_preexisting"):
                watcher.observe()

    def test_service_restart_during_observation_is_rejected(self):
        with patch.object(reboot,"live_snapshot",return_value=self.after), \
             patch.object(reboot,"baseline_path",return_value=Path("/fixture/baseline")), \
             patch.object(reboot,"read_baseline",return_value=self.before), \
             patch.object(reboot,"compare_reboot",
                          return_value={"schedule_persistence_proven":True}), \
             patch.object(watcher,"_state_path",return_value=Path("/fixture")), \
             patch.object(watcher,"linux_boot_time",return_value=self.boot), \
             patch.object(watcher,"current_invocation",
                          side_effect=[INVOC,OTHER]), \
             patch.object(watcher,"_read_observations",
                          return_value={"matching_completed_receipts":1,
                                        "latest_completed_utc":self.boot.isoformat()}):
            with self.assertRaisesRegex(watcher.PostBootRuleError,
                                         "service_invocation_changed"):
                watcher.observe()

    def test_old_linux_boot_fails_even_with_completed_forged_receipt(self):
        self.before["captured_utc"]=(self.boot+timedelta(minutes=1)).isoformat()
        with patch.object(reboot,"live_snapshot",return_value=self.after), \
             patch.object(reboot,"baseline_path",return_value=Path("/fixture/baseline")), \
             patch.object(reboot,"read_baseline",return_value=self.before), \
             patch.object(reboot,"compare_reboot",
                          return_value={"schedule_persistence_proven":True}), \
             patch.object(watcher,"_state_path",return_value=Path("/fixture")), \
             patch.object(watcher,"linux_boot_time",return_value=self.boot):
            with self.assertRaisesRegex(watcher.PostBootRuleError,
                                         "kernel_boot_not_newer"):
                watcher.observe()

    def test_invalid_wait_and_time_fields_rejected(self):
        for value in (-1,181,1.5,"30",True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(watcher.PostBootRuleError,
                                             "invalid_wait_limit"):
                    watcher.observe(value)
        with self.assertRaisesRegex(watcher.PostBootRuleError,
                                     "naive_runtime_timestamp"):
            watcher._date("2026-11-07T01:30:00")
        with self.assertRaisesRegex(watcher.PostBootRuleError,
                                     "invalid_runtime_timestamp"):
            watcher._date("garbage")

    def test_absent_real_vps_does_not_report_success(self):
        out=io.StringIO()
        with patch.object(watcher,"observe",
                          side_effect=PostError("not_a_real_vps")), \
             contextlib.redirect_stdout(out):
            code=watcher.main([])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(out.getvalue())["status"],"NOT_VERIFIED")


PostError = watcher.PostBootRuleError
