"""Stage 136: scoped note AES-GCM and explicitly authorized offline migration."""
import base64
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_scoped_agent_memory import LocalScopedAgentMemory, ScopedMemoryError
from local_scoped_memory_crypto import ScopedMemoryCipher, ScopedMemoryCryptoError, PREFIX
from migrate_scoped_memory_encryption import migrate_encrypted_notes, MigrationBlocked

KEY=base64.urlsafe_b64encode(b"Q"*32).decode("ascii")
WRONG_KEY=base64.urlsafe_b64encode(b"W"*32).decode("ascii")
TEXT="Confidential scoped note: source explanation belongs to owner."
def note(text=TEXT,revision=0):
    return {"project_id":"test_project","agent_id":"test_agent",
            "note_id":"critical_note","text":text,"source_kind":"OWNER_NOTE",
            "source_label":"owner direct statement","retention_days":30,
            "expected_revision":revision}


@unittest.skipUnless(importlib.util.find_spec("cryptography"),"PyCA optional dependency not installed")
class ScopedEncryptionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"scope.sqlite3"

    def test_fresh_encrypted_notes_are_ciphertext_and_provenance_enforced(self):
        memory=LocalScopedAgentMemory(self.path,encryption_key=KEY)
        self.assertTrue(memory.at_rest_encrypted)
        saved=memory.put(note())
        self.assertEqual(saved["text"],TEXT)
        selected,refs=memory.select("test_project","test_agent",["critical_note"])
        self.assertEqual(selected[0]["text"],TEXT)
        self.assertTrue(memory.require_revisions(refs,{"agent_id":"test_agent"}))
        with memory.lock:
            stored=memory.db.execute("SELECT text FROM scoped_agent_memory").fetchone()[0]
        self.assertTrue(stored.startswith(PREFIX))
        self.assertNotIn(TEXT,stored)
        revised=memory.put(note("Owner edited revised note",1))
        self.assertEqual(revised["revision"],2)
        with self.assertRaisesRegex(ScopedMemoryError,"scoped_memory_context_changed"):
            memory.require_revisions(refs,{"agent_id":"test_agent"})
        memory.close()
        recovered=LocalScopedAgentMemory(self.path,encryption_key=KEY)
        self.assertEqual(recovered.list("test_project","test_agent")[0]["text"],
                         "Owner edited revised note")
        recovered.close()
        with self.assertRaisesRegex(ScopedMemoryError,"encryption_key_required"):
            LocalScopedAgentMemory(self.path)
        with self.assertRaisesRegex(ScopedMemoryError,"scoped_memory_unlock_failed"):
            LocalScopedAgentMemory(self.path,encryption_key=WRONG_KEY)

    def test_authenticated_metadata_tampering_fails_closed(self):
        memory=LocalScopedAgentMemory(self.path,encryption_key=KEY)
        memory.put(note())
        with memory.db:
            memory.db.execute(
                "UPDATE scoped_agent_memory SET source_label='tampered source'")
        with self.assertRaisesRegex(ScopedMemoryError,"scoped_memory_unlock_failed"):
            memory.list("test_project","test_agent")
        memory.close()

    def test_plaintext_database_refuses_silent_encryption_conversion(self):
        memory=LocalScopedAgentMemory(self.path)
        memory.put(note())
        memory.close()
        with self.assertRaisesRegex(ScopedMemoryError,"requires_offline_migration"):
            LocalScopedAgentMemory(self.path,encryption_key=KEY)
        checker=LocalScopedAgentMemory(self.path)
        self.assertEqual(checker.list("test_project","test_agent")[0]["text"],TEXT)
        checker.close()

    def test_explicit_migration_transaction_reopen_and_ref_integrity(self):
        old=LocalScopedAgentMemory(self.path)
        old.put(note())
        refs=old.select("test_project","test_agent",["critical_note"])[1]
        old.close()
        with self.assertRaisesRegex(MigrationBlocked,"approval_required"):
            migrate_encrypted_notes(self.path,KEY)
        outcome=migrate_encrypted_notes(self.path,KEY,acknowledged=True)
        self.assertEqual(outcome["rows_migrated"],1)
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT text,revision,generation FROM scoped_agent_memory").fetchone()
        self.assertTrue(row[0].startswith(PREFIX))
        self.assertNotIn(TEXT,row[0])
        self.assertEqual(row[1],refs[0]["revision"])
        self.assertEqual(row[2],refs[0]["generation"])
        encrypted=LocalScopedAgentMemory(self.path,encryption_key=KEY)
        self.assertEqual(encrypted.list("test_project","test_agent")[0]["text"],TEXT)
        self.assertTrue(encrypted.require_revisions(refs,{"agent_id":"test_agent"}))
        encrypted.close()
        with self.assertRaisesRegex(MigrationBlocked,"already_encrypted"):
            migrate_encrypted_notes(self.path,KEY,acknowledged=True)

    def test_malformed_row_migration_rolls_back_entire_set(self):
        original=LocalScopedAgentMemory(self.path)
        original.put(note())
        other=note("Second text")
        other["note_id"]="second_note"
        original.put(other)
        original.close()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE scoped_agent_memory SET text='' WHERE note_id='second_note'")
        with self.assertRaisesRegex(MigrationBlocked,"invalid_plaintext"):
            migrate_encrypted_notes(self.path,KEY,acknowledged=True)
        with sqlite3.connect(self.path) as db:
            rows=db.execute("SELECT text FROM scoped_agent_memory ORDER BY note_id").fetchall()
        self.assertEqual(rows,[("Confidential scoped note: source explanation belongs to owner.",),("",)])
        self.assertFalse(any(ScopedMemoryCipher.is_sealed(x[0]) for x in rows))

    def test_corrupted_ciphertext_and_key_format_are_rejected(self):
        with self.assertRaisesRegex(ScopedMemoryCryptoError,"invalid_32_byte_key"):
            ScopedMemoryCipher("not-32-byte-key")
        cipher=ScopedMemoryCipher(KEY)
        payload=cipher.seal(TEXT,"test_project","test_agent","critical_note",1,
            "a"*32,"OWNER_NOTE","owner","2026-11-09T00:00:00+00:00")
        with self.assertRaisesRegex(ScopedMemoryCryptoError,"unlock_failed"):
            cipher.unseal(payload,"test_project","test_agent","other_note",1,
                "a"*32,"OWNER_NOTE","owner","2026-11-09T00:00:00+00:00")
        with self.assertRaisesRegex(ScopedMemoryCryptoError,"unlock_failed"):
            cipher.unseal(payload+"corruption","test_project","test_agent",
                "critical_note",1,"a"*32,"OWNER_NOTE","owner",
                "2026-11-09T00:00:00+00:00")


if __name__=="__main__":
    unittest.main()
