"""Regression checks: legacy bare-preflight mutation is disabled.

All supported mock review writes require a single-use grant. These tests
ensure the old public method cannot be revived as an accidental bypass.
"""
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


class LegacyReviewDisabledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mock.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.store.reserve("mock_inbox", "evt", {"service_requested": "web"})
        self.transitions = MockReviewTransitions(self.store)

    def old_write(self, identity="evt", version=0,
                  preflight="PRECHECK_PASSED_NO_TRANSITION", reviewer="mock_operator"):
        return self.transitions.acknowledge_hold(
            "mock_inbox", identity, event_ref=_event_ref("mock_inbox", identity),
            expected_version=version, preflight_status=preflight,
            reviewer_ref=reviewer)

    def test_read_only_inspection_still_available(self):
        self.assertEqual({"status": "OPEN", "version": 0, "external_dispatch_count": 0,
                          "event_state_changed": False},
                         self.transitions.inspect("mock_inbox", "evt"))

    def test_legacy_success_string_no_longer_writes(self):
        self.assertEqual("LEGACY_PATH_DISABLED", self.old_write()["status"])
        self.assertEqual("OPEN", self.transitions.inspect("mock_inbox", "evt")["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "evt"))

    def test_legacy_returns_no_customer_or_reviewer_information(self):
        output = self.old_write(reviewer="SECRET_OPERATOR")
        rendered = json.dumps(output)
        self.assertNotIn("SECRET_OPERATOR", rendered)
        self.assertNotIn("evt", rendered)
        self.assertNotIn("web", rendered)
        self.assertFalse(output["event_state_changed"])
        self.assertEqual(0, output["external_dispatch_count"])

    def test_legacy_forged_versions_still_denied(self):
        for version in (0, 1, -1, True, "0"):
            with self.subTest(version=version):
                self.assertEqual("LEGACY_PATH_DISABLED",
                                 self.old_write(version=version)["status"])

    def test_legacy_invalid_preflight_still_denied(self):
        self.assertEqual("LEGACY_PATH_DISABLED",
                         self.old_write(preflight="EXPIRED_OR_INVALID_WINDOW")["status"])

    def test_legacy_nonexistent_event_still_denied(self):
        self.assertEqual("LEGACY_PATH_DISABLED",
                         self.old_write(identity="missing")["status"])

    def test_legacy_concurrent_attempts_all_denied(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(lambda _: self.old_write()["status"], range(40)))
        self.assertEqual(["LEGACY_PATH_DISABLED"] * 40, statuses)
        self.assertEqual("OPEN", self.transitions.inspect("mock_inbox", "evt")["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "evt"))

    def test_legacy_cannot_create_review_state_row(self):
        self.old_write()
        with self.store._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM mock_review_transitions").fetchone()[0]
        self.assertEqual(0, count)

    def test_forged_event_ref_cannot_bypass_disabled_legacy_path(self):
        result = self.transitions.acknowledge_hold(
            "mock_inbox", "evt", event_ref="f" * 64,
            expected_version=0, preflight_status="PRECHECK_PASSED_NO_TRANSITION",
            reviewer_ref="mock_operator")
        self.assertEqual("LEGACY_PATH_DISABLED", result["status"])
        self.assertEqual("OPEN", self.transitions.inspect("mock_inbox", "evt")["status"])

    def test_review_state_persists_as_open_after_reopening(self):
        self.old_write()
        reopened = MockReviewTransitions(DurableMockEventStore(self.path))
        self.assertEqual("OPEN", reopened.inspect("mock_inbox", "evt")["status"])
        self.assertEqual(0, reopened.inspect("mock_inbox", "evt")["version"])

    def test_direct_completion_cannot_hide_pending_review(self):
        self.assertEqual("DIRECT_COMPLETION_DISABLED",
                         self.store.mark_mock_completed("mock_inbox", "evt")["status"])
        self.assertEqual("OPEN", self.transitions.inspect("mock_inbox", "evt")["status"])

    def test_invalid_mock_source_rejected_for_inspection(self):
        self.assertEqual("INVALID_EVENT", self.transitions.inspect("gmail", "evt")["status"])

    def test_storage_failure_does_not_produce_fake_read_success(self):
        self.path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.transitions.inspect("mock_inbox", "evt")


if __name__ == "__main__":
    unittest.main()
