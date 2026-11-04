"""Failure injection and hard-link alias tests on disposable synthetic SQLite only."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
import lead_inquiry_synthetic_recovery as recovery


class RecoveryFaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "source.sqlite"
        self.backup = root / "backup.sqlite"
        self.restored = root / "restored.sqlite"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE mock_events (source TEXT,event_id TEXT,digest TEXT,state TEXT)")
            db.execute("INSERT INTO mock_events VALUES ('mock_inbox','private1','hash','PENDING')")
            db.execute("CREATE TABLE mock_synthetic_fixture_marker (marker TEXT)")
            db.execute("INSERT INTO mock_synthetic_fixture_marker VALUES "
                       "('AI_AUTOMATION_STUDIO_SYNTHETIC_V1')")
        self.source_before = self.source.read_bytes()

    def run_recovery(self):
        return recovery.rehearse_synthetic_backup_restore(
            self.source, self.backup, self.restored)

    def fail_at(self, stage):
        seen = []
        def checkpoint(current):
            seen.append(current)
            if current == stage:
                raise OSError("SECRET_FAILURE_PATH")
        with patch.object(recovery, "_recovery_checkpoint", checkpoint):
            output = self.run_recovery()
        self.assertIn(stage, seen)
        self.assertEqual("REHEARSAL_FAILED", output["status"])
        self.assertFalse(output["restored_matches_backup"] is True)
        self.assertNotIn("SECRET_FAILURE_PATH", str(output))
        self.assertNotIn("private1", str(output))
        self.assertEqual(0, output["external_dispatch_count"])
        self.assertEqual(self.source_before, self.source.read_bytes())
        return output

    def test_fail_after_backup_reserved_leaves_no_restore(self):
        self.fail_at("backup_reserved")
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.restored.exists())

    def test_fail_after_backup_copied_leaves_verified_source_and_backup(self):
        self.fail_at("backup_copied")
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.restored.exists())
        with sqlite3.connect(self.backup) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM mock_events").fetchone()[0])

    def test_fail_after_restore_reserved_never_claims_success(self):
        self.fail_at("restore_reserved")
        self.assertTrue(self.backup.exists())
        self.assertTrue(self.restored.exists())

    def test_fail_after_restore_copied_never_claims_success(self):
        self.fail_at("restore_copied")
        self.assertTrue(self.backup.exists())
        self.assertTrue(self.restored.exists())
        with sqlite3.connect(self.restored) as db:
            self.assertEqual("PENDING", db.execute(
                "SELECT state FROM mock_events").fetchone()[0])

    def test_partial_artifacts_are_not_overwritten_on_retry(self):
        self.fail_at("restore_reserved")
        before_backup = self.backup.read_bytes()
        before_restore = self.restored.read_bytes()
        retry = self.run_recovery()
        self.assertEqual("DESTINATION_EXISTS", retry["status"])
        self.assertEqual(before_backup, self.backup.read_bytes())
        self.assertEqual(before_restore, self.restored.read_bytes())

    def test_source_hardlink_is_rejected(self):
        linked = self.source.parent / "same-inode.sqlite"
        try:
            os.link(self.source, linked)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unsupported")
        result = self.run_recovery()
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED", result["status"])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.restored.exists())

    def test_hardlink_alias_as_backup_cannot_overwrite_source(self):
        try:
            os.link(self.source, self.backup)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unsupported")
        result = self.run_recovery()
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED", result["status"])
        self.assertEqual(self.source_before, self.source.read_bytes())
        self.assertFalse(self.restored.exists())

    def test_source_under_symlinked_parent_is_rejected(self):
        alias = self.source.parent / "shortcut"
        real = self.source.parent / "data"
        real.mkdir()
        path = real / "nested.sqlite"
        path.write_bytes(self.source.read_bytes())
        try:
            alias.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported")
        result = recovery.rehearse_synthetic_backup_restore(
            alias / "nested.sqlite", self.backup, self.restored)
        self.assertEqual("SOURCE_NOT_FOUND_OR_UNSUPPORTED", result["status"])
        self.assertFalse(self.backup.exists())

    def test_success_without_fault_is_unchanged(self):
        self.assertEqual("SYNTHETIC_REHEARSAL_PASSED", self.run_recovery()["status"])
        self.assertEqual(self.source_before, self.source.read_bytes())

    def test_unexpected_non_os_fault_is_not_suppressed(self):
        # Programmer bugs are not camouflaged as an environmental recovery failure.
        with patch.object(recovery, "_recovery_checkpoint",
                          side_effect=AssertionError("programming fault")):
            with self.assertRaises(AssertionError):
                self.run_recovery()
        self.assertEqual(self.source_before, self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
