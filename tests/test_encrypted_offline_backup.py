"""Stage #111 disposable encrypted backup test, never owner actual state."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
if HAS_CRYPTO:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import backup_local_state as backup


@unittest.skipUnless(HAS_CRYPTO, "optional cryptography dependency not installed")
class EncryptedOfflineRecoveryTests(unittest.TestCase):
    PASSWORD = "A-Very-Long-Private-Offline-Backup-Key-2026"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "owner"
        self.source.mkdir(mode=0o700)
        (self.source / "operator.json").write_text('{"fixture":"owner"}')
        (self.source / "operator.json").chmod(0o600)
        (self.source / "managed-webhook-ledgers").mkdir(mode=0o700)
        ledger = self.source / "managed-webhook-ledgers" / "ledger.sqlite3"
        with sqlite3.connect(ledger) as db:
            db.execute("CREATE TABLE ledgers(id INTEGER PRIMARY KEY, status TEXT)")
            db.execute("INSERT INTO ledgers(status) VALUES ('ACKED')")
        ledger.chmod(0o600)
        self.archive = self.base / "state.aasbkp"
        self.restore_path = self.base / "recovered"

    def create(self):
        with patch.object(backup, "_studio_stopped", return_value=True):
            return backup.create_backup(self.source, self.archive, self.PASSWORD)

    def test_e2e_encrypt_verify_disposable_restore_with_live_sqlite_snapshot(self):
        status = self.create()
        self.assertEqual(status["status"], "ENCRYPTED_OWNER_BACKUP_CREATED")
        encrypted = self.archive.read_bytes()
        self.assertTrue(encrypted.startswith(backup.MAGIC))
        self.assertNotIn(b"ACKED", encrypted)
        self.assertNotIn(b"fixture", encrypted)
        self.assertNotIn(b"operator.json", encrypted)
        self.assertEqual(backup.verify_backup(self.archive, self.PASSWORD), {
            "status": "ENCRYPTED_BACKUP_INTEGRITY_VERIFIED",
            "file_count": 2, "sqlite_count": 1,
            "can_restore_active_owner_state": False,
        })
        result = backup.restore_disposable(
            self.archive, self.restore_path, self.PASSWORD,
            active_root=self.source,
        )
        self.assertEqual(result["status"], "DISPOSABLE_RESTORE_VERIFIED")
        self.assertEqual((self.restore_path / "operator.json").read_text(),
                         '{"fixture":"owner"}')
        self.assertTrue((self.source / "operator.json").is_file())
        recovered = self.restore_path / "managed-webhook-ledgers" / "ledger.sqlite3"
        with sqlite3.connect(f"{recovered.as_uri()}?mode=ro", uri=True) as db:
            self.assertEqual(db.execute("SELECT status FROM ledgers").fetchone(),
                             ("ACKED",))
        with self.assertRaisesRegex(backup.BackupError, "restore_destination_exists"):
            backup.restore_disposable(self.archive, self.restore_path,
                                      self.PASSWORD, active_root=self.source)

    def test_refuses_backup_while_local_server_active(self):
        with patch.object(backup, "_studio_stopped", return_value=False):
            with self.assertRaisesRegex(backup.BackupError, "stop_studio"):
                backup.create_backup(self.source, self.archive, self.PASSWORD)
        self.assertFalse(self.archive.exists())

    def test_wrong_passphrase_and_tampering_fail_before_destination_creation(self):
        self.create()
        with self.assertRaisesRegex(backup.BackupError, "authentication_failed"):
            backup.restore_disposable(
                self.archive, self.restore_path, "DefinitelyWrong-Password",
                active_root=self.source,
            )
        self.assertFalse(self.restore_path.exists())
        original = self.archive.read_bytes()
        altered = self.base / "tampered.aasbkp"
        altered.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaisesRegex(backup.BackupError, "authentication_failed"):
            backup.verify_backup(altered, self.PASSWORD)
        self.assertFalse(self.restore_path.exists())

    def test_refuses_existing_archive_or_active_root_restore(self):
        self.create()
        first = self.archive.read_bytes()
        with patch.object(backup, "_studio_stopped", return_value=True):
            with self.assertRaisesRegex(backup.BackupError, "destination_exists"):
                backup.create_backup(self.source, self.archive, self.PASSWORD)
        self.assertEqual(first, self.archive.read_bytes())
        with self.assertRaisesRegex(backup.BackupError, "active_owner_state_restore_forbidden"):
            backup.restore_disposable(
                self.archive, self.source, self.PASSWORD, active_root=self.source
            )

    def test_refuses_symlinked_source_and_unsafe_inventory(self):
        linked = self.base / "alias"
        try:
            linked.symlink_to(self.source, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with patch.object(backup, "_studio_stopped", return_value=True):
            with self.assertRaisesRegex(backup.BackupError, "symlink"):
                backup.create_backup(linked, self.archive, self.PASSWORD)
        linked.unlink()
        (self.source / "bad-path").symlink_to(self.base / "external")
        with patch.object(backup, "_studio_stopped", return_value=True):
            with self.assertRaisesRegex(backup.BackupError, "symlink"):
                backup.create_backup(self.source, self.archive, self.PASSWORD)

    def test_invalid_archive_member_and_bad_sqlite_rejected_even_with_valid_aead(self):
        manifest = {
            "version": 1, "kind": "offline-owner-state",
            "captured_utc": "2026-11-07T00:00:00Z",
            "entries": [{"path": "../escape.txt", "type": "file",
                         "size": 4, "sha256": hashlib.sha256(b"evil").hexdigest()}],
        }
        raw = BytesIO()
        with zipfile.ZipFile(raw, "w") as zf:
            zf.writestr(backup.MANIFEST, json.dumps(manifest))
            zf.writestr("../escape.txt", b"evil")
        forged = self.base / "crafted.aasbkp"
        forged.write_bytes(backup._encrypt(raw.getvalue(), self.PASSWORD))
        with self.assertRaises(backup.BackupError):
            backup.restore_disposable(
                forged, self.restore_path, self.PASSWORD, active_root=self.source
            )
        self.assertFalse(self.restore_path.exists())
        (self.source / "broken.sqlite3").write_bytes(b"not sqlite")
        (self.source / "broken.sqlite3").chmod(0o600)
        with patch.object(backup, "_studio_stopped", return_value=True):
            with self.assertRaises(backup.BackupError):
                backup.create_backup(self.source, self.archive, self.PASSWORD)

    def test_rejects_unsafe_paths_and_short_password(self):
        for name in ("/absolute", "a/../escape", "C:/bad", "a\\b", "CON.txt",
                     "a//b", "a/", "_aas_manifest.json", "a/<bad>"):
            self.assertFalse(backup._valid_name(name), name)
        self.assertTrue(backup._valid_name("managed-webhook-ledgers/id.sqlite3"))
        with self.assertRaisesRegex(backup.BackupError, "passphrase_policy"):
            backup._encrypt(b"data", "short")

    def test_cli_never_prints_passphrase_or_paths(self):
        self.create()
        output = StringIO()
        with patch.object(backup.getpass, "getpass",
                          return_value=self.PASSWORD), redirect_stdout(output):
            rc = backup.main(["verify", "--archive", str(self.archive)])
        self.assertEqual(rc, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "ENCRYPTED_BACKUP_INTEGRITY_VERIFIED")
        self.assertNotIn(self.PASSWORD, output.getvalue())
        self.assertNotIn(str(self.archive), output.getvalue())
        self.assertNotIn("operator.json", output.getvalue())


if __name__ == "__main__":
    unittest.main()
