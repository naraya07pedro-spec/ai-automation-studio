"""Offline marked-synthetic recovery regression tests. Never use real data."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_synthetic_recovery import rehearse_synthetic_backup_restore


class SyntheticRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.db"
        self.backup = Path(self.temp.name) / "backup.db"
        self.restored = Path(self.temp.name) / "restored.db"

    def create(self, *, legacy=False, marked=True):
        with sqlite3.connect(self.source) as db:
            if legacy:
                db.execute("CREATE TABLE mock_events (source TEXT,event_id TEXT,"
                           "payload_hash TEXT,decision TEXT)")
                db.execute("INSERT INTO mock_events VALUES ('mock_input','id1','digest',"
                           "'READY_FOR_REVIEW')")
            else:
                db.execute("CREATE TABLE mock_events (source TEXT,event_id TEXT,"
                           "digest TEXT,state TEXT)")
                db.execute("INSERT INTO mock_events VALUES ('mock_input','id1','digest',"
                           "'COMPLETED')")
                db.execute("INSERT INTO mock_events VALUES ('mock_input','id2','digest',"
                           "'PENDING')")
            if marked:
                db.execute("CREATE TABLE mock_synthetic_fixture_marker (marker TEXT)")
                db.execute("INSERT INTO mock_synthetic_fixture_marker VALUES "
                           "('AI_AUTOMATION_STUDIO_SYNTHETIC_V1')")

    def run_rehearsal(self):
        return rehearse_synthetic_backup_restore(self.source, self.backup, self.restored)

    def test_state_backup_restored_and_historical_completion_preserved(self):
        self.create()
        before = self.source.read_bytes()
        result = self.run_rehearsal()
        self.assertEqual("SYNTHETIC_REHEARSAL_PASSED", result["status"])
        self.assertEqual("EVENT_STATE_V1", result["schema"])
        self.assertEqual(2, result["total_rows"])
        self.assertTrue(result["restored_matches_backup"])
        self.assertEqual(before, self.source.read_bytes())
        with sqlite3.connect(self.restored) as db:
            self.assertEqual([("COMPLETED",), ("PENDING",)],
                             db.execute("SELECT state FROM mock_events ORDER BY event_id").fetchall())

    def test_legacy_backup_preserves_classification_not_completion(self):
        self.create(legacy=True)
        result = self.run_rehearsal()
        self.assertEqual("SYNTHETIC_REHEARSAL_PASSED", result["status"])
        self.assertEqual("LEGACY_DECISION_V1", result["schema"])
        with sqlite3.connect(self.restored) as db:
            self.assertEqual("READY_FOR_REVIEW", db.execute(
                "SELECT decision FROM mock_events").fetchone()[0])

    def test_no_marker_rejected_before_creating_outputs(self):
        self.create(marked=False)
        self.assertEqual("SYNTHETIC_MARKER_REQUIRED", self.run_rehearsal()["status"])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.restored.exists())

    def test_missing_source_fails_closed(self):
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED", self.run_rehearsal()["status"])

    def test_source_directory_rejected(self):
        result = rehearse_synthetic_backup_restore(self.temp.name, self.backup, self.restored)
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED", result["status"])

    def test_existing_backup_is_never_overwritten(self):
        self.create()
        self.backup.write_bytes(b"EXISTING_BACKUP")
        self.assertEqual("REHEARSAL_FAILED", self.run_rehearsal()["status"])
        self.assertEqual(b"EXISTING_BACKUP", self.backup.read_bytes())
        self.assertFalse(self.restored.exists())

    def test_existing_restore_is_never_overwritten(self):
        self.create()
        self.restored.write_bytes(b"EXISTING_RESTORE")
        self.assertEqual("REHEARSAL_FAILED", self.run_rehearsal()["status"])
        self.assertEqual(b"EXISTING_RESTORE", self.restored.read_bytes())

    def test_path_collision_source_backup(self):
        self.create()
        result = rehearse_synthetic_backup_restore(self.source, self.source, self.restored)
        self.assertEqual("PATH_COLLISION", result["status"])

    def test_path_collision_backup_restore(self):
        self.create()
        result = rehearse_synthetic_backup_restore(self.source, self.backup, self.backup)
        self.assertEqual("PATH_COLLISION", result["status"])

    def test_unknown_schema_never_backup(self):
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE mock_events (source TEXT,data TEXT)")
        self.assertEqual("UNSUPPORTED_SOURCE_SCHEMA", self.run_rehearsal()["status"])
        self.assertFalse(self.backup.exists())

    def test_corrupt_source_never_backup(self):
        self.source.write_bytes(b"NOT SQLITE PRIVATE")
        self.assertEqual("UNSUPPORTED_SOURCE_SCHEMA", self.run_rehearsal()["status"])
        self.assertFalse(self.backup.exists())

    def test_unrecognized_status_denied(self):
        self.create()
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO mock_events VALUES ('mock_input','id3','digest','SENT')")
        self.assertEqual("UNSUPPORTED_SOURCE_SCHEMA", self.run_rehearsal()["status"])

    def test_symlinked_source_rejected(self):
        self.create()
        alias = Path(self.temp.name) / "alias.db"
        try:
            alias.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported")
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED",
                         rehearse_synthetic_backup_restore(alias, self.backup, self.restored)["status"])

    def test_missing_destination_parent_rejected(self):
        self.create()
        invalid = Path(self.temp.name) / "nonexistent" / "backup.db"
        self.assertEqual("REHEARSAL_FAILED",
                         rehearse_synthetic_backup_restore(self.source, invalid, self.restored)["status"])

    def test_result_never_exposes_source_content(self):
        self.create()
        result = str(self.run_rehearsal())
        self.assertNotIn("mock_input", result)
        self.assertNotIn("id1", result)
        self.assertNotIn(str(self.source), result)

    def test_no_external_dispatch_or_migration(self):
        self.create()
        result = self.run_rehearsal()
        self.assertEqual(0, result["external_dispatch_count"])
        self.assertFalse(result["historical_migration_performed"])

    def test_second_rehearsal_cannot_overwrite_previous_artifacts(self):
        self.create()
        self.assertEqual("SYNTHETIC_REHEARSAL_PASSED", self.run_rehearsal()["status"])
        prior = self.backup.read_bytes()
        self.assertEqual("REHEARSAL_FAILED", self.run_rehearsal()["status"])
        self.assertEqual(prior, self.backup.read_bytes())


if __name__ == "__main__":
    unittest.main()
