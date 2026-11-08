"""Read-only semantic recovery of encrypted note text from a sealed backup."""
import base64
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"app"))
import offline_owner_backup as backup
import verify_encrypted_scoped_backup as verifier
from local_scoped_agent_memory import LocalScopedAgentMemory

PASS="fixture-strong-two-keys-independent-2026"
KEY=base64.urlsafe_b64encode(b"M"*32).decode("ascii")
WRONG=base64.urlsafe_b64encode(b"N"*32).decode("ascii")
PRIVATE="private proof secret that must NEVER appear in archived ciphertext"


@unittest.skipUnless(importlib.util.find_spec("cryptography"),
                     "Pinned crypto tests run in dedicated CI workflow")
class ScopedEncryptedBackupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.owner=self.base/".ai-automation-studio"
        self.owner.mkdir(mode=0o700)
        (self.owner/"operator.json").write_text(json.dumps({
            "version":1,"salt":"a"*32,"scrypt_hash":"b"*64}),encoding="ascii")
        (self.owner/"operator.json").chmod(0o600)
        self.archive=self.base/"backup.aasbak"
        self.guard=patch.object(backup,"_offline_guard",side_effect=nullcontext)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def _memory(self,key):
        return LocalScopedAgentMemory(
            self.owner/"scoped-agent-memory.sqlite3",encryption_key=key)

    def _put(self,store,idx="note_one"):
        return store.put({
            "project_id":"recovery_project","agent_id":"research_agent",
            "note_id":idx,"text":PRIVATE,
            "source_kind":"OWNER_NOTE","source_label":"synthetic owner",
            "retention_days":30,"expected_revision":0})

    def test_double_secret_actual_decrypt_with_no_plaintext_staging(self):
        store=self._memory(KEY)
        self._put(store)
        self._put(store,"second_note")
        store.close()
        result=backup.create(self.owner,self.archive,PASS)
        self.assertEqual(result["status"],"ENCRYPTED_OFFLINE_BACKUP_CREATED")
        self.assertNotIn(PRIVATE.encode(),self.archive.read_bytes())
        files_before={p.relative_to(self.owner).as_posix():p.read_bytes()
                      for p in self.owner.rglob("*") if p.is_file()}
        verified=verifier.verify_scoped_notes_archive(self.archive,PASS,KEY)
        self.assertEqual(verified["status"],
                         "ENCRYPTED_SCOPED_NOTES_RECOVERABLE_FROM_ARCHIVE")
        self.assertEqual(verified["notes_decrypted_and_validated"],2)
        self.assertEqual(verified["scopes_validated"],1)
        self.assertTrue(verified["independent_scoped_key_verified"])
        self.assertFalse(verified["plaintext_written_to_disk"])
        self.assertFalse(verified["physical_off_device_copy_verified"])
        self.assertNotIn(PRIVATE,json.dumps(verified))
        self.assertFalse((self.base/"restored").exists())
        self.assertEqual(
            files_before,{p.relative_to(self.owner).as_posix():p.read_bytes()
                      for p in self.owner.rglob("*") if p.is_file()})

    def test_wrong_backup_passphrase_or_separate_note_key_fail_closed(self):
        store=self._memory(KEY)
        self._put(store)
        store.close()
        backup.create(self.owner,self.archive,PASS)
        for passphrase,key in [(PASS+"wrong",KEY),(PASS,WRONG),(PASS,"")]:
            with self.subTest(passphrase=passphrase==PASS,key=key==KEY),\
                 self.assertRaises(verifier.RecoveryProofError):
                verifier.verify_scoped_notes_archive(self.archive,passphrase,key)
        self.assertEqual(len(list(self.base.glob("*.aasbak"))),1)

    def test_plaintext_mixed_and_missing_note_data_not_misreported(self):
        store=LocalScopedAgentMemory(self.owner/"scoped-agent-memory.sqlite3")
        self._put(store)
        store.close()
        backup.create(self.owner,self.archive,PASS)
        with self.assertRaisesRegex(verifier.RecoveryProofError,
                                    "plaintext_or_mixed"):
            verifier.verify_scoped_notes_archive(self.archive,PASS,KEY)
        self.archive.unlink()
        (self.owner/"scoped-agent-memory.sqlite3").unlink()
        store=self._memory(KEY)
        store.close()
        backup.create(self.owner,self.archive,PASS)
        with self.assertRaisesRegex(verifier.RecoveryProofError,
                                    "no_encrypted_notes"):
            verifier.verify_scoped_notes_archive(self.archive,PASS,KEY)

    def test_encrypted_archive_with_tampered_authenticated_scope_fails(self):
        store=self._memory(KEY)
        self._put(store)
        # Corrupt the domain-bound source label while preserving SQLite integrity.
        with store.db:
            store.db.execute(
                "UPDATE scoped_agent_memory SET source_label='injected provenance'")
        store.close()
        backup.create(self.owner,self.archive,PASS)
        with self.assertRaisesRegex(verifier.RecoveryProofError,
                                    "metadata_or_decryption_failed"):
            verifier.verify_scoped_notes_archive(self.archive,PASS,KEY)

    def test_no_scoped_database_does_not_claim_key_recovery(self):
        backup.create(self.owner,self.archive,PASS)
        with self.assertRaisesRegex(verifier.RecoveryProofError,
                                    "database_absent"):
            verifier.verify_scoped_notes_archive(self.archive,PASS,KEY)


if __name__=="__main__":
    unittest.main()
