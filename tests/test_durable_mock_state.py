"""Durable mock state tests (no live actions or external network)."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from durable_mock_state import DurableMockEventStore


class DurableMockStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dbpath = Path(self.tmp.name) / "mock-events.db"
        self.store = DurableMockEventStore(self.dbpath)

    def record(self, event_id="evt-01", payload=None, source="mock_inbox",
               decision="NEEDS_REVIEW", store=None):
        return (store or self.store).record(
            source, event_id, {"service_requested": "website"} if payload is None else payload,
            decision
        )

    def test_recorded_decision_survives_new_instance(self):
        self.assertEqual("RECORDED", self.record()["status"])
        restarted = DurableMockEventStore(self.dbpath)
        self.assertEqual(
            {"status": "DUPLICATE", "decision": "NEEDS_REVIEW", "external_dispatch_count": 0},
            self.record(store=restarted)
        )

    def test_conflicting_payload_after_restart_is_blocked(self):
        self.record()
        restarted = DurableMockEventStore(self.dbpath)
        result = self.record(payload={"service_requested": "other"}, store=restarted)
        self.assertEqual("CONFLICT", result["status"])
        self.assertEqual("NEEDS_REVIEW", result["decision"])

    def test_new_ids_are_distinct(self):
        self.assertEqual("RECORDED", self.record("a")["status"])
        self.assertEqual("RECORDED", self.record("b")["status"])

    def test_key_is_scoped_to_source(self):
        self.assertEqual("RECORDED", self.record(source="mock_a")["status"])
        self.assertEqual("RECORDED", self.record(source="mock_b")["status"])

    def test_canonical_dict_order_is_duplicate(self):
        self.record(payload={"a": 1, "b": 2})
        self.assertEqual("DUPLICATE", self.record(payload={"b": 2, "a": 1})["status"])

    def test_invalid_identity_or_source_does_not_write(self):
        self.assertEqual("INVALID_ID", self.record(event_id=" ")["status"])
        self.assertEqual("MOCK_ONLY", self.record(source="external_crm")["status"])
        self.assertEqual("RECORDED", self.record()["status"])

    def test_invalid_payload_and_decision_do_not_write(self):
        self.assertEqual("INVALID_PAYLOAD", self.record(payload={"bad": object()})["status"])
        self.assertEqual("INVALID_DECISION", self.record(decision="SENT")["status"])
        self.assertEqual("RECORDED", self.record()["status"])

    def test_100_competing_connections_cannot_insert_twice(self):
        def worker(_):
            instance = DurableMockEventStore(self.dbpath)
            return self.record(store=instance)["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(worker, range(100)))
        self.assertEqual(1, statuses.count("RECORDED"))
        self.assertEqual(99, statuses.count("DUPLICATE"))

    def test_db_only_stores_hash_not_inquiry_payload(self):
        secret = "SYNTHETIC_PRIVATE_CONTACT_DETAILS"
        result = self.record(payload={"message": secret})
        self.assertNotIn(secret, json.dumps(result))
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(
                [(64, "NEEDS_REVIEW")],
                db.execute("SELECT length(payload_hash), decision FROM mock_events").fetchall()
            )
            self.assertNotIn(secret, str(db.execute("SELECT * FROM mock_events").fetchall()))

    def test_invalid_database_location_rejected(self):
        with self.assertRaises(ValueError):
            DurableMockEventStore(":memory:")
        with self.assertRaises(ValueError):
            DurableMockEventStore(Path(self.tmp.name) / "missing" / "test.db")

    def test_persistence_is_real_file(self):
        self.record()
        self.assertTrue(self.dbpath.is_file())
        self.assertGreater(self.dbpath.stat().st_size, 0)

    def test_payload_size_limit_fails_closed(self):
        result = self.record(payload={"message": "x" * 262145})
        self.assertEqual("PAYLOAD_TOO_LARGE", result["status"])
        self.assertEqual(0, result["external_dispatch_count"])


if __name__ == "__main__":
    unittest.main()
