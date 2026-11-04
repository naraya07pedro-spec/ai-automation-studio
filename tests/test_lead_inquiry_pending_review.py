"""Mock pending review tests; no external dispatch or status elevation."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore
from lead_inquiry_pending_review import MockPendingReview, _ACK


class PendingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.review = MockPendingReview(self.store)

    def pending(self, identity="event-1", payload=None):
        self.assertEqual("RESERVED_PENDING", self.store.reserve(
            "mock_inbox", identity, {} if payload is None else payload)["status"])

    def test_empty_list(self):
        self.assertEqual([], self.review.list_pending())

    def test_list_redacts_identity_and_payload(self):
        secret = "INQUIRY_PRIVATE_VALUE"
        self.pending("customer-email@example.com", {"message": secret})
        result = self.review.list_pending()
        self.assertEqual(1, len(result))
        self.assertEqual("PENDING", result[0]["state"])
        self.assertFalse(result[0]["acknowledged"])
        serialized = json.dumps(result)
        self.assertNotIn("customer-email@example.com", serialized)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("mock_inbox", serialized)

    def test_acknowledge_keeps_event_pending(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        result = self.review.acknowledge(ref, "operator_1", _ACK)
        self.assertEqual("ACKNOWLEDGED_STILL_PENDING", result["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-1"))
        self.assertTrue(self.review.list_pending()[0]["acknowledged"])
        self.assertEqual("NEEDS_RECONCILIATION", self.store.reserve("mock_inbox", "event-1", {})["status"])

    def test_acknowledgement_survives_new_instance(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        self.review.acknowledge(ref, "operator_1", _ACK)
        second = MockPendingReview(DurableMockEventStore(self.path))
        self.assertTrue(second.list_pending()[0]["acknowledged"])
        self.assertEqual("ALREADY_ACKNOWLEDGED", second.acknowledge(ref, "operator_2", _ACK)["status"])

    def test_unknown_ref_not_found(self):
        self.pending()
        self.assertEqual("NOT_FOUND_OR_NOT_PENDING",
                         self.review.acknowledge("a" * 64, "operator_1", _ACK)["status"])

    def test_acknowledgement_literal_required(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        self.assertEqual("ACKNOWLEDGEMENT_REQUIRED",
                         self.review.acknowledge(ref, "operator_1", "yes")["status"])
        self.assertFalse(self.review.list_pending()[0]["acknowledged"])

    def test_invalid_reviewer_and_ref_blocked(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        self.assertEqual("INVALID_REVIEWER_REF",
                         self.review.acknowledge(ref, "person name@example.com", _ACK)["status"])
        self.assertEqual("INVALID_EVENT_REF",
                         self.review.acknowledge("not-a-digest", "operator_1", _ACK)["status"])
        self.assertEqual("INVALID_REVIEWER_REF",
                         self.review.acknowledge(ref, "", _ACK)["status"])

    def test_completed_event_not_visible(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        self.store.mark_mock_completed("mock_inbox", "event-1")
        self.assertEqual([], self.review.list_pending())
        self.assertEqual("NOT_FOUND_OR_NOT_PENDING",
                         self.review.acknowledge(ref, "operator_1", _ACK)["status"])

    def test_invalid_limits_rejected(self):
        for n in (0, 101, -1, True, "10"):
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.review.list_pending(limit=n)

    def test_list_limit_is_bounded(self):
        for i in range(3):
            self.pending(f"id-{i}")
        self.assertEqual(2, len(self.review.list_pending(limit=2)))

    def test_store_corruption_or_lock_error_not_masked_as_success(self):
        self.pending()
        self.path.unlink()
        self.assertRaises(sqlite3.OperationalError, self.review.list_pending)

    def test_no_external_dispatch_in_ack_result(self):
        self.pending()
        ref = self.review.list_pending()[0]["event_ref"]
        output = self.review.acknowledge(ref, "operator_1", _ACK)
        self.assertEqual(0, output["external_dispatch_count"])

    def test_distinct_identity_keys_generate_distinct_refs(self):
        self.pending("id-1")
        self.pending("id-2")
        refs = [row["event_ref"] for row in self.review.list_pending()]
        self.assertEqual(2, len(set(refs)))


if __name__ == "__main__":
    unittest.main()
