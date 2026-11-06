"""No reboot nor live Tencent access in tests. Real SQLite and owner-only file fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import verify_private_vps_reboot as checker

BOOT1 = "9a32b40f-2f11-4738-a569-129de6baf001"
BOOT2 = "9a32b40f-2f11-4738-a569-129de6baf002"


class RebootContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.home.chmod(0o700)
        self.state = self.home / ".ai-automation-studio"
        self.state.mkdir(mode=0o700)
        (self.state / "operator.json").write_text(
            '{"password_hash":"DUMMY_PRIVATE_FIXTURE_ONLY"}')
        (self.state / "operator.json").chmod(0o600)
        self.uid = os.geteuid()

    def tearDown(self):
        self.tmp.cleanup()

    def create_db(self, filename, table, id_field, run_table, ids):
        path = self.state / filename
        db = sqlite3.connect(path)
        with db:
            db.execute("CREATE TABLE " + table + "(" + id_field + " TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE " + run_table + "(id TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO " + table + "(" + id_field +
                           ") VALUES(?)", [(x,) for x in ids])
        db.close()
        path.chmod(0o600)
        return path

    def create_rule_schedule(self, ids=("rule-a",)):
        return self.create_db("recurring-schedules.sqlite3", "recurring_schedules",
                              "schedule_id", "recurring_schedule_runs", ids)

    def create_ai_schedule(self, ids=("ai-a",)):
        return self.create_db("readonly-ai-schedules.sqlite3", "ai_schedules",
                              "task_id", "ai_schedule_runs", ids)

    def snapshot(self, boot=BOOT1):
        return {"schema":1,"boot_id":boot,
                "state":checker.read_state_snapshot(self.home,self.uid),
                "captured_utc":"2026-11-07T00:00:00+00:00",
                "checks":{"systemd_service_active":True,
                          "systemd_service_enabled":True}}

    def test_read_only_snapshot_of_rule_and_ai_db_with_no_private_rows(self):
        self.create_rule_schedule(("a1", "a2"))
        self.create_ai_schedule(("b1",))
        snap=self.snapshot()
        self.assertEqual(snap["state"]["stores"]["recurring-schedules.sqlite3"]["task_count"],2)
        self.assertEqual(snap["state"]["stores"]["readonly-ai-schedules.sqlite3"]["task_count"],1)
        blob=json.dumps(snap)
        self.assertNotIn("DUMMY_PRIVATE_FIXTURE_ONLY",blob)
        self.assertNotIn('"a1"',blob)
        self.assertNotIn('"b1"',blob)
        self.assertEqual(snap["state"]["stores"]["recurring-schedules.sqlite3"]["run_count"],0)

    def test_real_readonly_db_unchanged_after_snapshot(self):
        path=self.create_rule_schedule(("rule-1",))
        before=path.read_bytes()
        self.snapshot()
        self.assertEqual(path.read_bytes(),before)

    def test_different_boot_same_schedule_and_owner_passes(self):
        self.create_rule_schedule()
        self.create_ai_schedule()
        before=self.snapshot()
        after=self.snapshot(BOOT2)
        result=checker.compare_reboot(before,after)
        self.assertEqual(result["status"],
                         "LIVE_PRIVATE_VPS_REBOOT_AND_SCHEDULE_PERSISTENCE_PASSED")
        self.assertEqual(result["task_count_preserved"],2)
        self.assertTrue(result["schedule_persistence_proven"])
        self.assertFalse(result["independent_tencent_identity_verified"])
        self.assertFalse(result["automatic_start_without_manual_intervention_proven"])
        self.assertFalse(result["24_hour_uptime_verified"])

    def test_same_boot_does_not_count_as_reboot(self):
        self.create_rule_schedule()
        before=self.snapshot()
        with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                     "reboot_not_observed"):
            checker.compare_reboot(before,self.snapshot())

    def test_no_existing_schedule_cannot_claim_task_persistence(self):
        before=self.snapshot()
        result=checker.compare_reboot(before,self.snapshot(BOOT2))
        self.assertEqual(result["status"],"LIVE_PRIVATE_VPS_REBOOT_SERVICE_ONLY_PASSED")
        self.assertIsNone(result["checks"]["schedule_id_sets_preserved"])
        self.assertFalse(result["schedule_persistence_proven"])

    def test_missing_database_after_reboot_fails(self):
        path=self.create_ai_schedule()
        before=self.snapshot()
        path.unlink()
        with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                     "durable_schedule_state_changed"):
            checker.compare_reboot(before,self.snapshot(BOOT2))

    def test_missing_ids_after_reboot_fails_even_if_count_unchanged(self):
        self.create_rule_schedule(("A","B"))
        before=self.snapshot()
        path=self.state/"recurring-schedules.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("UPDATE recurring_schedules SET schedule_id='C' WHERE schedule_id='A'")
        with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                     "durable_schedule_state_changed"):
            checker.compare_reboot(before,self.snapshot(BOOT2))

    def test_decreased_history_after_reboot_fails(self):
        path=self.create_rule_schedule()
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO recurring_schedule_runs(id) VALUES('receipt1')")
        before=self.snapshot()
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM recurring_schedule_runs")
        with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                     "durable_schedule_state_changed"):
            checker.compare_reboot(before,self.snapshot(BOOT2))

    def test_increased_run_history_does_not_fail(self):
        path=self.create_rule_schedule()
        before=self.snapshot()
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO recurring_schedule_runs(id) VALUES('receipt1')")
        result=checker.compare_reboot(before,self.snapshot(BOOT2))
        self.assertTrue(result["schedule_persistence_proven"])

    def test_rotated_owner_config_during_reboot_fails_conservatively(self):
        self.create_rule_schedule()
        before=self.snapshot()
        (self.state/"operator.json").write_text('{"password_hash":"CHANGED"}')
        after=self.snapshot(BOOT2)
        with self.assertRaisesRegex(checker.RebootAcceptanceError,"owner_config_changed"):
            checker.compare_reboot(before,after)

    def test_missing_owner_config_fails_before_writing_baseline(self):
        (self.state/"operator.json").unlink()
        with self.assertRaisesRegex(checker.RebootAcceptanceError,"state_file_missing"):
            self.snapshot()

    def test_insecure_db_permissions_and_symlink_refused(self):
        db=self.create_rule_schedule()
        db.chmod(0o644)
        with self.assertRaisesRegex(checker.RebootAcceptanceError,"permissions"):
            self.snapshot()
        db.unlink()
        destination=self.home/"outside.sqlite3"
        destination.write_bytes(b"not a db")
        db.symlink_to(destination)
        with self.assertRaisesRegex(checker.RebootAcceptanceError,"unsafe_state"):
            self.snapshot()

    def test_corrupt_sqlite_does_not_imply_survival(self):
        path=self.state/"recurring-schedules.sqlite3"
        path.write_bytes(b"not-a-sqlite-db")
        path.chmod(0o600)
        with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                     "schedule_state_unreadable"):
            self.snapshot()

    def test_new_baseline_created_once_with_private_permissions(self):
        self.create_rule_schedule()
        shot=self.snapshot()
        with patch("verify_private_vps_reboot.os.geteuid",return_value=self.uid):
            path=checker.baseline_path(self.home)
            self.assertTrue(checker.store_baseline(path,shot))
            self.assertEqual(checker.read_baseline(path),shot)
            with self.assertRaisesRegex(checker.RebootAcceptanceError,"existing_baseline"):
                checker.store_baseline(path,shot)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)

    def test_symlinked_or_world_readable_baseline_denied(self):
        self.create_rule_schedule()
        with patch("verify_private_vps_reboot.os.geteuid",return_value=self.uid):
            path=checker.baseline_path(self.home)
            path.symlink_to(self.state/"operator.json")
            with self.assertRaisesRegex(checker.RebootAcceptanceError,"existing_baseline"):
                checker.store_baseline(path,self.snapshot())
            with self.assertRaisesRegex(checker.RebootAcceptanceError,"unsafe_state_file"):
                checker.read_baseline(path)
            path.unlink()
            checker.store_baseline(path,self.snapshot())
            path.chmod(0o644)
            with self.assertRaisesRegex(checker.RebootAcceptanceError,"permissions"):
                checker.read_baseline(path)

    def test_baseline_malformed_schema_is_blocked(self):
        with patch("verify_private_vps_reboot.os.geteuid",return_value=self.uid):
            path=checker.baseline_path(self.home)
            path.write_text('{"schema":1,"boot_id":"'+BOOT1+'","state":{"stores":{}}}')
            path.chmod(0o600)
            with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                         "baseline_invalid_schema"):
                checker.read_baseline(path)

    def test_main_never_reboots_or_restarts_service(self):
        self.create_rule_schedule()
        with patch("verify_private_vps_reboot.live_snapshot",
                   side_effect=checker.RebootAcceptanceError("fixture_not_live")), \
             patch("verify_private_vps_reboot.subprocess.run") as call:
            import contextlib,io
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                code=checker.main(["--capture-before-reboot"])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(out.getvalue())["status"],"NOT_VERIFIED")
            call.assert_not_called()

    def test_linux_service_guard_refuses_root_even_with_active_service(self):
        with patch("verify_private_vps_reboot.os.geteuid",return_value=0), \
             patch("verify_private_vps_reboot.subprocess.run") as call:
            if sys.platform == "linux" and Path("/run/systemd/system").is_dir():
                with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                             "run_as_dedicated_nonroot"):
                    checker.live_snapshot()
                call.assert_not_called()

    def test_live_snapshot_requires_permanent_enabled_unit_and_exact_user(self):
        checks=[]
        def systemd(args):
            checks.append(tuple(args))
            return "fixtureuser" if args[0]=="show" else "enabled"
        synthetic={"owner_config_sha256":"f"*64,
                   "stores":{p[0]:{"present":False,"task_count":0,
                                  "ids_sha256":None,"run_count":0}
                             for p in checker.STATE_STORES}}
        with patch("verify_private_vps_reboot._linux_systemd",return_value=True), \
             patch("verify_private_vps_reboot.os.geteuid",return_value=1001), \
             patch("verify_private_vps_reboot.pwd.getpwuid",
                   return_value=SimpleNamespace(pw_name="fixtureuser",pw_dir="/fixture")), \
             patch("verify_private_vps_reboot._systemctl",side_effect=systemd), \
             patch("verify_private_vps_reboot.runtime.probe",
                   return_value={"status":"LIVE_PRIVATE_VPS_SNAPSHOT_PASSED"}), \
             patch("verify_private_vps_reboot.read_state_snapshot",
                   return_value=synthetic), \
             patch("verify_private_vps_reboot._boot_id",return_value=BOOT1):
            result=checker.live_snapshot()
        self.assertEqual(result["boot_id"],BOOT1)
        self.assertEqual(checks,[("show","--property=User","--value"),
                                  ("is-enabled",)])
        self.assertTrue(result["checks"]["systemd_service_enabled"])

    def test_live_snapshot_rejects_enabled_runtime_and_disabled(self):
        for state in ("enabled-runtime","disabled","static","masked"):
            with self.subTest(systemd=state):
                def systemd(args):
                    return "fixtureuser" if args[0]=="show" else state
                with patch("verify_private_vps_reboot._linux_systemd",return_value=True), \
                     patch("verify_private_vps_reboot.os.geteuid",return_value=1001), \
                     patch("verify_private_vps_reboot.pwd.getpwuid",
                           return_value=SimpleNamespace(pw_name="fixtureuser",pw_dir="/fixture")), \
                     patch("verify_private_vps_reboot._systemctl",side_effect=systemd), \
                     patch("verify_private_vps_reboot.runtime.probe") as probe:
                    with self.assertRaisesRegex(
                            checker.RebootAcceptanceError,"not_persistently_enabled"):
                        checker.live_snapshot()
                    probe.assert_not_called()

    def test_live_snapshot_rejects_wrong_service_user_and_root(self):
        with patch("verify_private_vps_reboot._linux_systemd",return_value=True), \
             patch("verify_private_vps_reboot.os.geteuid",return_value=1001), \
             patch("verify_private_vps_reboot.pwd.getpwuid",
                   return_value=SimpleNamespace(pw_name="fixtureuser",pw_dir="/fixture")), \
             patch("verify_private_vps_reboot._systemctl",return_value="anotheruser"):
            with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                         "matching_systemd_service_user"):
                checker.live_snapshot()
        with patch("verify_private_vps_reboot._linux_systemd",return_value=True), \
             patch("verify_private_vps_reboot.os.geteuid",return_value=0):
            with self.assertRaisesRegex(checker.RebootAcceptanceError,
                                         "run_as_dedicated_nonroot"):
                checker.live_snapshot()

    def test_main_after_without_a_real_baseline_fails(self):
        self.create_rule_schedule()
        with patch("verify_private_vps_reboot.live_snapshot",
                   return_value=self.snapshot()), \
             patch("verify_private_vps_reboot.baseline_path",
                   return_value=self.home/"missing-baseline.json"):
            import contextlib,io
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                code=checker.main(["--verify-after-reboot"])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(out.getvalue())["status"],"NOT_VERIFIED")
