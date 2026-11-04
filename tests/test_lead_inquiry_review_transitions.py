"""Test that review-only CAS changes are atomic, durable and fail closed."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore
from lead_inquiry_pending_review import _event_ref
from lead_inquiry_review_transitions import MockReviewTransitions


class ReviewTransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mock.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.store.reserve("mock_inbox", "evt", {"service_requested": "web"})
        self.transitions = MockReviewTransitions(self.store)

    def change(self, *, store=None, identity="evt", version=0, preflight="PRECHECK_PASSED_NO_TRANSITION",
               reviewer="mock_operator"):
        t = MockReviewTransitions(store or self.store)
        return t.acknowledge_hold("mock_inbox", identity, event_ref=_event_ref("mock_inbox", identity),
                                  expected_version=version, preflight_status=preflight,
                                  reviewer_ref=reviewer)

    def test_pending_opens_at_version_zero(self):
        self.assertEqual({"status": "OPEN", "version": 0, "external_dispatch_count": 0,
                          "event_state_changed": False}, self.transitions.inspect("mock_inbox", "evt"))

    def test_mock_hold_increments_version_without_completing_event(self):
        result = self.change()
        self.assertEqual("ACKNOWLEDGED_HOLD", result["status"])
        self.assertEqual(1, result["version"])
        self.assertFalse(result["event_state_changed"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "evt"))

    def test_reopen_persists_review_state(self):
        self.change()
        reopened = MockReviewTransitions(DurableMockEventStore(self.path))
        self.assertEqual("ACKNOWLEDGED_HOLD", reopened.inspect("mock_inbox", "evt")["status"])
        self.assertEqual(1, reopened.inspect("mock_inbox", "evt")["version"])

    def test_repeat_from_old_version_is_stale(self):
        self.change()
        self.assertEqual("STALE_VERSION", self.change()["status"])

    def test_repeat_with_current_version_is_not_allowed(self):
        self.change()
        self.assertEqual("ALREADY_HELD", self.change(version=1)["status"])

    def test_expired_or_denied_preflight_cannot_modify(self):
        self.assertEqual("PREFLIGHT_REQUIRED", self.change(preflight="EXPIRED_OR_INVALID_WINDOW")["status"])
        self.assertEqual("OPEN", self.transitions.inspect("mock_inbox", "evt")["status"])

    def test_wrong_ref_cannot_modify(self):
        out = self.transitions.acknowledge_hold("mock_inbox", "evt", event_ref="a" * 64,
            expected_version=0, preflight_status="PRECHECK_PASSED_NO_TRANSITION", reviewer_ref="mock")
        self.assertEqual("EVENT_MISMATCH", out["status"])

    def test_invalid_version_rejected(self):
        for version in (-1, True, "0", 1.5):
            with self.subTest(version=version):
                self.assertEqual("INVALID_VERSION", self.change(version=version)["status"])

    def test_invalid_reviewer_rejected(self):
        self.assertEqual("INVALID_REVIEWER_REF", self.change(reviewer="email@example.com")["status"])

    def test_missing_or_completed_event_cannot_be_held(self):
        self.assertEqual("NOT_PENDING", self.change(identity="missing")["status"])
        self.store.mark_mock_completed("mock_inbox", "evt")
        self.assertEqual("NOT_PENDING", self.change()["status"])

    def test_concurrent_same_version_single_winner(self):
        def run(_):
            return self.change(store=DurableMockEventStore(self.path))["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(run, range(40)))
        self.assertEqual(1, statuses.count("ACKNOWLEDGED_HOLD"))
        self.assertEqual(39, statuses.count("STALE_VERSION"))
        self.assertEqual("PENDING", self.store.state("mock_inbox", "evt"))

    def test_no_identity_or_payload_in_result(self):
        result = self.change(reviewer="mock_operator_secret")
        serialized = json.dumps(result)
        self.assertNotIn("mock_operator_secret", serialized)
        self.assertNotIn("evt", serialized)
        self.assertNotIn("web", serialized)
        self.assertEqual(0, result["external_dispatch_count"])

    def test_invalid_mock_source_rejected(self):
        self.assertEqual("INVALID_EVENT", self.transitions.inspect("gmail", "evt")["status"])

    def test_database_failure_propagates_without_false_success(self):
        self.path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.transitions.inspect("mock_inbox", "evt")


if __name__ == "__main__":
    unittest.main()
