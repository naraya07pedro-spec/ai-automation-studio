"""Stage 123: encrypted two-copy recovery drill, no real user state or network.

Crypto-enabled CI tests real AES-256-GCM sealed owner snapshots and SQLite.
The final semantic audit uses the real Stage 118 six-ledger fixture pipeline.
"""
from contextlib import closing, nullcontext
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"tests"))
import offline_owner_backup as backup
import owner_encrypted_recovery_drill as drill
import owner_acceptance_audit as auditor
import test_owner_acceptance_audit as stage118

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None
PASSWORD="strong-fixture-stage-123-encrypted-passphrase"


@unittest.skipUnless(HAS_CRYPTO,"Dedicated Stage123 workflow installs pinned cryptography")
class OwnerSealedRecoveryDrillTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.owner=base/".ai-automation-studio"
        self.owner.mkdir(mode=0o700)
        self.operator=self.owner/"operator.json"
        self.operator.write_text(json.dumps({
            "version":1,"salt":"a"*32,"scrypt_hash":"b"*64
        }),encoding="utf-8")
        self.operator.chmod(0o600)
        self.dbpath=self.owner/"agent-task-plans.sqlite3"
        with closing(sqlite3.connect(self.dbpath)) as conn:
            with conn:
                conn.execute("CREATE TABLE known_state (id INTEGER PRIMARY KEY, content TEXT)")
                conn.execute("INSERT INTO known_state(content) VALUES('private plan')")
        self.dbpath.chmod(0o600)
        self.primary=base/"primary.aasbak"
        self.secondary=base/"copy.aasbak"
        self.restored=base/"disposable"
        self.guard=patch.object(backup,"_offline_guard",side_effect=nullcontext)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def run_drill(self,**kwargs):
        return drill.drill(self.owner,self.primary,self.secondary,PASSWORD,**kwargs)

    def test_verified_sealed_copy_and_independent_decryption_without_plaintext_restore(self):
        before=self.dbpath.read_bytes()
        result=self.run_drill()
        self.assertEqual(result["status"],"SEALED_TWO_COPY_RECOVERY_DRILL_VERIFIED")
        self.assertEqual(result["authenticated_archive_file_count"],2)
        self.assertTrue(result["copies_byte_identical"])
        self.assertTrue(result["primary_archive_authenticated"])
        self.assertTrue(result["secondary_archive_authenticated"])
        self.assertFalse(result["plaintext_restored"])
        self.assertFalse(result["physical_off_device_copy_independently_verified"])
        self.assertFalse(result["semantic_six_ledger_audit_passed"])
        self.assertFalse(result["external_credentials_recovered"])
        self.assertFalse(self.restored.exists())
        self.assertEqual(before,self.dbpath.read_bytes())
        self.assertEqual(self.primary.read_bytes(),self.secondary.read_bytes())
        self.assertEqual(result["sealed_sha256"],hashlib.sha256(
            self.secondary.read_bytes()).hexdigest())
        for secret in (PASSWORD,"private plan","scrypt_hash"):
            self.assertNotIn(secret,json.dumps(result))
            self.assertNotIn(secret.encode(),self.primary.read_bytes())

    def test_optional_disposable_restore_actual_sqlite_readback(self):
        got=self.run_drill(restore_directory=self.restored,plaintext_consent=True)
        self.assertEqual(got["status"],"DISPOSABLE_TWO_COPY_RESTORE_CHECKED")
        self.assertTrue(got["plaintext_restored"])
        self.assertTrue(got["disposable_plaintext_folder_requires_owner_cleanup"])
        with closing(sqlite3.connect(self.restored/"agent-task-plans.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT content FROM known_state").fetchone()[0],"private plan")
        self.assertEqual((self.restored/"operator.json").read_bytes(),
                         self.operator.read_bytes())

    def test_explicit_consent_and_plan_requirements_fail_before_creating_archives(self):
        for kwargs in (
            {"restore_directory":self.restored},
            {"plan_id":"a"*24},
            {"plan_id":"not-a-plan","restore_directory":self.restored,"plaintext_consent":True},
            {"expected_host":"fixture.n8n.cloud"},
        ):
            with self.subTest(kwargs=kwargs),self.assertRaises(drill.RecoveryDrillError):
                self.run_drill(**kwargs)
            self.assertFalse(self.primary.exists())
            self.assertFalse(self.secondary.exists())

    def test_primary_equals_secondary_rejected_without_files(self):
        with self.assertRaisesRegex(drill.RecoveryDrillError,"must_differ"):
            drill.drill(self.owner,self.primary,self.primary,PASSWORD)
        self.assertFalse(self.primary.exists())

    def test_existing_secondary_preserved_and_primary_not_created(self):
        self.secondary.write_bytes(b"old-owner-archive")
        with self.assertRaisesRegex(drill.RecoveryDrillError,"must_not_exist"):
            self.run_drill()
        self.assertEqual(self.secondary.read_bytes(),b"old-owner-archive")
        self.assertFalse(self.primary.exists())

    def test_primary_must_not_be_overwritten(self):
        self.primary.write_bytes(b"some-prior-sealed-data")
        with self.assertRaisesRegex(drill.RecoveryDrillError,"must_not_exist"):
            self.run_drill()
        self.assertEqual(self.primary.read_bytes(),b"some-prior-sealed-data")
        self.assertFalse(self.secondary.exists())

    def test_archive_inside_git_or_owner_denied_without_creating_files(self):
        with self.assertRaisesRegex(backup.BackupError,"inside_live_owner"):
            drill.drill(self.owner,self.owner/"unsafe.aasbak",self.secondary,PASSWORD)
        with self.assertRaisesRegex(backup.BackupError,"inside_live_owner"):
            drill.drill(self.owner,ROOT/"unsafe.aasbak",self.secondary,PASSWORD)
        self.assertFalse(self.secondary.exists())

    def test_bad_encryption_passphrase_aborts_without_any_archive(self):
        with self.assertRaisesRegex(backup.BackupError,"passphrase"):
            drill.drill(self.owner,self.primary,self.secondary,"weak")
        self.assertFalse(self.primary.exists())
        self.assertFalse(self.secondary.exists())

    def test_secondary_copy_corruption_detected_before_any_restore(self):
        real=drill._sealed_copy_once
        def mutate_after_copy(*args,**kwargs):
            real(*args,**kwargs)
            destination=args[1]
            raw=bytearray(destination.read_bytes())
            raw[-1]^=1
            destination.write_bytes(raw)
        with patch.object(drill,"_sealed_copy_once",side_effect=mutate_after_copy):
            with self.assertRaisesRegex(drill.RecoveryDrillError,
                                        "encrypted_copies_not_identical"):
                self.run_drill(restore_directory=self.restored,plaintext_consent=True)
        self.assertTrue(self.primary.exists())
        self.assertTrue(self.secondary.exists())
        self.assertFalse(self.restored.exists())

    def test_secondary_creation_failure_keeps_original_sealed_archive(self):
        with patch.object(drill,"_sealed_copy_once",
                          side_effect=drill.RecoveryDrillError("synthetic_disk_full")):
            with self.assertRaisesRegex(drill.RecoveryDrillError,"synthetic_disk_full"):
                self.run_drill()
        self.assertTrue(self.primary.exists())
        self.assertFalse(self.secondary.exists())
        self.assertEqual(backup.verify(self.primary,PASSWORD)["file_count"],2)

    def test_wrong_copy_credential_fails_decryption_after_equal_hash(self):
        actual=drill.verify
        count=[0]
        def failed_second(path,password):
            count[0]+=1
            if count[0]==2:
                return actual(path,password+"wrong")
            return actual(path,password)
        with patch.object(drill,"verify",side_effect=failed_second):
            with self.assertRaisesRegex(backup.BackupError,
                                        "backup_authentication_failed"):
                self.run_drill()
        self.assertTrue(self.primary.exists())
        self.assertTrue(self.secondary.exists())
        self.assertFalse(self.restored.exists())

    def test_cli_error_json_does_not_expose_paths_or_secrets(self):
        with patch.object(drill.Path,"home",return_value=self.owner.parent), \
             patch.object(drill.getpass,"getpass",side_effect=[PASSWORD,PASSWORD]), \
             patch("builtins.input",return_value="RESTORE_TEST_PLAINTEXT"), \
             patch("sys.stdout",new_callable=io.StringIO) as output:
            code=drill.main(["--primary",str(self.primary),
                             "--secondary",str(self.secondary),
                             "--plan-id","not-valid"])
        self.assertEqual(code,2)
        report=json.loads(output.getvalue())
        self.assertEqual(report["status"],"RECOVERY_DRILL_NOT_PROVEN")
        self.assertNotIn(str(self.owner.parent),output.getvalue())
        self.assertNotIn(PASSWORD,output.getvalue())
        self.assertFalse(self.primary.exists())


