"""Offline owner backup acceptance with disposable, non-user fixture state."""
from contextlib import closing, nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import offline_owner_backup as backup

REAL_OFFLINE_GUARD = backup._offline_guard

PASSWORD = "fixture strong passphrase never published in production"


class EncryptedOfflineRecoveryTests(unittest.TestCase):
    def setUp(self):
        try:
            backup._crypto()
        except backup.BackupError:
            self.skipTest("optional pinned cryptography dependency required")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A broad legacy test suite also binds the real Studio port. Only
        # functional fixture tests bypass the socket guard; the production
        # guard is independently tested below with a failing bind.
        self.guard_patch = patch.object(backup, "_offline_guard",
                                        side_effect=lambda: nullcontext())
        self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)
        self.base = Path(self.tmp.name)
        self.owner = self.base / ".ai-automation-studio"
        self.owner.mkdir(mode=0o700)
        self.operator = self.owner / "operator.json"
        self.operator.write_text(json.dumps({
            "version": 1, "salt": "aa" * 16, "scrypt_hash": "bb" * 32,
        }), encoding="utf-8")
        self.operator.chmod(0o600)
        self.vault = self.owner / "managed-webhooks-vault.json"
        self.vault.write_text('{"encrypted":"SECRET-FIXTURE-DATA"}', encoding="utf-8")
        self.vault.chmod(0o600)
        self.database = self.owner / "readonly-ai-schedules.sqlite3"
        with closing(sqlite3.connect(self.database)) as conn:
            with conn:
                conn.execute("CREATE TABLE test_task (id INTEGER PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO test_task(value) VALUES (?)", ("private-stored-task",))
        self.database.chmod(0o600)
        self.ledger = self.owner / "managed-webhook-ledgers"
        self.ledger.mkdir(mode=0o700)
        self.ledger_file = self.ledger / "abc.sqlite3"
        with closing(sqlite3.connect(self.ledger_file)) as conn:
            with conn:
                conn.execute("CREATE TABLE test_receipt (value TEXT)")
                conn.execute("INSERT INTO test_receipt VALUES ('receipt')")
        self.ledger_file.chmod(0o600)
        self.archive = self.base / "private.aasbak"
        self.test_folder = self.base / "test-restore"

    def _create(self):
        return backup.create(self.owner, self.archive, PASSWORD)

    def test_encrypted_roundtrip_and_create_only_rehearsal(self):
        before = {p.relative_to(self.owner).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.owner.rglob("*") if p.is_file()}
        result = self._create()
        self.assertEqual(result["status"], "ENCRYPTED_OFFLINE_BACKUP_CREATED")
        self.assertEqual(result["file_count"], 4)
        blob = self.archive.read_bytes()
        for text in (b"SECRET-FIXTURE-DATA", b"private-stored-task", b"scrypt_hash"):
            self.assertNotIn(text, blob)
        self.assertTrue(blob.startswith(backup.MAGIC))
        self.assertEqual(backup.verify(self.archive, PASSWORD)["file_count"], 4)
        with self.assertRaisesRegex(backup.BackupError, "consent"):
            backup.restore_test(self.archive, self.test_folder, PASSWORD)
        self.assertFalse(self.test_folder.exists())
        out = backup.restore_test(self.archive, self.test_folder, PASSWORD,
                                  consent=True)
        self.assertEqual(out["status"], "DISPOSABLE_RESTORE_TO_NEW_DIRECTORY_CHECKED")
        self.assertFalse(out["live_owner_state_modified"])
        self.assertEqual({p.relative_to(self.owner).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in self.owner.rglob("*") if p.is_file()}, before)
        self.assertEqual(self.vault.read_bytes(),
                         (self.test_folder / "managed-webhooks-vault.json").read_bytes())
        with closing(sqlite3.connect(self.test_folder / "readonly-ai-schedules.sqlite3")) as conn:
            self.assertEqual(conn.execute("SELECT value FROM test_task").fetchone()[0],
                             "private-stored-task")
        self.assertEqual((self.test_folder / "managed-webhook-ledgers" /
                          "abc.sqlite3").is_file(), True)

    def test_wrong_passphrase_and_ciphertext_tamper_refuse_restore(self):
        self._create()
        with self.assertRaisesRegex(backup.BackupError, "authentication"):
            backup.verify(self.archive, PASSWORD + "wrong")
        bad = bytearray(self.archive.read_bytes())
        bad[-1] ^= 1
        self.archive.write_bytes(bad)
        with self.assertRaisesRegex(backup.BackupError, "authentication"):
            backup.restore_test(self.archive, self.test_folder, PASSWORD,
                                consent=True)
        self.assertFalse(self.test_folder.exists())

    def test_existing_output_and_existing_restore_are_not_overwritten(self):
        self.archive.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self._create()
        self.assertEqual(self.archive.read_bytes(), b"original")
        self.archive.unlink()
        self._create()
        self.test_folder.mkdir()
        marker = self.test_folder / "must-stay.txt"
        marker.write_text("unchanged")
        with self.assertRaisesRegex(backup.BackupError, "must_not_exist"):
            backup.restore_test(self.archive, self.test_folder, PASSWORD,
                                consent=True)
        self.assertEqual(marker.read_text(), "unchanged")

    def test_live_server_lock_blocks_creation_before_reading_state(self):
        with patch.object(backup, "_offline_guard") as gate:
            gate.side_effect = backup.BackupError("stop_local_studio_before_backup")
            with self.assertRaisesRegex(backup.BackupError, "stop_local_studio"):
                self._create()
        self.assertFalse(self.archive.exists())

    def test_symlink_and_sqlite_journal_fail_closed(self):
        with tempfile.TemporaryDirectory() as second:
            foreign = Path(second) / "secret"
            foreign.write_text("unexpected")
            link = self.owner / "untrusted.txt"
            try:
                link.symlink_to(foreign)
            except OSError:
                # Some Windows CI identities lack symlink creation permission.
                pass
            else:
                with self.assertRaisesRegex(backup.BackupError, "symlink"):
                    self._create()
                link.unlink()
        Path(str(self.database) + "-wal").write_bytes(b"uncertain-wal")
        with self.assertRaisesRegex(backup.BackupError, "sidecar"):
            self._create()
        self.assertFalse(self.archive.exists())

    def test_bad_source_permissions_and_weak_password(self):
        self.operator.chmod(0o644)
        if os.name == "posix":
            with self.assertRaisesRegex(backup.BackupError, "permissions"):
                self._create()
        self.operator.chmod(0o600)
        with self.assertRaisesRegex(backup.BackupError, "passphrase"):
            backup.create(self.owner, self.archive, "short")

    def test_actual_offline_guard_refuses_busy_port(self):
        # Preserve real production guard; simulate a busy port without
        # contending with unrelated HTTP tests on 127.0.0.1:8765.
        original = REAL_OFFLINE_GUARD
        self.guard_patch.stop()
        try:
            with patch.object(backup.socket, "socket") as socket_ctor:
                socket_ctor.return_value.bind.side_effect = OSError("busy")
                with self.assertRaisesRegex(backup.BackupError, "stop_local_studio"):
                    with original():
                        pass
                socket_ctor.return_value.close.assert_called_once()
        finally:
            self.guard_patch.start()

    def test_archive_inside_owner_or_repository_denied_without_writes(self):
        with self.assertRaisesRegex(backup.BackupError, "inside_live_owner"):
            backup._outside_important_roots(self.owner / "unsafe.aasbak", self.owner)
        with self.assertRaisesRegex(backup.BackupError, "inside_live_owner"):
            backup._outside_important_roots(ROOT / "unsafe.aasbak", self.owner)
        self.assertFalse((self.owner / "unsafe.aasbak").exists())

    def test_reject_absolute_traversal_and_nonportable_paths(self):
        for name in ("../operator.json", "/tmp/private", "a//b",
                     "a/../../b", "x\\y", "C:secret", "a/CON",
                     "a/invalid.", "a/<bad>.json", "a/a?.json", "a/file|name", "a/name*"):
            with self.subTest(name=name):
                self.assertFalse(backup._valid_rel(name))
        self.assertTrue(backup._valid_rel("managed-webhook-ledgers/key_1.sqlite3"))

    def test_modified_manifest_or_duplicate_archive_entry_refused(self):
        files = {"operator.json": self.operator.read_bytes()}
        plain = backup._pack(files)
        self.assertEqual(backup._unpack(plain), files)
        with zipfile.ZipFile(io.BytesIO(plain)) as z:
            self.assertIn(backup.MANIFEST, z.namelist())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(backup.MANIFEST, b'{"version":1,"files":{}}')
            z.writestr("operator.json", b"fixture")
        with self.assertRaisesRegex(backup.BackupError, "manifest"):
            backup._unpack(buf.getvalue())
        other = io.BytesIO()
        with zipfile.ZipFile(other, "w") as z:
            z.writestr(backup.MANIFEST, b'{"version":1,"files":{}}')
            with patch("warnings.warn"):
                z.writestr(backup.MANIFEST, b'{"version":1,"files":{}}')
        with self.assertRaisesRegex(backup.BackupError, "entries"):
            backup._unpack(other.getvalue())

    def test_corrupt_owner_verifier_rejected_before_backup_and_restore(self):
        original = self.operator.read_bytes()
        self.operator.write_text('{"version":99}')
        with self.assertRaisesRegex(backup.BackupError, "owner_config_invalid"):
            self._create()
        self.assertFalse(self.archive.exists())
        self.operator.write_bytes(original)
        invalid = backup._pack({"operator.json": b'{"version":99}'})
        with self.assertRaisesRegex(backup.BackupError, "owner_config_invalid"):
            backup._unpack(invalid)

    def test_malicious_file_directory_collision_rejected(self):
        files = {"operator.json": self.operator.read_bytes(),
                 "nested": b"file", "nested/child.txt": b"subfile"}
        with self.assertRaisesRegex(backup.BackupError, "archive_path_collision"):
            backup._unpack(backup._pack(files))

    def test_corrupt_sqlite_snapshot_fails_without_archive(self):
        self.database.write_bytes(b"broken sqlite bytes")
        with self.assertRaisesRegex(backup.BackupError, "sqlite_snapshot_failed"):
            self._create()
        self.assertFalse(self.archive.exists())

    def test_backup_file_count_and_total_byte_limits(self):
        with patch.object(backup, "MAX_TOTAL", 50):
            with self.assertRaisesRegex(backup.BackupError, "backup_size"):
                self._create()
        self.assertFalse(self.archive.exists())

    def test_export_file_stays_encrypted_and_unlogged(self):
        exported = self.owner / "exports"
        exported.mkdir(mode=0o700)
        (exported / "private report.md").write_text("very private notes")
        (exported / "private report.md").chmod(0o600)
        result = self._create()
        self.assertEqual(result["file_count"], 5)
        self.assertNotIn("private", json.dumps(result).lower())
        self.assertNotIn(b"very private notes", self.archive.read_bytes())
        self.assertEqual(backup.inspect(self.archive, PASSWORD)["exports/private report.md"],
                         b"very private notes")


if __name__ == "__main__":
    unittest.main()
