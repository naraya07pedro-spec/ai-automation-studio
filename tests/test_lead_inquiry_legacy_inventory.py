"""Read-only historical mock schema inventory; no automatic migration."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_legacy_inventory import inventory_mock_sqlite


class LegacyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "history.sqlite"

    def create_state(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE mock_events (source TEXT, event_id TEXT,"
                       "digest TEXT, state TEXT)")
            db.execute("INSERT INTO mock_events VALUES (?,?,?,?)",
                       ("mock_inbox", "PRIVATE_EVENT", "hash", "COMPLETED"))
            db.execute("INSERT INTO mock_events VALUES (?,?,?,?)",
                       ("mock_inbox", "OTHER_EVENT", "hash", "PENDING"))

    def create_decisions(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE mock_events (source TEXT, event_id TEXT,"
                       "payload_hash TEXT, decision TEXT)")
            db.execute("INSERT INTO mock_events VALUES (?,?,?,?)",
                       ("mock_inbox", "LEGACY_SECRET", "hash", "NEEDS_REVIEW"))

    def test_state_schema_reports_pending_and_historical_completed(self):
        self.create_state()
        result = inventory_mock_sqlite(self.path)
        self.assertEqual("INVENTORY_ONLY", result["status"])
        self.assertEqual("EVENT_STATE_V1", result["schema"])
        self.assertEqual({"COMPLETED": 1, "PENDING": 1, "TOTAL": 2}, result["counts"])
        self.assertFalse(result["migration_performed"])

    def test_legacy_decisions_are_not_equated_with_completed(self):
        self.create_decisions()
        result = inventory_mock_sqlite(self.path)
        self.assertEqual("LEGACY_DECISION_V1", result["schema"])
        self.assertEqual({"NEEDS_REVIEW": 1, "READY_FOR_REVIEW": 0, "TOTAL": 1},
                         result["counts"])
        self.assertNotIn("COMPLETED", result["counts"])

    def test_missing_file_is_not_created(self):
        self.assertEqual("FILE_NOT_FOUND_OR_UNSUPPORTED",
                         inventory_mock_sqlite(self.path)["status"])
        self.assertFalse(self.path.exists())

    def test_directory_is_rejected(self):
        self.assertEqual("FILE_NOT_FOUND_OR_UNSUPPORTED",
                         inventory_mock_sqlite(self.tmp.name)["status"])

    def test_symlink_is_rejected(self):
        self.create_state()
        link = Path(self.tmp.name) / "alias.sqlite"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on test runner")
        self.assertEqual("FILE_NOT_FOUND_OR_UNSUPPORTED",
                         inventory_mock_sqlite(link)["status"])

    def test_empty_sqlite_file_no_table(self):
        with sqlite3.connect(self.path):
            pass
        self.assertEqual("NO_EVENT_TABLE", inventory_mock_sqlite(self.path)["status"])

    def test_unknown_schema_is_denied(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE mock_events (source TEXT, event_id TEXT, payload TEXT)")
        self.assertEqual("UNRECOGNIZED_SCHEMA", inventory_mock_sqlite(self.path)["status"])

    def test_extra_column_does_not_silently_match(self):
        self.create_state()
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE mock_events ADD COLUMN extra TEXT")
        self.assertEqual("UNRECOGNIZED_SCHEMA", inventory_mock_sqlite(self.path)["status"])

    def test_unknown_state_value_fails_closed(self):
        self.create_state()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO mock_events VALUES (?,?,?,?)",
                       ("mock_inbox", "INVALID", "hash", "DELIVERED"))
        result = inventory_mock_sqlite(self.path)
        self.assertEqual("UNRECOGNIZED_VALUES", result["status"])
        self.assertIsNone(result["counts"])

    def test_unknown_legacy_decision_value_fails_closed(self):
        self.create_decisions()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO mock_events VALUES (?,?,?,?)",
                       ("mock_inbox", "INVALID", "hash", "SENT"))
        self.assertEqual("UNRECOGNIZED_VALUES",
                         inventory_mock_sqlite(self.path)["status"])

    def test_inventory_does_not_mutate_rows_or_file(self):
        self.create_state()
        before = self.path.read_bytes()
        self.assertEqual("INVENTORY_ONLY", inventory_mock_sqlite(self.path)["status"])
        after = self.path.read_bytes()
        self.assertEqual(before, after)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM mock_events").fetchone()[0])

    def test_no_sensitive_identifiers_returned(self):
        self.create_state()
        result = str(inventory_mock_sqlite(self.path))
        self.assertNotIn("PRIVATE_EVENT", result)
        self.assertNotIn("OTHER_EVENT", result)
        self.assertNotIn(str(self.path), result)

    def test_corrupt_file_fails_without_details(self):
        self.path.write_bytes(b"PRIVATE_UNKNOWN_DATA_NOT_SQLITE")
        result = inventory_mock_sqlite(self.path)
        self.assertEqual("INSPECTION_FAILED", result["status"])
        self.assertNotIn("PRIVATE_UNKNOWN_DATA", str(result))

    def test_empty_known_schema_yields_zero_counts(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE mock_events (source TEXT,event_id TEXT,"
                       "digest TEXT,state TEXT)")
        self.assertEqual({"COMPLETED": 0, "PENDING": 0, "TOTAL": 0},
                         inventory_mock_sqlite(self.path)["counts"])

    def test_unrecognized_object_named_mock_events_is_denied(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE VIEW mock_events AS SELECT 'x' AS source")
        self.assertEqual("UNRECOGNIZED_SCHEMA",
                         inventory_mock_sqlite(self.path)["status"])

    def test_no_external_dispatch(self):
        self.create_state()
        self.assertEqual(0, inventory_mock_sqlite(self.path)["external_dispatch_count"])


if __name__ == "__main__":
    unittest.main()