@unittest.skipUnless(HAS_CRYPTO,"Dedicated Stage123 workflow installs pinned cryptography")
class OwnerSixLedgerRestoreIntegrationTests(unittest.TestCase):
    def test_two_encrypted_copies_restore_actual_six_ledger_chain(self):
        fixture=stage118.OfflineEvidenceAcceptance(methodName=
            "test_complete_authentic_local_chain_and_zero_network")
        try:
            fixture.setUp()
            base=fixture.home.parent
            primary=base/"new-owner-sealed.aasbak"
            secondary=base/"independent-archive-copy.aasbak"
            restored=base/"recovered-six-ledgers"
            with patch.object(backup,"_offline_guard",side_effect=nullcontext):
                result=drill.drill(
                    fixture.home,primary,secondary,stage118.PASSPHRASE,
                    restore_directory=restored,plaintext_consent=True,
                    plan_id=stage118.PID,
                    expected_host=stage118.N8N_HOST,
                    expected_workflow=stage118.N8N_WID,
                    expected_node=stage118.N8N_NODE)
            self.assertEqual(result["status"],"RESTORED_SIX_LEDGER_CHAIN_CONSISTENT")
            self.assertEqual(result["local_ledger_layers_checked"],6)
            self.assertTrue(result["semantic_six_ledger_audit_passed"])
            self.assertFalse(result["physical_off_device_copy_independently_verified"])
            self.assertEqual(fixture.external_posts,1)
            self.assertEqual(fixture.get_calls,1)
            self.assertFalse(result["provider_business_effect_verified"])
        finally:
            fixture.doCleanups()

    def test_semantic_audit_failure_rejects_ready_claim_and_keeps_plaintext_forensics(self):
        fixture=stage118.OfflineEvidenceAcceptance(methodName=
            "test_complete_authentic_local_chain_and_zero_network")
        try:
            fixture.setUp()
            base=fixture.home.parent
            primary=base/"sealed.aasbak"
            copy=base/"secondary.aasbak"
            restored=base/"drill-restored"
            with patch.object(backup,"_offline_guard",side_effect=nullcontext):
                with self.assertRaisesRegex(auditor.AcceptanceError,
                                             "provider_host_not_owner_pinned"):
                    drill.drill(
                        fixture.home,primary,copy,stage118.PASSPHRASE,
                        restore_directory=restored,plaintext_consent=True,
                        plan_id=stage118.PID,
                        expected_host="wrong.app.n8n.cloud")
            self.assertTrue(primary.exists())
            self.assertTrue(copy.exists())
            self.assertTrue(restored.exists())
        finally:
            fixture.doCleanups()


if __name__=="__main__":
    unittest.main()
