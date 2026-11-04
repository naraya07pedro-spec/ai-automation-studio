"""End-to-end mock review facade tests, no production auth or external effects."""
import concurrent.futures
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore
from lead_inquiry_review_orchestrator import MockReviewOrchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.store.reserve("mock_inbox", "event-1", {"message": "SECRET_INQUIRY"})
        self.now = 1000
        self.allowed = True
        self.app = self.make_app()

    def make_app(self):
        return MockReviewOrchestrator(
            DurableMockEventStore(self.path), authority=lambda *_: self.allowed,
            clock=lambda: self.now)

    def request(self, app=None, event_id="event-1"):
        return (app or self.app).request_hold_grant(
            "mock_inbox", event_id,
            evidence_digest=hashlib.sha256(b"synthetic evidence").hexdigest(),
            operator_ref="mock_operator")

    def test_supported_round_trip(self):
        grant = self.request()
        self.assertEqual("MOCK_GRANT_ISSUED", grant["status"])
        held = self.app.hold_with_grant("mock_inbox", "event-1", token=grant["token"])
        self.assertEqual("ACKNOWLEDGED_HOLD", held["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-1"))
        self.assertEqual(0, held["external_dispatch_count"])

    def test_replay_across_instances_denied(self):
        token = self.request()["token"]
        self.app.hold_with_grant("mock_inbox", "event-1", token=token)
        self.assertEqual("GRANT_ALREADY_USED",
                         self.make_app().hold_with_grant("mock_inbox", "event-1", token=token)["status"])

    def test_default_deny_without_policy(self):
        app = MockReviewOrchestrator(self.store)
        self.assertEqual("ISSUER_NOT_CONFIGURED", self.request(app)["status"])

    def test_denied_authority(self):
        self.allowed = False
        self.assertEqual("NOT_AUTHORIZED", self.request()["status"])

    def test_expiry_is_owned_by_clock(self):
        token = self.request()["token"]
        self.now += 60
        self.assertEqual("GRANT_EXPIRED",
                         self.app.hold_with_grant("mock_inbox", "event-1", token=token)["status"])

    def test_public_method_rejects_verifier_and_now_injection(self):
        with self.assertRaises(TypeError):
            self.app.request_hold_grant("mock_inbox", "event-1",
                evidence_digest="a"*64, operator_ref="mock_operator",
                verifier=lambda *_: True)
        with self.assertRaises(TypeError):
            self.app.hold_with_grant("mock_inbox", "event-1", token="x", now=0)

    def test_pending_list_redacted(self):
        rows = self.app.list_pending()
        self.assertEqual(1, len(rows))
        text = json.dumps(rows)
        self.assertNotIn("SECRET_INQUIRY", text)
        self.assertNotIn("event-1", text)
        self.assertNotIn("mock_inbox", text)

    def test_inspection_does_not_change_state(self):
        self.assertEqual("OPEN", self.app.inspect_review("mock_inbox", "event-1")["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-1"))

    def test_missing_evidence_fails_closed(self):
        result = self.app.request_hold_grant("mock_inbox", "event-1",
            evidence_digest="", operator_ref="mock_operator")
        self.assertEqual("INVALID_EVIDENCE_DIGEST", result["status"])

    def test_non_mock_source_denied(self):
        result = self.app.request_hold_grant("gmail", "event-1",
            evidence_digest="a"*64, operator_ref="mock_operator")
        self.assertEqual("INVALID_EVENT", result["status"])

    def test_concurrent_single_token_one_winner(self):
        token = self.request()["token"]
        def action(_):
            return self.make_app().hold_with_grant(
                "mock_inbox", "event-1", token=token)["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(action, range(24)))
        self.assertEqual(1, outcomes.count("ACKNOWLEDGED_HOLD"))
        self.assertEqual(23, outcomes.count("GRANT_ALREADY_USED"))

    def test_bad_storage_does_not_fallback(self):
        class BrokenStore:
            def _connect(self):
                raise sqlite3.OperationalError("SECRET_PATH")
        # Facade constructors may fail closed; no successful grant on error.
        with self.assertRaises(sqlite3.OperationalError):
            MockReviewOrchestrator(BrokenStore())

    def test_no_public_direct_review_writer(self):
        self.assertFalse(hasattr(self.app, "acknowledge_hold"))
        self.assertFalse(hasattr(self.app, "mark_mock_completed"))

    def test_hold_result_does_not_echo_token(self):
        token = self.request()["token"]
        result = self.app.hold_with_grant("mock_inbox", "event-1", token=token)
        self.assertNotIn(token, json.dumps(result))
        self.assertFalse(result["event_state_changed"])


if __name__ == "__main__":
    unittest.main()
